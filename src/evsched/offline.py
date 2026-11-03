"""Offline perfect-information benchmark: one solve over the whole day with every session known.

v2 additions: the billed peak can be a rolling mean (``demand_window``), tapering batteries are
modelled with the linear two-stage bound (``taper``), and ``solve_offline_month`` solves all days
of a month jointly with one shared billed peak, which is the true monthly optimum.
"""

from dataclasses import replace
from typing import Optional

import cvxpy as cp
import numpy as np
from acnportal.acnsim.models import Linear2StageBattery
from acnportal.signals.tariffs import TimeOfUseTariff

from . import MAX_RATE, PERIOD, TARIFF
from .model import (DemandSpec, EVBlock, Infrastructure, Result, RobustSpec, TaperSpec, Weights, build,
                    run_solver, solve)
from .solar import SolarProfile


def _day_inputs(events, network, start, taper: bool):
    evs = [e.ev for _, e in events.queue if e.event_type == "Plugin"]
    infra = Infrastructure.from_network(network)
    T = max(ev.departure for ev in evs)
    ub = np.zeros((len(evs), T))
    for i, ev in enumerate(evs):
        ub[i, ev.arrival: ev.departure] = MAX_RATE
    block = EVBlock(
        np.array([infra.station_ids.index(ev.station_id) for ev in evs]),
        ub,
        np.array([ev.requested_energy for ev in evs]),
    )
    spec = None
    if taper and all(isinstance(ev._battery, Linear2StageBattery) for ev in evs):
        b = [ev._battery for ev in evs]
        spec = TaperSpec(np.array([x._capacity for x in b]), np.array([x._current_charge / x._capacity for x in b]),
                         np.array([x._max_power for x in b]), b[0]._transition_soc)
    tariff = TimeOfUseTariff(TARIFF)
    prices = np.array(tariff.get_tariffs(start, T, PERIOD))
    return evs, infra, block, spec, prices, tariff.get_demand_charge(start)


def _station_rates(evs, rates, T):
    out = {}
    for i, ev in enumerate(evs):
        out.setdefault(ev.station_id, np.zeros(T))
        out[ev.station_id] += rates[i]
    return out


def solve_offline(events, network, start, weights: Weights, hard: bool = False,
                  solar: Optional[SolarProfile] = None, robust: Optional[RobustSpec] = None,
                  taper: bool = False, demand_window: int = 1):
    """Return ({station_id: rates over the day}, model Result) for the sessions in ``events``."""
    evs, infra, block, spec, prices, dc = _day_inputs(events, network, start, taper)
    T = block.ub.shape[1]
    if solar is not None:
        # perfect foresight: the realised PV of a SolarDay (E9), else E5's forecast
        pv_hat, pv_dev = (solar.actual(0, T), np.zeros(T)) if hasattr(solar, "actual") else solar.window(0, T)
        robust = replace(robust or RobustSpec(None, None), pv_hat=pv_hat, pv_dev=pv_dev)
    demand = DemandSpec(demand_window) if demand_window > 1 else None
    res: Result = solve(block, infra, prices, weights, PERIOD, demand_charge=dc, robust=robust, hard=hard,
                        demand=demand, taper=spec)
    return _station_rates(evs, res.rates, T), res


def solve_offline_month(day_events, network, weights: Weights, taper: bool = True, demand_window: int = 3,
                        solar=None):
    """Jointly optimise every day of a month with one shared billed peak.

    ``day_events`` is a list of (events, start). The objective is the sum of the daily objectives
    (without their own demand terms) plus demand_charge * max_d max_t Pbar_{d,t}. ``solar`` (one
    ``SolarDay`` per day) makes cost, valley and the billed peak act on net import with realised PV.
    Returns ([{station_id: rates}] per day, month peak kW, wall time, solver time).
    """
    day_w = replace(weights, demand=0.0, hinge=0.0)
    parts, cons, objective, peak = [], [], 0, cp.Variable(nonneg=True)
    dc = 0.0
    for d, (events, start) in enumerate(day_events):
        evs, infra, block, spec, prices, dc = _day_inputs(events, network, start, taper)
        T = block.ub.shape[1]
        robust = None if solar is None else RobustSpec(solar[d].actual(0, T), np.zeros(T))
        b = build(block, infra, prices, day_w, PERIOD, robust=robust, demand=DemandSpec(demand_window), taper=spec)
        cons += b.cons + [peak >= b.billed]
        objective = objective + b.objective
        parts.append((evs, block, b))
    prob = cp.Problem(cp.Minimize(objective + weights.demand * dc * peak), cons)
    elapsed = run_solver(prob)
    if prob.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Monthly offline problem {prob.status}")
    rates = [_station_rates(evs, np.clip(b.R.value, 0, block.ub), block.ub.shape[1]) for evs, block, b in parts]
    return rates, float(peak.value), elapsed, prob.solver_stats.solve_time or 0.0

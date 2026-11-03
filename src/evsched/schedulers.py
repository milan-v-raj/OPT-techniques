"""ACN-Sim scheduling algorithms built on the convex model."""

from dataclasses import replace
from typing import Dict, Optional

import numpy as np
from acnportal.algorithms import BaseAlgorithm, SimpleRampdown

from . import MAX_RATE, PERIOD
from .forecast import ArrivalForecaster
from .metrics import rolling_mean
from .model import DemandSpec, EVBlock, Infrastructure, RobustSpec, Weights, solve
from .solar import SolarProfile

DAY = 24 * 60 // PERIOD


class StationRampdown(SimpleRampdown):
    """``SimpleRampdown`` keyed by station id. acnportal's ``apply_upper_bound_estimate`` looks the
    bounds up by station id while ``SimpleRampdown`` keys them by session id, so with real session
    ids the stock estimator never lowers anything."""

    def get_maximum_rates(self, sessions):
        by_session = super().get_maximum_rates(sessions)
        return {s.station_id: by_session[s.session_id] for s in sessions}


class ConvexMPC(BaseAlgorithm):
    """Model-predictive scheduler: re-solves the convex model every period over the horizon up
    to the latest departure of the active EVs and applies the first period.

    Args:
        weights: objective weights (see ``model.Weights`` / ``model.PRESETS``).
        hard: enforce exact energy delivery (falls back to soft if infeasible).
        solar: PV forecast; enables the robust/net-import formulation. A ``SolarDay`` (E9) gives a
            nowcast-blended forecast each period, and the billed history uses realised net import.
        robust: ``RobustSpec`` template (gammas, price deviation); PV arrays come from ``solar``.
        billing: v2 demand handling. The billed peak is the ``demand_window``-period rolling mean,
            with the last actual powers as constants, and the demand floor is
            max(month_peak_kw, peak_target_kw, today's billed peak so far).
        finish_offset: soft finish-by, EVs should be done this many periods before departure.
        rampdown: lower each EV's upper bound with ACN's ``SimpleRampdown`` (tapering batteries).
        forecaster: adds expected future arrivals as ghost EVs on virtual phase-group columns. With
            its ``ghost_fix``, ghost windows and energy are also cut at ``horizon``, and with
            ``stochastic`` ghost energy is priced at the expected-unmet weight like a real EV's.
        horizon: cap on the look-ahead in periods (None: up to the latest departure). EVs leaving
            later must be served within the cap.
        event_driven: re-solve only on plug-in / unplug and apply the whole plan in between
            (ACN-Sim's event-triggered recompute). Exact when the simulated EVs follow the model
            (ideal batteries, nominal model), and several times faster.
        stochastic: plan each EV against the predictor's conditional (stay, kWh) scenarios
            (``SessionPredictor.scenarios``) and minimise the expected unmet energy instead of a
            single predicted departure and kWh (needs an ``EstimatedInterface``; no finish-by).

    The past actual powers come from the simulator's ``charging_rates`` (private: the interface
    exposes no aggregate history).
    """

    def __init__(self, weights: Weights, hard: bool = False, solar: Optional[SolarProfile] = None,
                 robust: Optional[RobustSpec] = None, name: str = "mpc", billing: bool = False,
                 demand_window: int = 1, month_peak_kw: float = 0.0, peak_target_kw: float = 0.0,
                 finish_offset: int = 0, rampdown: bool = False,
                 forecaster: Optional[ArrivalForecaster] = None, horizon: Optional[int] = None,
                 event_driven: bool = False, stochastic: bool = False):
        super().__init__()
        self.weights = weights
        self.hard = hard
        self.solar = solar
        self.robust = robust
        self.name = name
        self.billing = billing
        self.demand_window = demand_window
        self.month_peak_kw = month_peak_kw
        self.peak_target_kw = peak_target_kw
        self.finish_offset = finish_offset
        self.rampdown = SimpleRampdown() if rampdown else None
        self.forecaster = forecaster
        self.horizon = horizon
        self.event_driven = event_driven
        self.stochastic = stochastic
        self.max_recompute = None if event_driven else 1
        self.fallbacks = 0
        self.solver_times = []
        self._infra = None
        self._vinfra = None

    def register_interface(self, interface):
        super().register_interface(interface)
        if self.rampdown is not None:
            self.rampdown.register_interface(interface)

    def _demand_spec(self, iface) -> DemandSpec:
        sim = iface._simulator
        t = iface.current_time
        past = sim.network._voltages @ sim.charging_rates[:, :t] / 1000 if t > 0 else np.zeros(0)
        if hasattr(self.solar, "actual") and t > 0:  # E9: the bill sees net import
            past = np.clip(past - self.solar.actual(0, t), 0, None)
        w = self.demand_window
        today = float(rolling_mean(past, w).max()) if len(past) else 0.0
        return DemandSpec(w, past[-(w - 1):] if w > 1 else np.zeros(0),
                          max(self.month_peak_kw, self.peak_target_kw, today))

    def schedule(self, active_sessions) -> Dict[str, list]:
        sessions = [s for s in active_sessions if s.remaining_demand > 1e-6 and s.remaining_time > 0]
        if not sessions:
            return {}
        iface = self.interface
        if self._infra is None:
            self._infra = Infrastructure.from_info(iface.infrastructure_info())
        infra = self._infra

        n = len(sessions)
        T = max(s.arrival_offset + s.remaining_time for s in sessions)
        bounds = self.rampdown.get_maximum_rates(sessions) if self.rampdown is not None else {}
        idx = [infra.station_ids.index(s.station_id) for s in sessions]
        windows = [(s.arrival_offset, s.arrival_offset + s.remaining_time) for s in sessions]
        rates = [min(iface.max_pilot_signal(s.station_id), MAX_RATE, bounds.get(s.session_id, np.inf))
                 for s in sessions]
        demand = [s.remaining_demand for s in sessions]
        finish_by = [e - self.finish_offset if e - a > self.finish_offset else 0 for a, e in windows]
        scenarios = None
        if self.stochastic:
            now = iface.current_time
            scenarios = [iface.predictor.scenarios(s.arrival, now - s.arrival, s.energy_delivered)
                         if s.arrival <= now else None for s in sessions]
            for i, sc in enumerate(scenarios):
                if sc is not None:
                    windows[i] = (0, int(sc[0].max()))
                    demand[i] = float(sc[1].max())
            finish_by = [0] * n
            T = max(e for _, e in windows)
        if self.forecaster is not None:
            if self._vinfra is None:
                self._vinfra = infra.with_virtual()
            infra = self._vinfra
            now = iface.current_time
            fix = getattr(self.forecaster, "ghost_fix", False)
            end = max(DAY, now + T)
            if fix and self.horizon is not None:  # cut ghost windows (and energy) at the horizon
                end = min(end, now + self.horizon)
            g = self.forecaster.ghosts(now, infra.virtual_idx(), end)
            idx += list(g.col)
            windows += [(a - now, d - now) for a, d in zip(g.arrival, g.departure)]
            rates += list(g.rate)
            demand += list(g.energy)
            finish_by += [0] * len(g.col)
            if scenarios is not None:  # ghost_fix: price ghost energy like a real EV's (one certain scenario)
                scenarios += ([(np.array([d - now]), np.array([e]), np.ones(1)) for d, e in zip(g.departure, g.energy)]
                              if fix else [None] * len(g.col))
            T = max([T] + [d for _, d in windows])
        if self.horizon is not None and T > self.horizon:
            T = self.horizon
            keep = [i for i, (a, _) in enumerate(windows) if a < T]  # drop ghosts arriving after the cap
            idx, windows, rates, demand, finish_by = ([v[i] for i in keep]
                                                      for v in (idx, windows, rates, demand, finish_by))
            finish_by = [min(f, T) for f in finish_by]
            if scenarios is not None:  # a departure beyond the cap is scored at the cap
                scenarios = [None if scenarios[i] is None else
                             (np.minimum(scenarios[i][0], T),) + tuple(scenarios[i][1:]) for i in keep]
        ub = np.zeros((len(idx), T))
        for i, ((a, e), r) in enumerate(zip(windows, rates)):
            ub[i, a:e] = r
        evs = EVBlock(np.array(idx), ub, np.array(demand), np.array(finish_by), scenarios)
        robust = None
        if self.solar is not None:
            pv_hat, pv_dev = self.solar.window(iface.current_time, T)
            robust = replace(self.robust or RobustSpec(None, None), pv_hat=pv_hat, pv_dev=pv_dev)
        volts = float(np.mean(infra.voltages))
        res = solve(
            evs, infra, iface.get_prices(T), self.weights, iface.period,
            demand_charge=iface.get_demand_charge(),
            prev_peak_kw=iface.get_prev_peak() * volts / 1000,
            robust=robust, hard=self.hard,
            demand=self._demand_spec(iface) if self.billing else None,
        )
        self.fallbacks += res.fallback
        self.solver_times.append(res.solver_time)
        if self.event_driven:
            return {s.station_id: res.rates[i].tolist() for i, s in enumerate(sessions[:n])}
        return {s.station_id: [float(res.rates[i, 0])] for i, s in enumerate(sessions[:n])}


class ReplayScheduler(BaseAlgorithm):
    """Replays a precomputed station x time schedule (e.g. the offline optimum) inside ACN-Sim."""

    def __init__(self, station_rates: Dict[str, np.ndarray], name: str = "offline"):
        super().__init__()
        self.station_rates = station_rates
        self.name = name
        self.max_recompute = 1

    def schedule(self, active_sessions) -> Dict[str, list]:
        t = self.interface.current_time
        out = {}
        for s in active_sessions:
            r = self.station_rates.get(s.station_id)
            out[s.station_id] = [float(r[t]) if r is not None and t < len(r) else 0.0]
        return out

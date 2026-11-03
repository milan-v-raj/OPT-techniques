"""Run one (day, algorithm) simulation and return a metrics row."""

import time
import warnings
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Optional

from acnportal import acnsim, algorithms
from acnportal.acnsim.interface import Interface
from acnportal.signals.tariffs import TimeOfUseTariff

from . import PERIOD, TARIFF
from .estimates import SessionPredictor, interface_type
from .events import build_events, make_network
from .forecast import ArrivalForecaster
from .metrics import evaluate
from .model import PRESETS, PRESETS_V2, RobustSpec, Weights
from .offline import solve_offline, solve_offline_month
from .schedulers import ConvexMPC, ReplayScheduler, StationRampdown
from .solar import SolarProfile, solar_day

BASELINES = {
    "uncontrolled": lambda: algorithms.UncontrolledCharging(),
    "edf": lambda: algorithms.SortedSchedulingAlgo(algorithms.earliest_deadline_first),
    "llf": lambda: algorithms.SortedSchedulingAlgo(algorithms.least_laxity_first),
    "round_robin": lambda: algorithms.RoundRobin(algorithms.first_come_first_served),
}


RAMPDOWN_BASELINES = {
    "uncontrolled": lambda: algorithms.UncontrolledCharging(),
    "edf": lambda: algorithms.SortedSchedulingAlgo(algorithms.earliest_deadline_first, estimate_max_rate=True,
                                                   max_rate_estimator=StationRampdown()),
    "llf": lambda: algorithms.SortedSchedulingAlgo(algorithms.least_laxity_first, estimate_max_rate=True,
                                                   max_rate_estimator=StationRampdown()),
    "round_robin": lambda: algorithms.RoundRobin(algorithms.first_come_first_served, estimate_max_rate=True,
                                                 max_rate_estimator=StationRampdown()),
}


@dataclass
class V2Config:
    """Billing-accurate month setup (E7)."""

    battery: str = "taper"  # "ideal" | "taper"
    demand_window: int = 3  # billed peak = 15-min rolling mean
    finish_offset: int = 6  # soft finish-by 30 min before departure
    peak_target_kw: float = 0.0  # soft ceiling P*
    rampdown: bool = True  # SimpleRampdown upper bounds (MPC and baselines)
    horizon: int = 144  # MPC look-ahead cap: 12 h (one 52 h stay otherwise sets a ~2-day horizon)
    info: str = "perfect"  # what schedulers know about departure / energy, see INFO_LEVELS
    pv_kw: float = 0.0  # E9: behind-the-meter PV with synthetic weather (``solar.solar_day``); bill on net import


# Information levels (E8): "perfect" = true values; the others are SessionPredictor settings
INFO_LEVELS = {
    "predicted": dict(q_stay=0.5, q_kwh=0.5),  # arrival-hour median + conditional updates
    "conservative": dict(q_stay=0.25, q_kwh=0.75),  # early departure, large need
    "naive": dict(naive=True),  # one global median, no updates
}
# stochastic MPC: median SessionInfo for everything else, but each MPC minimises the expected unmet
# energy over the predictor's (stay, kWh) scenarios at this weight ($/kWh)
STOCHASTIC = {"stochastic-1": 1.0, "stochastic-10": 10.0, "stochastic-30": 30.0, "stochastic-100": 100.0}
INFO_LEVELS.update({k: INFO_LEVELS["predicted"] for k in STOCHASTIC})


def preset_name(algo: str) -> str:
    """'mpc:combined_qp+forecast' -> 'combined_qp'."""
    return algo.split(":", 1)[1].split("+")[0]


def make_scheduler(algo: str, weights: Optional[Weights] = None, cfg: Optional[V2Config] = None, **kwargs):
    if algo in BASELINES:
        return (RAMPDOWN_BASELINES if cfg is not None and cfg.rampdown else BASELINES)[algo]()
    if algo.startswith("mpc"):
        presets = PRESETS if cfg is None else PRESETS_V2
        return ConvexMPC(weights or presets[preset_name(algo)], name=algo, **kwargs)
    raise ValueError(f"Unknown algorithm {algo}")


def v2_kwargs(cfg: V2Config, month_peak_kw: float = 0.0, forecaster=None) -> dict:
    return dict(billing=True, demand_window=cfg.demand_window, month_peak_kw=month_peak_kw,
                peak_target_kw=cfg.peak_target_kw, finish_offset=cfg.finish_offset, rampdown=cfg.rampdown,
                forecaster=forecaster, horizon=cfg.horizon)


def simulate(scheduler, events, network, start, iface_type=Interface):
    sim = acnsim.Simulator(deepcopy(network), scheduler, deepcopy(events), start, period=PERIOD,
                           signals={"tariff": TimeOfUseTariff(TARIFF)}, verbose=False,
                           interface_type=iface_type)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sim.run()
    infeasible = sum("Invalid schedule" in str(w.message) for w in caught)
    return sim, infeasible


def run(sessions, day, algo: str, weights: Optional[Weights] = None, hard: bool = False,
        solar: Optional[SolarProfile] = None, robust: Optional[RobustSpec] = None, seed: int = 0,
        return_sim: bool = False, cfg: Optional[V2Config] = None, month_peak_kw: float = 0.0,
        history=None, **tags):
    """Simulate ``algo`` on ``day``.

    ``algo`` is a baseline name, ``mpc:<preset>`` (or ``mpc`` with explicit ``weights``), or
    ``offline:<preset>`` for the perfect-information benchmark replayed in the simulator.
    With ``cfg`` (v2) the batteries, presets and MPC demand handling follow ``V2Config``;
    ``month_peak_kw`` is the billed peak already set this month, and a ``+forecast`` suffix on an
    MPC algo adds ghost EVs forecast from ``history`` (sessions before ``day``); ``+forecast+fix``
    corrects their windows (``forecast.ArrivalForecaster`` ``ghost_fix``). ``cfg.info``
    other than "perfect" makes the online schedulers see predicted departure and energy
    (``estimates.EstimatedInterface``) while the simulator keeps the true values.
    """
    network = make_network()
    events, start, info = build_events(sessions, day, network, seed,
                                       battery=cfg.battery if cfg is not None else "ideal")
    t0 = time.perf_counter()
    offline_terms = {}
    iface_type = Interface
    if cfg is not None and cfg.pv_kw > 0:
        solar = solar_day(day, cfg.pv_kw)
    if algo.startswith("offline"):
        w = weights or (PRESETS if cfg is None else PRESETS_V2)[preset_name(algo)]
        v2 = {} if cfg is None else {"taper": cfg.battery == "taper", "demand_window": cfg.demand_window}
        rates, res = solve_offline(events, network, start, w, hard=hard, solar=solar, robust=robust, **v2)
        scheduler = ReplayScheduler(rates, name=algo)
        offline_terms = {"offline_solve_time": res.solve_time, "offline_fallback": res.fallback}
    else:
        kwargs = {}
        if algo.startswith("mpc"):
            kwargs = {"hard": hard, "solar": solar, "robust": robust}
            if cfg is None and solar is None:
                # v1 nominal: ideal batteries follow the model, so re-solving only on plug-in /
                # unplug gives the same schedule (robust re-solves differ: the budget shrinks)
                kwargs["event_driven"] = True
            if cfg is not None:
                forecaster = (ArrivalForecaster(history, day, ghost_fix="+fix" in algo)
                              if "+forecast" in algo else None)
                kwargs.update(v2_kwargs(cfg, month_peak_kw, forecaster))
                if cfg.info in STOCHASTIC:
                    weights = replace(weights or PRESETS_V2[preset_name(algo)], expected=STOCHASTIC[cfg.info],
                                      finish=0.0)
                    kwargs["stochastic"] = True
                    tags["es_weight"] = STOCHASTIC[cfg.info]
        scheduler = make_scheduler(algo, weights, cfg, **kwargs)
        if cfg is not None and cfg.info != "perfect" and algo != "uncontrolled":
            iface_type = interface_type(SessionPredictor(history, day, **INFO_LEVELS[cfg.info]))
    sim, infeasible = simulate(scheduler, events, network, start, iface_type)
    pred_stats = sim.scheduler.interface.stats() if iface_type is not Interface else {}
    row = {**info, "algo": algo, "hard": hard, **tags, **evaluate(sim, solar), **offline_terms, **pred_stats,
           "infeasible_schedules": infeasible, "wall_time": time.perf_counter() - t0,
           "fallbacks": getattr(scheduler, "fallbacks", 0)}
    return (row, sim) if return_sim else row


def billed_peak_key(cfg: V2Config) -> str:
    """Column holding the billed 15-min peak: gross load, or net import with PV (E9)."""
    return "import_peak15_kw" if cfg.pv_kw > 0 else "peak15_kw"


def run_month(sessions, days, algo: str, cfg: V2Config, history=None, on_day=None, **tags):
    """Run ``algo`` over ``days`` in order, carrying the month-to-date billed (15-min) peak into
    each day. Returns (rows, solve stats); ``on_day(row, sim)`` is called after each day."""
    rows, stats, peak, key = [], [], 0.0, billed_peak_key(cfg)
    for day in sorted(days):
        row, sim = run(sessions, day, algo, cfg=cfg, month_peak_kw=peak, history=history, return_sim=True, **tags)
        row["mtd_peak_before"] = peak
        peak = max(peak, row[key])
        rows.append(row)
        stats += [dict(day=day, algo=algo, **st) for st in getattr(sim.scheduler, "solve_stats", [])]
        if on_day is not None:
            on_day(row, sim)
    return rows, stats


def run_offline_month(sessions, days, preset: str, cfg: V2Config, on_day=None):
    """Joint monthly offline optimum (one shared billed peak), each day replayed in ACN-Sim. With
    ``cfg.pv_kw`` the optimum knows the realised PV and bills net import (E9)."""
    network = make_network()
    days = sorted(days)
    built = [build_events(sessions, d, network, battery=cfg.battery) for d in days]
    solar = [solar_day(d, cfg.pv_kw) for d in days] if cfg.pv_kw > 0 else [None] * len(days)
    rates, peak, wall, solver = solve_offline_month([(ev, st) for ev, st, _ in built], network,
                                                    PRESETS_V2[preset], taper=cfg.battery == "taper",
                                                    demand_window=cfg.demand_window,
                                                    solar=solar if cfg.pv_kw > 0 else None)
    rows, mtd, key = [], 0.0, billed_peak_key(cfg)
    for (events, start, info), r, sd in zip(built, rates, solar):
        sim, infeasible = simulate(ReplayScheduler(r, name=f"offline:{preset}"), events, network, start)
        row = {**info, "algo": f"offline:{preset}", **evaluate(sim, sd), "infeasible_schedules": infeasible,
               "mtd_peak_before": mtd, "model_month_peak_kw": peak, "offline_solve_time": wall,
               "offline_solver_time": solver}
        mtd = max(mtd, row[key])
        rows.append(row)
        if on_day is not None:
            on_day(row, sim)
    return rows

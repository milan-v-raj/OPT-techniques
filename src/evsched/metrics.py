"""Evaluation metrics for a finished simulation (wraps ``acnsim.analysis``)."""

import numpy as np
import pandas as pd
from acnportal import acnsim

from .model import PHASE_IDS


def rolling_mean(P: np.ndarray, k: int = 3, hist=()) -> np.ndarray:
    """Trailing k-period mean of P (the billed 15-min demand for k = 3 at 5-min periods). Periods
    before the start are taken from ``hist`` (most recent last), else zero."""
    hist = np.asarray(hist, dtype=float)[-(k - 1):] if k > 1 else np.zeros(0)
    full = np.concatenate([np.zeros(k - 1 - len(hist)), hist, P])
    return np.convolve(full, np.ones(k) / k, mode="valid")


def month_summary(rows: pd.DataFrame, demand_charge: float) -> pd.DataFrame:
    """Per-day month-to-date accounting for one scheduler's month (rows in day order): billed peak
    carried so far, the increment each day adds, and the bill under 5-min and 15-min demand."""
    rows = rows.sort_values("day").copy()
    rows["mtd_peak_before"] = rows["peak15_kw"].cummax().shift(fill_value=0.0)
    rows["demand_increment_kw"] = (rows["peak15_kw"] - rows["mtd_peak_before"]).clip(lower=0)
    rows["daily_bill"] = rows["energy_cost"] + demand_charge * rows["demand_increment_kw"]
    return rows


def month_bill(rows: pd.DataFrame, demand_charge: float) -> dict:
    """September bill: energy over the days plus the demand charge on the month's highest peak."""
    energy = rows["energy_cost"].sum()
    return {"energy": energy, "peak5_kw": rows["peak_kw"].max(), "peak15_kw": rows["peak15_kw"].max(),
            "bill_5min": energy + demand_charge * rows["peak_kw"].max(),
            "bill_15min": energy + demand_charge * rows["peak15_kw"].max()}


def nema_unbalance(sim) -> np.ndarray:
    """NEMA current unbalance of the secondary phase currents, only where load is present."""
    cc = acnsim.constraint_currents(sim, constraint_ids=list(PHASE_IDS))
    I = np.vstack([cc[p] for p in PHASE_IDS])
    mean = I.mean(axis=0)
    mask = mean > 1.0
    return (I.max(axis=0)[mask] - mean[mask]) / mean[mask]


def max_constraint_utilisation(sim) -> float:
    """max_t,m |I_m(t)| / limit_m  (> 1 means the schedule violated the infrastructure)."""
    cc = acnsim.constraint_currents(sim)
    limits = dict(zip(sim.network.constraint_index, sim.network.magnitudes))
    return float(max(v.max() / limits[k] for k, v in cc.items()))


def net_import_cost(sim, pv: np.ndarray) -> np.ndarray:
    """Energy cost of grid import max(P - pv, 0) for one or more PV traces (rows, same length as P)."""
    P = acnsim.aggregate_power(sim)
    pv = np.atleast_2d(pv)
    prices = np.array(sim.signals["tariff"].get_tariffs(sim.start, len(P), sim.period))
    return np.clip(P - pv, 0, None) @ prices * sim.period / 60


def solar_metrics(sim, solar) -> dict:
    """Grid-side metrics with behind-the-meter PV (``SolarDay``): import = max(P - pv, 0), surplus unpaid."""
    P = acnsim.aggregate_power(sim)
    pv = solar.actual(0, len(P))
    used = np.minimum(P, pv)
    imp = P - used
    dt = sim.period / 60
    prices = np.array(sim.signals["tariff"].get_tariffs(sim.start, len(P), sim.period))
    pv_kwh, used_kwh = pv.sum() * dt, used.sum() * dt
    return {"import_kwh": float(imp.sum() * dt), "import_cost": float(imp @ prices * dt),
            "import_peak_kw": float(imp.max()), "import_peak15_kw": float(rolling_mean(imp, 15 // int(sim.period)).max()),
            "pv_kwh": float(pv_kwh), "pv_used_kwh": float(used_kwh), "pv_surplus_kwh": float(pv_kwh - used_kwh),
            "self_consumption": float(used_kwh / pv_kwh) if pv_kwh > 0 else np.nan,
            "self_sufficiency": float(used_kwh / (P.sum() * dt)) if P.sum() > 0 else np.nan}


def evaluate(sim, solar=None, n_pv_samples: int = 200) -> dict:
    """Collect all report metrics for a finished simulation."""
    P = acnsim.aggregate_power(sim)
    P15 = rolling_mean(P, 15 // int(sim.period))
    day = P[: 24 * 60 // int(sim.period)]  # fixed 24 h window from local midnight
    unb = nema_unbalance(sim)
    requested = acnsim.total_energy_requested(sim)
    delivered = acnsim.total_energy_delivered(sim)
    stats = [s for s in getattr(sim.scheduler, "solve_stats", []) if s["active_sessions"] > 0]
    solve_times = np.array([s["solve_time"] for s in stats]) if stats else np.zeros(1)
    solver_times = np.array(getattr(sim.scheduler, "solver_times", None) or [0.0])
    out = {
        "energy_requested_kwh": requested,
        "energy_delivered_kwh": delivered,
        "pct_energy_delivered": 100 * delivered / requested,
        "unmet_kwh": requested - delivered,
        "energy_cost": acnsim.energy_cost(sim),
        "demand_charge": acnsim.demand_charge(sim),
        "peak_kw": float(P.max()),
        "par": float(P.max() / day.mean()),
        "peak15_kw": float(P15.max()),
        "par15": float(P15.max() / day.mean()),
        "load_std_kw": float(day.std()),
        "sum_sq_load": float(np.sum(day ** 2) * sim.period / 60),
        "unbalance_mean": float(unb.mean()) if unb.size else np.nan,
        "unbalance_max": float(unb.max()) if unb.size else np.nan,
        "max_utilisation": max_constraint_utilisation(sim),
        "solve_time_mean": float(solve_times.mean()),
        "solve_time_max": float(solve_times.max()),
        "solver_time_mean": float(solver_times.mean()),
        "n_solves": len(stats),
    }
    if solar is not None and hasattr(solar, "actual"):
        out.update(solar_metrics(sim, solar))
    elif solar is not None:
        costs = net_import_cost(sim, solar.sample(n_pv_samples, len(P)))
        out.update({
            "net_cost_forecast": float(net_import_cost(sim, solar.window(0, len(P))[0])[0]),
            "net_cost_mean": float(costs.mean()),
            "net_cost_p95": float(np.percentile(costs, 95)),
            "net_cost_worst": float(costs.max()),
        })
    return out

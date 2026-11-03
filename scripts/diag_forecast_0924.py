"""One-day diagnostic (E9 extension, REPORT_solar_stochastic.md): why combined_qp+forecast @ stochastic-30 with 50 kW PV jumps its billed import
peak 61.5 -> 71.7 kW on 2018-09-24. Re-simulates that day from the same month-to-date floor under
variants that switch off one suspected cause each. The code is patched at runtime only.

Usage: python scripts/diag_forecast_0924.py [variant ...]   (all variants by default, one process each)

Variants
    base       : as in the month run
    noforecast : combined_qp (no ghosts), same floor
    w10        : forecast MPC at stochastic-10
    ghost30    : ghost energy priced like real EVs (one certain scenario at the stochastic weight)
    curbin     : current-hour ghost scaled by the share of the hour left, arriving next period
    nopeak     : ghost load excluded from the billed-peak / P* hinge terms (kept in cost, valley, limits)
    edge       : ghost energy scaled by the share of its window inside the 12 h horizon
    fixed      : production ``+fix`` (edge + ghost30 + curbin), no runtime patches
    combo      : ghost30 + curbin + nopeak
"""
import inspect
import os
import sys
import textwrap
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJ = Path(r"E:\EVcharging\project")
sys.path.insert(0, str(PROJ / "src"))
sys.path.insert(0, str(PROJ / "scripts"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("EVSCHED_SOLVER_THREADS", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

DAY, FLOOR = "2018-09-24", 61.45089729584126
OUT = PROJ / "results" / "diag_0924"
VARIANTS = {
    "base": dict(algo="mpc:combined_qp+forecast", info="stochastic-30"),
    "noforecast": dict(algo="mpc:combined_qp", info="stochastic-30"),
    "w10": dict(algo="mpc:combined_qp+forecast", info="stochastic-10"),
    "ghost30": dict(algo="mpc:combined_qp+forecast", info="stochastic-30", ghost30=True),
    "curbin": dict(algo="mpc:combined_qp+forecast", info="stochastic-30", curbin=True),
    "nopeak": dict(algo="mpc:combined_qp+forecast", info="stochastic-30", nopeak=True),
    "edge": dict(algo="mpc:combined_qp+forecast", info="stochastic-30", edge=True),
    "fixed": dict(algo="mpc:combined_qp+forecast+fix", info="stochastic-30"),  # the ghost_fix now in the code
    "combo": dict(algo="mpc:combined_qp+forecast", info="stochastic-30", ghost30=True, curbin=True, nopeak=True),
}


def _patch(src_obj, module, edits, dedent=False):
    src = inspect.getsource(src_obj)
    if dedent:
        src = textwrap.dedent(src)
    for old, new in edits:
        assert old in src, old
        src = src.replace(old, new)
    ns = module.__dict__
    exec(compile(src, f"<patched {src_obj.__name__}>", "exec"), ns)
    return ns[src_obj.__name__]


def apply_patches(v):
    from evsched import experiment, forecast, model, schedulers
    from evsched.forecast import ArrivalForecaster

    if v.get("nopeak"):
        model.build = _patch(model.build, model, [(
            "        full = cp.hstack([hist, grid]) if w > 1 else grid\n",
            "        gb = grid\n"
            "        gm = np.isin(idx, infra.virtual_idx())\n"
            "        if gm.any():\n"
            "            Pr = (volts[~gm] / 1000) @ R[np.flatnonzero(~gm)]\n"
            "            gb = cp.Variable(T, nonneg=True)\n"
            "            cons.append(gb >= (Pr - (robust.pv_hat[:T] - robust.gamma_solar * robust.pv_dev[:T])\n"
            "                               if robust is not None else Pr))\n"
            "        full = cp.hstack([hist, gb]) if w > 1 else gb\n")])
    if v.get("ghost30"):
        schedulers.ConvexMPC.schedule = _patch(schedulers.ConvexMPC.schedule, schedulers, [(
            "scenarios += [None] * len(g.col)",
            "scenarios += [(np.array([d - now]), np.array([e]), np.array([1.0]))\n"
            "                              for d, e in zip(g.departure, g.energy)]")], dedent=True)
    if v.get("edge"):
        schedulers.ConvexMPC.schedule = _patch(schedulers.ConvexMPC.schedule, schedulers, [(
            "        keep = [i for i, (a, _) in enumerate(windows) if a < T]  # drop ghosts arriving after the cap\n",
            "        keep = [i for i, (a, _) in enumerate(windows) if a < T]  # drop ghosts arriving after the cap\n"
            "        demand = [dm * min(1.0, (T - a) / max(e - a, 1)) if i >= n else dm\n"
            "                  for i, (dm, (a, e)) in enumerate(zip(demand, windows))]\n")], dedent=True)
    if v.get("curbin"):
        class CurBin(ArrivalForecaster):
            def ghosts(self, t, virtual_cols, horizon_end):
                B = 12  # periods per 60-min bin
                start = self.bins.index.to_numpy() * B
                end = start + B
                cur = (start <= t) & (end > t)
                live = (self.arrival > t) | cur
                frac = np.where(cur, (end - t) / B, 1.0)[live]
                a = np.where(cur, t + 1, self.arrival)[live]
                d = np.minimum(np.maximum(self.arrival[live] + self.stay[live], a + 1), horizon_end)
                ok = d > a
                a, d, frac = a[ok], d[ok], frac[ok]
                rate = self.bins["rate"].to_numpy()[live][ok] * frac
                kwh = self.bins["kwh"].to_numpy()[live][ok]
                k = len(virtual_cols)
                return forecast.GhostRows(col=np.tile(virtual_cols, len(a)), arrival=np.repeat(a, k),
                                          departure=np.repeat(d, k),
                                          rate=np.repeat(rate * forecast.MAX_RATE / k, k),
                                          energy=np.repeat(rate * kwh / k, k))
        experiment.ArrivalForecaster = CurBin

    # logging: stash the last built problem, record the plan after every MPC solve
    _build = model.build
    last = {}

    def build_log(evs, infra, *a, **k):
        b = _build(evs, infra, *a, **k)
        last.update(b=b, evs=evs, infra=infra)
        return b
    model.build = build_log
    _sched = schedulers.ConvexMPC.schedule

    def sched_log(self, active):
        last.clear()
        out = _sched(self, active)
        if last.get("b") is not None and last["b"].billed is not None and last["b"].R.value is not None:
            b, evs, infra = last["b"], last["evs"], last["infra"]
            billed = np.asarray(b.billed.value, dtype=float).ravel()
            R = np.asarray(b.R.value)
            gm = np.isin(evs.station_idx, infra.virtual_idx()) if hasattr(infra, "virtual_idx") else np.zeros(len(R), bool)
            volts = infra.voltages[evs.station_idx]
            Pg, Pr = volts[gm] / 1000 @ R[gm], volts[~gm] / 1000 @ R[~gm]
            j = int(np.argmax(billed))
            if not hasattr(self, "diag"):
                self.diag = []
            self.diag.append(dict(t=self.interface.current_time, floor=self._demand_spec(self.interface).floor_kw,
                                  plan_peak=billed.max(), plan_peak_at=j, real_at=Pr[j], ghost_at=Pg[j],
                                  real_now=Pr[0], ghost_now=Pg[0], billed_now=billed[0],
                                  ghost_kwh_plan=float(Pg.sum() * 5 / 60), n_real=int((~gm).sum()),
                                  n_ghost=int(gm.sum())))
        return out
    schedulers.ConvexMPC.schedule = sched_log


def work(name):
    v = VARIANTS[name]
    apply_patches(v)
    from evsched.data import load_sessions
    from evsched.experiment import run
    from run_experiments import v2_config
    tags = {}
    row, sim = run(load_sessions(), DAY, v["algo"], cfg=v2_config(v["info"], 50.0), month_peak_kw=FLOOR,
                   history=load_sessions(months=("2018-08", "2018-09")), return_sim=True)
    from acnportal import acnsim
    from evsched.solar import solar_day
    power = acnsim.aggregate_power(sim)
    pv = solar_day(DAY, 50.0).actual(0, len(power))
    OUT.mkdir(exist_ok=True)
    np.savez(OUT / f"{name}.npz", power=power, pv=pv)
    pd.DataFrame(getattr(sim.scheduler, "diag", [])).to_csv(OUT / f"{name}_plan.csv", index=False)
    keep = ["algo", "import_peak15_kw", "peak15_kw", "energy_delivered_kwh", "energy_requested_kwh", "import_cost",
            "pv_used_kwh", "infeasible_schedules", "fallbacks", "wall_time"]
    r = {k: row.get(k) for k in keep}
    print(f"[diag] {name} import15={r['import_peak15_kw']:.1f} delivered={r['energy_delivered_kwh']:.1f} "
          f"({r['wall_time']:.0f}s)", flush=True)
    return dict(variant=name, **r)


if __name__ == "__main__":
    names = sys.argv[1:] or list(VARIANTS)
    with ProcessPoolExecutor(len(names)) as pool:
        rows = list(pool.map(work, names))
    df = pd.DataFrame(rows)
    df["delivered_pct"] = 100 * df.energy_delivered_kwh / df.energy_requested_kwh
    df["demand_increase_usd"] = 15.51 * (df.import_peak15_kw - FLOOR).clip(lower=0)
    print(df.round(2).to_string(index=False))
    old = pd.read_csv(OUT / "summary.csv") if (OUT / "summary.csv").exists() else None
    if old is not None:  # keep variants from earlier invocations
        df = pd.concat([old[~old.variant.isin(df.variant)], df], ignore_index=True)
    df.to_csv(OUT / "summary.csv", index=False)

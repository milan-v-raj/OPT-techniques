"""Run the project experiments and write CSVs to results/.

Experiments
    baselines : E1  built-in baselines vs MPC presets on every day
    offline   : E3  offline perfect-information optimum (replayed in ACN-Sim) for each preset
    sweep     : E2  weight sweep cost / valley / phase -> Pareto fronts
    energy    : E4  soft vs hard energy constraint
    robust    : E5  robust MPC with synthetic PV: gamma_solar x gamma_price grid
    scaling   : E6  offline solve time vs number of EVs and model class (LP / QP / SOCP)

v2 (billing-accurate month: 15-min billed peak carried across days, taper batteries, finish-by)
    target        : peak target P* = P90 of August 2018 offline-optimal daily 15-min peaks
    month         : E7  every scheduler over September, days in sequence (one job per scheduler)
    offline_month : E7  joint monthly offline optimum, replayed day by day

v3 (imperfect information: schedulers see predicted, not true, departure and energy)
    prediction_errors : plug-in prediction vs truth for every September session (no simulation)
    stochastic_showcase : per-EV prediction error and delivery on the showcase day, predicted vs
                          stochastic-10 (combined + forecast; needs month_info.csv)
    month_info        : E8  the E7 month for each --info level (predicted, conservative, naive, and the
                            stochastic MPC levels stochastic-1/-10/-30/-100: expected unmet energy over the
                            predictor's (stay, kWh) scenarios at weight 1/10/30/100 $/kWh; MPCs only)

v4 (behind-the-meter PV with synthetic weather; the bill is on net import)
    solar_weather : daily sky type, clearness, forecast and PV energy (no simulation)
    month_solar   : E9  the MPCs over the E7 month with --pv-kw of PV, for each --info-solar level
                    (perfect, predicted, conservative, or a stochastic MPC level such as stochastic-30),
                    plus the joint monthly offline optima with realised PV (--presets with no value skips them)

Usage
    python scripts/run_experiments.py baselines [--days 2018-09-05 ...] [--workers 12]
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")  # parallelism is across processes; BLAS threads cost ~3 GB per process

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from evsched import RESULTS_DIR  # noqa: E402
from evsched.data import load_sessions  # noqa: E402
from evsched.events import weekdays  # noqa: E402
from evsched.model import PRESETS, RobustSpec, Weights  # noqa: E402
from evsched.solar import clear_sky_profile  # noqa: E402

SWEEP_DAYS = ["2018-09-05", "2018-09-11", "2018-09-18", "2018-09-20", "2018-09-25"]


def jobs_baselines(days):
    algos = ["uncontrolled", "edf", "llf", "round_robin"] + [f"mpc:{p}" for p in PRESETS]
    return [dict(day=d, algo=a) for d in days for a in algos]


def jobs_offline(days):
    return [dict(day=d, algo=f"offline:{p}") for d in days for p in PRESETS]


def jobs_sweep(days):
    jobs = []
    for valley in [0, 0.03, 0.1, 0.3, 1, 3, 10]:
        for phase in [0, 1, 10]:
            w = Weights(cost=1.0, valley=valley, phase=phase)
            jobs += [dict(day=d, algo="mpc", weights=w, w_valley=valley, w_phase=phase) for d in days]
    return jobs


def jobs_energy(days):
    return [dict(day=d, algo=f"mpc:{p}", hard=h) for d in days for p in ("cost_lp", "valley_qp") for h in (False, True)]


def jobs_robust(days):
    solar = clear_sky_profile()
    jobs = []
    for gs in [0, 0.25, 0.5, 1.0]:
        for gp in [0, 12, 24, 48]:
            spec = RobustSpec(None, None, gamma_solar=gs, price_dev=0.5, gamma_price=gp)
            jobs += [dict(day=d, algo="mpc:cost_demand_lp", solar=solar, robust=spec, gamma_solar=gs, gamma_price=gp)
                     for d in days]
    return jobs


SHOWCASE_DAY = "2018-09-05"
TAGS = ("hard", "w_valley", "w_phase", "gamma_solar", "gamma_price")


def save_trace(sim, row, subdir="", pv_kw=0.0):
    """Aggregate power and secondary phase currents of a showcase-day run, for the figures (plus the
    realised PV and its day-ahead forecast when ``pv_kw`` > 0)."""
    from acnportal import acnsim
    from evsched.model import PHASE_IDS

    label = row["algo"].replace(":", "-").replace("+", "_") + "".join(f"_{k}={row[k]}" for k in TAGS if k in row)
    out = RESULTS_DIR / "traces" / subdir / row["day"]
    out.mkdir(parents=True, exist_ok=True)
    cc = acnsim.constraint_currents(sim, constraint_ids=list(PHASE_IDS))
    power = acnsim.aggregate_power(sim)
    extra = {}
    if pv_kw > 0:
        from evsched.solar import solar_day
        sd = solar_day(row["day"], pv_kw)
        extra = dict(pv=sd.actual(0, len(power)), pv_day_ahead=sd.day_ahead(0, len(power)))
    np.savez(out / f"{label}.npz", power=power, phases=np.vstack([cc[p] for p in PHASE_IDS]), **extra)


def _work(job):
    from evsched.experiment import run
    sessions = load_sessions()
    row, sim = run(sessions, return_sim=True, **job)
    if job["day"] == SHOWCASE_DAY:
        save_trace(sim, row)
    stats = [dict(day=job["day"], algo=row["algo"], **s) for s in getattr(sim.scheduler, "solve_stats", [])]
    return row, stats if row["algo"].startswith("mpc") else []


def run_pool(jobs, workers, name):
    rows, stats = [], []
    t0 = time.time()
    with ProcessPoolExecutor(workers) as pool:
        futures = [pool.submit(_work, j) for j in jobs]
        for i, f in enumerate(as_completed(futures), 1):
            row, s = f.result()
            rows.append(row)
            stats += s
            print(f"[{name}] {i}/{len(jobs)} {row['day']} {row['algo']} ({time.time() - t0:.0f}s)", flush=True)
    out = RESULTS_DIR / f"{name}.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    if stats:
        pd.DataFrame(stats).to_csv(RESULTS_DIR / f"{name}_solve_stats.csv", index=False)
    print(f"wrote {out}")


def run_scaling(days):
    """Offline solve time vs number of EVs for LP / QP / SOCP-with-phase objectives."""
    from evsched.events import build_events, make_network
    from evsched.offline import solve_offline
    from acnportal.acnsim.events import EventQueue

    sessions = load_sessions()
    network = make_network()
    rows = []
    for day in days:
        events, start, _ = build_events(sessions, day, network)
        plug = [e for _, e in events.queue]
        rng = np.random.default_rng(0)
        for n in [10, 20, 40, 60, len(plug)]:
            sub = EventQueue([plug[i] for i in sorted(rng.choice(len(plug), min(n, len(plug)), replace=False))])
            for preset in ("cost_lp", "valley_qp", "combined_qp"):
                _, res = solve_offline(sub, network, start, PRESETS[preset])
                rows.append(dict(day=day, n_evs=min(n, len(plug)), preset=preset, solve_time=res.solve_time))
                print(rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(RESULTS_DIR / "scaling.csv", index=False)


TARGET_FILE = RESULTS_DIR / "peak_target.json"
V2_ALGOS = (["uncontrolled", "edf", "llf", "round_robin"] + [f"mpc:{p}" for p in PRESETS]
            + ["mpc:combined_qp+forecast"])
# uncontrolled ignores departure / energy (its E7 rows stand for every level); under PRESETS_V2
# cost_demand_lp is the same problem as cost_lp
INFO_ALGOS = [a for a in V2_ALGOS if a not in ("uncontrolled", "mpc:cost_demand_lp")]
STOCHASTIC_LEVELS = ["stochastic-1", "stochastic-10", "stochastic-30", "stochastic-100"]  # experiment.STOCHASTIC, MPCs only


def _target_work(day):
    from evsched.experiment import V2Config, run
    return run(load_sessions(months=("2018-08", "2018-09")), day, "offline:combined_qp", cfg=V2Config())


def run_target(workers):
    """P* = P90 of the August 2018 weekday offline-optimal (combined + demand, taper) 15-min peaks."""
    days = weekdays(load_sessions(months=("2018-08", "2018-09")), month="2018-08", holidays=())
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(_target_work, days))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "target.csv", index=False)
    target = float(np.percentile(df["peak15_kw"], 90))
    TARGET_FILE.write_text(json.dumps({"peak_target_kw": target, "quantile": 0.9, "days": days}, indent=1))
    print(f"P* = {target:.1f} kW from {len(days)} August days -> {TARGET_FILE}")


def v2_config(info="perfect", pv_kw=0.0):
    from evsched.experiment import V2Config
    target = json.loads(TARGET_FILE.read_text())["peak_target_kw"] if TARGET_FILE.exists() else 0.0
    return V2Config(peak_target_kw=target, info=info, pv_kw=pv_kw)


def _month_work(job):
    from evsched.experiment import run_month
    algo, days, info, pv_kw = job["algo"], job["days"], job.get("info", "perfect"), job.get("pv_kw", 0.0)
    perfect = info == "perfect"
    if pv_kw > 0:  # E9
        parts, trace_dir, tags = "month_solar_parts", f"v4/{info}", {"info": info, "pv_kw": pv_kw}
        name = algo.replace(":", "-").replace("+", "_") + f"@{info}"
    else:
        parts, trace_dir = ("month_parts", "v2") if perfect else ("month_info_parts", f"v3/{info}")
        name = algo.replace(":", "-").replace("+", "_") + ("" if perfect else f"@{info}")
        tags = {} if perfect else {"info": info}
    parts = RESULTS_DIR / parts
    parts.mkdir(exist_ok=True)
    done = []

    def on_day(row, sim):
        done.append(row)
        pd.DataFrame(done).to_csv(parts / f"{name}.csv", index=False)
        if row["day"] == SHOWCASE_DAY:
            save_trace(sim, row, trace_dir, pv_kw)
        imp = f" import15={row['import_peak15_kw']:.1f}" if pv_kw > 0 else ""
        print(f"[month] {name} {row['day']} peak15={row['peak15_kw']:.1f}{imp} ({row['wall_time']:.0f}s)", flush=True)

    rows, stats = run_month(load_sessions(), days, algo, v2_config(info, pv_kw),
                            history=load_sessions(months=("2018-08", "2018-09")), on_day=on_day, **tags)
    return rows, [dict(s, **tags) for s in stats] if algo.startswith("mpc") else []


def run_months(days, algos, workers, infos=("perfect",)):
    """One job per (scheduler, information level), all in one pool of ``workers`` processes.
    Levels other than "perfect" (E8) merge into month_info.csv, replacing earlier rows of the same level."""
    jobs = [dict(algo=a, days=days, info=i) for i in infos for a in algos]
    jobs.sort(key=lambda j: (not j["algo"].startswith("mpc"), j["algo"] == "mpc:cost_lp"))  # long MPC months first
    rows, stats = [], []
    with ProcessPoolExecutor(min(workers, len(jobs))) as pool:
        for r, s in pool.map(_month_work, jobs):
            rows += r
            stats += s
    perfect = tuple(infos) == ("perfect",)
    name = "month" if perfect else "month_info"
    for out, df in ((RESULTS_DIR / f"{name}.csv", pd.DataFrame(rows)),
                    (RESULTS_DIR / f"{name}_solve_stats.csv", pd.DataFrame(stats))):
        if not perfect and out.exists():
            old = pd.read_csv(out)
            df = pd.concat([old[~old["info"].isin(infos)], df], ignore_index=True)
        df.to_csv(out, index=False)
    print(f"wrote {RESULTS_DIR / f'{name}.csv'}")


def run_prediction_errors(days):
    """Plug-in predictions vs truth for every session of ``days``, for each information level."""
    from evsched.estimates import SessionPredictor
    from evsched.events import build_events, make_network
    from evsched.experiment import INFO_LEVELS

    sessions, history, network = load_sessions(), load_sessions(months=("2018-08", "2018-09")), make_network()
    rows = []
    for day in days:
        events, _, _ = build_events(sessions, day, network)
        evs = [e.ev for _, e in events.queue]
        for level, kw in INFO_LEVELS.items():
            if level.startswith("stochastic"):  # same plug-in prediction as "predicted"
                continue
            p = SessionPredictor(history, day, **kw)
            for ev in evs:
                stay, kwh = p.initial(ev.arrival)
                rows.append(dict(day=day, info=level, session_id=ev.session_id, arrival=ev.arrival,
                                 pred_stay=stay, true_stay=ev.departure - ev.arrival,
                                 pred_kwh=kwh, true_kwh=ev.requested_energy))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "prediction_errors.csv", index=False)
    df["stay_err_h"] = (df.pred_stay - df.true_stay) * 5 / 60
    df["kwh_err"] = df.pred_kwh - df.true_kwh
    print(df.groupby("info")[["stay_err_h", "kwh_err"]].agg(["mean", "median", lambda x: x.abs().mean()]).round(2))


def run_stochastic_showcase(algo="mpc:combined_qp+forecast", infos=("predicted", "stochastic-10")):
    """Per-EV plug-in prediction vs delivery on the showcase day, rerun with each level's month-to-date
    peak from month_info.csv (the month runs keep aggregates only)."""
    from evsched import PERIOD
    from evsched.experiment import run
    mi = pd.read_csv(RESULTS_DIR / "month_info.csv")
    rows = []
    for info in infos:
        mtd = mi[(mi.algo == algo) & (mi["info"] == info) & (mi.day == SHOWCASE_DAY)]["mtd_peak_before"].iloc[0]
        row, sim = run(load_sessions(), SHOWCASE_DAY, algo, cfg=v2_config(info), month_peak_kw=mtd,
                       history=load_sessions(months=("2018-08", "2018-09")), return_sim=True)
        for sid, (ps, ts, pk, tk) in sim.scheduler.interface.plugin.items():
            rows.append(dict(info=info, session_id=sid, pred_stay_h=ps * PERIOD / 60, true_stay_h=ts * PERIOD / 60,
                             stay_err_h=(ps - ts) * PERIOD / 60, pred_kwh=pk, requested_kwh=tk,
                             delivered_kwh=sim.ev_history[sid].energy_delivered))
        print(f"[showcase] {info}: delivered {row['pct_energy_delivered']:.1f}% peak15 {row['peak15_kw']:.1f}", flush=True)
    pd.DataFrame(rows).to_csv(RESULTS_DIR / "stochastic_showcase.csv", index=False)


def _offline_month_work(job):
    from evsched.experiment import run_offline_month
    pv_kw = job.get("pv_kw", 0.0)
    trace_dir = "v4/perfect" if pv_kw > 0 else "v2"
    return run_offline_month(load_sessions(), job["days"], job["preset"], v2_config(pv_kw=pv_kw),
                             on_day=lambda row, sim: save_trace(sim, row, trace_dir, pv_kw)
                             if row["day"] == SHOWCASE_DAY else None)


SOLAR_ALGOS = [a for a in INFO_ALGOS if a.startswith("mpc")]  # E9: MPCs only


def run_solar_weather():
    """Synthetic weather of every calendar date from August to early October."""
    from evsched import PERIOD
    from evsched.solar import weather_day
    rows = []
    for d in pd.date_range("2018-08-01", "2018-10-05"):
        w = weather_day(d)
        rows.append(dict(date=w.date, sky=w.sky, k=w.k, k_hat=w.k_hat, pv_kwh=w.real.sum() * PERIOD / 60,
                         forecast_kwh=(w.clear * w.k_hat).sum() * PERIOD / 60,
                         clear_sky_kwh=w.clear.sum() * PERIOD / 60, pv_max_kw=w.real.max()))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "solar_weather.csv", index=False)
    sep = df[df.date.str.startswith("2018-09")]
    print(sep.round(2).to_string(index=False))
    print(sep[["pv_kwh", "forecast_kwh"]].describe().round(1))
    print(sep.sky.value_counts())


def _solar_work(job):
    if job["kind"] == "offline":
        rows = _offline_month_work(job)
        return [dict(r, info="perfect", pv_kw=job["pv_kw"]) for r in rows], []
    return _month_work(job)


def run_month_solar(days, algos, workers, infos, presets, pv_kw):
    """E9: MPC months with PV (one job per scheduler and level) and the offline months, in one pool."""
    jobs = [dict(kind="offline", preset=p, days=days, pv_kw=pv_kw) for p in presets]
    jobs += [dict(kind="month", algo=a, days=days, info=i, pv_kw=pv_kw) for i in infos for a in algos]
    rows, stats = [], []
    with ProcessPoolExecutor(min(workers, len(jobs))) as pool:
        for r, s in pool.map(_solar_work, jobs):
            rows += r
            stats += s
            print(f"[month_solar] {r[0]['algo']} @{r[0]['info']} done", flush=True)
    for out, df in ((RESULTS_DIR / "month_solar.csv", pd.DataFrame(rows)),
                    (RESULTS_DIR / "month_solar_solve_stats.csv", pd.DataFrame(stats))):
        if out.exists() and len(df):  # replace only the (algo, info) pairs just run
            old = pd.read_csv(out)
            done = set(zip(df["algo"], df["info"]))
            df = pd.concat([old[[k not in done for k in zip(old["algo"], old["info"])]], df], ignore_index=True)
        df.to_csv(out, index=False)
    print(f"wrote {RESULTS_DIR / 'month_solar.csv'}")


def run_offline_months(days, presets):
    """One process per preset (each monthly solve is a single long Clarabel run)."""
    rows = []
    with ProcessPoolExecutor(len(presets)) as pool:
        futures = {pool.submit(_offline_month_work, dict(preset=p, days=days)): p for p in presets}
        for f in as_completed(futures):
            r = f.result()
            rows += r
            print(f"[offline_month] {futures[f]} done, solve {r[-1]['offline_solve_time']:.0f}s", flush=True)
    out, df = RESULTS_DIR / "offline_month.csv", pd.DataFrame(rows)
    if out.exists():  # keep presets from earlier runs
        old = pd.read_csv(out)
        df = pd.concat([old[~old.algo.isin(df.algo)], df], ignore_index=True)
    df.to_csv(out, index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("exp", choices=["baselines", "offline", "sweep", "energy", "robust", "scaling",
                                        "target", "month", "offline_month", "month_info",
                                        "prediction_errors", "solar_weather", "month_solar",
                                        "stochastic_showcase"])
    parser.add_argument("--days", nargs="+")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--algos", nargs="+", help="month / month_info: schedulers to run")
    parser.add_argument("--info", nargs="+", default=["predicted", "conservative", "naive"],
                        choices=["predicted", "conservative", "naive", *STOCHASTIC_LEVELS],
                        help="month_info: information levels")
    parser.add_argument("--presets", nargs="*", help="offline_month / month_solar: offline presets "
                        "(default combined_qp; month_solar: cost_lp combined_qp)")
    parser.add_argument("--pv-kw", type=float, default=50.0, help="month_solar: PV size")
    parser.add_argument("--info-solar", nargs="+", default=["perfect", "predicted"],
                        choices=["perfect", "predicted", "conservative", "naive", *STOCHASTIC_LEVELS],
                        help="month_solar: levels")
    args = parser.parse_args()

    all_days = weekdays(load_sessions())
    default_days = SWEEP_DAYS if args.exp in ("sweep", "robust", "scaling") else all_days
    days = args.days or default_days
    if args.exp == "target":
        run_target(args.workers)
    elif args.exp == "month":
        run_months(days, args.algos or V2_ALGOS, args.workers)
    elif args.exp == "month_info":
        stochastic = set(args.info) <= set(STOCHASTIC_LEVELS)
        run_months(days, args.algos or (SOLAR_ALGOS if stochastic else INFO_ALGOS), args.workers, args.info)
    elif args.exp == "prediction_errors":
        run_prediction_errors(days)
    elif args.exp == "stochastic_showcase":
        run_stochastic_showcase()
    elif args.exp == "offline_month":
        run_offline_months(days, args.presets or ["combined_qp"])
    elif args.exp == "solar_weather":
        run_solar_weather()
    elif args.exp == "month_solar":
        run_month_solar(days, args.algos or SOLAR_ALGOS, args.workers, args.info_solar,
                        args.presets if args.presets is not None else ["cost_lp", "combined_qp"], args.pv_kw)
    elif args.exp == "scaling":
        run_scaling(days)
    else:
        jobs = globals()[f"jobs_{args.exp}"](days)
        run_pool(jobs, args.workers, args.exp)

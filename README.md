# Grid-Aware Smart EV Charging — Convex Optimisation on ACN-Sim

Optimization Techniques course project (Group 1). Convex LP/QP/SOCP charging schedulers evaluated
in [acnportal](../acnportal) ACN-Sim on real Caltech ACN-Data sessions (static snapshot).

## Setup

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -e ../acnportal "setuptools<81" cvxpy clarabel pandas pyarrow matplotlib pytest jupyter nbformat
```

`setuptools<81` is required because acnportal still imports `pkg_resources`.

## Data

The static snapshot's `session data/caltech_sessions.json` is empty, so sessions are rebuilt
from the per-session time series (`evsched/data.py`): arrival = first sample, departure = first
`UNPLUGGED` sample, energy = max cumulative kWh. The result is cached as Parquet in `results/`.

```bash
.venv/Scripts/python.exe scripts/build_sessions.py --months 2018-09
```

Hardware station ids cannot be mapped to the Caltech space ids, so each arriving EV is placed on
a free EVSE of `caltech_acn` on the least-occupied phase group (`evsched/events.py`).
Requested energy = delivered kWh and departure = actual departure (perfect user information).

## Model (`evsched/model.py`)

Variable `R[i,t]` (A). Constraints: rate bounds inside the plugged-in window, soft energy
fulfilment `E_i + u_i = demand_i` (or hard `u = 0`), and exact three-phase network limits
`|Σ A_mi R_it e^{jφ_i}| ≤ limit_m` (second-order cones). Objective is a weighted sum of:

| term | form | class |
|---|---|---|
| TOU energy cost | Σ p_t P_t Δt | LP |
| demand charge | dc · max(max_t P_t, previous peak) | LP |
| valley filling | Δt Σ P_t² / P_ref | QP |
| phase imbalance | Δt Σ \|I⁻_t\|² / I_ref (negative-sequence current) | QP |
| unmet energy | Σ u_i (large weight) | LP |

The robust extension prices net import `S_t ≥ P_t − (p̂v_t − Γ_solar δ_t)` under a Bertsimas–Sim
budget `Γ_price` on upward price deviations. It stays an LP, and Γ = 0 gives back the nominal model.

Presets: `cost_lp`, `cost_demand_lp`, `valley_qp`, `phase_qp`, `combined_qp` (`model.PRESETS`).

## Experiments

```bash
.venv/Scripts/python.exe scripts/run_experiments.py baselines   # E1: baselines vs MPC presets, all weekdays
.venv/Scripts/python.exe scripts/run_experiments.py offline     # E3: offline optimum, replayed in ACN-Sim
.venv/Scripts/python.exe scripts/run_experiments.py sweep       # E2: cost/valley/phase weight sweep (5 days)
.venv/Scripts/python.exe scripts/run_experiments.py energy      # E4: soft vs hard energy constraint
.venv/Scripts/python.exe scripts/run_experiments.py robust      # E5: Γ_solar × Γ_price grid with synthetic PV (5 days)
.venv/Scripts/python.exe scripts/run_experiments.py scaling     # E6: solve time vs #EVs
```

Add `--days 2018-09-05` to run a single day and `--workers N` to set parallelism.
Outputs: `results/<exp>.csv`, `results/<exp>_solve_stats.csv`, `results/traces/2018-09-05/*.npz`.
Figures and tables: open `notebooks/figures.ipynb` (writes to `figures/` and `results/table_*.csv`).

## v2: billing-accurate month (E7)

E1–E6 price every day on its own. v2 (`experiment.V2Config`, `model.PRESETS_V2`) follows the real bill instead:

- **15-min billed peak.** The demand term uses the rolling mean P̄_t = (P_t + P_{t−1} + P_{t−2})/3, and the MPC carries the last two actual powers as constants. Metrics report both `peak_kw` (5-min) and `peak15_kw`.
- **Monthly carry.** Each scheduler's September runs day by day. The month-to-date billed peak is the demand floor, and every MPC preset pays the full $15.51/kW on any increase (weight 1).
- **Peak target.** Soft ceiling with floor = max(P*, month-to-date peak), plus a per-period exceedance hinge. P* is the P90 of the August 2018 offline-optimal daily peaks.
- **Finish-by.** Soft deadline 30 min before departure ($1/kWh shortfall penalty).
- **Tapering batteries.** ACN `Linear2StageBattery`, sized with `batt_cap_fn`. The MPC and baselines use `SimpleRampdown`. `schedulers.StationRampdown` fixes acnportal's session/station key mismatch, which otherwise disables the stock estimator. The offline benchmark models the taper as a linear constraint.
- **12 h MPC horizon.** The look-ahead is capped at 144 periods (`V2Config.horizon`). Otherwise a single 52 h stay sets a two-day horizon for every solve.
- **Arrival forecast** (`mpc:combined_qp+forecast`, `evsched/forecast.py`). Expected arrivals per hour from the previous 4 weeks become ghost EVs on virtual phase-group columns.

```bash
.venv/Scripts/python.exe scripts/build_sessions.py --months 2018-08 2018-09   # history for P* and the forecaster
.venv/Scripts/python.exe scripts/run_experiments.py target          # P* -> results/peak_target.json
.venv/Scripts/python.exe scripts/run_experiments.py month           # E7: every scheduler, days in sequence
.venv/Scripts/python.exe scripts/run_experiments.py offline_month --presets combined_qp cost_lp   # joint monthly offline optima
```

`month` runs one process per scheduler and writes progress to `results/month_parts/` after each day.
Set `EVSCHED_SOLVER_THREADS=2` when many jobs run at once (it caps Clarabel's threads per process; on 16 cores the whole
suite finishes in about 1 h). `offline_month` merges presets into `results/offline_month.csv`. The two joint solves take
10–17 min each. Results and discussion: `RESULTS.md` (E7) and `REPORT.md` §8.
Outputs: `results/month.csv`, `results/offline_month.csv`, `results/traces/v2/2018-09-05/*.npz`.

## v3: imperfect information (E8)

In E7 the schedulers read each EV's true departure and kWh. In E8 they see predictions; ACN-Sim keeps the true values
(`evsched/estimates.py`). ACN-Data's driver estimates (`userInputs`) are not in the static snapshot, so predictions come
from history: past sessions from the previous 28 weekdays, binned by arrival hour.

- `predicted`: median stay and kWh of the arrival-hour bin. Updated conditionally when an EV overstays (median of longer
  stays) or is still charging after its predicted kWh (median of larger requests).
- `conservative`: the same, but with P25 stay and P75 kWh.
- `naive`: one global median for every EV, no updates. An EV past its guess is no longer charged.

`EstimatedInterface` is passed to ACN-Sim as `interface_type`, so EDF/LLF/RR, the rampdown estimators and `ConvexMPC`
all see the predictions without code changes. The offline benchmark stays perfect-information.

```bash
.venv/Scripts/python.exe scripts/run_experiments.py prediction_errors   # plug-in prediction vs truth (seconds)
EVSCHED_SOLVER_THREADS=1 .venv/Scripts/python.exe scripts/run_experiments.py month_info --workers 12   # all three levels
```

`month_info` runs 8 informed schedulers × 3 levels as 24 jobs in one pool of `--workers` processes. `--info` picks the
levels; uncontrolled uses no information, so its E7 rows stand in, and `cost_demand_lp` equals `cost_lp` under v2. Results
merge into `results/month_info.csv` (`info` column), and progress is written to `results/month_info_parts/`. The script sets
`OPENBLAS_NUM_THREADS=1` (unless already set): each process otherwise commits about 3 GB for BLAS buffers, and 27 processes
ran out of memory on a 32 GB machine. With 12 workers and 1 solver thread, the run uses at most 12 cores. Results:
`RESULTS.md` (E8) and `REPORT.md` §9.

**Stochastic MPC (E8 extension).** The levels `stochastic-1`, `stochastic-10`, `stochastic-30` and `stochastic-100` use the `predicted`
interface. Each MPC minimises the expected unmet energy over up to 20 conditioned historical (stay, kWh) scenarios per EV
(`SessionPredictor.scenarios`, `ConvexMPC(stochastic=True)`, `Weights.expected`), at 1 / 10 / 30 / 100 $/kWh. Only the 5 MPCs run.
Each solve is about 15× slower (KKT fill-in from the running-total chain), so the 20 jobs take about 8 h on 12 workers:

```bash
EVSCHED_SOLVER_THREADS=1 .venv/Scripts/python.exe scripts/run_experiments.py month_info --info stochastic-1 stochastic-10 stochastic-30 stochastic-100 --workers 12
EVSCHED_SOLVER_THREADS=1 .venv/Scripts/python.exe scripts/run_experiments.py stochastic_showcase   # per-EV rows for 2018-09-05
```

Write-up: `REPORT_stochastic.md` (a separate companion; `REPORT.md` is unchanged).

## v4: on-site solar (E9)

E9 adds a 50 kW behind-the-meter PV array to the E7 month and reruns the MPCs; the heuristics are not rerun. The bill uses
net import, max(EV load − PV, 0): TOU energy plus the 15-min import peak carried over the month. PV surplus earns nothing.

- **Weather** (`evsched/solar.py`, `weather_day`): synthetic, and seeded by date, so every run sees the same sky. Each day
  draws a sky type (clear 70% / partly cloudy 20% / overcast 10%) and a daily clearness. AR(1) passing clouds are added on
  top of the clear-sky curve. Each simulated day gets only its own day's sun (`solar_day`), so overnight EVs cannot use the
  next day's PV twice.
- **Forecast** (`SolarDay.window`): a day-ahead clearness with error, blended each period with the clearness measured in
  the last period. The blend decays back to the day-ahead value with a 2 h time constant (persistence nowcast).
- **Model**: with PV, energy cost, billed demand and the P* hinge act on forecast net import. The valley term flattens the
  net load P − PV̂. `ConvexMPC` carries the realised net-import history for the billed peak.
- **Runs**: 5 MPCs (`cost_lp`, `valley_qp`, `phase_qp`, `combined_qp`, `combined_qp+forecast`) × `--info-solar`
  {perfect, predicted}, plus the joint monthly offline optima (`cost_lp`, `combined_qp`) with realised PV.

```bash
.venv/Scripts/python.exe scripts/run_experiments.py solar_weather   # results/solar_weather.csv (seconds)
EVSCHED_SOLVER_THREADS=1 .venv/Scripts/python.exe scripts/run_experiments.py month_solar --workers 12
```

Outputs:
- `results/month_solar.csv`, with `info` and `pv_kw` columns and the `import_*`, `pv_*` and `self_*` metrics;
- `results/month_solar_parts/`;
- `results/traces/v4/{info}/2018-09-05/`, whose traces also hold the PV arrays.

Results: `RESULTS.md` (E9) and `REPORT.md` §10.

**Stochastic MPC with PV (E9 extension).** `--info-solar` also accepts the stochastic levels of the E8 extension
(`stochastic-1/-10/-30/-100`), which add the expected-unmet-energy term to the PV-aware MPC. `--presets` with no value
skips the offline months. The `conservative` + PV baseline and `stochastic-30` take 10 jobs, about 3 h on 10 workers,
and merge into `results/month_solar.csv` without touching the existing rows:

```bash
EVSCHED_SOLVER_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/Scripts/python.exe scripts/run_experiments.py month_solar --info-solar conservative stochastic-30 --presets --workers 10
```

**Fixed ghost-EV forecast (`+fix`).** Appending `+fix` to a `+forecast` scheduler cuts the ghost EVs' windows and energy at the
horizon, prices them at the expected-unmet weight in stochastic mode, and counts only the remaining share of the current hour.
It is opt-in, so earlier forecast results reproduce unchanged. Month runs, one job each, about 4 h:

```bash
EVSCHED_SOLVER_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/Scripts/python.exe scripts/run_experiments.py month_solar --algos "mpc:combined_qp+forecast+fix" "mpc:cost_lp+forecast+fix" --info-solar stochastic-30 --presets --workers 2
```

Diagnosis of the 24 September jump, 9 variants in parallel, writing `results/diag_0924/`:

```bash
.venv/Scripts/python.exe scripts/diag_forecast_0924.py
```

Write-up: `REPORT_solar_stochastic.md`. Figures: `figures/v4_stochastic_frontier.png`, `figures/v4_stochastic_day.png` and
`figures/v4_forecast_fix_day.png`.

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests
```

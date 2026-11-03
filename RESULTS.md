# Results summary — Caltech ACN, September 2018 weekdays

Setup: 19 weekdays (Labor Day excluded), 1,772 real sessions (none dropped for lack of a free EVSE), `caltech_acn` network (54 EVSEs,
150 kW transformer, 3-phase limits), SCE TOU-EV-4 tariff, 5-min MPC. Values are means over days
unless stated. Raw rows: `results/*.csv`; tables: `results/table_*.csv`; figures: `figures/`.

## E1 — Baselines vs convex MPC (19 days) · `figures/metrics_bars.png`, `figures/load_profile.png`

| Method | Energy cost ($/day) | Peak (kW) | PAR | Mean NEMA unbalance | Energy delivered | Max limit utilisation |
|---|---|---|---|---|---|---|
| Uncontrolled | 97.3 | 172.8 | 5.25 | 0.302 | 99.94% | **1.57 (violates)** |
| EDF | 97.8 | 142.2 | 4.32 | 0.301 | 99.94% | 1.00 |
| LLF | 97.7 | 143.4 | 4.36 | 0.301 | 99.94% | 1.00 |
| Round robin | 97.6 | 144.2 | 4.39 | 0.299 | 99.84% | 1.00 |
| MPC cost (LP) | **86.9** | 145.9 | 4.65 | 0.308 | 99.91% | 1.00 |
| MPC cost+demand (LP) | 129.2 | 80.7 | **2.55** | 0.252 | 99.85% | 0.94 |
| MPC valley (QP) | 132.4 | 82.4 | 2.73 | 0.319 | 99.81% | 0.95 |
| MPC phase (QP) | 133.4 | 83.1 | 2.65 | **0.237** | 99.87% | 0.92 |
| MPC combined (QP) | 124.4 | **79.2** | 2.60 | 0.243 | 99.81% | 0.90 |

- Uncontrolled charging breaks the network limits on every day (up to 1.57× a limit, ~27 infeasible periods/day).
- The cost LP is 11% cheaper on energy than uncontrolled, but keeps the morning peak at the transformer limit.
- The valley / demand / combined schedules halve the peak (173 → 79 kW). That halves the demand charge ($15.51/kW) but costs about $30/day more energy, because load moves into the 12:00–18:00 peak-price window.
- September bill (19 weekdays of energy plus the demand charge on the month's highest peak): combined QP $3,892 < cost LP $3,980 < cost+demand LP $4,011 < EDF $4,183 < uncontrolled $5,140. The combined objective is cheapest overall, but only 2% below the cost LP. Its larger benefit is grid-side: a 50% lower peak and 21% lower unbalance.
- The phase penalty cuts mean unbalance by about 21% (0.30 → 0.24). Valley filling alone does not reduce it.

## E2 — Weight sweep (5 days, cost weight = 1) · `figures/pareto.png`
- Peak falls steeply up to w_valley ≈ 0.3 (146 → 84 kW, +$24/day), then flattens: a clear knee.
- w_phase = 1 lowers unbalance to about 0.21 at modest cost (~$99/day). w_phase = 10 gives little extra.

## E3 — Online MPC vs offline perfect-information optimum (19 days)

| Preset | Energy cost MPC / offline | Peak MPC / offline (kW) | Unbalance MPC / offline |
|---|---|---|---|
| cost LP | 86.9 / 87.0 | 145.9 / 145.9 | 0.308 / 0.307 |
| cost+demand LP | 129.2 / 95.6 | 80.7 / 70.6 | 0.252 / 0.279 |
| valley QP | 132.4 / 119.2 | 82.4 / 47.2 | 0.319 / 0.305 |
| combined QP | 124.4 / 112.6 | 79.2 / 56.7 | 0.243 / 0.232 |

The price-only LP loses nothing online. Peak-oriented objectives lose a lot (valley peak 82 vs 47 kW), because MPC cannot anticipate the morning arrival burst. This gap is the value of arrival forecasts. Offline full-day solves took 16–26 s here, with 14 solves running at once (2–5 s when run alone, see E6).

## E4 — Soft vs hard energy constraint (19 days)
The hard constraint is infeasible in about 32 (cost LP) and 38 (valley QP) MPC calls per day. These are sessions whose recorded kWh cannot fit their 5-min-rounded window at 32 A. With the soft fallback, results are identical (unmet 0.76 / 1.56 kWh/day, about 0.1–0.2% of demand).

## E5 — Robust extension, 50 kW synthetic PV ±40%, price deviation ρ = 50% (5 days) · `figures/robust.png`

| Γ_solar | Γ_price | Net cost at forecast | Mean realised | P95 realised | Worst realised |
|---|---|---|---|---|---|
| 0 | 0 (nominal) | 49.8 | 55.0 | 62.7 | 65.6 |
| 0.5 | 12 | 49.5 | 51.6 | 58.7 | 61.5 |
| 1.0 | 0 | 55.6 | 55.8 | 58.6 | 60.6 |
| **1.0** | **12** | **49.5** | **50.3** | **54.9** | **57.4** |

Worst-case net cost falls 12.5%, and P95 falls 12.5%, with no loss at the forecast. The mean also improves: import cost is convex in PV, so a nominal plan that uses the forecast PV exactly is penalised by every PV shortfall (Jensen gap), and the robust plan hedges against that. Very large Γ_price (48) starts to give the gain back.

## E6 — Computation · `figures/scaling.png`
- Offline full-day problem (~90–106 EVs × ~600 periods, 8 SOC constraint families): 2–5 s (Clarabel). LP and QP cost about the same; the time is dominated by the cone constraints.
- Online MPC: median 0.05 s at 5 active EVs, rising to about 1–1.6 s at 45–50 active EVs, well inside the 5-min control period. The solver accounts for about 80% of each call, and cvxpy compilation for the rest.

## E7 (v2) — Billing-accurate month · `figures/v2_bill.png`, `figures/v2_mtd_peak.png`, `figures/v2_forecast_day.png`, `figures/v2_energy.png`

**Setup.** Same 19 days, now as one month with these changes:

- **Billed peak:** 15-min rolling mean, also inside the model.
- **Monthly carry:** each scheduler runs the days in order, carrying the month-to-date billed peak.
- **Demand weight:** 1 (full $15.51/kW) on every MPC preset.
- **Peak target:** soft ceiling with P* = 54.9 kW, the P90 of August 2018 offline daily peaks.
- **Finish-by:** soft deadline 30 min before departure.
- **Batteries:** ACN `Linear2StageBattery` sized with `batt_cap_fn`. Schedulers use `SimpleRampdown`, through `StationRampdown`, which fixes an acnportal key bug.
- **Forecast run:** ghost-EV arrival forecast (previous 4 weeks) on the combined QP.
- **Offline:** joint monthly optima (perfect information, linear taper model), replayed in ACN-Sim.

With weight 1 on demand, `cost_lp` and `cost_demand_lp` are the same problem ("bill LP"), and both give identical rows. Full table: `results/table_month.csv`.

| Scheduler | Energy ($) | Billed peak, 15-min (kW) | 5-min peak (kW) | **Bill ($)** | Bill if 5-min billing ($) | Delivered | ¢ per kWh delivered | Unbalance |
|---|---|---|---|---|---|---|---|---|
| Uncontrolled | 1,874 | 191.6 | 195.8 | 4,845 | 4,910 | 100.09% | 31.5 | 0.295 |
| EDF | 1,883 | 144.0 | 145.9 | 4,116 | 4,147 | 99.92% | 26.8 | 0.294 |
| LLF | 1,880 | 142.9 | 144.8 | 4,096 | 4,126 | 99.92% | 26.7 | 0.294 |
| Round robin | 1,878 | 142.5 | 144.0 | 4,088 | 4,111 | 99.77% | 26.7 | 0.293 |
| MPC bill (LP) | 1,941 | 70.3 | 97.5 | **3,031** | 3,453 | 98.92% | **19.9** | 0.275 |
| MPC valley (QP) | 2,445 | 78.5 | 83.0 | 3,663 | 3,733 | 95.88% | 24.9 | 0.316 |
| MPC phase (QP) | 2,435 | 68.6 | 80.0 | 3,498 | 3,675 | 96.76% | 23.5 | **0.240** |
| MPC combined (QP) | 2,313 | 77.6 | 83.1 | 3,518 | 3,603 | 96.50% | 23.7 | 0.248 |
| **MPC combined + forecast** | 2,109 | 67.4 | 74.6 | 3,155 | 3,266 | 97.38% | 21.1 | 0.247 |
| Offline bill LP (month) | 2,046 | 71.2 | 81.0 | 3,150 | 3,302 | 99.57% | 20.6 | 0.284 |
| Offline combined (month) | 2,203 | **64.0** | **67.0** | 3,196 | 3,242 | 99.59% | 20.9 | 0.251 |

All controlled schedules stay within the network limits (max utilisation 1.00, no infeasible periods). Uncontrolled reaches 2.06× a limit.

**Transformer cap.** EDF, LLF and round robin check every network constraint before charging, including the transformer's secondary and primary limits. A balanced load on 208 V allows at most 150.1 kW. Their highest 5-min peaks:

| Runs | Highest EDF / LLF / RR peak |
|---|---|
| E1 | 150.1 kW (at the limit) |
| E7 | 145.9 kW |
| E8, any level | 147.6 kW |

Their utilisation never exceeds 1.000. Only uncontrolled charging goes over (212 kW in E1, 196 kW in E7). It is kept as the unconstrained reference.

**Value of arrival forecasting** (combined QP, without → with forecast):

- Billed peak 77.6 → 67.4 kW (−13%).
- Bill $3,518 → $3,155 (−$362, −10.3%).
- Energy delivered 96.50% → 97.38%.
- The peak gap to the offline combined optimum shrinks from 13.6 kW to 3.4 kW, so 75% of it is closed.
- Per kWh delivered, the bill (21.1¢) is within 1% of the offline benchmark (20.9¢).
- Forecast accuracy over the 19 days: arrivals MAPE 5.7%, energy MAPE 7.8%, bias −35 kWh/day.

**Other findings**

- **The bill LP is the cheapest online controller** ($3,031, 19.9¢/kWh).
  - After day 3 it sits exactly on its 70.3 kW month-to-date peak every day. Capacity that is already paid for is treated as free, and it is used to buy cheap energy.
- **15-min billing invites 5-min spikes.**
  - The bill LP's 5-min peak (97.5 kW) is 39% above its billed peak. Under 5-min billing its bill would be $423 higher.
  - The QPs' valley terms keep the two definitions close: within $70–180.
- **P* = 54.9 kW only shaped the first two days.**
  - The bill LP and the combined QP held 54.9–55.1 kW on 4–5 September.
  - On 6 September (97 sessions, 830 kWh) the peak rose to 70–78 kW.
  - The joint offline optimum needs 64.0 kW for September, so August's P90 was below what this month allows.
- **Tapering batteries cost the online MPCs 1–4% of energy.**
  - The baselines deliver 99.8–99.9% and the offline optima 99.6%.
  - `batt_cap_fn` picks the smallest battery size and highest initial charge for which charging at full rate over the *whole stay* just delivers the recorded energy. So any deferral of such an EV is lost for good, and `SimpleRampdown` reveals the taper only once it has started.
  - The QPs defer the most and lose the most. Forecasting helps by starting early cars sooner.
  - This also confounds raw bills, so compare ¢ per kWh delivered.
  - The offline bill LP's bill ($3,150) is above the online bill LP's ($3,031) only because it delivers 100 kWh more. On its objective (bill + $100 per unmet kWh) it is $9,765 vs $19,605.
- **The offline optima still miss 0.4% in replay.**
  - The linear taper bound is exact at full pilot.
  - At partial pilot, ACN's taper stage delivers less than the pilot.

**Computation.**

- Rate variables now exist only inside each EV's plug-in window (sparse lift), and the taper "energy so far" is a per-EV recursion. Results are unchanged; a one-day check reproduced the same metrics.
- Offline full day: 11 s → 0.9 s. With taper: over 15 min → 43 s.
- MPC per call, with 10 jobs × 2 solver threads: median 0.05–0.11 s, P99 2.0–2.9 s, max 6.2 s. Forecasting adds about 30% per call.
- Joint monthly offline solve: 17 min (combined QP), 10 min (bill LP).

## E8 (v3) — Imperfect information · `figures/v3_info_cost.png`, `figures/v3_pred_error.png`, `figures/v3_day.png`

The E7 month (same billing, taper batteries, P*, finish-by and forecast), but the schedulers see **predicted** departure and kWh. ACN-Sim keeps the true values. The static snapshot has no driver `userInputs`, so predictions come from the previous 28 weekdays, binned by arrival hour (`evsched/estimates.py`). Information levels:

- **perfect**: true values (E7);
- **predicted**: bin medians, re-predicted conditionally when an EV overstays or is still charging after its predicted kWh;
- **conservative**: P25 stay and P75 kWh, same updates;
- **naive**: one global median, no updates.

That is 8 informed schedulers × 3 levels = 24 month-jobs. Uncontrolled ignores the information, so its E7 row stands in, and the offline optima stay perfect-information. Full table: `results/table_info.csv`.

**Prediction quality at plug-in** (1,772 September sessions, predicted − true):

| Level | Stay MAE | Stay bias | Leave later than predicted | kWh MAE | kWh bias | Need more than predicted | Day's kWh, predicted / true |
|---|---|---|---|---|---|---|---|
| predicted | 2.6 h | −0.2 h | 49% | 4.5 | −0.95 | 51% | 0.90 (0.71–1.04) |
| conservative | 3.2 h | −2.4 h | 73% | 5.5 | +3.99 | 26% | 1.47 (1.17–1.68) |
| naive | 3.5 h | −0.9 h | 50% | 4.5 | −1.12 | 51% | 0.88 (0.71–1.00) |

Energy is right-skewed, so per-session medians undercount a day's total energy by 10% on average, and by up to 29% on some days.

**September, by information level** (bill = 19 days of energy + $15.51 × monthly 15-min peak):

| Scheduler | Perfect bill · peak · delivered | Predicted | Conservative | Naive |
|---|---|---|---|---|
| EDF | $4,116 · 144.0 kW · 99.9% | $4,059 · 140.6 · 99.7% | $4,059 · 140.3 · 99.9% | $3,505 · 145.0 · 67.4% |
| LLF | $4,096 · 142.9 · 99.9% | $4,037 · 139.1 · 99.6% | $4,138 · 145.5 · 99.8% | $3,453 · 141.7 · 67.4% |
| Round robin | $4,088 · 142.5 · 99.8% | $4,086 · 142.4 · 99.7% | $4,092 · 142.5 · 99.8% | $3,495 · 144.4 · 67.4% |
| MPC bill (LP) | **$3,031** · 70.3 · 98.9% | $2,924 · 54.9 · 92.3% | **$3,261** · 79.7 · 97.9% | $1,871 · 54.9 · 58.8% |
| MPC valley (QP) | $3,663 · 78.5 · 95.9% | $2,844 · 62.5 · 74.6% | $3,514 · 90.9 · 93.8% | $2,047 · 58.5 · 52.2% |
| MPC phase (QP) | $3,498 · 68.6 · 96.8% | $2,855 · 60.5 · 76.3% | $3,479 · 89.1 · 94.7% | $2,116 · 58.2 · 55.1% |
| MPC combined (QP) | $3,518 · 77.6 · 96.5% | $2,782 · 62.1 · 76.4% | $3,470 · 89.9 · 94.3% | $1,997 · 57.7 · 52.9% |
| MPC combined + forecast | $3,155 · 67.4 · 97.4% | $2,688 · 61.3 · 80.6% | $3,312 · 82.0 · 94.9% | $2,117 · 57.5 · 60.4% |

Bill per kWh delivered (¢), in the order perfect / predicted / conservative / naive:

| Scheduler | ¢/kWh |
|---|---|
| EDF | 26.8 / 26.5 / 26.4 / 33.8 |
| LLF | 26.7 / 26.4 / 27.0 / 33.3 |
| RR | 26.7 / 26.7 / 26.7 / 33.7 |
| Bill LP | 19.9 / 20.6 / 21.7 / 20.7 |
| Valley | 24.9 / 24.8 / 24.4 / 25.5 |
| Phase | 23.5 / 24.3 / 23.9 / 25.0 |
| Combined | 23.7 / 23.7 / 24.0 / 24.6 |
| Combined + forecast | 21.1 / 21.7 / 22.7 / 22.8 |

All schedules stayed within the network limits (max utilisation ≤ 1.00, no infeasible periods), and the month checks passed (monotone month-to-date peak; increments sum to the billed peak).

**Findings**

- **The greedy heuristics barely notice predictions.** EDF, LLF and RR stay within 0.3 points of energy and ±$60 of their E7 bills under predicted or conservative information.
  - They charge as soon as possible, so the predictions only change the order of service.
  - The conditional updates keep every car charging until it is really done.
- **Updates matter more than accuracy.** The naive level's errors are about as large as the predicted level's (kWh MAE 4.5 for both). Without updates, though, every car stops at the global median of about 7 kWh, or at its guessed departure, so every scheduler delivers only 52–67%.
  - Naive bills look cheap only because a third of the energy is missing. Per kWh delivered they are the worst for the heuristics (33–34¢).
- **The MPCs are the sensitive ones.** With medians they deliver 74–81% (QPs) and 92% (bill LP).
  - The medians undercount each day's total energy, so the MPC holds the peak near P* (55–62 kW) and defers charging.
  - The larger requests show up only once the predicted amount has been delivered. By then the car may be about to leave, or its battery has entered the taper.
  - Their raw bills fall, but their ¢/kWh barely moves: they simply buy less.
- **Conservative predictions fix most of it, at the price of a higher peak.**
  - Assuming an early departure and a large need: 94–98% delivered, but billed peaks of 80–91 kW (against 67–78 kW with perfect information).
  - Bill LP: $3,261, which is +$231 (+7.6%) and −1.0 point of energy against perfect information, or 21.7¢/kWh against 19.9¢.
  - Combined + forecast: $3,312 (+$157) at 94.9%.
- **The convex MPC still wins with realistic information.** The conservative bill LP is 20% cheaper than the best heuristic under the same information (EDF predicted, $4,059), and 18% cheaper per kWh (21.7 vs 26.5¢), though it delivers 1.8 points less energy.
  - The forecast still helps under conservative information: combined + forecast costs $158 less than combined ($3,312 vs $3,470), with an 8 kW lower peak.
- **The value of perfect information** for the best online controller is therefore about $230 a month (8%) plus about 1% of the energy. Real driver estimates (ACN-Data `userInputs`) could recover part of that.

**Computation** (24 jobs, 12 workers × 1 solver thread): per-call median 0.02–0.03 s, P99 0.6–0.7 s, max 2.1 s. A simulated MPC day takes 14–83 s (mean 26 s for the bill LP, 55 s with the forecast). The predicted horizons are shorter than the true ones, so solves are faster than in E7.

**Implementation note.** RoundRobin allocates pilots in 0.1 A steps, so it can never deliver the last few watt-hours of a predicted request. A first run therefore stalled its energy re-predictions (81.8% delivered). "Request met" now has a 0.05 kWh tolerance (`estimates.DONE_TOL`), with a unit test, and all 24 jobs were rerun.

### E8 extension — Stochastic MPC: expected unmet energy · `figures/v3_stochastic_frontier.png`, `figures/v3_stochastic_day.png`

The same predictions as `predicted`, but each MPC minimises the expected unmet energy over up to 20 historical (stay, kWh) scenarios per EV. The scenarios are conditioned every period on "still plugged in" and "not finished", at weight w $/kWh. Finish-by is dropped. Full write-up: `REPORT_stochastic.md`.

| MPC | predicted | conservative | w = 1 | w = 10 | w = 30 | w = 100 |
|---|---|---|---|---|---|---|
| Bill LP | $2,924 · 54.9 kW · 92.3% | $3,261 · 79.7 kW · 97.9% | $2,972 · 54.9 · 93.2% | $2,989 · 54.9 · 93.5% | $3,197 · 72.6 · **97.7%** | $3,689 · 115.6 · 99.7% |
| Valley QP | $2,844 · 62.5 · 74.6% | $3,514 · 90.9 · 93.8% | $2,819 · 54.6 · 83.2% | $2,987 · 54.9 · 92.3% | $3,200 · 70.8 · **96.3%** | $3,844 · 122.4 · 97.9% |
| Phase QP | $2,855 · 60.5 · 76.3% | $3,479 · 89.1 · 94.7% | $2,965 · 54.9 · 91.6% | $2,994 · 54.9 · 93.0% | $3,257 · 78.2 · **97.5%** | $3,748 · 117.6 · 98.6% |
| Combined QP | $2,782 · 62.1 · 76.4% | $3,470 · 89.9 · 94.3% | $2,605 · 54.5 · 78.4% | $2,987 · 54.9 · 92.6% | $3,178 · 68.2 · **96.3%** | $3,708 · 114.3 · 98.3% |
| Combined + forecast | $2,688 · 61.3 · 80.6% | $3,312 · 81.9 · 94.9% | $2,643 · 54.9 · 83.1% | $3,182 · 69.5 · **96.2%** | $3,286 · 80.9 · **97.5%** | $3,707 · 115.5 · 98.5% |

*Each cell: September bill · billed 15-min peak (kW) · energy delivered.*

- **w = 10:**
  - The QPs rise from 75% to 92–93% at the P* peak, for about $2,990: $480–530 below conservative. Their bill per kWh is the lowest of any level (21¢).
  - Combined + forecast reaches 96.2% at $130 below conservative, with a 12 kW lower peak. It dominates conservative.
- **w = 30 (the knee):** every MPC delivers 96.3–97.7% at billed peaks of 68–81 kW, $25–314 below conservative (93.8–97.9% at 80–91 kW). This is the cheapest weight tested that clears 95% for every MPC. The weight was picked in-sample, after the 10/100 runs.
- **w = 100:** every MPC delivers 98–99.7%, but the morning rush gives billed peaks of 114–122 kW, $240–430 above conservative.
- **Computation:**
  - Solves take a mean of 1.4–2.0 s (P99 about 12 s) against 0.12 s for `predicted`. The cause is KKT fill-in from the per-EV running-total chain.
  - The first 15 jobs took 47 CPU-h, 5.1 h on 12 workers. The 5 jobs at w = 30 took 1.5–3.2 h each (mean solve 1.25 s).
  - 0 infeasible schedules and utilisation ≤ 1.

## E9 (v4) — On-site solar · `figures/v4_weather.png`, `figures/v4_solar_bill.png`, `figures/v4_solar_day.png`

The E7 month with a **50 kW behind-the-meter PV array**, run for the MPCs only; the heuristics are not rerun. The bill is on net import, max(EV load − PV, 0): TOU energy plus $15.51 × the month's highest 15-min import peak. PV surplus earns nothing.

- **Weather:** synthetic and seeded by date. Each day draws a sky type (clear 70% / partly cloudy 20% / overcast 10%) and a daily clearness, plus AR(1) passing clouds. The 19 weekdays drew 12 clear and 7 partly cloudy days, and September drew no overcast day.
- **PV:** 324 kWh per weekday on average, about 40% of EV energy.
- **Forecast:** the MPC sees a day-ahead clearness (daily energy error 33 kWh), blended each period with the last measured clearness. The blend decays back to the day-ahead value over 2 h. The nowcast's mean absolute error is 1.1 kW for the next period, 3.1 kW at 1 h and 3.4 kW at 2 h; the day-ahead forecast alone errs by 4.1 kW.
- **Model:** energy cost, billed peak and the P* hinge use forecast net import. The valley term flattens net load P − PV̂. The MPC carries the realised net-import history and the month-to-date import peak.
- **Runs:** 5 MPCs × {perfect, predicted} information, plus the joint offline months (bill LP, combined) with known PV. Each simulated day gets only its own day's sun, so overnight EVs cannot reuse the next day's PV. Full table: `results/table_solar.csv`.

**September, bill · billed 15-min peak · delivered** (no PV = E7 / E8; with PV, the peak is the import peak):

| Scheduler | Perfect, no PV | Perfect, 50 kW PV | Predicted, no PV | Predicted, 50 kW PV |
|---|---|---|---|---|
| MPC bill (LP) | $3,031 · 70.3 · 98.9% | $2,514 · **97.2** · 98.6% (−17%) | $2,924 · 54.9 · 92.3% | $2,235 · 87.0 · 84.3% (−24%) |
| MPC valley (QP) | $3,663 · 78.5 · 95.9% | $2,332 · 66.6 · 96.2% (−36%) | $2,844 · 62.5 · 74.6% | $1,648 · 54.9 · 74.7% (−42%) |
| MPC phase (QP) | $3,498 · 68.6 · 96.8% | $2,300 · 64.1 · 97.2% (−34%) | $2,855 · 60.5 · 76.3% | $1,694 · 54.9 · 76.6% (−41%) |
| MPC combined (QP) | $3,518 · 77.6 · 96.5% | $2,235 · 66.0 · 96.7% (−36%) | $2,782 · 62.1 · 76.4% | $1,629 · 54.9 · 75.8% (−41%) |
| MPC combined + forecast | $3,155 · 67.4 · 97.4% | **$1,852** · 54.9 · 98.3% (−41%) | $2,688 · 61.3 · 80.6% | $1,624 · 54.8 · 83.8% (−40%) |
| Offline bill LP | $3,150 · 71.2 · 99.6% | **$1,675** · 55.1 · 99.6% (−47%) | | |
| Offline combined | $3,196 · 64.0 · 99.6% | $1,863 · 54.1 · 99.6% (−42%) | | |

All schedules stayed within the network limits (utilisation 1.000, no infeasible periods). The month checks passed: the month-to-date import peak never decreases, and daily increments sum to the billed peak.

**Findings**

- **Almost all PV is used by every MPC:** self-consumption is 98–99% (93–97% with predicted information), and self-sufficiency is about 40%. A 50 kW array rarely exceeds the midday EV load.
- **The solar-aware QPs gain 34–41%.**
  - The net-load valley term puts charging under the PV curve, which is also the on-peak window (26.7¢/kWh).
  - Combined + forecast holds the import peak at P* all month. At $1,852 it is within 11% of the offline bill LP, and slightly below the offline combined optimum ($1,863), which also pays for valley and phase terms.
  - Gross peaks rise (67 → 81 kW for combined + forecast), but they are not billed and stay well inside the transformer limit.
- **The bill LP gains only 17%: PV forecast errors ratchet its billed peak.**
  - It fills import exactly up to the month-to-date floor, net of forecast PV. Any PV shortfall then sets a new floor: the import peak climbs 55.8 → 97.2 kW, with 34 of the 41 kW of increments on the 7 partly cloudy days.
  - That costs about $650 in demand charge against the offline bill LP. With PV it becomes the most expensive online MPC: its demand charge is $1,508 against $852 for combined + forecast, at almost equal energy cost ($1,006 against $1,001).
  - Without PV (E7), its floor stayed at 70.3 kW all month.
- **Predicted information:** the QPs again gain 40–42% and hold the import peak at P*, with delivery at E8 levels (75–84%). The bill LP's delivery drops 92.3% → 84.3%: it defers more charging into the solar hours, and early leavers miss it.

**Computation** (12 jobs, 12 workers × 1 solver thread, 25 min wall time): per-call median 0.03–0.06 s, P99 0.4–1.1 s, max 2.0 s. A simulated MPC day takes 31–79 s. The joint offline months took 13 min (bill LP) and 15 min (combined).

## E9 extension — Stochastic MPC with PV · `figures/v4_stochastic_frontier.png`, `figures/v4_stochastic_day.png`, `figures/v4_forecast_fix_day.png`

The E9 month (50 kW PV, bill on net import) rerun for the 5 MPCs at **stochastic-30**: the E8 extension's expected unmet energy over the conditioned (stay, kWh) scenarios at 30 $/kWh, unchanged and not re-tuned for PV. A **conservative + PV** baseline was added. The perfect, predicted and offline rows are the existing E9 rows, unchanged. Full write-up: `REPORT_solar_stochastic.md`. Full table: `results/table_solar.csv`.

**September with 50 kW PV, bill · billed 15-min import peak · delivered:**

| MPC | Predicted | Conservative | Stochastic-30 | Days ≥ 95%: cons. / stoch. |
|---|---|---|---|---|
| Bill (LP) | $2,235 · 87.0 · 84.3% | $2,360 · 88.7 · 97.4% | **$1,994 · 65.1 · 98.2%** | 17 / 19 |
| Valley (QP) | $1,648 · 54.9 · 74.7% | $2,198 · 77.3 · 93.4% | **$1,934 · 62.9 · 97.2%** | 5 / 18 |
| Phase (QP) | $1,694 · 54.8 · 76.6% | $2,191 · 77.3 · 94.5% | **$1,928 · 61.5 · 97.6%** | 10 / 19 |
| Combined (QP) | $1,629 · 54.9 · 75.8% | $2,173 · 77.2 · 93.9% | **$1,919 · 61.5 · 97.3%** | 8 / 18 |
| Combined + forecast | $1,624 · 54.8 · 83.8% | $2,139 · 77.2 · 94.9% | **$2,090 · 72.6 · 97.9%** | 12 / 19 |

All 10 new jobs: 0 infeasible schedules, 0 fallbacks, utilisation ≤ 1.00. The month-to-date import peak carry is correct.

- **Every MPC clears 95%** (97.2–98.2%) and beats `conservative` + PV on delivery, bill ($49–365 less) and import peak (5–24 kW lower).
- **Cheaper than perfect information** for the four non-forecast MPCs ($315–520 less, perfect: $2,235–2,514). Front-loading moves energy into the cheap, PV-free morning.
- **The bill LP's PV ratchet mostly disappears:** its import peak is 65.1 kW, against 97.2 kW (perfect) and 87.0 kW (predicted). After day one, partly-cloudy-day increments are 10.2 kW, against 34.1 kW with perfect information. By midday, load sits below PV, so a PV dip eats spilled surplus instead of raising import.
- **Cost:** PV self-consumption falls from 93–97% to 85–89%, which is free here because surplus earns nothing. The QPs' import peak rises 6.6–8.0 kW above P*, mostly on partly cloudy days. The forecast MPC gains least: ghost EVs plus front-loading reach 72.6 kW.
- **Computation:** mean solve 0.4–1.0 s, P99 7–10 s, max 15 s; 3.0–15 min per simulated day; about 3 h wall time on 10 workers.

**Forecast fix (`+fix`).** 10.2 kW of the forecast MPC's 72.6 kW import peak came from one day, 24 September. Near the end of the 12 h horizon the evening ghost EVs kept their full energy at $100/kWh in windows only a few periods long. The plan showed a phantom 77 kW evening peak, which made a 72 kW real morning peak look free. A re-simulation of that day from the same floor (`scripts/diag_forecast_0924.py`, `results/diag_0924/`) found the cause. The opt-in `+fix` suffix:
- scales ghost energy by the share of its window inside the horizon;
- prices ghosts at the expected-unmet weight (30 $/kWh) through one certain scenario each;
- lets the hour in progress count only its remaining share of arrivals.

On 24 September the fix keeps import at 61.4 kW (original 71.7 kW). Month results at stochastic-30 with PV (0 infeasible schedules, 0 fallbacks, existing rows unchanged):

| MPC | Bill · import peak · delivered | Days ≥ 95% | ¢/kWh |
|---|---|---|---|
| Combined + forecast (original) | $2,090 · 72.6 · 97.9% | 19 | 13.9 |
| **Combined + fixed forecast** | **$1,904 · 60.2 · 97.8%** | 19 | 12.7 |
| Bill (LP) | $1,994 · 65.1 · 98.2% | 19 | 13.2 |
| **Bill (LP) + fixed forecast** (new) | **$1,961 · 63.1 · 98.4%** | 19 | 13.0 |

Combined + fixed forecast is the cheapest online MPC: $15 below the combined QP, and $235 below `conservative` + PV. Its clear-day ratchet falls from 13.0 kW to 0.1 kW. What remains, for both fixed-forecast MPCs, comes on partly cloudy days. Solves are slower: mean 1.3–1.4 s, max 21 s. Earlier forecast results (E7–E9, E8 extension) used the original ghosts and are unchanged.

## Modelling assumptions to state in the report
- BASIC (continuous 0–32 A) EVSEs instead of the real discrete-pilot AeroVironment/ClipperCreek units, to keep the problem convex.
- Requested energy = recorded delivered kWh, and departure = actual unplug time. E1–E7 give these true values to the schedulers. E8 gives them predictions from 4 weeks of history instead; the simulator keeps the truth. The static snapshot has no driver estimates (`userInputs`).
- E8: an EV that is still in ACN-Sim's active list after its predicted energy tells the controller only that it wants more, not how much. This matches a charger reporting that the car has stopped accepting charge.
- Hardware station ids are not mapped to real spaces. EVs are assigned to free EVSEs on the least-loaded phase, so phase results reflect a plausible, not the historical, phase allocation.
- The demand charge column applies the full monthly rate to each simulated day's peak (E1–E6). E7 bills the month's highest 15-min peak properly.
- E7: days are simulated one after another but independently. A session that runs past midnight does not overlap the next day's load.
- E7: tapering batteries are fitted by ACN's `batt_cap_fn`, which gives the most non-ideal battery consistent with the recorded energy. This is a pessimistic case for any scheduler that defers charging.
- E9: the weather is synthetic (one seed, no overcast day in September), PV feeds only the EV load, and surplus earns nothing. Each simulated day sees only its own day's sun.
- The robust formulation uses per-period solar protection Γ_solar ∈ [0,1] plus a Bertsimas–Sim budget on price deviations. A Bertsimas–Sim budget on solar across time does not give an LP here. A budget on the whole objective means maximising a convex function (no compact dual). A per-row budget has only one uncertain coefficient per row, so any Γ ≥ 1 collapses to full protection.

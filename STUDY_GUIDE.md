Yes. Let’s build this from zero, in the order you need to understand it.

**You do not need to memorize every sentence of the report. You need to understand the chain of decisions: what problem each term solves, how it becomes code, and what the results actually show.**

I have now reviewed:
- Your **latest 10-slide presentation PDF**.
- The **14-page report**.
- The implementation now available on GitHub: model, schedulers, prediction, forecasting, solar, simulation, metrics, and tests.
- The detailed experiment write-ups.

I also ran the tests: **32 passed; one was skipped because the external raw dataset is absent.** I did **not** reproduce the month-long results, so those remain reported experimental results.

One important change from earlier in our conversation: **the latest GitHub `main` now contains the source code.** Earlier, the checkout only contained figures.

---

# 0. The entire project in one story

Imagine an office with 54 EV chargers.

Many employees arrive in the morning and plug in their vehicles.

If every vehicle charges immediately:
- The electrical load becomes high.
- Some electrical limits may be exceeded.
- The facility pays a large peak-demand charge.

But a vehicle that needs two hours of charging might remain parked for six hours.

**We use that extra time as flexibility.**

Our controller repeatedly asks:

> “Given the vehicles currently connected, their remaining energy needs, their likely departure times, electricity prices, and network limits, how much current should each vehicle receive now?”

We formulate this as a **convex optimization problem**.

We solve it using **CVXPY and Clarabel**, apply the next five-minute charging decision, and repeat.

Then we make the problem progressively more realistic:

1. Account for the **monthly electricity bill** correctly.
2. Replace known driver requirements with **predictions**.
3. Handle prediction uncertainty using **historical scenarios**.
4. Add **solar generation and forecast errors**.
5. Add expected future arrivals through a corrected **arrival forecast**.

**The project is not “we invented a new solver.”**

It is:

> “We formulated and implemented a practical EV-charging optimization model, integrated it with a simulator, and evaluated how different objectives and uncertainty treatments affect cost, service, and electrical feasibility.”

Keep that sentence in your head.

---

# 1. First understand the electrical quantities

## 1.1 EV versus EVSE

- **EV:** the electric vehicle.
- **EVSE:** Electric Vehicle Supply Equipment—the charging station.

The controller sends a charging-current limit to the EVSE.

That command is often called a **pilot signal**.

### Pilot signal

A pilot of 20 A means:

> “This vehicle is permitted to draw up to 20 amperes.”

It does not guarantee that the battery will actually draw 20 A. A nearly full battery may accept less.

This distinction matters when we discuss battery tapering.

---

## 1.2 Current, power, and energy are different

### Current: amperes, A

This is what the optimizer controls.

```math
R_{it}=\text{current allocated to EV }i\text{ during interval }t.
```

### Power: kilowatts, kW

Power is the instantaneous rate of energy transfer.

The project uses:

```math
P_{it}=\frac{V_iR_{it}}{1000}.
```

The modeled charger voltage is **208 V**.

At the maximum current of **32 A**:

```math
P_{it}=\frac{208\times32}{1000}=6.656\text{ kW}.
```

### Energy: kilowatt-hours, kWh

Energy is power multiplied by time.

One control period is five minutes:

```math
\Delta=\frac{5}{60}=\frac{1}{12}\text{ hour}.
```

Therefore, charging at 32 A for one period delivers approximately:

```math
E_{it}=6.656\times\frac{1}{12}
=0.555\text{ kWh}.
```

**Remember:**
- A: the control command.
- kW: the charging rate.
- kWh: how much energy the vehicle receives.

---

## 1.3 Why parking time creates flexibility

Suppose a vehicle:
- Arrives at 9 a.m.
- Leaves at 3 p.m.
- Requires 10 kWh.

At 6.656 kW, its ideal minimum charging time is:

```math
\frac{10}{6.656}\approx1.5\text{ hours}.
```

But it remains parked for six hours.

So we do not necessarily need to charge it immediately at full rate.

We can distribute its charging across the six-hour window.

This spare time is related to **laxity**:

```math
\text{Laxity}
=
\text{time remaining}
-
\text{minimum charging time required}.
```

More laxity means more scheduling flexibility.

**Do not promise that every vehicle has this much flexibility.** Some vehicles have short stays, large requests, or tapering batteries.

---

## 1.4 Why three phases matter

The garage is supplied by a three-phase electrical network.

Chargers are connected between phase pairs:
- AB
- BC
- CA

The modeled phase angles are:
- $`30^\circ`$
- $`-90^\circ`$
- $`150^\circ`$

Two schedules can have the same total kW but different loading on individual phases.

Therefore:

> Keeping total power below a site-level number is not sufficient to guarantee that every feeder and phase is safe.

That is why we model the network explicitly.

---

# 2. What data and system did we use?

## 2.1 Study setting

The main evaluation uses:
- Caltech Adaptive Charging Network.
- 54 charging stations.
- A transformer described in the final report as **150 kVA**.
- 1,772 charging sessions.
- 19 weekdays in September 2018.
- Five-minute simulation intervals.

**Your slide says 150 kW.** For the physical transformer rating, say **150 kVA**, consistent with the final report. Under the model’s near-unity-power-factor interpretation, the balanced real-power capacity is approximately 150 kW.

Do not treat kW and kVA as universally interchangeable.

---

## 2.2 Where did the sessions come from?

**ACN-Data** supplies the historical data.

The static dataset had an empty session-summary file, so the project reconstructed session summaries from individual time-series files.

In `src/evsched/data.py`:

| Session field | How the code obtains it |
|---|---|
| Arrival | First timestamp |
| Departure | First `UNPLUGGED` timestamp; otherwise last timestamp |
| Energy | Maximum cumulative “Energy Delivered (kWh)” |
| Station identifier | Extracted from the filename |

The code removes sessions with:
- Less than 0.1 kWh.
- Less than five minutes of duration.

It converts timestamps to:

```python
TIMEZONE = "America/Los_Angeles"
```

Cleaned sessions are cached in Parquet format.

### Crucial assumption

**Recorded delivered energy is used as the simulated energy request.**

We do not have independent proof that this equals every driver’s original desired energy.

Say:

> “We use historically delivered energy as a proxy for the charging requirement.”

That is more accurate than saying:

> “We have the exact energy every driver originally requested.”

---

## 2.3 Was the physical charger assignment historical?

Not exactly.

The dataset’s hardware identifiers could not be matched to the simulator’s charger identifiers.

Therefore, `events.py` assigns each arriving EV to:
1. A free charger.
2. On the phase group with the fewest occupied chargers.
3. With a seeded random tie-break.

This means:
- Session timing and energy are historical.
- Charger/phase assignment is synthetic.
- The assignment is held consistent across scheduling comparisons.

**Therefore, phase-balancing results describe the modeled assignment, not a reconstruction of the exact historical parking arrangement.**

---

## 2.4 What tariff did we use?

The code specifies:

```python
TARIFF = "sce_tou_ev_4_march_2019"
```

The reported summer weekday prices are:

| Time | Price |
|---|---:|
| Midnight–8 a.m. | \$0.0562/kWh |
| 8 a.m.–noon | \$0.0925/kWh |
| Noon–6 p.m. | \$0.2667/kWh |
| 6 p.m.–11 p.m. | \$0.0925/kWh |
| 11 p.m.–midnight | \$0.0562/kWh |

Demand charge:

```math
d=15.51\text{ dollars per kW}.
```

**Precision point:** the experiment applies this tariff definition to September 2018 session data. These are simulated bills under the selected tariff, not recovered utility invoices.

---

# 3. Why is the electricity bill an optimization problem?

There are two main financial components.

## 3.1 Energy charge

```math
\text{Energy charge}
=
\sum_t p_tP_t\Delta.
```

Where:
- $`p_t`$: electricity price in interval $`t`$.
- $`P_t`$: total charging power.
- $`\Delta`$: interval duration in hours.

This term encourages charging during cheaper hours.

---

## 3.2 Demand charge

The monthly demand charge depends on the **highest billed power**, not total energy.

The project models billed demand as a trailing 15-minute average:

```math
\bar P_t=
\frac{P_t+P_{t-1}+P_{t-2}}{3}.
```

Then:

```math
\text{Demand charge}
=
15.51\max_t\bar P_t.
```

### Simple example

A 100 kW monthly billed peak gives:

```math
15.51\times100=\$1,551.
```

A 60 kW peak gives:

```math
15.51\times60=\$930.60.
```

Same total energy can therefore lead to very different bills.

### Why energy-cost minimization alone is insufficient

If many EVs all charge in the cheapest interval, the energy charge may decrease while the peak-demand charge increases.

**The two cost components can pull in opposite directions.**

That is the central motivation for the bill-oriented optimizer.

---

# 4. What exactly are we optimizing?

## 4.1 Decision variable

The main mathematical variable is:

```math
R_{it}\ge0.
```

Read this as:

> “Charging current for vehicle $`i`$ during time interval $`t`$.”

It forms a matrix:

```math
R\in\mathbb R^{n\times T}.
```

- Rows: vehicles.
- Columns: future five-minute periods.

Example:

```math
R=
\begin{bmatrix}
20&20&10&0\\
0&10&20&20
\end{bmatrix}.
```

Interpretation:
- EV 1 gets 20 A, 20 A, 10 A, then zero.
- EV 2 gets zero, 10 A, 20 A, then 20 A.

This is a schedule, not a prediction of driver behavior.

---

## 4.2 Main notation dictionary

| Symbol | Meaning | Typical code name |
|---|---|---|
| $`i`$ | EV index | Row index |
| $`t`$ | Time-period index | Column index |
| $`T`$ | Number of planning intervals | `T` |
| $`m`$ | Network-constraint index | Constraint row |
| $`s`$ | Historical scenario index | Scenario arrays |
| $`R_{it}`$ | Charging current, A | `R`, `rates` |
| $`\bar r_{it}`$ | Current upper bound | `evs.ub` |
| $`V_i`$ | Charger voltage | `infra.voltages` |
| $`e_i`$ | Remaining energy requirement | `evs.demand` |
| $`E_{it}`$ | Energy delivered in one interval | Per-period energy expressions |
| $`u_i`$ | Unmet energy | `u` |
| $`P_t`$ | Aggregate EV power | `P` |
| $`p_t`$ | Energy price | `prices` |
| $`d`$ | Demand-charge rate | `demand_charge` |
| $`\bar P_t`$ | Rolling billed power | `billed` |
| $`P^\star`$ | Historical peak-planning target | `peak_target_kw` |
| $`F_{\mathrm{mtd}}`$ | Month-to-date billed peak | `month_peak_kw` |
| $`F`$ | Current planning floor | `DemandSpec.floor_kw` |
| $`A`$ | Network coefficient matrix | `infra.A` |
| $`c_m`$ | Current limit for constraint $`m`$ | `infra.limits` |
| $`\phi_i`$ | Phase angle | `infra.phases` |
| $`I_t^-`$ | Negative-sequence current | Computed from coefficients |
| $`d_{is}`$ | Remaining time in scenario $`s`$ | Scenario departure array |
| $`k_{is}`$ | Remaining energy in scenario $`s`$ | Scenario energy array |
| $`\pi_{is}`$ | Scenario probability/weight | Scenario weights |
| $`z_{is}`$ | Scenario-specific unmet energy | `z` |
| $`C_i(d)`$ | Energy delivered by deadline $`d`$ | Running total `C` |
| $`S_t`$ | Nonnegative grid import | `S` in PV formulation |
| $`\widehat{pv}_t`$ | Forecast solar power | `pv_hat` |
| $`\delta_t`$ | Solar deviation allowance | `pv_dev` |

**Naming trap:** `S` is also used locally in the tapering implementation for previously delivered energy. Variable names are local to their context; not every `S` means grid import.

---

# 5. The constraints: what schedules are allowed?

A constraint is a rule the optimizer must obey.

The objective says what is desirable. Constraints say what is permitted.

## 5.1 Charging-rate and availability constraints

```math
0\le R_{it}\le\bar r_{it}.
```

For a connected EV, the upper bound may be 32 A or lower.

Outside its availability window:

```math
\bar r_{it}=0.
```

Therefore, the same constraint handles both:
- Maximum charging current.
- No charging before arrival or after departure.

### Why continuous current?

Continuous values such as 17.4 A keep the optimization convex.

Real chargers may allow only discrete current levels.

The project deliberately relaxes that hardware restriction.

A deployment would need:
- Feasibility-aware rounding, or
- Mixed-integer optimization.

**Naively rounding every current upward could violate a network limit.**

---

## 5.2 Energy requirement

Energy delivered to EV $`i`$:

```math
\sum_t E_{it}
=
\sum_t\frac{V_iR_{it}\Delta}{1000}.
```

The deterministic formulation uses:

```math
\sum_tE_{it}+u_i=e_i,
\qquad u_i\ge0.
```

### Why add $`u_i`$?

Suppose the EV needs 10 kWh but can physically receive only 8 kWh before departure.

Without slack:

```math
8=10
```

is impossible, so the problem becomes infeasible.

With slack:

```math
8+2=10.
```

The optimizer records 2 kWh of unmet demand.

The objective heavily penalizes it.

### Why not force complete delivery?

Because:
- Some recorded requests cannot fit within rounded time windows.
- Shared network capacity may be insufficient.
- Batteries may taper.
- Predictions may be wrong.

The report’s hard-energy experiment encountered about **32–38 infeasible calls per day**, then fell back to the soft formulation.

### Important qualification

A penalty of \$100/kWh strongly discourages unmet energy, but it is still a **soft preference**, not a mathematical guarantee of full delivery.

---

## 5.3 Network constraints

The network has eight modeled limits:
- Two pod/feeders.
- Three secondary-phase currents.
- Three primary-phase currents.

The phasor constraint is:

```math
\left|
\sum_iA_{mi}R_{it}e^{j\phi_i}
\right|
\le c_m.
```

### What does each part mean?

- $`A_{mi}`$: how EV $`i`$'s current contributes to network element $`m`$.
- $`R_{it}`$: chosen charging current.
- $`e^{j\phi_i}`$: phase-angle information.
- Absolute value: magnitude of the resulting complex current.
- $`c_m`$: allowable magnitude.

### Why complex numbers?

AC currents on different phases have different angles.

They must be added as vectors, not just ordinary positive numbers.

Using:

```math
e^{j\phi}=\cos\phi+j\sin\phi,
```

the constraint becomes:

```math
\left\|
\begin{bmatrix}
\sum_iA_{mi}\cos\phi_iR_{it}\\
\sum_iA_{mi}\sin\phi_iR_{it}
\end{bmatrix}
\right\|_2
\le c_m.
```

This is a **second-order cone constraint**.

### How to explain SOC without sounding complicated

> “The network current has real and imaginary components. Its magnitude is their Euclidean norm. Bounding that norm gives a second-order cone constraint.”

### Small numerical margin

The code uses:

```python
margin = 1e-3
```

So it effectively limits current to:

```math
c_m-0.001\text{ A}.
```

This avoids tiny solver-tolerance violations.

### What “exact network constraints” means

They are exact phasor-current constraints for the adopted fixed-voltage network model.

Do not say this is a full nonlinear AC power-flow model with every voltage, loss, and reactive-power effect.

---

# 6. The objective: why does it choose one feasible schedule over another?

The report’s main objective is:

```math
\begin{aligned}
J={}&
w_c\sum_tp_tP_t\Delta\\
&+w_dd\max\left\{\max_t\bar P_t,F\right\}\\
&+w_h\Delta\sum_t(\bar P_t-F)^+\\
&+\frac{w_v\Delta}{P_{\mathrm{ref}}}\sum_tP_t^2\\
&+\frac{w_\phi\Delta}{I_{\mathrm{ref}}}\sum_t|I_t^-|^2\\
&+w_u\sum_iu_i.
\end{aligned}
```

You should explain this **term by term**, not read the entire equation aloud.

---

## 6.1 Energy-cost term

```math
w_c\sum_tp_tP_t\Delta.
```

Purpose:

> Buy energy during cheaper tariff periods.

`weights.cost` controls its importance.

---

## 6.2 Monthly demand-charge term

```math
w_dd\max\left\{\max_t\bar P_t,F\right\}.
```

Purpose:

> Avoid raising the billed peak.

The floor is implemented as:

```math
F=\max\left(
P^\star,\,
\text{previous-days peak},\,
\text{today's peak so far}
\right).
```

### Why remember the previous peak?

Suppose the facility has already reached 70 kW this month.

Using 65 kW tomorrow does not increase the demand charge.

Using 80 kW adds:

```math
(80-70)\times15.51=\$155.10.
```

Therefore, the marginal cost of peak demand depends on the month’s history.

### “Already paid for” does not mean electricity is free

It means **no additional demand charge below that peak**.

Energy charges still apply.

---

## 6.3 What is $`P^\star=54.93`$ kW?

It is a historical planning target.

The code:
1. Runs offline daily optimization on August data.
2. Extracts the daily billed peaks.
3. Takes their 90th percentile.

It is:
- Not the transformer rating.
- Not a hard constraint.
- Not a promise that September demand remains below 55 kW.

It is an operating target.

### Why the 90th percentile?

It gives a high-but-not-extreme historical reference.

It is a design choice, not a proven universally optimal percentile.

---

## 6.4 How is the maximum made solver-friendly?

Introduce an auxiliary peak variable $`q`$:

```math
q\ge\bar P_t\quad\forall t,
```

```math
q\ge F.
```

Then minimize:

```math
dq.
```

This is called an **epigraph formulation**.

Because increasing $`q`$ costs money, the solver chooses the smallest feasible $`q`$.

CVXPY performs this kind of transformation for expressions such as:

```python
cp.maximum(cp.max(billed), demand.floor_kw)
```

---

## 6.5 Peak-exceedance hinge

```math
w_h\Delta\sum_t(\bar P_t-F)^+,
```

where:

```math
x^+=\max(x,0).
```

Example:
- $`\bar P_t=60`$, $`F=55`$: penalty applies to 5.
- $`\bar P_t=50`$, $`F=55`$: penalty is zero.

The demand term cares about the single highest peak.

The hinge also penalizes **the extent and duration of exceeding the floor**.

It does not itself force a perfectly smooth profile.

---

## 6.6 Valley filling: why square the power?

```math
\frac{w_v\Delta}{P_{\mathrm{ref}}}\sum_tP_t^2.
```

Consider two schedules with equal energy over two equal intervals:

- Schedule A: 10 kW, 0 kW.
- Schedule B: 5 kW, 5 kW.

Their squared-load sums:

```math
10^2+0^2=100,
```

```math
5^2+5^2=50.
```

The second is preferred.

Thus:

> Squaring power makes large peaks disproportionately expensive and encourages spreading the load.

`P_ref = 150` scales the numerical magnitude. It is **not a separate hard limit**.

Also, the base experiment does not model a general building-load profile. It primarily flattens EV load; with PV it flattens forecast net load.

---

## 6.7 Phase-balancing term

```math
\frac{w_\phi\Delta}{I_{\mathrm{ref}}}
\sum_t|I_t^-|^2.
```

The negative-sequence current is:

```math
I_t^-=
\frac13\left(
I_{A,t}+a^2I_{B,t}+aI_{C,t}
\right),
\qquad a=e^{j2\pi/3}.
```

You do not need to derive symmetrical components during the presentation.

Say:

> “Negative-sequence current measures an unwanted component of three-phase imbalance. We penalize its squared magnitude to encourage balanced phase loading.”

### Why not just flatten total power?

A flat total load can still be distributed unevenly across phases.

Load flattening and phase balancing solve different problems.

### Why no zero-sequence penalty?

For the modeled line-to-line charger connections:

```math
I_A+I_B+I_C=0.
```

The zero-sequence component is therefore zero in this model.

---

## 6.8 Unmet-energy penalty

```math
w_u\sum_iu_i,
\qquad w_u=100\text{ dollars/kWh}.
```

This is a **modeling penalty**, not a fee actually paid to the electricity utility.

Its job is to make service failures expensive in the optimization.

### Objective value versus electricity bill

They are not the same.

```math
\text{Optimization objective}
=
\text{bill-related terms}
+
\text{service and grid-quality penalties}.
```

The reported electricity bill excludes artificial shortfall, flatness, and phase penalties.

This distinction is essential when comparing MPC with the offline benchmark.

---

## 6.9 Two extra terms present in the code

### Early-charging tie-break

```python
early = 1e-3
```

The term is approximately:

```math
10^{-3}\sum_t\frac{t}{T}P_t\Delta.
```

Later charging receives slightly more penalty.

Purpose:

> Prefer earlier charging when schedules are otherwise nearly equivalent.

It is not a replacement for modeling uncertain departure.

### Soft finish-by term

The deterministic monthly controller prefers to finish **30 minutes before departure**.

An additional shortfall variable measures how much scheduled energy remains undelivered by this early deadline.

Penalty:

```math
1\text{ dollar/kWh}.
```

This is softer than the main \$100/kWh unmet-energy penalty.

**In stochastic mode, the finish-by term is disabled**, because scenario-specific deadlines already represent departure risk.

---

# 7. Why is the problem convex?

A convex problem has:
1. A convex objective.
2. A convex feasible set.

Here:
- Charging bounds are linear.
- Energy equations are affine.
- Network norms are convex SOC constraints.
- Max and positive-part functions are convex.
- Squared affine expressions are convex.
- Weights are nonnegative.

### Why does convexity matter?

For a convex problem, a local optimum is a global optimum.

A suitable numerical solver can efficiently find a globally optimal solution of the **formulated model**, within tolerances.

### Two qualifications

**First:** global optimum does not imply a unique schedule.

Several schedules may have the same objective value.

**Second:** an optimal five-minute planning decision does not imply the globally best realized monthly outcome.

The controller does not know the future.

---

## LP, QP, and SOCP: what should you say?

### LP

Linear objective and linear constraints.

### QP

Quadratic objective, usually with linear constraints in the standard definition.

### SOCP

Contains second-order cone constraints such as:

```math
\|Bx+b\|_2\le c^\top x+d.
```

### Your project

- **BILL-LP:** linear/piecewise-linear objective with SOC constraints.
- **COMBINED-QP:** convex quadratic objective with SOC constraints.

Therefore:

> “The names refer to the objective. The complete bill-oriented problem is an SOCP because of the network constraints.”

**Your slide’s “Still an LP” should mean “the new scenario term remains linear/piecewise linear.” It does not make the entire network-constrained problem a pure LP.**

---

# 8. How do we solve it? Understand the three separate layers

Memorize this distinction:

| Layer | What it does |
|---|---|
| **MPC** | Decides when to replan and how much of the plan to apply |
| **CVXPY** | Expresses and compiles the mathematical optimization problem |
| **Clarabel** | Numerically solves that problem |

And:

| Supporting tool | Role |
|---|---|
| **ACN-Sim** | Simulates EVs, chargers, batteries, and the network |
| **ACN-Data** | Historical session data |
| **SCS** | Fallback solver |

---

## 8.1 Model Predictive Control, step by step

At 9:00 a.m.:

1. Identify active EVs.
2. Read energy already delivered.
3. Obtain requirements or predictions.
4. Read network limits and prices.
5. Update the billed peak.
6. Construct a future schedule.
7. Apply only the **9:00–9:05** currents.

At 9:05:

1. Observe what actually happened.
2. Add any new arrivals.
3. Remove departures/finished sessions.
4. Update forecasts.
5. Solve again.

This is **receding-horizon control**.

### Why not apply the whole plan?

Because:
- New cars arrive.
- Cars leave early.
- Batteries accept less current than expected.
- Solar output differs from forecast.

Replanning prevents an old plan from being followed blindly.

---

## 8.2 Why a 12-hour horizon?

The horizon extends to the latest relevant departure, capped at:

```math
144\times5\text{ minutes}=12\text{ hours}.
```

The report documents an unusually long stay of about 52 hours.

Without a cap, one such session could create a huge optimization horizon for every call.

The cap balances foresight and computation.

**Trade-off:** demands and scenario deadlines beyond the cap are handled at the capped horizon. This can create conservative behavior, so 12 hours is a modeling choice, not a theoretically perfect horizon.

---

## 8.3 What Clarabel does conceptually

Clarabel uses an interior-point method for conic optimization.

At a high level, it:
1. Receives the objective and constraints in a standard form.
2. Iteratively computes primal and dual updates.
3. Solves structured linear systems.
4. Reduces constraint violations and optimality residuals.
5. Stops when numerical tolerances are met.

You did **not** implement the interior-point algorithm.

You implemented:
- The model.
- The controller.
- The experiment pipeline.

The solver supplies the numerical optimization algorithm.

---

# 9. The experiment names: do not mix them up

The report uses setup names; the code uses experiment/version names.

| Presentation/report | Code/write-up terminology | Meaning |
|---|---|---|
| S1 | v1, E1–E6 | Daily simplified experiments |
| S2 | v2, E7 | Monthly demand billing |
| S3 | v3, E8 | Predicted driver information |
| SAA extension | E8 extension | Scenario-based unmet energy |
| S4 | v4, E9 | Solar generation |
| SAA + PV | E9 extension | Scenarios combined with solar |
| Corrected forecast | `+forecast+fix` | Corrected ghost-EV forecast |

These are extensions of one shared model—not unrelated projects.

---

# 10. S1: what did the initial experiments establish?

S1 is mostly in the report appendix.

It uses simpler daily scheduling, ideal batteries, and perfect connected-driver information.

Its experiments ask:

| Experiment | Question |
|---|---|
| E1 | How do objectives compare with heuristics? |
| E2 | What happens when weights change? |
| E3 | How much does future information help? |
| E4 | Should energy constraints be hard or soft? |
| E5 | Does robust optimization help under uncertainty? |
| E6 | How does runtime scale? |

### Useful findings

- Energy-only optimization reduces energy cost but can retain high peaks.
- Flattening reduces peaks but can move charging into expensive hours.
- Phase penalties improve phase balance.
- Hard energy constraints can become infeasible.
- Online peak control suffers from not knowing future arrivals.

### Why move beyond S1?

Because the actual demand charge is monthly.

Resetting the peak daily and spreading the monthly charge approximately across weekdays does not reproduce the same decision incentives.

That motivates S2.

---

# 11. S2: monthly billing and the main 26% result

## 11.1 What changed?

S2 adds:
- Three-period rolling billed demand.
- Month-to-date peak carried between days.
- Historical peak target.
- Full demand-charge weight.
- Tapering batteries.
- Soft early-finish preference.
- Twelve-hour horizon.

Each scheduler runs through the days in order.

After each day:

```python
peak = max(peak, row[billed_peak_key])
```

The next day receives that peak.

---

## 11.2 What baselines did we compare against?

### Uncontrolled

Give every EV its maximum current immediately.

It ignores the shared network limits.

It is an **infeasible reference**, not a safe operating policy.

### EDF: Earliest Deadline First

Prioritize the EV leaving soonest.

Allocate as much as possible while retaining network feasibility.

### LLF: Least Laxity First

Prioritize the EV with the least scheduling flexibility:

```math
\text{Laxity}
=
\text{time left}
-
\frac{\text{energy left}}{\text{maximum power}}.
```

### Round Robin

Increase EV allocations in small rounds while maintaining feasibility.

### Why do the heuristics have high bills?

They protect the network but are largely **bill-blind**.

They charge aggressively now rather than planning around the monthly peak.

---

## 11.3 Important simulator detail

ACN-Sim checks whether a schedule violates network limits, but an invalid schedule can still be applied with a warning.

Therefore:

> The simulator does not automatically make an unsafe controller safe.

Our controller must enforce the limits.

That explains why uncontrolled charging can report high energy delivery despite being infeasible.

---

## 11.4 Main S2 results

| Controller | Bill | Billed peak | Delivery |
|---|---:|---:|---:|
| Uncontrolled | \$4,845 | 191.6 kW | 100.1% |
| EDF | \$4,116 | 144.0 kW | 99.9% |
| LLF | \$4,096 | 142.9 kW | 99.9% |
| Round Robin | \$4,088 | 142.5 kW | 99.8% |
| **MPC-BILL-LP** | **\$3,031** | **70.3 kW** | **98.9%** |
| MPC-COMBINED-QP | \$3,518 | 77.6 kW | 96.5% |
| Offline BILL-LP | \$3,150 | 71.2 kW | 99.6% |

### Calculate the savings

Relative to Round Robin:

```math
\frac{4088-3031}{4088}\times100
\approx25.9\%.
```

Peak reduction:

```math
\frac{142.5-70.3}{142.5}\times100
\approx50.7\%.
```

### What should you say?

> “The bill-oriented MPC reduced the simulated monthly bill by about 26% and the billed peak by about 51% relative to the cheapest heuristic, while respecting the network limits and delivering 98.9% of the required energy.”

### What should you not hide?

Round Robin delivers 99.8%, versus 98.9% for MPC.

That is a **0.9-percentage-point service difference**.

---

## 11.5 Why is COMBINED-QP more expensive?

It optimizes more than the bill:
- Flatness.
- Phase balance.
- Service.
- Electricity costs.

A more balanced schedule may use more expensive periods.

So:

> “Combined” does not mean “always cheapest.” It means “multiple objectives are included.”

---

## 11.6 Why does MPC have a lower bill than OPT?

This is a likely viva question.

The answer is **not**:

> “Our online algorithm beats the global optimum.”

The benchmark:
- Has more information.
- Uses a battery-taper model unavailable to online MPC.
- Delivers more energy.
- Optimizes a penalized objective, not just the reported bill.
- Is replayed in a simulator with some model mismatch.

Therefore, the raw bills are not equal-service objective values.

Say:

> “The online controller’s lower bill is partly associated with lower delivered energy. The offline benchmark is not being beaten on an identical optimization problem and identical service outcome.”

Also, **19.9 versus 20.6 cents/kWh** is close, but it is not a rigorous “within 3% optimality guarantee.”

---

## 11.7 Billed peak versus five-minute peak

MPC-BILL-LP has:
- 15-minute billed peak: **70.3 kW**.
- Highest five-minute power: **97.5 kW**.

Why?

A short spike can be averaged with lower neighboring intervals.

The electrical network constraints still apply every five minutes.

**A lower billed peak does not mean every five-minute load is below that number.**

---

# 12. Battery tapering: why known requirements still do not give 100% delivery

## 12.1 What is tapering?

A battery can often accept high power when relatively empty.

Near full charge, its accepted power decreases.

Therefore:
- The commanded pilot is an upper bound.
- Actual charging may be lower.

The monthly simulations use:

```python
Linear2StageBattery
```

The transition state of charge is approximately 80%.

---

## 12.2 What does the online controller know?

The online MPC does not receive full battery capacity and initial state-of-charge information.

It uses `SimpleRampdown` to observe when actual charging falls below the commanded pilot and lower future rate estimates.

### Why is delayed charging risky?

If you delay too long and the battery later tapers, you may not have enough remaining time to finish.

Replanning cannot recover time already lost.

---

## 12.3 What does the offline optimizer use?

It includes a linear taper bound:

```math
E_{it}\le
\kappa_i
\left(
H_i-\sum_{\tau \lt t}E_{i\tau}
\right),
```

where:
- $`H_i`$: initial battery headroom.
- $`\kappa_i`$: precomputed factor from battery parameters.

As previous energy delivery increases, remaining headroom decreases, reducing allowable energy per period.

Because the parameters are fixed, the bound is affine in the decision variables.

### Important limitation

The report notes mismatch at partial pilot levels, so even offline replay can miss a small amount of energy.

---

## 12.4 An implementation issue the project fixed

The library’s rampdown estimator returns bounds keyed by **session ID**.

Some baseline code looks them up by **station ID**.

That mismatch can silently disable the estimated bound.

`StationRampdown` converts session-keyed bounds to station-keyed bounds for those baselines.

The custom MPC reads the session-keyed estimator directly.

This is an example of practical integration work beyond simply writing an equation.

---

# 13. S3: what changes when driver behavior is unknown?

Up to S2, connected EVs expose their true modeled requirements.

That is optimistic.

In S3, the controller receives predictions.

The simulator still keeps the real values for:
- Actual departure.
- Battery behavior.
- Evaluation of delivered energy.

### How is that separation implemented?

`EstimatedInterface` replaces the information exposed to the scheduler.

It does **not** change the underlying EV’s truth.

This avoids accidentally evaluating against the controller’s own guesses.

---

## 13.1 Historical prediction

For a target day:
1. Take the previous four calendar weeks.
2. Keep weekday sessions, excluding the configured holiday.
3. Group by arrival hour.
4. Estimate stay and energy from that group.

If there are fewer than ten samples, widen to neighboring arrival-hour bins.

### Correction to your slide

It says “previous 28 weekdays.”

The implementation uses:

> **The preceding 28 calendar days, filtered to weekdays.**

---

## 13.2 Median predictor

```math
\widehat{\text{stay}}=Q_{0.5}(\text{historical stays}),
```

```math
\widehat{\text{energy}}=Q_{0.5}(\text{historical energies}).
```

This gives a typical driver estimate.

### Why can it fail?

Two reasons:

**Early departure:** a vehicle leaves earlier than predicted, so energy planned for later is never delivered.

**Underestimated demand:** the vehicle needs more than the predicted energy.

Median energy can also undercount aggregate demand when the distribution is right-skewed.

---

## 13.3 Conditional updates

If an EV remains connected past its predicted departure:
- Consider historical stays longer than its current elapsed stay.
- Recompute the relevant quantile.

If it has received its predicted energy but remains unfinished:
- Consider larger historical requests.
- Increase the energy estimate.

The point predictor uses safeguards such as:
- At least another period of stay.
- Minimum 0.5 kWh increase.
- A 0.05 kWh “predicted request met” tolerance.

### Why the tolerance?

Round Robin uses small discrete current increments.

Without a tolerance, an EV might remain a few watt-hours below its predicted request forever, preventing the update from triggering.

---

## 13.4 Conservative predictor

Uses:

```math
\widehat{\text{stay}}=Q_{0.25},
```

```math
\widehat{\text{energy}}=Q_{0.75}.
```

Interpretation:

> Assume an earlier departure and a larger energy requirement.

This encourages earlier charging and improves delivery.

But it may raise the peak and the bill.

---

## 13.5 Naive predictor

Uses one global median for all EVs, without conditional updates.

It is a diagnostic comparison.

Its poor performance demonstrates that **updating predictions matters**, not just the initial estimate.

---

## 13.6 Why EDF is less affected than MPC

EDF already tries to charge quickly.

Prediction errors primarily affect service order.

A flattening MPC deliberately spreads charging toward the predicted departure.

Therefore, an overly optimistic departure estimate can be especially damaging.

This is the lesson:

> A schedule can be mathematically optimal for inaccurate inputs and still perform poorly in reality.

---

# 14. SAA: the most important uncertainty extension

## 14.1 What does SAA mean?

**Sample-Average Approximation.**

Instead of one departure and one energy estimate, use several plausible historical outcomes.

We do not optimize for one “typical driver.”

We optimize against the expected shortfall across plausible behaviors.

---

## 14.2 What is a scenario here?

For one EV, a scenario contains:
- Remaining time until departure.
- Remaining energy requirement.
- A probability/weight.

For example:

| Scenario | Leaves in | Needs | Weight |
|---|---:|---:|---:|
| 1 | 1 hour | 4 kWh | 0.25 |
| 2 | 2 hours | 6 kWh | 0.25 |
| 3 | 4 hours | 8 kWh | 0.25 |
| 4 | 5 hours | 10 kWh | 0.25 |

These are illustrative numbers, not project results.

### Why retain stay and energy as pairs?

Because their relationship may matter.

Sampling stay and energy independently could create unrealistic combinations.

The implementation preserves historical pairs.

---

## 14.3 How scenarios are constructed

In `SessionPredictor.scenarios()`:

1. Start with historical sessions near the EV’s arrival hour.
2. Keep only sessions consistent with observations:
   - Stay exceeds time already elapsed.
   - Energy exceeds energy already delivered.
3. Widen neighboring bins if too few remain.
4. Sort by stay.
5. Select up to 20 spread-out samples.
6. Assign equal weights.
7. Convert total stays and energy into remaining time and energy.

If no historical pair remains:
- Use one fallback scenario.
- Another 30 minutes.
- Another 0.5 kWh.

**The scenarios are refreshed as observations change.**

---

## 14.4 The scenario shortfall formula

Energy scheduled before scenario departure:

```math
C_i(d_{is})=
\sum_{t \lt d_{is}}E_{it}.
```

Unmet energy in that scenario:

```math
\left(k_{is}-C_i(d_{is})\right)^+.
```

Implement with a nonnegative auxiliary variable:

```math
z_{is}\ge k_{is}-C_i(d_{is}),
```

```math
z_{is}\ge0.
```

Expected shortfall:

```math
\sum_i\sum_s\pi_{is}z_{is}.
```

Add the penalty:

```math
w_{\mathrm{ES}}
\sum_i\sum_s\pi_{is}z_{is}.
```

### Read it aloud like this

> “For every possible departure and energy requirement, we calculate how much energy would still be missing. We average those shortfalls using scenario weights and penalize that expected unmet energy.”

---

## 14.5 A two-scenario example

An EV needs 5 kWh.

It has:
- 50% probability of leaving early.
- 50% probability of staying late.

Suppose all 5 kWh are scheduled late.

Then:
- Early-departure shortfall: 5 kWh.
- Late-departure shortfall: zero.

Expected shortfall:

```math
0.5(5)+0.5(0)=2.5\text{ kWh}.
```

At:

```math
w_{\mathrm{ES}}=30\text{ dollars/kWh},
```

the penalty is:

```math
30\times2.5=\$75.
```

The optimizer compares that penalty with the extra electricity, demand, and flatness costs of charging earlier.

It may decide that earlier charging is worthwhile.

---

## 14.6 What changes from the deterministic model?

For scenario-based EVs:
- Remove the single predicted-energy equality as the service target.
- Penalize expected shortfall instead.
- Allow scheduling up to the latest scenario departure.
- Cap energy at the largest plausible remaining request:

```math
\sum_tE_{it}\le\max_sk_{is}.
```

- Disable the single-deadline finish-by penalty.

### Why disable finish-by?

Otherwise we would combine:
- Risk represented by multiple scenario deadlines.
- A separate arbitrary early deadline.

The report’s design avoids counting essentially the same departure risk twice.

---

## 14.7 Is there a separate charging schedule for every scenario?

No.

The code uses a **shared planned current schedule** and evaluates its shortfall under different departure/energy scenarios.

It does not construct a full branching multistage scenario tree with separate actions for every future.

Feedback comes from replanning at the next MPC step.

This distinction is useful in a detailed viva.

---

## 14.8 Why does SAA remain convex?

Each shortfall is a maximum of:
- Zero.
- An affine function of charging decisions.

That is convex and piecewise linear.

The epigraph constraints are linear.

Therefore, SAA does not destroy convexity.

---

## 14.9 What is SAA-30?

It means:

```math
w_{\mathrm{ES}}=30\text{ dollars/kWh}.
```

**It does not mean 30 scenarios.**

The model uses **up to 20 scenarios**.

The tested penalty weights were:
- 1
- 10
- 30
- 100

Higher weight generally prioritizes delivery more strongly.

### Why choose 30?

It was an empirical compromise:
- Small weights left too much unmet energy.
- A weight of 100 improved delivery but raised peaks and bills.
- A weight of 30 recovered roughly 96–98% delivery at a more moderate cost.

**It was selected in-sample on the same month.** It is not a universally optimal reliability price.

---

## 14.10 Your slide 5 results

| Information | Combined-QP bill / delivery | BILL-LP bill / delivery |
|---|---|---|
| Median | \$2,782 / 76.4% | \$2,924 / 92.3% |
| Conservative | \$3,470 / 94.3% | \$3,261 / 97.9% |
| SAA-30 | \$3,178 / 96.3% | \$3,197 / 97.7% |

### How to explain the slide

> “Median predictions give low bills partly because substantial energy is not delivered. Conservative predictions improve delivery but raise the bill. SAA uses multiple historical outcomes and recovers high delivery at a lower bill than the conservative strategy.”

### Important nuance

For BILL-LP:
- Conservative: 97.9%.
- SAA: 97.7%.

So do not say SAA improves **both metrics for every controller**.

It improves the trade-off; for BILL-LP it saves money with a small delivery reduction.

### What does 96.3% mean?

It means:

```math
100\times
\frac{\text{total energy delivered}}
{\text{total energy requested}}.
```

It does **not** mean 96.3% of vehicles were fully charged.

It does **not** mean a 96.3% probabilistic guarantee.

---

# 15. S4: adding solar generation

## 15.1 What is behind-the-meter PV?

Solar generation and chargers share the electricity meter.

The utility bills the remaining grid import.

If:
- EV load is 80 kW.
- PV produces 30 kW.

Then:

```math
\text{Import}=80-30=50\text{ kW}.
```

If:
- EV load is 20 kW.
- PV produces 30 kW.

Then paid import is zero.

The remaining 10 kW earns no export revenue in this model.

---

## 15.2 Mathematical formulation

Introduce nonnegative import:

```math
S_t\ge P_t-\widehat{pv}_t,
```

```math
S_t\ge0.
```

Cost and demand objectives push it toward:

```math
S_t=\max(P_t-\widehat{pv}_t,0).
```

Then:
- Energy cost uses $`S_t`$.
- Demand charge uses rolling $`\bar S_t`$.
- Peak-exceedance hinge uses $`S_t`$.
- Valley filling uses the signed net load:

```math
\sum_t(P_t-\widehat{pv}_t)^2.
```

### Why distinguish import and signed net load?

- Import clips negative values at zero.
- Signed net load can be negative when PV exceeds charging demand.

The squared signed-net-load term encourages matching charging with PV.

---

## 15.3 Do network constraints get relaxed because of PV?

In this implementation, network constraints still act on the **gross EV charging currents**.

The phase penalty is unchanged.

The report assumes balanced PV for the phase interpretation, but does not explicitly insert inverter currents into every network constraint.

So say:

> “PV changes billing and the scheduling objective. The original gross-current network constraints remain in place.”

Do not claim the code performs a complete PV-integrated three-phase power-flow calculation.

---

## 15.4 Is the solar data real?

No.

The PV profile is synthetic.

The model selects a daily sky type:
- Clear: probability 70%.
- Partly cloudy: 20%.
- Overcast: 10%.

These are generation probabilities, not necessarily the exact fractions in a 19-day sample.

The realized experiment drew:
- 12 clear weekdays.
- 7 partly cloudy weekdays.
- No overcast weekday.

That is a limitation.

---

## 15.5 What is AR(1) cloud noise?

The cloud state follows:

```math
z_t=\rho z_{t-1}+\sqrt{1-\rho^2}\epsilon_t,
```

with $`\rho=0.9`$.

Interpretation:

> If it is cloudy now, it is likely to remain somewhat cloudy in the next interval.

This produces temporally correlated deviations rather than unrelated random jumps every five minutes.

---

## 15.6 What is a persistence nowcast?

The controller combines:
- A day-ahead estimate.
- The most recent observed solar condition.

For near-future intervals, recent observations matter strongly.

For farther intervals, the forecast gradually returns to the day-ahead estimate.

The decay time is two hours:

```math
24\text{ five-minute periods}.
```

Conceptually:

```math
\widehat{pv}_{t+h|t}
=
pv^{\mathrm{clear}}_{t+h}
\left[
\widehat k+
(c_{\mathrm{observed}}-\widehat k)e^{-h/24}
\right].
```

The controller uses previous observations—not future realized PV.

The offline benchmark does know the realized future PV.

---

# 16. Peak ratcheting: the key explanation for slide 6

Understand this example thoroughly.

Suppose the billed peak already reached is 55 kW.

The bill-oriented controller plans:
- EV load: 85 kW.
- Forecast PV: 30 kW.

Expected import:

```math
85-30=55\text{ kW}.
```

So it expects no increase in the demand charge.

But actual PV is only 20 kW.

Actual import becomes:

```math
85-20=65\text{ kW}.
```

If this raises the rolling billed peak, the new paid peak becomes 65 kW.

Next time, the controller may use that new 65 kW level.

Another PV shortfall can raise it again.

This is the **ratchet**.

### Why does it not come back down?

The monthly demand charge uses a maximum.

Once a higher billed peak occurs, later low loads cannot erase it.

### How to read the graph

The y-axis is **month-to-date billed import peak**.

It is not instantaneous power.

Therefore:
- A flat segment means no new record.
- An upward step means a new monthly record.
- It cannot decrease.

This is exactly the explanation to give when pointing at slide 6.

---

## 16.1 Why flattening helps

A flattening objective often keeps import below the paid peak rather than repeatedly filling right up to it.

This leaves headroom for forecast errors.

It is an observed protective effect, not a formal robustness guarantee.

---

## 16.2 Why SAA helps with PV even though it models driver uncertainty

SAA encourages some charging earlier because of possible early departures.

Then less load remains later.

That can leave more headroom or surplus PV, reducing the chance that a solar shortfall creates a new import peak.

**The SAA scenarios model driver behavior, not solar scenarios.**

The benefit against PV errors is an indirect effect of the changed schedule.

---

## 16.3 Does using more solar always mean a lower bill?

No.

Maximizing PV self-consumption is not the same objective as minimizing the electricity bill while delivering energy reliably.

Charging earlier may:
- Use less PV later.
- Improve delivery.
- Reduce demand-charge risk.

The stochastic solar experiments sometimes used a smaller fraction of available PV but still achieved a lower bill.

---

# 17. What does “+F” mean? Expected future arrivals

The base online controller sees only connected EVs.

But it can estimate future demand from history.

## 17.1 Ghost EVs

For each future arrival-hour bin, the code estimates:
- Expected arrivals per day: $`\lambda_b`$.
- Mean energy per arrival: $`\bar k_b`$.
- Mean stay.

It creates aggregate virtual EVs, split across phase groups.

Per group:

```math
\text{Expected energy}
=
\frac{\lambda_b\bar k_b}{3},
```

```math
\text{Current cap}
=
\frac{\lambda_b\times32}{3}.
```

These are **ghost EVs**.

They reserve expected capacity in the optimization.

They are not real vehicles, and their current commands are never applied.

---

## 17.2 How can fractional expected EVs make sense?

An expected arrival count of 1.5 is not a literal half vehicle.

It is an aggregate expectation over historical days.

The controller uses it as an approximate future load.

This is a certainty-equivalent forecast, not a full distribution over future arrivals.

---

## 17.3 The horizon-edge bug

Suppose a ghost EV represents:
- 10 kWh.
- A four-hour charging window.

Only the first hour of that window lies inside the current 12-hour horizon.

The original forecast kept the full 10 kWh requirement inside that truncated one-hour window.

That created an artificially concentrated planned load and a **phantom peak**.

Because the objective takes the maximum across the whole plan, that phantom future peak made a higher real morning peak appear less costly.

The morning peak happened.

The phantom evening peak often disappeared when the horizon moved forward.

But the real billed peak had already increased.

---

## 17.4 The three-part fix

The corrected implementation:

1. **Truncates ghost windows and scales their energy** to the fraction lying inside the horizon.
2. **Prices ghost shortfall at the SAA weight**, rather than the stronger deterministic penalty.
3. Counts only the **remaining share of the current arrival hour**.

In the code:

```text
mpc:combined_qp+forecast+fix
```

In the final report/presentation:

```text
Combined-QP+F
```

### Why this is a meaningful engineering result

It demonstrates that:
- The convex solver can solve the supplied problem correctly.
- A poor forecast representation can still create bad decisions.
- Fixing the formulation matters as much as solver choice.

---

## 17.5 Final reported PV configuration

Combined-QP with SAA-30 and corrected forecast:

- Bill: **\$1,904**.
- Billed import peak: **60.2 kW**.
- Delivery: **97.8%**.

Offline bill benchmark:
- Bill: \$1,675.
- Delivery: 99.6%.

### Careful wording for “best online controller”

Say:

> “Among the final report’s listed online configurations delivering at least 95% of the requested energy, this has the lowest bill.”

Do not say it is universally cheapest across every result ever produced.

Some lower bills correspond to substantial under-delivery; older write-ups also include uncorrected-forecast runs excluded from the final report.

---

# 18. Backup slide: robust optimization

This is a **separate daily experiment**, not the same thing as SAA-30.

## 18.1 Stochastic versus robust

### SAA

Uses scenario probabilities to minimize expected shortfall.

Question:

> “What happens on average across plausible outcomes?”

### Robust optimization

Protects against unfavorable values within an uncertainty set.

Question:

> “How bad can the modeled cost become within the allowed uncertainty?”

These are different approaches.

---

## 18.2 Solar protection

Forecast PV:

```math
\widehat{pv}_t.
```

Maximum deviation:

```math
\delta_t.
```

Protected PV:

```math
\widehat{pv}_t-\Gamma_{\mathrm{solar}}\delta_t.
```

Then:

```math
S_t\ge
P_t-
\left(
\widehat{pv}_t-\Gamma_{\mathrm{solar}}\delta_t
\right).
```

- $`\Gamma_{\mathrm{solar}}=0`$: trust the forecast.
- $`\Gamma_{\mathrm{solar}}=1`$: use the full specified shortfall protection.

In this experiment:

```math
\delta_t=0.4\widehat{pv}_t.
```

---

## 18.3 Price uncertainty budget

Prices can rise by up to 50%:

```math
p_t^{\mathrm{actual}}
=
p_t(1+\rho z_t),
\qquad \rho=0.5.
```

The uncertainty variables satisfy:

```math
0\le z_t\le1,
```

```math
\sum_tz_t\le\Gamma_{\mathrm{price}}.
```

A budget of 12 allows the equivalent of twelve full five-minute deviations.

**They need not be twelve consecutive periods.**

The budget avoids assuming maximum price increase everywhere simultaneously.

---

## 18.4 How is the worst-case cost made tractable?

The inner price-uncertainty problem is linear.

Using LP duality, it becomes:

```math
\sum_tp_tS_t\Delta
+
\Gamma_{\mathrm{price}}\lambda
+
\sum_t\mu_t,
```

subject to:

```math
\lambda+\mu_t
\ge
\rho p_tS_t\Delta,
```

```math
\lambda,\mu_t\ge0.
```

Here:
- $`\lambda,\mu_t`$ are mathematical auxiliary variables.
- They are not charger commands.

The new cost formulation is linear, while the full network-constrained problem still includes SOC constraints.

---

## 18.5 Why not use the same budget for solar?

Clipped grid import includes:

```math
\max(P_t-pv_t,0).
```

A time-coupled worst-case solar problem involves maximizing a convex piecewise-linear expression over an uncertainty set.

It does not admit the same straightforward LP-duality reformulation used for uncertain prices.

Therefore, the project uses:
- Per-period solar protection.
- Budgeted price uncertainty.

Avoid the overly broad claim that “no LP representation could ever exist.” The defensible point is that the simple price-budget dualization does not directly transfer.

---

## 18.6 Robust experiment results

Reported net energy costs:

| Metric | Nominal | Selected robust configuration |
|---|---:|---:|
| Mean daily cost | \$54.95 | \$50.33 |
| Worst sampled daily cost | \$65.61 | \$57.37 |

That is approximately:
- 8% lower mean.
- 12.5% lower worst sampled cost.

### Qualifications

- Five evaluated days.
- Synthetic PV.
- 200 sampled PV traces per run.
- Realized evaluation prices remained nominal.
- “Worst” means worst among sampled PV outcomes, not proof of the worst possible realized utility bill.

---

# 19. The codebase: what every important file does

Here is the actual execution structure.

```text
Historical time-series files
        ↓
data.py
Clean session table
        ↓
events.py
Network + EVs + batteries + arrival events
        ↓
experiment.py
Choose scheduler and information setting
        ↓
ACN-Sim
        ↓ every control step
schedulers.py → ConvexMPC.schedule()
        ↓
estimates.py / forecast.py / solar.py
        ↓
model.py → build() → solve()
        ↓
CVXPY → Clarabel
        ↓
First-period current commands
        ↓
ACN-Sim updates batteries and time
        ↓
metrics.py
        ↓
CSV results / NPZ traces
        ↓
notebooks/figures.ipynb
```

## 19.1 `src/evsched/__init__.py`

Shared constants:

```python
PERIOD = 5
VOLTAGE = 208
MAX_RATE = 32
TIMEZONE = "America/Los_Angeles"
TARIFF = "sce_tou_ev_4_march_2019"
```

Also defines data and results paths.

---

## 19.2 `data.py`

Parses and cleans historical sessions.

Important functions:
- `parse_session_file()`
- `build_sessions()`
- `load_sessions()`

---

## 19.3 `events.py`

Creates:
- The network.
- Session-to-charger assignments.
- Batteries.
- Plug-in events.

Important functions:
- `make_network()`
- `assign_evses()`
- `make_battery()`
- `build_events()`

---

## 19.4 `model.py`: the mathematical core

Important data structures:

### `Weights`

Holds objective coefficients:

```python
cost
demand
valley
phase
unmet
early
finish
hinge
expected
```

### `Infrastructure`

Holds:
- Constraint matrix.
- Limits.
- Phase angles.
- Voltages.
- Station and constraint identifiers.

### `EVBlock`

Holds:
- Station indices.
- Current upper-bound matrix.
- Remaining energy requirements.
- Optional early-finish deadlines.
- Optional scenarios.

### `DemandSpec`

Holds:
- Rolling-demand window.
- Previous measured powers.
- Current peak floor.

### `RobustSpec`

Holds:
- PV forecast.
- PV deviation.
- Solar protection.
- Price uncertainty parameters.

### `TaperSpec`

Holds battery parameters for the offline taper constraint.

### `build()`

Constructs:
- Decision expressions.
- Constraints.
- Named objective terms.

### `solve()`

Builds the CVXPY problem and solves it.

The central structure is:

```python
prob = cp.Problem(cp.Minimize(b.objective), b.cons)
elapsed = run_solver(prob)
```

### `run_solver()`

Primary solver:

```python
prob.solve(
    solver=cp.CLARABEL,
    max_threads=int(os.environ.get("EVSCHED_SOLVER_THREADS", 0))
)
```

On a solver exception:

```python
prob.solve(solver=cp.SCS, eps=1e-6)
```

The hard-energy option also has a separate fallback to the soft formulation.

Do not confuse:
- Solver fallback.
- Hard-to-soft constraint fallback.

---

## 19.5 An important sparse implementation detail

Mathematically we describe a full matrix $`R`$.

But many entries are forced to zero because vehicles are absent.

The code creates decision variables only for available charging intervals:

```python
rows, cols = np.nonzero(evs.ub > 0)
x = cp.Variable(rows.size, nonneg=True)
```

It then lifts `x` into a matrix-shaped expression `R` using a sparse matrix.

### Why?

It avoids creating thousands of variables that are known to be zero.

Same mathematical scheduling model, smaller numerical representation.

---

## 19.6 Network-cone pruning

Some network constraints cannot possibly bind for a particular interval.

The code checks a conservative upper bound:

```math
\sum_i|A_{mi}|\bar r_{it}.
```

If this is already below the limit, the corresponding cone can be omitted.

This reduces solve size without relaxing a potentially active limit.

---

## 19.7 Cumulative-energy recursion

For scenario shortfall and tapering, the code needs cumulative energy.

Instead of a dense cumulative-sum representation, it uses:

```math
C_{t+1}=C_t+E_t.
```

Each equality connects neighboring intervals.

This preserves sparse structure better.

### Why can SAA still be slower?

Additional cumulative variables and scenario constraints couple decisions across time.

During linear-system factorization, new nonzero entries can appear.

That is **fill-in**.

The relevant linear system is associated with the **Karush–Kuhn–Tucker optimality conditions**, or KKT system.

For a viva:

> “Scenario constraints increase time coupling, which increases factorization work even when the number of added variables is moderate.”

---

## 19.8 `schedulers.py`

### `ConvexMPC.schedule()`

It:
1. Filters active unfinished EVs.
2. Gets infrastructure.
3. Builds charging windows and upper bounds.
4. Reads remaining requirements.
5. Adds scenarios if enabled.
6. Adds forecast ghosts if enabled.
7. Caps the horizon.
8. Reads solar forecasts.
9. Builds the demand-floor state.
10. Calls the shared solver.
11. Returns only the first-period real-EV currents.

The output has the form:

```python
{
    station_id: [current_for_next_period]
}
```

### `ReplayScheduler`

Replays an offline-computed schedule in the simulator.

### `StationRampdown`

Fixes the identifier mismatch for baseline rate estimation.

---

## 19.9 `estimates.py`

Contains:
- `SessionPredictor`
- `EstimatedInterface`

Handles:
- Arrival-hour bins.
- Quantiles.
- Conditional updates.
- Historical scenarios.
- Separation of controller information from simulator truth.

---

## 19.10 `forecast.py`

Contains:
- Shared historical-window selection.
- `ArrivalForecaster`.
- Ghost-EV generation.
- Corrected current-hour and horizon-edge handling.

---

## 19.11 `solar.py`

Contains:
- Clear-sky curve.
- Synthetic cloud/weather generation.
- Date-based random seeds.
- Actual PV.
- Day-ahead forecast.
- Persistence nowcast.

Using the same date-based seed means each controller sees the same realized weather.

---

## 19.12 `offline.py`

Builds:
- Full-day perfect-information problems.
- Joint monthly problems.

The joint monthly optimization introduces **one shared peak variable across all days**.

It does not simply optimize each day independently and call that the monthly optimum.

The resulting schedules are replayed with `ReplayScheduler`.

---

## 19.13 `experiment.py`

Connects everything.

Important names:
- `V2Config`
- `make_scheduler()`
- `simulate()`
- `run()`
- `run_month()`
- `run_offline_month()`

Configuration controls:
- Battery model.
- Billing window.
- Horizon.
- Information level.
- PV size.
- Rampdown.
- Forecasting.

### Why deep-copy the simulation objects?

So one controller’s battery-state changes do not contaminate another controller’s experiment.

---

## 19.14 `metrics.py`

Computes:
- Requested and delivered energy.
- Delivery percentage.
- Energy cost.
- Five-minute peak.
- Fifteen-minute peak.
- Phase-unbalance statistics.
- Maximum constraint utilization.
- Runtime statistics.
- PV import and usage metrics.

Maximum utilization:

```math
\max_{m,t}\frac{|I_{mt}|}{c_m}.
```

- Below 1: within limits.
- Equal to 1: at a limit.
- Above 1: violation.

PV self-consumption:

```math
\frac{\text{PV energy used by EVs}}{\text{PV energy generated}}.
```

PV self-sufficiency:

```math
\frac{\text{PV energy used by EVs}}{\text{total EV energy}}.
```

These are different metrics.

---

## 19.15 `scripts/run_experiments.py`

Runs suites and writes:
- Per-day CSV results.
- Solver statistics.
- NPZ traces.

Typical sequence, **after installing dependencies and supplying the external dataset**:

```bash
python scripts/build_sessions.py --months 2018-08 2018-09

python scripts/run_experiments.py target
python scripts/run_experiments.py month
python scripts/run_experiments.py offline_month --presets cost_lp combined_qp

python scripts/run_experiments.py month_info \
  --info predicted conservative naive

python scripts/run_experiments.py month_info \
  --info stochastic-1 stochastic-10 stochastic-30 stochastic-100

python scripts/run_experiments.py month_solar \
  --info-solar conservative stochastic-30 --presets

python scripts/run_experiments.py month_solar \
  --algos "mpc:combined_qp+forecast+fix" \
  --info-solar stochastic-30 --presets
```

### Naming trap in the presets

In S1:
- `cost_lp` is energy-oriented.
- `cost_demand_lp` adds daily approximated demand cost.

In S2:
- `PRESETS_V2` adds full demand, hinge, and finish weights.
- `cost_lp` becomes the monthly **BILL-LP**.
- `cost_demand_lp` becomes effectively the same monthly objective.

Therefore, the same code preset name must be interpreted with its configuration.

---

## 19.16 Notebook and diagnostic script

`notebooks/figures.ipynb`:
- Reads saved results.
- Aggregates tables.
- Generates plots.

It is not the charging optimizer.

`scripts/diag_forecast_0924.py`:
- Investigates the forecast bug on September 24.
- Changes one suspected mechanism at a time.
- Logs planned versus realized peaks.

It contains a machine-specific path, so it is not portable without adjustment.

### Reproducibility caveat

The uploaded code expects external data and the ACN package environment.

GitHub code alone is not the full raw-data bundle.

The main numerical results cannot be reproduced here merely by opening the repository.

---

# 20. How did we test whether the code behaves correctly?

The tests are not just “the program runs.”

They check meaningful properties.

| Test category | What is checked |
|---|---|
| Cost objective | Charging moves to cheaper periods |
| Valley filling | A toy example produces a flat load |
| Network limits | Capacity is respected |
| Soft energy | Correct shortfall when demand exceeds capacity |
| Hard energy | Falls back when infeasible |
| Monthly billing | Rolling average and peak carry are correct |
| Finish-by | Charging moves earlier |
| Taper | Linear bound approximately tracks the battery model |
| Information | Scheduler sees predictions while simulator retains truth |
| No leakage | Future sessions are excluded from prediction history |
| SAA | High shortfall penalties protect early leavers |
| PV | Charging responds to solar; billing uses net import |
| Ghost fix | Current-hour share and truncated energy are correct |

My run:

> **32 passed, 1 skipped.**

The skipped test needs a specific external dataset file.

### What passing tests does not establish

It does not prove:
- The reported monthly bills were independently reproduced.
- The assumptions perfectly match reality.
- Every possible hardware deployment is safe.
- The chosen SAA weight generalizes.

It gives evidence that tested implementation behaviors match their intended definitions.

---

# 21. Runtime: how to explain the solver table

The control period is:

```math
300\text{ seconds}.
```

Selected reported results:

| Configuration | Median | P99 | Maximum |
|---|---:|---:|---:|
| S2 BILL-LP | 0.09 s | 2.29 s | 3.8 s |
| S3 SAA Combined | 0.15 s | 8.27 s | 10.3 s |
| S4 SAA Combined + corrected forecast | 1.31 s | 12.79 s | 21.4 s |

### Median

Half the calls finish faster than this.

### P99

Approximately 99% of calls finish within this time.

### Maximum

The slowest observed call.

### Main conclusion

```math
21.4\text{ s} \lt 300\text{ s}.
```

The tested online controllers finish within the control interval.

### Do not say every optimization finishes within five minutes

The joint offline monthly problems take:
- 595 seconds.
- 1,031 seconds.

Those are benchmarks, not online control calls.

### Why can a month-long sweep take hours?

Because it runs thousands of individually short solves across many configurations.

Per-call real-time feasibility and total experiment runtime are different.

---

# 22. What is genuinely new here, and what is prior work?

The foundation comes from:
- Adaptive Charging Networks.
- Convex MPC for EV charging.
- Valley-filling optimization.
- Budgeted robust optimization.

Your contribution is primarily the **formulation, integration, and empirical evaluation** of practical additions:
- Monthly demand accounting.
- Predicted driver information.
- Scenario-based service penalties.
- PV-aware billing.
- Corrected arrival forecasting.
- Comparisons and implementation fixes.

A good answer:

> “We build on the ACN convex scheduling framework rather than introducing a new optimization algorithm. Our contribution is integrating practical billing and uncertainty features and evaluating their impact on cost, charging delivery, and network feasibility.”

---

# 23. What to say on each of your actual slides

These are speaking guides. Understand them first; do not read them mechanically.

## Slide 1 — Title

> “Our project studies workplace EV charging. Instead of letting every vehicle charge immediately, we optimize the charging current assigned to each EV. We use convex optimization inside a model-predictive controller and evaluate it with real Caltech charging sessions.”

---

## Slide 2 — Problem

> “Many vehicles arrive in the morning, so immediate charging creates a large demand peak. The bill includes both time-of-use energy charges and a monthly charge based on the highest 15-minute demand. Most vehicles remain parked longer than their required charging time, so we can redistribute charging. The challenge is to reduce cost while respecting the network and supplying energy before departure.”

When pointing at $`P^\star`$:

> “This is a historical planning target, not the transformer limit or a guaranteed cap.”

---

## Slide 3 — Formulation

> “Our decision variable is the charging current for each EV in each five-minute interval. We enforce availability and rate bounds, allow penalized unmet energy, and represent three-phase current limits as second-order cones. The objective combines electricity cost with optional load-flattening and phase-balancing penalties. CVXPY builds the problem and Clarabel solves it. We apply only the first interval and replan.”

Then:

> “The LP and QP labels describe the objective; the full problem includes SOC constraints.”

---

## Slide 4 — Monthly results

> “With monthly billing, the bill-oriented MPC reduces the bill to \$3,031 and the billed peak to 70.3 kW. Compared with the feasible heuristics, this is about a 26% bill reduction and roughly half the billed peak. It delivers 98.9% of the required energy. The combined objective costs more because it also values smoothness and phase balance.”

If asked about OPT:

> “The offline benchmark delivers more energy and optimizes the full penalized model, so the lower online bill is not proof of outperforming the offline optimum.”

---

## Slide 5 — Driver uncertainty

> “A point prediction can make the optimizer postpone charging until after a vehicle actually leaves. Median predictions therefore produce low bills but poor energy delivery. Conservative predictions recover delivery by assuming shorter stays and larger requests. SAA instead considers up to 20 historical stay-and-energy scenarios and penalizes expected shortfall. At weight 30, delivery recovers to roughly 96–98%.”

Point to the formula:

> “This variable is the unmet energy in one scenario. We average it across scenarios and include it in the objective.”

---

## Slide 6 — Solar

> “With solar, the bill is based on net grid import. A bill-oriented controller may plan import exactly at the monthly peak already paid for. If solar output is lower than forecast, actual import exceeds that level and creates a new monthly peak. The staircase shows this ratchet. Flattening and scenario-based charging reduce the effect. The corrected forecast configuration achieves \$1,904, a 60.2 kW import peak, and 97.8% delivery.”

---

## Slide 7 — Conclusion

> “The main result is that convex scheduling substantially reduces peak-related cost while respecting the network. But realistic uncertainty matters: inaccurate point predictions can reduce charging service, and solar errors can raise the billed peak. Scenario-based shortfall penalties and careful forecast modeling improve the trade-off. These are simulation results from one site and one month, so broader validation is still needed.”

---

## Backup slide 8 — Solver

> “The maximum reported online call is 21.4 seconds, well below the 300-second control interval. Scenario-based models are slower because they add time-coupled cumulative-energy constraints. The offline monthly solves are longer and are not real-time controllers.”

---

## Backup slide 9 — Full results

> “Every bill must be read alongside energy delivery. The cheapest numerical bill is not necessarily the best schedule if vehicles receive too little energy. Also, perfect driver information does not mean perfect solar forecasts or knowledge of future arrivals.”

---

## Backup slide 10 — Robust extension

> “This is a separate daily robustness experiment. We protect against PV shortfall per interval and use a Bertsimas–Sim budget for price deviations. The price uncertainty has a linear dual reformulation. The chosen configuration reduces the worst sampled net energy cost by about 12.5%, but this is a small synthetic-PV experiment, not a monthly deployment guarantee.”

---

# 24. Corrections and traps to avoid tomorrow

| Slide/report wording | Safer explanation |
|---|---|
| “150 kW transformer” | Physical rating: 150 kVA; approximately 150 kW at the modeled balanced unity-power-factor interpretation |
| “EDF is the best heuristic” | EDF is a representative heuristic; Round Robin has the lowest S2 bill |
| “Still an LP” | The added term is linear/piecewise linear; network constraints remain SOC |
| “Previous 28 weekdays” | Previous four calendar weeks, filtered to weekdays |
| “Perfect information” | Known connected-EV requirements, not all future arrivals |
| “MPC beats OPT” | Reported bill and delivered energy differ; not an identical-objective optimality comparison |
| “Within 3% of optimum” | Close reported cost per kWh; no rigorous 3% guarantee |
| “97.8% of vehicles fully charged” | 97.8% of requested energy delivered in aggregate |
| “Best online controller” | Qualify by the compared configurations and delivery level |
| “Every solve fits five minutes” | Every reported selected **online** solve; not the offline joint-month solve |
| “SAA predicts drivers better” | It changes how uncertainty is used; it is not a new forecasting model |
| “50 kW real solar array data” | Synthetic 50 kW PV scenario |
| “Actual September utility bill” | Simulated bill under the selected tariff |

Two deeper limitations worth remembering:
- Days are simulated separately; overnight sessions are not jointly combined with the next day’s new arrivals.
- There is no modeled building load or export revenue.

Also, some supplementary documents retain older descriptions and results. For tomorrow, use the **final PDF’s result selection**, and use the **code for implementation details**.

---

# 25. Likely viva questions and concise answers

## “What is your optimization variable?”

> “The charging current allocated to each EV in each five-minute interval.”

## “What is your objective?”

> “Minimize electricity cost and penalized charging shortfall, with optional load-flattening and phase-balancing terms.”

## “Why use current instead of power?”

> “The EVSE control command is current, and the network constraints are expressed in currents. With fixed voltage, power is a linear conversion.”

## “Why convex optimization?”

> “The continuous-current model has affine energy relations, convex objective terms, and SOC network limits. That allows efficient global optimization of each planning problem.”

## “Why not use machine learning?”

> “The main task is constrained decision-making, not prediction alone. Historical prediction supplies uncertain inputs; optimization chooses feasible actions.”

## “Did you train a neural network?”

> “No. The predictors use historical bins, quantiles, conditional updates, and historical scenario pairs.”

## “Why not use a genetic algorithm?”

> “This formulation is convex, so we can exploit that structure directly rather than use a heuristic global search without the same optimality guarantees.”

## “Why soft energy constraints?”

> “Some requests are infeasible within the available time and network capacity. Penalized slack keeps the controller operational and quantifies the shortfall.”

## “Does \$100/kWh guarantee complete charging?”

> “No. It strongly prioritizes delivery but remains a soft penalty.”

## “How is the network handled?”

> “We constrain the magnitude of each relevant current phasor sum using a second-order cone.”

## “What is the difference between flattening and balancing?”

> “Flattening redistributes load across time. Balancing redistributes current across phases.”

## “What is SAA-30?”

> “Sample-average expected-unmet-energy optimization with a penalty weight of \$30/kWh, using up to 20 historical scenarios per EV.”

## “Is SAA a chance constraint?”

> “No. It minimizes expected shortfall rather than enforcing a probability-of-service requirement.”

## “What does +F mean?”

> “A corrected historical arrival forecast represented by virtual ghost EVs.”

## “What did you implement yourselves?”

> “The shared optimization model, MPC integration, data reconstruction, information interface, historical predictors, scenario generation, PV forecasting, experiment pipeline, metrics, and implementation fixes. CVXPY and Clarabel provide modeling and solving infrastructure.”

## “What is your strongest result?”

> “In the monthly no-PV experiment, BILL-LP reduces the bill by about 26% and billed peak by about 51% relative to Round Robin, while respecting network limits and delivering 98.9% of requested energy.”

## “What is the biggest limitation?”

> “One site and one month, with synthetic phase assignment and PV weather, continuous charging controls, and in-sample selection of the scenario penalty.”

## “What would you do next?”

> “Validate across months and sites, tune weights out of sample, use measured PV and richer driver information, and handle discrete charger pilot levels.”

---

# 26. Your last-minute revision sheet

If you remember only this, you can explain the main presentation coherently.

### Problem
Morning arrivals create high charging demand and expensive monthly peaks.

### Flexibility
Vehicles often remain parked longer than their minimum charging time.

### Decision
Current per EV per five-minute interval:

```math
R_{it}.
```

### Constraints
- Charging bounds.
- Plug-in windows.
- Soft energy requirement.
- Three-phase SOC network limits.

### Objective
- Energy cost.
- Monthly demand cost.
- Peak-exceedance penalty.
- Optional flatness.
- Optional phase balance.
- Unmet-energy penalty.

### Method
MPC: plan ahead, apply one interval, observe, repeat.

### Software
**Python → CVXPY → Clarabel**, with **ACN-Sim** for simulation.

### S2 result
**\$4,088 → \$3,031**, approximately 26% lower bill.

**142.5 → 70.3 kW**, approximately 51% lower billed peak.

### S3 problem
Median predictions encourage charging too late or planning too little energy.

### S3 solution
Historical scenarios and expected-unmet-energy penalties.

**76.4% → 96.3%** delivery for Combined-QP.

### S4 problem
Solar forecast misses create irreversible increases in the monthly billed peak.

### S4 response
Net-import scheduling, flattening, SAA, and corrected arrival forecasts.

### Final selected PV result
**\$1,904 · 60.2 kW · 97.8% delivery.**

### Computation
Selected maximum online solve: **21.4 seconds**, versus a **300-second** control interval.

### Honesty
Simulation—not deployment. Historical energy is a proxy for demand. Low bill alone is not success.

---

## Your opening sentence tomorrow

> **“Our project asks how to charge workplace EVs at lower cost without overloading the electrical network. We use convex optimization to allocate charging currents every five minutes, then study what changes when driver requirements and solar generation are uncertain.”**

## Your closing sentence tomorrow

> **“The main lesson is that a useful charging schedule must balance cost, electrical feasibility, and charging service. Convex optimization gives us a tractable way to make that trade-off, but the quality of the uncertainty model determines how well the schedule performs in practice.”**

# Speaker notes

12 main slides + 3 backup slides. Suggested main presentation: 10–12 minutes.

## Slide 1

Opening: Our project looks at a workplace garage where many EVs arrive together. We choose charging currents rather than letting every car charge immediately. The goal is to lower the bill without overloading the network or leaving too much charging demand unmet. This is a simulation study using real session data, not a live deployment. Suggested duration for slides 1–12: about 10–12 minutes. Speaker split can be 1–5, 6–9, and 10–12.

## Slide 2

Explain the distinction between energy and demand: energy is total kWh, while demand is the highest billed average kW. The uncontrolled case has about $1,874 of energy charges and $2,972 of demand charges. Those rounded components sum to $4,846 while the table reports a rounded total of $4,845. Do not interpret the uncontrolled schedule as feasible: it exceeds a network limit by up to 2.06 times. The useful flexibility is the difference between parking time and required charging time.

## Slide 3

We are not claiming to invent convex EV charging or MPC. Our contribution is integrating and evaluating practical extensions within an existing framework. The three main extensions are monthly billing, historical scenarios for driver uncertainty, and behind-the-meter PV. Bertsimas–Sim robust optimization is also used in an exploratory daily appendix experiment, separate from the main S2–S4 results.

## Slide 4

The transformer rating is 150 kVA, not 150 kW. The controller sees a real EV only when it plugs in. Perfect information in the online cases means known requirements of connected EVs, not knowledge of every future arrival. OPT is an offline benchmark with future information. The model uses continuous current control, approximately 0–32 A. The simpler daily S1 setup is in the report appendix; this presentation focuses on S2–S4.

## Slide 5

R is the decision variable in amperes. The availability bound becomes zero before arrival and after departure. The energy equation includes a nonnegative slack u, which is penalized rather than forced to zero. The report found hard energy requirements could become infeasible because recorded requests do not always fit rounded five-minute windows. B(m) is a two-row matrix with A(m,i)cos(phi_i) and A(m,i)sin(phi_i), so its Euclidean norm is the magnitude of the current phasor sum. This is a second-order cone constraint. Voltage is treated as a model parameter. This is not a full AC power-flow model.

## Slide 6

The default unmet-energy penalty is $100/kWh. BILL-LP refers to the objective, not the class of the full problem. With the network cones it is an SOCP. COMBINED-QP has a convex quadratic objective and SOC constraints; it can also be expressed as an SOCP using an epigraph reformulation. With nonnegative weights the formulation is convex. A global optimum of the current planning problem is not a guarantee of a globally optimal realized monthly bill, because the online controller lacks future information.

## Slide 7

At each control step we observe the state, update predictions, rebuild the convex problem and solve it. We implement just the first action, then repeat after five minutes. CVXPY translates the mathematical model into the solver representation; Clarabel computes the solution. SCS is a configured fallback, but the report says none of the monthly MPC runs needed it. ACN-Sim is the evaluation environment, not the optimization solver. The current repository snapshot contains figures and this deck, not the experiment code; implementation details here are reported in the project PDF.

## Slide 8

Round Robin is the cheapest reported heuristic: $4,088, compared with $4,096 for LLF and $4,116 for EDF. The relative bill reduction is 25.9% and the billed-peak reduction is 50.7%. Do not say EDF is the best heuristic. The energy charge for MPC-BILL-LP is actually slightly higher than for RR; the reduction in demand charge drives the total savings. The offline schedule delivers more energy and optimizes a penalized objective, so MPC having a lower realized bill does not prove it beats a global optimum. The report’s displayed 19.9 versus 20.6 cents/kWh is about a 3.4% difference, not a rigorous 3% optimality bound.

## Slide 9

Median uses a typical stay and energy request estimated from the previous four weeks. Conservative uses the 25th percentile stay and 75th percentile energy. SAA replaces a single target with weighted historical outcomes, conditioned on the vehicle still being present and not finished. The penalty is $30/kWh for SAA-30. The expected-shortfall term is piecewise linear and convex; the complete charging problem retains its SOC constraints. It is not a chance constraint and does not guarantee a service probability. The $2,782 median bill and 94.3% conservative delivery are transcribed from the original presentation’s S3 results. SAA-30 was tuned on the same month, so generalization needs further testing.

## Slide 10

PV is billed through nonnegative net import, with no export compensation. The bill-focused controller has little incentive to stay below a peak already paid for; solar forecast misses can ratchet that peak upward. The comparison changes the objective and information treatment, so do not attribute the entire difference to one term. Combined-QP with SAA-30 and corrected arrival forecasts costs $1,904 with a 60.2 kW peak and 97.8% delivery. It is the lowest-bill configuration among the listed S4 online results delivering at least 95%, not the absolute cheapest: a median-prediction controller costs $1,629 but delivers only 75.8%. OPT-BILL-LP with known future PV costs $1,675 and delivers 99.6%. +F denotes virtual future arrivals, with windows and energy corrected at the planning-horizon boundary.

## Slide 11

The timing statement applies to the reported tests, not every possible deployment. Deterministic calls are fast at the median, but scenario variables and future-arrival forecasts add cost. The maximum listed online call is 21.4 seconds, about 7.1% of a 300-second interval. Clarabel and CVXPY timing includes model construction and compilation. No monthly MPC run produced an infeasible schedule or needed the fallback solver. The offline 19-day solves took 595 and 1,031 seconds; do not include them in the claim that every solve is below five minutes.

## Slide 12

Close with the practical conclusion: the model produces schedules that trade electricity cost, grid feasibility and charging service. Cost alone is not an adequate evaluation metric. We have evidence of useful results in simulation, not a broad deployment guarantee. The mathematical framework is convex under the modeling assumptions, but hardware uses discrete pilots and uncertainty can affect realized performance. Future experiments should use other months and sites, measured PV, and PV-aware heuristic baselines. Pause for questions; slides 13–15 are backup material.

## Slide 13

P* is a historical planning target, not a hard physical limit. F_mtd is the month-to-date billed peak. The epigraph variable q represents the maximum over planned 15-minute demand and the floor. Subtracting a constant already-paid floor would not change the current minimizer. Carrying this state does not make MPC equivalent to a full-month optimizer. The report also includes a hinge penalty on exceedance above F. In SAA, C is cumulative delivered energy by the scenario departure and k is the corresponding energy requirement, conditioned on observed progress. Scenario weights sum to one for each EV. SAA is a convex expected-shortfall penalty, not a hard delivery guarantee.

## Slide 14

This backup explains why low cost cannot be read alone. Uncontrolled charging violates network constraints, and its 100.1% delivery is the reported simulation value, not a target. Median Combined-QP with solar has a lower bill than the recommended online configuration but leaves about a quarter of requested energy undelivered. The offline OPT benchmark sees future arrivals and, in S4, known PV. It is not directly deployable as an online controller. All values are reported rather than independently reproduced in this editing session.

## Slide 15

References are taken from the supplied report. Related appendix reference: Bertsimas and Sim (2004), The price of robustness, Operations Research 52(1), 35–53. This revised presentation focuses on the monthly S2–S4 experiments rather than the exploratory S1 robustness extension. The figures and experiment results were not regenerated during presentation editing.

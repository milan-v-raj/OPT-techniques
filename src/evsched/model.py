"""Shared convex scheduling model (LP / QP / SOCP) used by the online MPC, the offline benchmark
and the robust extension.

Decision variable R[i, t] >= 0 is the pilot current (A) of EV i in period t. Aggregate power is
P_t = sum_i V_i R[i, t] / 1000 (kW).

Constraints
    0 <= R <= ub                       (rate bounds; ub = 0 outside the EV's plugged-in window)
    E_i + u_i = demand_i, u_i >= 0     (soft energy fulfilment; ``hard`` forces u = 0)
    | sum_i A[m, i] R[i, t] e^{j phi_i} | <= limit_m   (exact phasor network limits, SOC; cones that
                                                         cannot bind even at ub are left out)

Objective (weighted sum, each term in comparable units)
    cost    : sum_t price_t * P_t * dt                     ($, LP)
    demand  : demand_charge * max(max_t P_t, prev_peak)    ($, LP)
    valley  : dt * sum_t P_t^2 / P_ref                     (QP, flattens load)
    phase   : dt * sum_t |I_neg,t|^2 / I_ref               (QP, negative-sequence current)
    unmet   : sum_i u_i                                    (kWh, large weight)
    early   : tiny tie-break preferring earlier charging

Billing-accurate options (v2; all default to the v1 behaviour)
    DemandSpec : billed peak on a rolling mean Pbar_t = mean(P_{t-w+1..t}) whose first w-1 entries are
                 known past powers, with a floor (month-to-date peak and/or peak target P*):
                     demand = demand_charge * max(max_t Pbar_t, floor),
                     hinge  = dt * sum_t pos(Pbar_t - floor)   (soft ceiling, spreads exceedance)
    finish-by  : EVBlock.finish_by[i] = period by which EV i should be done;
                     E_i(before finish_by) + v_i >= demand_i - u_i,  finish = sum_i v_i
    TaperSpec  : two-stage battery (ACN Linear2StageBattery) as a linear bound on per-period energy,
                     e_it <= kappa_i (cap_i (1 - soc0_i) - sum_{s<t} e_is),
                 kappa_i = 1 - exp(-max_dsoc_i / (1 - transition_soc)); exact for the constant-voltage stage.

Expected unmet energy (stochastic MPC, E8 extension): EVBlock.scenarios[i] = (d_s, k_s, pi_s), the
periods until departure, kWh still needed and probability of each (stay, kWh) scenario. For such an
EV the soft target is replaced by a sample-average expected shortfall (piecewise linear, convex):
    C_i(d) = sum_{t<d} e_it,   z_is >= k_s - C_i(d_s),  z_is >= 0,   expected = sum_is pi_s z_is,
    E_i <= demand_i (= max_s k_s)

Robust extension (``RobustSpec``): with PV forecast pv_hat and deviation bound pv_dev, net import
S_t >= P_t - (pv_hat_t - gamma_solar * pv_dev_t), S_t >= 0 is priced instead of P_t, and the price
itself is uncertain, p_t in [p_t, p_t (1 + rho)], with Bertsimas-Sim budget gamma_price:
    sum_t p_t S_t dt + gamma_price * lam + sum_t mu_t,   lam + mu_t >= rho p_t dt S_t,  lam, mu >= 0.
gamma_solar = gamma_price = 0 recovers the nominal (PV-aware) problem. With PV the demand and hinge
terms act on S and the valley term flattens the net load P_t - (pv_hat_t - gamma_solar * pv_dev_t).
"""

import os
import time
from dataclasses import dataclass, field, replace
from typing import Optional

import cvxpy as cp
import numpy as np
import scipy.sparse as sp

A_OP = np.exp(2j * np.pi / 3)
PHASE_IDS = ("Secondary A", "Secondary B", "Secondary C")


@dataclass
class Weights:
    cost: float = 0.0
    demand: float = 0.0
    valley: float = 0.0
    phase: float = 0.0
    unmet: float = 100.0
    early: float = 1e-3
    finish: float = 0.0
    hinge: float = 0.0
    expected: float = 0.0


PRESETS = {
    "cost_lp": Weights(cost=1.0),
    "cost_demand_lp": Weights(cost=1.0, demand=1.0 / 21),  # monthly demand charge spread over ~21 weekdays
    "valley_qp": Weights(valley=1.0),
    "phase_qp": Weights(valley=0.1, phase=1.0),
    "combined_qp": Weights(cost=1.0, valley=1.0, phase=1.0),
}

# v2: every preset pays the full demand charge on increases of the carried monthly peak, plus the
# finish-by and soft-ceiling penalties ($/kWh).
PRESETS_V2 = {k: replace(w, demand=1.0, finish=1.0, hinge=1.0) for k, w in PRESETS.items()}


@dataclass
class Infrastructure:
    """Network data in the form returned by ``Interface.infrastructure_info()``."""

    A: np.ndarray  # M x N constraint matrix
    limits: np.ndarray  # M
    phases: np.ndarray  # N, degrees
    voltages: np.ndarray  # N
    constraint_ids: list
    station_ids: list

    @classmethod
    def from_info(cls, info):
        return cls(info.constraint_matrix, info.constraint_limits, info.phases, info.voltages,
                   list(info.constraint_ids), list(info.station_ids))

    @classmethod
    def from_network(cls, network):
        return cls(network.constraint_matrix, network.magnitudes, network._phase_angles,
                   network._voltages, list(network.constraint_index), list(network.station_ids))

    def with_virtual(self) -> "Infrastructure":
        """Append one virtual column per phase group (stations sharing a phase angle), whose
        constraint coefficients are the group's mean column. Forecast ("ghost") EVs sit on these
        columns, so each network row sees their expected share of load."""
        A, phases, volts, ids = [self.A], [self.phases], [self.voltages], list(self.station_ids)
        for ang in np.unique(self.phases):
            g = self.phases == ang
            A.append(self.A[:, g].mean(axis=1, keepdims=True))
            phases.append([ang])
            volts.append([self.voltages[g].mean()])
            ids.append(f"ghost_{ang:g}")
        return Infrastructure(np.hstack(A), self.limits, np.concatenate(phases), np.concatenate(volts),
                              self.constraint_ids, ids)

    def virtual_idx(self) -> np.ndarray:
        return np.array([i for i, s in enumerate(self.station_ids) if str(s).startswith("ghost_")])


@dataclass
class EVBlock:
    """EVs to schedule: station index into the infrastructure, per-period upper bounds, demand."""

    station_idx: np.ndarray  # n
    ub: np.ndarray  # n x T, amps (0 outside the plugged-in window)
    demand: np.ndarray  # n, kWh still to deliver
    finish_by: Optional[np.ndarray] = None  # n, period by which the EV should be done (<= 0: none)
    scenarios: Optional[list] = None  # n, None or (periods to departure, kWh needed, prob.) per scenario


@dataclass
class DemandSpec:
    window: int = 1  # periods in the billed rolling mean (3 = 15 min at 5-min periods)
    p_hist: np.ndarray = field(default_factory=lambda: np.zeros(0))  # last window-1 actual powers (kW)
    floor_kw: float = 0.0  # peak already paid for / target: max(P*, month-to-date, today so far)


@dataclass
class TaperSpec:
    cap: np.ndarray  # n, battery capacity (kWh)
    soc0: np.ndarray  # n, state of charge at the start of the horizon
    max_power: np.ndarray  # n, kW
    transition_soc: float = 0.8


@dataclass
class RobustSpec:
    pv_hat: np.ndarray  # T, forecast PV (kW)
    pv_dev: np.ndarray  # T, max shortfall below forecast (kW)
    gamma_solar: float = 0.0  # per-period protection level in [0, 1]
    price_dev: float = 0.0  # rho: relative upward price deviation
    gamma_price: float = 0.0  # Bertsimas-Sim budget on number of periods with price deviation


@dataclass
class Result:
    rates: np.ndarray
    status: str
    solve_time: float
    terms: dict = field(default_factory=dict)
    fallback: bool = False
    solver_time: float = 0.0  # time inside the solver; solve_time - solver_time is cvxpy compilation


def _phasor_coeffs(infra: Infrastructure, idx: np.ndarray):
    """Real/imag coefficient matrices (M' x n) of the constraint currents and the active limits."""
    A = infra.A[:, idx]
    rows = np.flatnonzero(np.abs(A).sum(axis=1) > 0)
    phi = np.deg2rad(infra.phases[idx])
    return A[rows] * np.cos(phi), A[rows] * np.sin(phi), infra.limits[rows]


def _neg_seq_coeffs(infra: Infrastructure, idx: np.ndarray):
    """Complex coefficients c_i with I_neg,t = sum_i c_i R[i, t], or None if no phase rows."""
    if not all(p in infra.constraint_ids for p in PHASE_IDS):
        return None
    a, b, c = (infra.A[infra.constraint_ids.index(p), idx] for p in PHASE_IDS)
    return (a + A_OP ** 2 * b + A_OP * c) / 3 * np.exp(1j * np.deg2rad(infra.phases[idx]))


def _expected_unmet(cons: list, scenarios: list, rows: np.ndarray, cols: np.ndarray, e) -> cp.Expression:
    """Sample-average expected shortfall over each EV's (departure, kWh) scenarios. ``rows``/``cols``
    are the EV and period of the window entries with energies ``e``; C is their per-EV running
    total (a recursion, like the taper's), and the energy delivered before d_s is C at the last
    entry with period < d_s."""
    m = rows.size
    first = np.r_[True, rows[1:] != rows[:-1]]
    nxt = np.flatnonzero(~first)
    C = cp.Variable(m)
    cons.append(C[first] == e[first])
    if nxt.size:
        cons.append(C[nxt] == C[nxt - 1] + e[nxt])
    starts = np.flatnonzero(first)
    sel_r, sel_c, k_all, pi_all = [], [], [], []
    for s0, i in zip(starts, rows[starts]):
        d, k, pi = scenarios[i]
        ev_cols = cols[s0: s0 + np.sum(rows == i)]
        cnt = np.searchsorted(ev_cols, d, side="left")  # entries with period < d_s
        for c, kk, pp in zip(cnt, k, pi):
            if c > 0:
                sel_r.append(len(k_all))
                sel_c.append(s0 + c - 1)
            k_all.append(kk)
            pi_all.append(pp)
    sel = sp.csr_matrix((np.ones(len(sel_r)), (sel_r, sel_c)), shape=(len(k_all), m))
    z = cp.Variable(len(k_all), nonneg=True)
    cons.append(z >= np.array(k_all) - sel @ C)
    return np.array(pi_all) @ z


@dataclass
class Built:
    """A built (unsolved) problem: variables, constraints and named objective terms."""

    R: cp.Variable
    cons: list
    terms: dict
    objective: cp.Expression
    billed: Optional[cp.Expression] = None  # rolling billed power (T,), when a DemandSpec is given


def build(
    evs: EVBlock,
    infra: Infrastructure,
    prices: np.ndarray,
    weights: Weights,
    period: float,
    demand_charge: float = 0.0,
    prev_peak_kw: float = 0.0,
    robust: Optional[RobustSpec] = None,
    hard: bool = False,
    demand: Optional[DemandSpec] = None,
    taper: Optional[TaperSpec] = None,
    p_ref: float = 150.0,
    i_ref: float = 416.0,
    margin: float = 1e-3,
) -> Built:
    """Build the scheduling problem for ``evs`` (n x T)."""
    n, T = evs.ub.shape
    dt = period / 60
    idx = evs.station_idx
    volts = infra.voltages[idx]

    # only rates inside the plugged-in windows are variables; R is a sparse lift of them (row-major,
    # so each EV's periods are contiguous in x)
    rows, cols = np.nonzero(evs.ub > 0)
    x = cp.Variable(rows.size, nonneg=True)
    lift = sp.csr_matrix((np.ones(rows.size), (rows * T + cols, np.arange(rows.size))), shape=(n * T, rows.size))
    R = cp.reshape(lift @ x, (n, T), order="C")
    P = (volts / 1000) @ R  # kW, shape (T,)
    energy = cp.multiply(volts * dt / 1000, cp.sum(R, axis=1))  # kWh per EV
    cons = [x <= evs.ub[rows, cols]]

    u = cp.Variable(n, nonneg=True)
    stoch = np.array([sc is not None for sc in evs.scenarios]) if evs.scenarios is not None else np.zeros(n, bool)
    det = np.flatnonzero(~stoch)
    if det.size:
        cons.append(energy[det] + u[det] == evs.demand[det])
    expected = None
    if stoch.any():
        sr = np.flatnonzero(stoch)
        cons += [energy[sr] <= evs.demand[sr], u[sr] == 0]
        jj = np.flatnonzero(stoch[rows])  # window entries of the scenario EVs (contiguous per EV)
        expected = _expected_unmet(cons, evs.scenarios, rows[jj], cols[jj],
                                   cp.multiply(volts[rows[jj]] * dt / 1000, x[jj]))
    if hard:
        cons.append(u[det] == 0)

    if taper is not None:
        e = cp.multiply(volts[rows] * dt / 1000, x)  # kWh per window period
        kappa = 1 - np.exp(-taper.max_power * dt / taper.cap / (1 - taper.transition_soc))
        headroom = taper.cap * (1 - taper.soc0)
        # S = energy delivered to the same EV before this period, as a per-EV recursion over its
        # window (cp.cumsum over the full horizon gives a dense triangle that stalls the solver)
        first = np.r_[True, rows[1:] != rows[:-1]]
        nxt = np.flatnonzero(~first)
        S = cp.Variable(rows.size)
        cons += [S[first] == 0, e <= cp.multiply(kappa[rows], headroom[rows] - S)]
        if nxt.size:
            cons.append(S[nxt] == S[nxt - 1] + e[nxt - 1])

    finish = None
    if evs.finish_by is not None and weights.finish:
        rows = np.flatnonzero(evs.finish_by > 0)
        if rows.size:
            mask = (np.arange(T)[None, :] < evs.finish_by[rows, None]).astype(float)
            early_energy = cp.multiply(volts[rows] * dt / 1000, cp.sum(cp.multiply(mask, R[rows]), axis=1))
            finish = cp.Variable(rows.size, nonneg=True)
            cons.append(early_energy + finish >= evs.demand[rows] - u[rows])

    re, im, limits = _phasor_coeffs(infra, idx)
    if len(limits):
        k = len(limits)
        # drop the cones that cannot bind: |sum_i A_mi R_it e^{j phi_i}| <= sum_i |A_mi| ub_it <= limit_m
        keep = np.flatnonzero(((np.hypot(re, im) @ evs.ub) > (limits - margin)[:, None]).ravel())
        if keep.size:
            X = cp.vstack([cp.reshape(re @ R, (k * T,), order="C")[keep],
                           cp.reshape(im @ R, (k * T,), order="C")[keep]])
            cons.append(cp.SOC(np.repeat(limits - margin, T)[keep], X, axis=0))

    terms, billed = {}, None
    net = P  # load the valley term flattens: net of forecast PV when there is PV
    if robust is None:
        grid = P
        if weights.cost:
            terms["cost"] = prices[:T] @ P * dt
    else:
        S = cp.Variable(T, nonneg=True)
        net = P - (robust.pv_hat[:T] - robust.gamma_solar * robust.pv_dev[:T])
        cons.append(S >= net)
        grid = S
        nominal = prices[:T] @ S * dt
        if robust.gamma_price > 0 and robust.price_dev > 0:
            lam = cp.Variable(nonneg=True)
            mu = cp.Variable(T, nonneg=True)
            cons.append(lam + mu >= robust.price_dev * cp.multiply(prices[:T] * dt, S))
            terms["cost"] = nominal + robust.gamma_price * lam + cp.sum(mu)
        else:
            terms["cost"] = nominal
    if demand is not None:
        w = demand.window
        hist = np.asarray(demand.p_hist, dtype=float)[-(w - 1):] if w > 1 else np.zeros(0)
        hist = np.concatenate([np.zeros(w - 1 - len(hist)), hist])
        full = cp.hstack([hist, grid]) if w > 1 else grid
        billed = sum(full[j: j + T] for j in range(w)) / w
        if weights.demand and demand_charge:
            terms["demand"] = demand_charge * cp.maximum(cp.max(billed), demand.floor_kw)
        if weights.hinge and demand.floor_kw > 0:  # a zero floor would just price all energy
            terms["hinge"] = dt * cp.sum(cp.pos(billed - demand.floor_kw))
    elif weights.demand and demand_charge:
        terms["demand"] = demand_charge * cp.maximum(cp.max(grid), prev_peak_kw)
    if weights.valley:
        terms["valley"] = dt * cp.sum_squares(net) / p_ref
    c = _neg_seq_coeffs(infra, idx)
    if weights.phase and c is not None:
        terms["phase"] = dt * (cp.sum_squares(c.real @ R) + cp.sum_squares(c.imag @ R)) / i_ref
    terms["unmet"] = cp.sum(u)
    if expected is not None:
        terms["expected"] = expected
    if finish is not None:
        terms["finish"] = cp.sum(finish)
    terms["early"] = (np.arange(T) / T) @ P * dt
    objective = sum(getattr(weights, k) * v for k, v in terms.items())
    return Built(R, cons, terms, objective, billed)


def run_solver(prob: cp.Problem) -> float:
    """Solve with Clarabel (SCS fallback); returns wall time."""
    t0 = time.perf_counter()
    try:
        # EVSCHED_SOLVER_THREADS caps Clarabel's threads when many jobs run in parallel (0 = all cores)
        prob.solve(solver=cp.CLARABEL, max_threads=int(os.environ.get("EVSCHED_SOLVER_THREADS", 0)))
    except cp.SolverError:
        prob.solve(solver=cp.SCS, eps=1e-6)
    return time.perf_counter() - t0


def solve(
    evs: EVBlock,
    infra: Infrastructure,
    prices: np.ndarray,
    weights: Weights,
    period: float,
    demand_charge: float = 0.0,
    prev_peak_kw: float = 0.0,
    robust: Optional[RobustSpec] = None,
    hard: bool = False,
    demand: Optional[DemandSpec] = None,
    taper: Optional[TaperSpec] = None,
    p_ref: float = 150.0,
    i_ref: float = 416.0,
    margin: float = 1e-3,
) -> Result:
    """Build and solve the scheduling problem; returns rates (n x T)."""
    n, T = evs.ub.shape
    if n == 0 or T == 0 or not (evs.ub > 0).any():
        return Result(np.zeros((n, T)), "empty", 0.0)
    b = build(evs, infra, prices, weights, period, demand_charge, prev_peak_kw, robust, hard, demand, taper,
              p_ref, i_ref, margin)
    R, terms = b.R, b.terms
    prob = cp.Problem(cp.Minimize(b.objective), b.cons)
    elapsed = run_solver(prob)

    if prob.status not in ("optimal", "optimal_inaccurate"):
        if hard:
            res = solve(evs, infra, prices, weights, period, demand_charge, prev_peak_kw, robust,
                        hard=False, demand=demand, taper=taper, p_ref=p_ref, i_ref=i_ref, margin=margin)
            return replace(res, fallback=True, solve_time=res.solve_time + elapsed)
        raise RuntimeError(f"Scheduling problem {prob.status}")

    rates = np.clip(R.value, 0, evs.ub)
    values = {k: float(v.value) for k, v in terms.items()}
    solver_time = prob.solver_stats.solve_time or 0.0
    return Result(rates, prob.status, elapsed, values, solver_time=solver_time)

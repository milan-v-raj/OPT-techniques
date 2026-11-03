"""What the controller knows about each session: predicted stay and energy instead of the truth.

ACN-Sim's ``SessionInfo`` carries every EV's true departure and requested energy, which no real
controller has. ``EstimatedInterface`` replaces them with predictions from recent history while
the simulator keeps the true values (batteries, departures and metrics are unchanged).

``SessionPredictor`` bins the weekday sessions of the preceding ``weeks`` weeks by arrival hour
(the ``ArrivalForecaster`` window). At plug-in a session gets the q-quantile of its bin's stays
and energies; bins with fewer than ``min_count`` sessions are widened to neighbouring hours.
Predictions are updated conditionally on what has been observed since:

    overstay  (t >= predicted departure):   stay <- Q_q(stay | stay > t - arrival)
    shortfall (predicted kWh delivered, EV not finished):   kWh <- Q_q(kWh | kWh > delivered)

The EV still appearing in the active list after its predicted energy is the controller's only
signal that it wants more: ACN-Sim drops EVs whose request is met, as a real EVSE reports when the
car stops accepting charge. How much more is not revealed.

``scenarios`` (stochastic MPC, E8 extension) returns the joint conditional distribution instead of
one guess: up to ``max_n`` historical (stay, kWh) pairs of the arrival bin with stay > elapsed and
kWh > delivered, stratified by stay and equally weighted. Bins are widened until ``min_n`` pairs
remain.

``naive=True`` is the no-prediction comparison: one global median for every EV and no updates.
An EV past its guessed departure or energy is no longer offered to the scheduler.
"""

from functools import partial

import numpy as np
from acnportal.acnsim.interface import Interface, SessionInfo

from . import PERIOD
from .forecast import history_window

SCENARIOS_MAX = 20  # scenarios per EV for the stochastic MPC
SCENARIOS_MIN = 10  # widen the arrival bins until at least this many conditioned pairs remain
OVERSTAY_STEP = 6  # periods added when no historical stay is longer than the elapsed time
MIN_KWH_STEP = 0.5  # smallest energy re-prediction step (kWh)
MIN_KWH = 0.1
DONE_TOL = 0.05  # kWh: a predicted request counts as met within this (under 1 min at full rate). Pilot
#                  increments (RoundRobin: 0.1 A steps) can leave a residual that never reaches 0


class SessionPredictor:
    def __init__(self, history, day, q_stay: float = 0.5, q_kwh: float = 0.5, min_count: int = 10,
                 naive: bool = False, weeks: int = 4, bin_minutes: int = 60, holidays=("2018-09-03",)):
        h, _ = history_window(history, day, weeks, bin_minutes, holidays)
        self.q_stay, self.q_kwh, self.naive = q_stay, q_kwh, naive
        self.bin_minutes, self.min_count = bin_minutes, min_count
        self.all = (h["stay"].to_numpy(), h["kwh_delivered"].to_numpy())
        self.by_bin = {b: (g["stay"].to_numpy(), g["kwh_delivered"].to_numpy()) for b, g in h.groupby("bin")}
        self.n_history = len(h)
        self._cache = {}
        self._scen = {}

    def bin_of(self, arrival: int) -> int:
        return int(arrival * PERIOD // self.bin_minutes)

    def samples(self, arrival: int):
        """(stays, kWh) of the arrival's bin, widened to b±1, b±2, ... until ``min_count``."""
        if self.naive:
            return self.all
        b = self.bin_of(arrival)
        if b not in self._cache:
            out = self.all
            for w in range(24 * 60 // self.bin_minutes):
                parts = [self.by_bin[k] for k in range(b - w, b + w + 1) if k in self.by_bin]
                if parts and sum(len(p[0]) for p in parts) >= self.min_count:
                    out = tuple(np.concatenate([p[i] for p in parts]) for i in (0, 1))
                    break
            self._cache[b] = out
        return self._cache[b]

    def initial(self, arrival: int):
        """(stay in periods, kWh) predicted at plug-in."""
        stay, kwh = self.samples(arrival)
        return (max(int(round(np.quantile(stay, self.q_stay))), 1),
                max(float(np.quantile(kwh, self.q_kwh)), MIN_KWH))

    def update_stay(self, arrival: int, elapsed: int) -> int:
        """Predicted total stay given the EV is still plugged in after ``elapsed`` periods (> elapsed)."""
        stay = self.samples(arrival)[0]
        tail = stay[stay > elapsed]
        guess = np.quantile(tail, self.q_stay) if tail.size else elapsed + OVERSTAY_STEP
        return max(int(np.ceil(guess)), elapsed + 1)

    def update_kwh(self, arrival: int, delivered: float) -> float:
        """Predicted total energy given ``delivered`` kWh did not finish the EV (> delivered)."""
        kwh = self.samples(arrival)[1]
        tail = kwh[kwh > delivered]
        guess = np.quantile(tail, self.q_kwh) if tail.size else delivered + 1.0
        return max(float(guess), delivered + MIN_KWH_STEP)


    def scenarios(self, arrival: int, elapsed: int, delivered: float, max_n: int = SCENARIOS_MAX,
                  min_n: int = SCENARIOS_MIN):
        """(periods left until departure, kWh still needed, weights) for an EV plugged in for ``elapsed``
        periods that has received ``delivered`` kWh and is not finished: historical pairs with
        stay > elapsed and kWh > delivered, widened over neighbouring bins until ``min_n`` remain,
        then ``max_n`` of them evenly spaced by stay. With no such pair anywhere, one scenario
        ``OVERSTAY_STEP`` periods ahead needing ``MIN_KWH_STEP`` more."""
        b = self.bin_of(arrival)
        key = (b, elapsed, round(delivered, 1))
        if key not in self._scen:
            stay = kwh = np.zeros(0)
            for w in range(24 * 60 // self.bin_minutes):
                parts = [self.by_bin[k] for k in range(b - w, b + w + 1) if k in self.by_bin]
                if not parts:
                    continue
                s, e = (np.concatenate([p[i] for p in parts]) for i in (0, 1))
                s = np.round(s).astype(int)
                m = (s > elapsed) & (e > delivered)
                stay, kwh = s[m], e[m]
                if stay.size >= min_n:
                    break
            if stay.size:
                order = np.argsort(stay, kind="stable")
                stay, kwh = stay[order], kwh[order]
                pick = np.unique(np.round(np.linspace(0, stay.size - 1, min(max_n, stay.size))).astype(int))
                stay, kwh = stay[pick], kwh[pick]
            self._scen[key] = (stay, kwh)
        stay, kwh = self._scen[key]
        if stay.size == 0:
            return np.array([OVERSTAY_STEP]), np.array([MIN_KWH_STEP]), np.ones(1)
        return stay - elapsed, np.maximum(kwh - delivered, 0.0), np.full(stay.size, 1 / stay.size)


class EstimatedInterface(Interface):
    """ACN-Sim interface whose sessions show predicted departure and energy (see module doc).

    Pass ``partial(EstimatedInterface, predictor=...)`` as the simulator's ``interface_type``; every
    consumer of the interface (scheduler, rampdown estimator, sorting keys, ``remaining_amp_periods``)
    then sees the predictions.
    """

    def __init__(self, simulator, predictor: SessionPredictor):
        super().__init__(simulator)
        self.predictor = predictor
        self.pred = {}  # session_id -> [departure, kWh]
        self.plugin = {}  # session_id -> (pred stay, true stay, pred kWh, true kWh)
        self.updates = {"stay": 0, "kwh": 0}

    def _active_sessions(self):
        t, p = self.current_time, self.predictor
        out = []
        for ev in self._active_evs:
            if ev.session_id not in self.pred:
                stay, kwh = p.initial(ev.arrival)
                self.pred[ev.session_id] = [ev.arrival + stay, kwh]
                self.plugin[ev.session_id] = (stay, ev.departure - ev.arrival, kwh, ev.requested_energy)
            dep, kwh = self.pred[ev.session_id]
            done = ev.energy_delivered >= kwh - DONE_TOL
            if p.naive:
                if t >= dep or done:
                    continue
            else:
                if t >= dep:
                    dep = ev.arrival + p.update_stay(ev.arrival, t - ev.arrival)
                    self.updates["stay"] += 1
                if done:
                    kwh = p.update_kwh(ev.arrival, ev.energy_delivered)
                    self.updates["kwh"] += 1
                self.pred[ev.session_id] = [dep, kwh]
            out.append(SessionInfo(ev.station_id, ev.session_id, kwh, ev.energy_delivered,
                                   ev.arrival, dep, dep, t))
        return out

    def stats(self) -> dict:
        """Plug-in prediction errors (predicted − true) and re-prediction counts for one day."""
        if not self.plugin:
            return {}
        ps, ts, pk, tk = map(np.array, zip(*self.plugin.values()))
        err_min = (ps - ts) * PERIOD
        return {"pred_stay_mae_min": float(np.abs(err_min).mean()), "pred_stay_bias_min": float(err_min.mean()),
                "pred_kwh_mae": float(np.abs(pk - tk).mean()), "pred_kwh_bias": float((pk - tk).mean()),
                "pct_overstay": float(100 * (ts > ps).mean()), "pct_under_kwh": float(100 * (tk > pk).mean()),
                "stay_updates": self.updates["stay"], "kwh_updates": self.updates["kwh"]}


def interface_type(predictor: SessionPredictor):
    return partial(EstimatedInterface, predictor=predictor)

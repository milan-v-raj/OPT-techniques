"""Arrival forecasting for the MPC: expected future EVs ("ghost EVs") from recent history.

For a target day, the weekday sessions of the preceding ``weeks`` weeks are binned by arrival
hour. Each bin b gives an expected number of arrivals per weekday lambda_b, a mean energy and a
mean stay. At MPC time t every future bin becomes one aggregate pseudo-EV per phase group (three
virtual network columns, see ``Infrastructure.with_virtual``) with

    window  [arrival_b, arrival_b + stay_b),   rate <= lambda_b / 3 * 32 A,   energy = lambda_b * kWh_b / 3.

Ghosts are scheduled together with the real EVs, so capacity, cost and peak are reserved for
them, but their rates are never applied. Only data from before the target day is used.

``ghost_fix`` (algo suffix ``+fix``) corrects two window effects:

    * the hour in progress keeps a ghost for the share of its arrivals still to come, arriving next
      period (without it, the whole hour is counted until its midpoint, then nothing);
    * a ghost whose window is cut at ``horizon_end`` keeps only that share of its energy (without it,
      a window cut to a few periods at the end of the MPC horizon must still take the bin's full
      energy, which plans a phantom peak there).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import MAX_RATE, PERIOD
from .events import day_start


@dataclass
class GhostRows:
    col: np.ndarray  # n_g, virtual column (phase group) index into the infrastructure
    arrival: np.ndarray  # n_g, period
    departure: np.ndarray  # n_g, period (exclusive)
    rate: np.ndarray  # n_g, aggregate amp limit
    energy: np.ndarray  # n_g, kWh


def history_window(history: pd.DataFrame, day, weeks: int = 4, bin_minutes: int = 60,
                   holidays=("2018-09-03",)):
    """Weekday sessions of the ``weeks`` weeks before ``day`` (no leakage), with the arrival-time
    ``bin`` and the ``stay`` in periods. Returns (sessions, number of history days)."""
    start = day_start(day)
    h = history[(history["connection_time"] >= start - pd.Timedelta(weeks=weeks))
                & (history["connection_time"] < start)].copy()
    dates = h["connection_time"].dt.date
    keep = (h["connection_time"].dt.weekday < 5) & ~dates.astype(str).isin(holidays)
    h, dates = h[keep], dates[keep]
    local = h["connection_time"].dt.tz_convert(start.tzinfo)
    minutes = local.dt.hour * 60 + local.dt.minute
    h["bin"] = (minutes // bin_minutes).astype(int)
    h["stay"] = (h["disconnect_time"] - h["connection_time"]).dt.total_seconds() / 60 / PERIOD
    return h, max(dates.nunique(), 1)


class ArrivalForecaster:
    def __init__(self, history: pd.DataFrame, day, weeks: int = 4, bin_minutes: int = 60,
                 min_rate: float = 0.05, holidays=("2018-09-03",), ghost_fix: bool = False):
        h, n_days = history_window(history, day, weeks, bin_minutes, holidays)
        g = h.groupby("bin").agg(n=("kwh_delivered", "size"), kwh=("kwh_delivered", "mean"),
                                 stay=("stay", "mean"))
        g["rate"] = g["n"] / n_days
        g = g[g["rate"] >= min_rate]
        self.n_days = n_days
        self.ghost_fix = ghost_fix
        self.bin_periods = bin_minutes // PERIOD
        self.bins = g
        self.arrival = ((g.index.to_numpy() + 0.5) * bin_minutes / PERIOD).astype(int)  # bin midpoint
        self.stay = np.maximum(np.round(g["stay"].to_numpy()).astype(int), 1)

    def expected_kwh(self) -> float:
        return float((self.bins["rate"] * self.bins["kwh"]).sum())

    def ghosts(self, t: int, virtual_cols: np.ndarray, horizon_end: int) -> GhostRows:
        """Ghost rows for bins arriving after period ``t``, windows clipped at ``horizon_end``."""
        a, end = self.arrival, self.arrival + self.stay
        rate, kwh = self.bins["rate"].to_numpy(), self.bins["kwh"].to_numpy()
        live = a > t
        if self.ghost_fix:  # the hour in progress: the share still to arrive, from the next period
            start = self.bins.index.to_numpy() * self.bin_periods
            cur = (start <= t) & (t < start + self.bin_periods)
            rate = np.where(cur, rate * (start + self.bin_periods - t) / self.bin_periods, rate)
            a = np.where(cur, t + 1, a)
            live = live | cur
        a, end, rate, kwh = a[live], end[live], rate[live], kwh[live]
        d = np.minimum(end, horizon_end)
        ok = d > a
        a, d, end, rate, kwh = a[ok], d[ok], end[ok], rate[ok], kwh[ok]
        energy = rate * kwh
        if self.ghost_fix:  # a window cut at horizon_end keeps only that share of the energy
            energy = energy * (d - a) / (end - a)
        k = len(virtual_cols)
        return GhostRows(
            col=np.tile(virtual_cols, len(a)),
            arrival=np.repeat(a, k),
            departure=np.repeat(d, k),
            rate=np.repeat(rate * MAX_RATE / k, k),
            energy=np.repeat(energy / k, k),
        )

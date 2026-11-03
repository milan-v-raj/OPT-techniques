"""Synthetic on-site PV (the dataset has no solar data).

E5 (``SolarProfile``): a clear-sky bell curve between sunrise and sunset scaled to the PV size,
repeated daily. Realised PV lies in [pv_hat - pv_dev, pv_hat + pv_dev] with pv_dev = dev_frac * pv_hat,
with temporally correlated (AR(1)) deviations to mimic passing clouds.

E9 (``SolarDay``): synthetic weather per calendar date, seeded by the date so every scheduler sees the
same sky. Each day draws a sky type and a daily clearness k_d; realised PV is

    pv_t = clear_t * clip(k_d + a * z_t, 0, 1.05),   z_t = rho z_{t-1} + sqrt(1 - rho^2) eps_t  (AR(1) clouds)

The controller gets a day-ahead clearness k_hat_d = k_d + noise, blended with the clearness measured
in the last period (persistence nowcast) that decays back to k_hat over ``tau`` periods:

    pv_hat(t + h | t) = clear_{t+h} * (k_hat + (c_{t-1} - k_hat) * exp(-h / tau)),   c_{t-1} = pv_{t-1} / clear_{t-1}
"""

from dataclasses import dataclass
from datetime import date as _date, timedelta

import numpy as np

from . import PERIOD

DAY = 24 * 60 // PERIOD


@dataclass
class SolarProfile:
    day_hat: np.ndarray  # one day of forecast PV, kW per period from local midnight
    day_dev: np.ndarray  # one day of maximum deviation, kW

    def window(self, start: int, length: int):
        """(pv_hat, pv_dev) for periods [start, start + length), wrapping daily."""
        idx = (start + np.arange(length)) % len(self.day_hat)
        return self.day_hat[idx], self.day_dev[idx]

    def sample(self, n: int, length: int, seed: int = 0, rho: float = 0.9) -> np.ndarray:
        """``n`` realised PV traces (n x length), each within forecast +- deviation."""
        pv_hat, pv_dev = self.window(0, length)
        rng = np.random.default_rng(seed)
        z = np.zeros((n, length))
        z[:, 0] = rng.uniform(-1, 1, n)
        for t in range(1, length):
            z[:, t] = rho * z[:, t - 1] + np.sqrt(1 - rho ** 2) * rng.uniform(-1, 1, n)
        return np.clip(pv_hat + np.clip(z, -1, 1) * pv_dev, 0, None)


def clear_sky_profile(capacity_kw: float = 50.0, sunrise: float = 6.75, sunset: float = 19.25,
                      dev_frac: float = 0.4) -> SolarProfile:
    """Clear-sky PV forecast (kW) for one day of periods from local midnight."""
    hours = np.arange(DAY) * PERIOD / 60
    x = np.clip((hours - sunrise) / (sunset - sunrise), 0, 1)
    pv_hat = capacity_kw * np.sin(np.pi * x) ** 1.2
    return SolarProfile(pv_hat, dev_frac * pv_hat)


# sky type: (probability, daily clearness range, cloud amplitude a); roughly a Pasadena September
SKY = {"clear": (0.70, (0.90, 1.00), 0.03),
       "partly": (0.20, (0.55, 0.85), 0.25),
       "overcast": (0.10, (0.20, 0.45), 0.08)}
MAX_CLEARNESS = 1.05


@dataclass
class WeatherDay:
    date: str
    sky: str
    k: float  # daily clearness
    k_hat: float  # day-ahead forecast of k
    real: np.ndarray  # realised PV (kW) for one day of periods from local midnight
    clear: np.ndarray  # clear-sky PV (kW)


def weather_day(day, capacity_kw: float = 50.0, seed: int = 0, rho: float = 0.9, k_err: float = 0.12) -> WeatherDay:
    """Synthetic weather for one calendar date (deterministic in ``day`` and ``seed``)."""
    d = _date.fromisoformat(str(day)[:10])
    rng = np.random.default_rng([seed, d.year * 10000 + d.month * 100 + d.day])
    names = list(SKY)
    sky = names[rng.choice(len(names), p=[SKY[n][0] for n in names])]
    _, (lo, hi), amp = SKY[sky]
    k = rng.uniform(lo, hi)
    k_hat = float(np.clip(k + rng.normal(0, k_err), 0.15, 1.0))
    z = np.zeros(DAY)
    z[0] = rng.normal()
    for t in range(1, DAY):
        z[t] = rho * z[t - 1] + np.sqrt(1 - rho ** 2) * rng.normal()
    clear = clear_sky_profile(capacity_kw).day_hat
    return WeatherDay(d.isoformat(), sky, float(k), k_hat, clear * np.clip(k + amp * z, 0, MAX_CLEARNESS), clear)


@dataclass
class SolarDay:
    """Realised PV and the controller's PV forecast, per period from a simulated day's local midnight.
    Periods past the end read the last value (0 at night)."""

    clear: np.ndarray
    real: np.ndarray
    k_hat: np.ndarray  # day-ahead clearness of each period's calendar day
    tau: float = 24.0  # nowcast decay (periods): 2 h

    def _idx(self, start: int, length: int) -> np.ndarray:
        return np.minimum(start + np.arange(length), len(self.clear) - 1)

    def actual(self, start: int, length: int) -> np.ndarray:
        """Realised PV (kW) for periods [start, start + length)."""
        return self.real[self._idx(start, length)]

    def day_ahead(self, start: int, length: int) -> np.ndarray:
        i = self._idx(start, length)
        return self.clear[i] * self.k_hat[i]

    def window(self, start: int, length: int):
        """(pv_hat, pv_dev) forecast at period ``start`` for [start, start + length): day-ahead
        clearness blended with the clearness measured in period start - 1. pv_dev = 0 (nominal)."""
        i = self._idx(start, length)
        k = self.k_hat[i]
        prev = min(start, len(self.clear)) - 1
        if prev >= 0 and self.clear[prev] > 0.02 * self.clear.max():
            c_obs = self.real[prev] / self.clear[prev]
            k = k + (c_obs - k) * np.exp(-np.arange(length) / self.tau)
        return self.clear[i] * np.clip(k, 0, MAX_CLEARNESS), np.zeros(length)


def solar_day(day, capacity_kw: float = 50.0, n_days: int = 1, seed: int = 0) -> SolarDay:
    """``SolarDay`` for a simulation starting at local midnight of ``day`` with ``n_days`` of weather.

    The default is the day's own sun only. Each day is simulated separately and EVs staying overnight
    run into the next day's simulation window, so giving them the next day's PV would use that sun twice.
    """
    d0 = _date.fromisoformat(str(day)[:10])
    w = [weather_day(d0 + timedelta(days=j), capacity_kw, seed) for j in range(n_days)]
    return SolarDay(np.concatenate([x.clear for x in w] + [[0.0]]), np.concatenate([x.real for x in w] + [[0.0]]),
                    np.r_[np.repeat([x.k_hat for x in w], DAY), w[-1].k_hat])

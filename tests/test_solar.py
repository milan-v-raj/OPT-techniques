import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acnportal import acnsim  # noqa: E402
from acnportal.acnsim.events import EventQueue, PluginEvent  # noqa: E402
from acnportal.acnsim.models import EV, Battery  # noqa: E402

from evsched import PERIOD, TIMEZONE  # noqa: E402
from evsched.events import make_network  # noqa: E402
from evsched.experiment import simulate  # noqa: E402
from evsched.metrics import rolling_mean, solar_metrics  # noqa: E402
from evsched.model import EVBlock, Infrastructure, RobustSpec, Weights, solve  # noqa: E402
from evsched.schedulers import ConvexMPC  # noqa: E402
from evsched.solar import DAY, SolarDay, clear_sky_profile, solar_day, weather_day  # noqa: E402

START = pd.Timestamp("2018-09-05").tz_localize(TIMEZONE).to_pydatetime()


def test_weather_is_deterministic_per_date_and_bounded():
    a, b = weather_day("2018-09-05"), weather_day("2018-09-05")
    assert (a.sky, a.k, a.k_hat) == (b.sky, b.k, b.k_hat)
    np.testing.assert_array_equal(a.real, b.real)
    assert not np.array_equal(a.real, weather_day("2018-09-06").real)
    assert (a.real >= 0).all() and a.real.max() <= 1.05 * 50 + 1e-9
    assert a.real[0] == 0 and a.real[-1] == pytest.approx(0, abs=1e-9)  # night
    s = solar_day("2018-09-05", n_days=2)  # concatenated calendar days
    np.testing.assert_array_equal(s.actual(DAY, DAY), weather_day("2018-09-06").real)


def test_nowcast_starts_at_observed_clearness_and_decays_to_day_ahead():
    n = 400
    s = SolarDay(np.full(n, 10.0), np.full(n, 5.0), np.full(n, 0.9))  # observed 0.5, forecast 0.9
    pv, dev = s.window(100, 300)
    assert pv[0] == pytest.approx(5.0) and pv[-1] == pytest.approx(9.0, abs=1e-3)
    assert pv[24] == pytest.approx(10 * (0.9 - 0.4 * np.exp(-1)))  # 2 h time constant
    assert (dev == 0).all()
    np.testing.assert_allclose(s.window(0, 5)[0], s.day_ahead(0, 5))  # nothing observed yet
    night = SolarDay(np.r_[0.0, np.full(n - 1, 10.0)], np.zeros(n), np.full(n, 0.9))
    np.testing.assert_allclose(night.window(1, 5)[0], night.day_ahead(1, 5))  # dark last period


def test_valley_term_flattens_net_load():
    infra = Infrastructure(np.ones((1, 2)), np.array([1000.0]), np.zeros(2), np.full(2, 200.0), ["f"], ["s0", "s1"])
    evs = EVBlock(np.array([0, 1]), np.full((2, 4), 32.0), np.array([2.0, 2.0]))
    pv = np.array([0.0, 4.0, 0.0, 0.0])
    res = solve(evs, infra, np.full(4, 0.1), Weights(valley=1.0), 60, robust=RobustSpec(pv, np.zeros(4)))
    P = res.rates.sum(axis=0) * 200 / 1000
    np.testing.assert_allclose(P, pv, atol=2e-2)  # all 4 kWh in the PV hour


def run_one_ev(solar, weights=Weights(valley=1.0)):
    """One EV 08:00-17:00 wanting 20 kWh on the Caltech network, scheduled by a billing MPC."""
    net = make_network()
    ev = EV(96, 204, 20.0, net.station_ids[0], "a", Battery(20.0, 0, 7))
    mpc = ConvexMPC(weights, solar=solar, billing=True, demand_window=3)
    sim, _ = simulate(mpc, EventQueue([PluginEvent(ev.arrival, ev)]), net, START)
    return sim, mpc


def clear_day(n_days=2):
    clear = np.tile(clear_sky_profile(50.0).day_hat, n_days)
    return SolarDay(clear, clear.copy(), np.ones(clear.size))


def test_pv_aware_mpc_charges_in_the_sun_and_bills_net_history():
    s = clear_day()
    noon = slice(11 * 12, 15 * 12)  # the clear-sky curve peaks at 13:00
    blind, _ = run_one_ev(None)
    aware, mpc = run_one_ev(s)
    Pb, Pa = acnsim.aggregate_power(blind), acnsim.aggregate_power(aware)
    assert Pa.sum() == pytest.approx(Pb.sum(), rel=1e-3)  # same energy
    assert Pa[noon].sum() > 0.95 * Pa.sum() > Pb[noon].sum()
    m = solar_metrics(aware, s)
    assert m["import_kwh"] < 0.05 * Pa.sum() * PERIOD / 60
    spec = mpc._demand_spec(mpc.interface)  # billed history is net import
    imp = np.clip(Pa - s.actual(0, len(Pa)), 0, None)
    np.testing.assert_allclose(spec.p_hist, imp[-2:], atol=1e-9)
    assert spec.floor_kw == pytest.approx(rolling_mean(imp, 3).max())


def test_solar_metrics_balance():
    sim, _ = run_one_ev(None)
    P = acnsim.aggregate_power(sim)
    half = clear_day()
    half = SolarDay(half.clear, 0.1 * half.real, half.k_hat)  # 5 kW peak PV, below the load
    m = solar_metrics(sim, half)
    dt = PERIOD / 60
    assert m["pv_used_kwh"] + m["pv_surplus_kwh"] == pytest.approx(m["pv_kwh"])
    assert m["import_kwh"] == pytest.approx(P.sum() * dt - m["pv_used_kwh"])
    assert m["import_peak_kw"] <= P.max() + 1e-9
    assert m["self_sufficiency"] == pytest.approx(m["pv_used_kwh"] / (P.sum() * dt))

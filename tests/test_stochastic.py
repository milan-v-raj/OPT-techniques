import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acnportal.acnsim.events import EventQueue, PluginEvent  # noqa: E402
from acnportal.acnsim.models import EV, Battery  # noqa: E402

from evsched import TIMEZONE  # noqa: E402
from evsched.estimates import SessionPredictor, interface_type  # noqa: E402
from evsched.events import make_network  # noqa: E402
from evsched.experiment import simulate  # noqa: E402
from evsched.forecast import ArrivalForecaster  # noqa: E402
from evsched.model import EVBlock, Infrastructure, Weights, solve  # noqa: E402
from evsched.schedulers import ConvexMPC  # noqa: E402
from evsched.solar import solar_day  # noqa: E402

DAY = "2018-09-05"
START = pd.Timestamp(DAY).tz_localize(TIMEZONE).to_pydatetime()
AUG = [d.date().isoformat() for d in pd.bdate_range("2018-08-08", "2018-08-31")]  # 18 weekdays (4-week window)


def history(spec):
    """spec: list of (date, arrival hour, stay hours, kWh)."""
    t = pd.to_datetime([f"{d} {h:02d}:00" for d, h, _, _ in spec]).tz_localize(TIMEZONE)
    stay = pd.to_timedelta([s for _, _, s, _ in spec], unit="h")
    return pd.DataFrame({"connection_time": t, "disconnect_time": t + stay,
                         "kwh_delivered": [k for *_, k in spec]})


def test_scenarios_are_conditioned_stratified_and_widened():
    eight = [(d, 8, s, s) for d, s in zip(AUG, range(1, 13))]  # 08:00: stays 1..12 h, kWh = stay
    p = SessionPredictor(history(eight), DAY)
    d, k, pi = p.scenarios(96, 0, 0.0)
    np.testing.assert_array_equal(d, np.arange(1, 13) * 12)
    np.testing.assert_allclose(k, np.arange(1, 13))
    assert pi.sum() == pytest.approx(1)
    d, k, pi = p.scenarios(96, 72, 8.5)  # still plugged in after 6 h with 8.5 kWh delivered
    np.testing.assert_array_equal(d, np.array([9, 10, 11, 12]) * 12 - 72)
    np.testing.assert_allclose(k, np.array([9, 10, 11, 12]) - 8.5)
    np.testing.assert_allclose(pi, 0.25)
    assert [a.tolist() for a in p.scenarios(96, 200, 0.0)] == [[6], [0.5], [1.0]]  # nothing longer: +30 min

    two = history(eight + [(d, 14, 2, 3) for d in AUG[:15]])  # 14:00: 15 short stays
    p = SessionPredictor(two, DAY)
    assert len(p.scenarios(14 * 12, 0, 0.0)[0]) == 15  # enough in its own bin
    d, k, _ = p.scenarios(14 * 12, 36, 0.0)  # 3 h in: no 14:00 stay is that long -> widen to 08:00
    np.testing.assert_array_equal(d + 36, np.arange(4, 13) * 12)

    many = SessionPredictor(history([(AUG[i % 18], 8, 1 + i / 10, 5) for i in range(60)]), DAY)
    d, _, pi = many.scenarios(96, 0, 0.0)
    assert len(d) == 20 and (np.diff(d) >= 0).all() and d[0] == 12 and d[-1] == round((1 + 5.9) * 12)
    np.testing.assert_allclose(pi, 1 / 20)


def one_ev_problem(w):
    """One EV, 6.4 kWh per hourly period, needs 5 kWh: leaves after 3 or 10 periods with equal
    probability. Energy costs 1 $/kWh in the first 3 periods and 0.1 afterwards."""
    infra = Infrastructure(np.ones((1, 1)), np.array([1000.0]), np.zeros(1), np.full(1, 200.0), ["f"], ["s0"])
    sc = (np.array([3, 10]), np.array([5.0, 5.0]), np.array([0.5, 0.5]))
    evs = EVBlock(np.array([0]), np.full((1, 10), 32.0), np.array([5.0]), scenarios=[sc])
    prices = np.r_[np.full(3, 1.0), np.full(7, 0.1)]
    res = solve(evs, infra, prices, Weights(cost=1.0, expected=w, early=0.0), 60)
    return res.rates[0] * 200 / 1000, res


def test_expected_unmet_trades_risk_against_price():
    e, res = one_ev_problem(1.0)  # risk 0.5 $/kWh < price gap 0.9: wait for cheap energy
    assert e[:3].sum() == pytest.approx(0, abs=1e-3) and e.sum() == pytest.approx(5, abs=1e-3)
    assert res.terms["expected"] == pytest.approx(0.5 * 5, abs=1e-3)
    e, res = one_ev_problem(100.0)  # risk 50 $/kWh: charge before the early departure
    assert e[:3].sum() == pytest.approx(5, abs=1e-3) and e.sum() <= 5 + 1e-3
    assert res.terms["expected"] == pytest.approx(0, abs=1e-3)


def test_stochastic_mpc_serves_an_early_leaver():
    # history: 08:00 arrivals leave after 3 h or 10 h (half each), all take 15 kWh; median stay 6.5 h
    h = history([(d, 8, 3 if i % 2 else 10, 15) for i, d in enumerate(AUG)])
    net = make_network()
    delivered = {}
    for name, algo in [("median", ConvexMPC(Weights(valley=1.0))),
                       ("stochastic", ConvexMPC(Weights(valley=1.0, expected=10.0), stochastic=True))]:
        ev = EV(96, 132, 15.0, net.station_ids[0], "a", Battery(15.0, 0, 7))  # really leaves after 3 h
        sim, _ = simulate(algo, EventQueue([PluginEvent(96, ev)]), net, START,
                          interface_type(SessionPredictor(h, DAY)))
        delivered[name] = sim.ev_history["a"].energy_delivered
    assert delivered["stochastic"] > 14.5
    assert delivered["median"] < 12


def test_stochastic_mpc_with_pv_still_serves_an_early_leaver():
    # E9 + stochastic: with PV the valley term on net load would defer into the solar hours
    h = history([(d, 8, 3 if i % 2 else 10, 15) for i, d in enumerate(AUG)])
    net = make_network()
    algo = ConvexMPC(Weights(valley=1.0, expected=10.0), stochastic=True, solar=solar_day(DAY, 50.0))
    ev = EV(96, 132, 15.0, net.station_ids[0], "a", Battery(15.0, 0, 7))
    sim, infeasible = simulate(algo, EventQueue([PluginEvent(96, ev)]), net, START,
                               interface_type(SessionPredictor(h, DAY)))
    assert infeasible == 0 and algo.fallbacks == 0
    assert sim.ev_history["a"].energy_delivered > 14.5


def test_ghost_fix_counts_the_hour_in_progress_and_cuts_energy_at_the_horizon():
    h = history([(d, 8, 4, 10) for d in AUG for _ in range(2)])  # 2 arrivals at 08:00 a day, 4 h, 10 kWh
    old, new = ArrivalForecaster(h, DAY), ArrivalForecaster(h, DAY, ghost_fix=True)
    cols = np.arange(3)
    kwh = lambda f, t, end=288: f.ghosts(t, cols, end).energy.sum()
    # 08:15: a quarter of the hour has passed; the old ghost keeps it all until 08:30, then drops it
    assert kwh(old, 99) == pytest.approx(20) and kwh(new, 99) == pytest.approx(15)
    assert new.ghosts(99, cols, 288).arrival.min() == 100
    assert kwh(old, 104) == 0 and kwh(new, 104) == pytest.approx(20 * 4 / 12)
    # 07:30 with the horizon ending at 09:30: only 1 h of the 4 h window fits
    assert kwh(old, 90, 114) == pytest.approx(20) and kwh(new, 90, 114) == pytest.approx(5)

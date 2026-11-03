import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evsched import DATA_ROOT  # noqa: E402
from evsched.data import parse_session_file  # noqa: E402
from evsched.model import EVBlock, Infrastructure, RobustSpec, Weights, solve  # noqa: E402

PERIOD = 60  # one-hour periods keep the toy numbers readable
V = 200.0


def toy_infra(limit=1000.0):
    """Two stations on the same phase sharing one feeder constraint."""
    return Infrastructure(np.ones((1, 2)), np.array([limit]), np.zeros(2), np.full(2, V), ["feeder"], ["s0", "s1"])


def toy_evs(demand=(2.0, 2.0), T=4, rate=32.0):
    return EVBlock(np.array([0, 1]), np.full((2, T), rate), np.array(demand))


def power(rates):
    return rates.sum(axis=0) * V / 1000


def test_cost_lp_charges_in_cheapest_periods():
    prices = np.array([0.3, 0.1, 0.1, 0.3])
    res = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0, early=0), PERIOD)
    p = power(res.rates)
    assert p[[0, 3]].sum() == pytest.approx(0, abs=1e-5)
    assert p.sum() == pytest.approx(4.0, rel=1e-5)


def test_valley_qp_is_flat():
    res = solve(toy_evs(), toy_infra(), np.ones(4), Weights(valley=1.0, early=0), PERIOD)
    np.testing.assert_allclose(power(res.rates), 1.0, rtol=1e-4)


def test_network_limit_respected():
    res = solve(toy_evs(demand=(9.0, 9.0)), toy_infra(limit=20.0), np.ones(4), Weights(cost=1.0), PERIOD)
    assert res.rates.sum(axis=0).max() <= 20.0 + 1e-6
    assert res.terms["unmet"] == pytest.approx(2.0, rel=1e-3)  # 4 kW * 4 h = 16 kWh < 18 kWh, soft constraint absorbs it


def test_hard_falls_back_when_infeasible_and_agrees_when_feasible():
    soft = solve(toy_evs(), toy_infra(), np.ones(4), Weights(valley=1.0), PERIOD)
    hard = solve(toy_evs(), toy_infra(), np.ones(4), Weights(valley=1.0), PERIOD, hard=True)
    np.testing.assert_allclose(soft.rates, hard.rates, atol=1e-4)
    assert not hard.fallback
    infeasible = solve(toy_evs(demand=(9.0, 9.0)), toy_infra(limit=20.0), np.ones(4), Weights(cost=1.0),
                       PERIOD, hard=True)
    assert infeasible.fallback


def test_robust_zero_budget_matches_nominal_without_pv():
    prices = np.array([0.3, 0.1, 0.2, 0.3])
    nominal = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0), PERIOD)
    spec = RobustSpec(np.zeros(4), np.zeros(4), gamma_solar=0.0, price_dev=0.5, gamma_price=0.0)
    robust = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0), PERIOD, robust=spec)
    assert robust.terms["cost"] == pytest.approx(nominal.terms["cost"], rel=1e-5)


def test_robust_solar_protection_shifts_load_off_uncertain_pv():
    prices = np.full(4, 0.2)
    spec = RobustSpec(np.array([0, 4.0, 4.0, 0]), np.array([0, 4.0, 0, 0]), gamma_solar=1.0)
    res = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0), PERIOD, robust=spec)
    p = power(res.rates)
    assert p[2] == pytest.approx(4.0, rel=1e-4)  # certain PV slot used first
    assert p[1] == pytest.approx(0.0, abs=1e-4) or p[1] < p[2]


def test_parse_session_file():
    f = DATA_ROOT / "caltech" / "California_Garage_01" / "2-39-127-19-2020-02-28T17-38-53-963812.csv.gz"
    if not f.exists():
        pytest.skip("static dataset not available")
    rec = parse_session_file(f)
    assert rec["station_id"] == "2-39-127-19"
    assert rec["kwh_delivered"] == pytest.approx(3.46511, rel=1e-4)
    assert rec["disconnect_time"].isoformat() == "2020-02-28T18:43:23+00:00"

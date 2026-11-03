import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acnportal.acnsim.models import Linear2StageBattery  # noqa: E402

from evsched.metrics import month_summary, rolling_mean  # noqa: E402
from evsched.model import DemandSpec, EVBlock, Infrastructure, TaperSpec, Weights, solve  # noqa: E402
from evsched.schedulers import StationRampdown  # noqa: E402

V = 200.0


def toy_infra(limit=1000.0):
    return Infrastructure(np.ones((1, 2)), np.array([limit]), np.zeros(2), np.full(2, V), ["feeder"], ["s0", "s1"])


def toy_evs(demand=(2.0, 2.0), T=4, rate=32.0, finish_by=None):
    return EVBlock(np.array([0, 1]), np.full((2, T), rate), np.array(demand), finish_by)


def power(rates):
    return rates.sum(axis=0) * V / 1000


def test_rolling_mean_with_history():
    np.testing.assert_allclose(rolling_mean(np.array([1.0, 2, 3, 4]), 3, hist=[6, 3]),
                               [10 / 3, 2, 2, 3])
    np.testing.assert_allclose(rolling_mean(np.array([3.0, 3, 3]), 3), [1, 2, 3])  # zero-padded start


def test_billed_demand_term_uses_rolling_mean_and_history():
    prices = np.array([0.1, 0.1, 0.1, 0.1])
    spec = DemandSpec(window=2, p_hist=np.array([5.0]), floor_kw=0.0)
    res = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0, demand=1.0), 60, demand_charge=10.0, demand=spec)
    billed = rolling_mean(power(res.rates), 2, hist=[5.0])
    assert res.terms["demand"] == pytest.approx(10.0 * billed.max(), rel=1e-4)
    assert power(res.rates)[0] == pytest.approx(0.0, abs=1e-4)  # avoids stacking on the 5 kW history


def test_floor_above_achievable_peak_leaves_pure_cost_solution():
    prices = np.array([0.3, 0.1, 0.1, 0.3])
    cost = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0), 60)
    floored = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0, demand=1.0), 60, demand_charge=10.0,
                    demand=DemandSpec(window=1, floor_kw=100.0))
    assert floored.terms["demand"] == pytest.approx(1000.0, rel=1e-6)
    assert floored.terms["cost"] == pytest.approx(cost.terms["cost"], rel=1e-5)


def test_window_one_spec_matches_v1_prev_peak_path():
    prices = np.array([0.3, 0.1, 0.2, 0.3])
    v1 = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0, demand=1.0), 60, demand_charge=5.0, prev_peak_kw=1.5)
    v2 = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0, demand=1.0), 60, demand_charge=5.0,
               demand=DemandSpec(window=1, floor_kw=1.5))
    np.testing.assert_allclose(v1.rates, v2.rates, atol=1e-4)


def test_finish_by_moves_energy_before_deadline():
    prices = np.array([0.3, 0.3, 0.3, 0.1])
    late = solve(toy_evs(), toy_infra(), prices, Weights(cost=1.0), 60)
    assert power(late.rates)[3] == pytest.approx(4.0, rel=1e-4)
    early = solve(toy_evs(finish_by=np.array([3, 3])), toy_infra(), prices, Weights(cost=1.0, finish=1.0), 60)
    assert power(early.rates)[:3].sum() == pytest.approx(4.0, rel=1e-4)
    assert early.terms["finish"] == pytest.approx(0.0, abs=1e-5)


def test_taper_bound_tracks_acn_two_stage_battery():
    period, volts, T = 5, 208.0, 72
    cap, init = 24.0, 14.0
    batt = Linear2StageBattery(cap, init, 32 * volts / 1000)
    acn = []
    for _ in range(T):
        acn.append(batt.charge(32, volts, period) * volts / 1000 * period / 60)
    acn = np.array(acn)
    infra = Infrastructure(np.ones((1, 1)), np.array([1e3]), np.zeros(1), np.full(1, volts), ["f"], ["s0"])
    evs = EVBlock(np.array([0]), np.full((1, T), 32.0), np.array([cap]))
    taper = TaperSpec(np.array([cap]), np.array([init / cap]), np.array([32 * volts / 1000]))
    res = solve(evs, infra, np.zeros(T), Weights(), period, taper=taper)
    lp = res.rates[0] * volts / 1000 * period / 60
    assert lp.sum() == pytest.approx(acn.sum(), rel=0.01)
    assert np.abs(np.cumsum(lp) - np.cumsum(acn)).max() < 0.02 * acn.sum()


def test_virtual_columns_are_phase_group_means():
    A = np.array([[1.0, 0, 1, 0], [0, 1, 0, 1]])
    infra = Infrastructure(A, np.ones(2), np.array([30.0, -90, 30, -90]), np.full(4, V), ["a", "b"],
                           ["s0", "s1", "s2", "s3"])
    v = infra.with_virtual()
    assert list(v.virtual_idx()) == [4, 5]
    np.testing.assert_allclose(v.A[:, 4], [0, 1])  # phase -90 group
    np.testing.assert_allclose(v.A[:, 5], [1, 0])  # phase 30 group


def test_station_rampdown_is_keyed_by_station_and_lowers_after_taper():
    est = StationRampdown()
    sessions = [SimpleNamespace(session_id="sess-1", station_id="CA-1")]
    iface = SimpleNamespace(last_applied_pilot_signals={"sess-1": 32.0}, last_actual_charging_rate={"sess-1": 20.0},
                            max_pilot_signal=lambda station: 32.0)
    est.register_interface(iface)
    bounds = est.get_maximum_rates(sessions)
    assert set(bounds) == {"CA-1"}
    assert bounds["CA-1"] == pytest.approx(21.0)


def test_month_summary_increments_sum_to_month_peak():
    import pandas as pd
    rows = pd.DataFrame({"day": ["d1", "d2", "d3"], "peak15_kw": [50.0, 40.0, 70.0], "energy_cost": [1.0, 1, 1]})
    out = month_summary(rows, demand_charge=10.0)
    assert out["demand_increment_kw"].tolist() == [50.0, 0.0, 20.0]
    assert out["daily_bill"].sum() == pytest.approx(3 + 10 * 70)

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acnportal import acnsim, algorithms  # noqa: E402
from acnportal.acnsim.events import EventQueue, PluginEvent  # noqa: E402
from acnportal.acnsim.models import EV, Battery  # noqa: E402

from evsched import TIMEZONE  # noqa: E402
from evsched.estimates import SessionPredictor, interface_type  # noqa: E402
from evsched.events import make_network  # noqa: E402

DAY = "2018-09-05"


def history(spec):
    """spec: list of (date, arrival hour, stay hours, kWh)."""
    t = pd.to_datetime([f"{d} {h:02d}:00" for d, h, _, _ in spec]).tz_localize(TIMEZONE)
    stay = pd.to_timedelta([s for _, _, s, _ in spec], unit="h")
    return pd.DataFrame({"connection_time": t, "disconnect_time": t + stay,
                         "kwh_delivered": [k for *_, k in spec]})


AUG = [d.date().isoformat() for d in pd.bdate_range("2018-08-13", "2018-08-31")]  # 15 weekdays


def two_bin_history():
    # 08:00 arrivals stay 10 h and take 10 kWh, 09:00 arrivals stay 2 h and take 4 kWh
    return history([(d, 8, 10, 10) for d in AUG] + [(d, 9, 2, 4) for d in AUG])


def test_no_leakage_and_bin_quantiles():
    h = pd.concat([two_bin_history(), history([(DAY, 8, 1, 50), ("2018-09-06", 8, 1, 50)])])
    p = SessionPredictor(h, DAY)
    assert p.n_history == 2 * len(AUG)  # sessions on / after the day are not used
    assert p.initial(8 * 12) == (120, pytest.approx(10.0))
    assert p.initial(9 * 12 + 3) == (24, pytest.approx(4.0))
    q = SessionPredictor(h, DAY, q_stay=0.25, q_kwh=0.75)
    assert q.initial(8 * 12) == (120, pytest.approx(10.0))  # one value per bin: every quantile agrees


def test_sparse_bin_widens_to_neighbours_and_naive_pools_everything():
    h = history([(d, 8, 10, 10) for d in AUG] + [(AUG[0], 14, 1, 1)])
    p = SessionPredictor(h, DAY, min_count=10)
    stay, kwh = p.samples(14 * 12)  # one session at 14:00, widened until it reaches the 08:00 bin
    assert len(stay) == len(AUG) + 1
    naive = SessionPredictor(two_bin_history(), DAY, naive=True)
    assert naive.initial(8 * 12) == naive.initial(20 * 12)
    assert len(naive.samples(0)[0]) == 2 * len(AUG)


def test_conditional_updates_exceed_observation():
    stays = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    p = SessionPredictor(history([(d, 8, s, s) for d, s in zip(AUG, stays)]), DAY, min_count=1)
    assert p.update_stay(8 * 12, 6 * 12) == int(np.ceil(np.quantile([84, 96, 108, 120, 132, 144], 0.5)))
    assert p.update_stay(8 * 12, 200) == 206  # nothing longer in history: +30 min
    assert p.update_kwh(8 * 12, 6.0) == pytest.approx(9.5)
    assert p.update_kwh(8 * 12, 20.0) == pytest.approx(21.0)
    assert p.update_kwh(8 * 12, 11.9) == pytest.approx(12.4)  # minimum step


def run_two_evs(predictor, algo=None):
    """EV a: arrives 08:00, really stays 3 h, wants 15 kWh; EV b: arrives 09:00, really stays 8 h, wants 20 kWh."""
    net = make_network()
    sa, sb = net.station_ids[:2]
    evs = [EV(96, 132, 15.0, sa, "a", Battery(15.0, 0, 7)), EV(108, 204, 20.0, sb, "b", Battery(20.0, 0, 7))]
    algo = algo or algorithms.SortedSchedulingAlgo(algorithms.earliest_deadline_first)
    seen = {}

    class Recorder(type(algo)):
        def schedule(self, active_sessions):
            for s in active_sessions:
                seen.setdefault(self.interface.current_time, {})[s.session_id] = (s.departure, s.requested_energy)
            return super().schedule(active_sessions)

    algo.__class__ = Recorder
    start = pd.Timestamp(DAY).tz_localize(TIMEZONE).to_pydatetime()
    sim = acnsim.Simulator(net, algo, EventQueue([PluginEvent(e.arrival, e) for e in evs]), start, period=5,
                           verbose=False, interface_type=interface_type(predictor))
    sim.run()
    return sim, algo, seen


def test_sessions_show_predictions_while_simulator_keeps_truth():
    sim, algo, seen = run_two_evs(SessionPredictor(two_bin_history(), DAY))
    # at 09:00 both are plugged in: a is predicted to leave 18:00, b at 11:00
    assert seen[108] == {"a": (96 + 120, pytest.approx(10.0)), "b": (108 + 24, pytest.approx(4.0))}
    hist = sim.ev_history
    assert (hist["a"].departure, hist["a"].requested_energy) == (132, 15.0)
    assert (hist["b"].departure, hist["b"].requested_energy) == (204, 20.0)
    # b overstays its 2 h prediction and wants more than 4 kWh: it is re-predicted and finishes
    iface = algo.interface
    assert iface.updates["stay"] > 0 and iface.updates["kwh"] > 0
    assert hist["b"].energy_delivered == pytest.approx(20.0, abs=1e-3)
    assert iface.stats()["pct_overstay"] == pytest.approx(50.0)


def test_edf_orders_by_predicted_departure():
    _, _, seen = run_two_evs(SessionPredictor(two_bin_history(), DAY))
    deps = seen[108]
    assert deps["b"][0] < deps["a"][0]  # truth is the other way round (b leaves 17:00, a 11:00)


def test_naive_drops_ev_after_guess_and_never_updates():
    sim, algo, seen = run_two_evs(SessionPredictor(two_bin_history(), DAY, naive=True))
    stay, kwh = algo.interface.predictor.initial(0)  # pooled medians: 6 h, 7 kWh
    assert (stay, kwh) == (72, pytest.approx(7.0))
    assert algo.interface.updates == {"stay": 0, "kwh": 0}
    assert sim.ev_history["a"].energy_delivered < 7.6  # a also stops at the guessed 7 kWh
    b = sim.ev_history["b"].energy_delivered
    assert kwh - 1e-3 <= b < kwh + 0.6  # b stops at the guessed 7 kWh of its 20
    assert max(t for t in seen if "b" in seen[t]) < 108 + stay


def test_round_robin_pilot_steps_do_not_block_energy_updates():
    # RoundRobin allocates in 0.1 A steps and cannot deliver a residual below one step
    sim, algo, _ = run_two_evs(SessionPredictor(two_bin_history(), DAY),
                               algorithms.RoundRobin(algorithms.first_come_first_served))
    assert algo.interface.updates["kwh"] > 0
    assert sim.ev_history["b"].energy_delivered == pytest.approx(20.0, abs=1e-3)

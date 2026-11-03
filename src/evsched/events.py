"""Turn a day of real sessions into an ACN-Sim EventQueue on the Caltech network.

The static data only carries hardware station ids, not the Caltech space ids used by
``caltech_acn``. Each arriving EV is therefore placed on a free EVSE of the network, choosing
the phase group (AB / BC / CA) with the fewest occupied EVSEs, with a seeded tie-break.
"""

from datetime import datetime
from typing import List, Tuple

import numpy as np
import pandas as pd
import pytz
from acnportal import acnsim
from acnportal.acnsim.events import EventQueue, PluginEvent
from acnportal.acnsim.models import EV, Battery, Linear2StageBattery
from acnportal.acnsim.models.battery import batt_cap_fn

from . import MAX_RATE, PERIOD, TIMEZONE, VOLTAGE


def make_network(transformer_cap: float = 150):
    """Caltech ACN with continuous (BASIC) EVSEs so the scheduling problem stays convex."""
    return acnsim.sites.caltech_acn(basic_evse=True, voltage=VOLTAGE, transformer_cap=transformer_cap)


def day_start(day) -> datetime:
    """Local midnight of ``day`` (date or 'YYYY-MM-DD')."""
    day = pd.Timestamp(day).date()
    return pytz.timezone(TIMEZONE).localize(datetime(day.year, day.month, day.day))


def weekdays(sessions: pd.DataFrame, month: str = "2018-09", holidays=("2018-09-03",)) -> List[str]:
    """Weekdays of ``month`` that have sessions, excluding holidays."""
    dates = sorted({d.isoformat() for d in sessions["connection_time"].dt.date})
    return [
        d for d in dates
        if d.startswith(month) and pd.Timestamp(d).weekday() < 5 and d not in holidays
    ]


def _to_period(ts: pd.Timestamp, start: datetime) -> int:
    return int((ts - start).total_seconds() // (60 * PERIOD))


def assign_evses(day_sessions: pd.DataFrame, network, start: datetime, seed: int = 0) -> Tuple[list, int]:
    """Assign each session to a free EVSE, balancing occupancy over the three phase groups.

    Returns a list of (session row, station_id, arrival, departure) and the number of dropped
    sessions (no free EVSE).
    """
    rng = np.random.default_rng(seed)
    station_ids = list(network.station_ids)
    phases = np.array([network.phase_angles[s] for s in station_ids])
    free_at = np.zeros(len(station_ids), dtype=int)  # period at which each EVSE becomes free
    assigned, dropped = [], 0
    for row in day_sessions.itertuples():
        arrival = _to_period(row.connection_time, start)
        departure = max(_to_period(row.disconnect_time, start), arrival + 1)
        free = np.flatnonzero(free_at <= arrival)
        if free.size == 0:
            dropped += 1
            continue
        busy = free_at > arrival
        load = {p: int(np.sum(busy & (phases == p))) for p in np.unique(phases)}
        best = min(load[phases[i]] for i in free)
        candidates = [i for i in free if load[phases[i]] == best]
        i = int(rng.choice(candidates))
        free_at[i] = departure
        assigned.append((row, station_ids[i], arrival, departure))
    return assigned, dropped


def make_battery(kwh: float, stay: int, kind: str = "ideal"):
    """Ideal battery sized to the request, or ACN's two-stage (tapering) battery sized with
    ``batt_cap_fn``: the smallest pack and highest initial charge that still deliver ``kwh`` in
    ``stay`` periods at full rate, which maximises the time spent in the taper region."""
    max_power = MAX_RATE * VOLTAGE / 1000
    if kind == "ideal":
        return Battery(kwh, 0, max_power)
    if kind == "taper":
        try:
            cap, init = batt_cap_fn(kwh, stay, VOLTAGE, PERIOD)
        except ValueError:  # request cannot be met within the stay even from empty: largest taper-free pack
            cap, init = max(100.0, kwh / 0.8), 0.0
        return Linear2StageBattery(cap, init, max_power)
    raise ValueError(f"Unknown battery kind {kind}")


def build_events(sessions: pd.DataFrame, day, network=None, seed: int = 0, battery: str = "ideal"):
    """EventQueue (and metadata) for all sessions arriving on local ``day``."""
    network = network if network is not None else make_network()
    start = day_start(day)
    end = start + pd.Timedelta(days=1)
    day_sessions = sessions[(sessions["connection_time"] >= start) & (sessions["connection_time"] < end)]
    assigned, dropped = assign_evses(day_sessions, network, start, seed)
    events = []
    for row, station_id, arrival, departure in assigned:
        batt = make_battery(row.kwh_delivered, departure - arrival, battery)
        ev = EV(arrival, departure, row.kwh_delivered, station_id, row.session_id, batt)
        events.append(PluginEvent(arrival, ev))
    info = {"day": str(pd.Timestamp(day).date()), "n_sessions": len(assigned), "dropped": dropped,
            "requested_kwh": float(sum(r.kwh_delivered for r, *_ in assigned))}
    return EventQueue(events), start, info

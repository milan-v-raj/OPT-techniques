"""Rebuild charging sessions from the static ACN-Data time-series snapshot.

The static snapshot ships an empty session JSON, so every session is reconstructed from its
per-session ``.csv.gz`` file:

* station id   -> file name prefix before the connection date (hardware id, e.g. ``2-39-123-23``)
* arrival      -> first timestamp in the file
* departure    -> first ``UNPLUGGED`` row (else the last row)
* kWh          -> max of the cumulative ``Energy Delivered (kWh)`` column
"""

import re
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from . import DATA_ROOT, RESULTS_DIR, TIMEZONE

FILE_RE = re.compile(r"^(?P<station>.+?)-(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>[\d-]+)\.csv\.gz$")

MIN_KWH = 0.1
MIN_DURATION_MIN = 5


def list_session_files(site_dir: Path, months: Iterable[str]) -> list:
    """Return session files in ``site_dir`` whose connection date falls in one of ``months`` ("YYYY-MM")."""
    months = tuple(months)
    files = []
    for f in site_dir.iterdir():
        m = FILE_RE.match(f.name)
        if m and m.group("date")[:7] in months:
            files.append(f)
    return sorted(files)


def parse_session_file(path: Path) -> Optional[dict]:
    """Summarise one time-series file into a session record (times are UTC)."""
    m = FILE_RE.match(path.name)
    df = pd.read_csv(path, compression="gzip", index_col=0)
    if df.empty:
        return None
    ts = pd.to_datetime(df.index, utc=True, format="ISO8601")
    unplugged = (df["Charging State"] == "UNPLUGGED").to_numpy()
    departure = ts[unplugged.argmax()] if unplugged.any() else ts[-1]
    return {
        "session_id": path.name[: -len(".csv.gz")],
        "station_id": m.group("station"),
        "connection_time": ts[0],
        "disconnect_time": departure,
        "kwh_delivered": float(df["Energy Delivered (kWh)"].max()),
        "peak_current": float(df["Charging Current (A)"].max()),
    }


def build_sessions(site_dir: Path, months: Iterable[str]) -> pd.DataFrame:
    """Parse all session files of ``months`` and return a cleaned session table in local time."""
    records = [r for r in map(parse_session_file, list_session_files(site_dir, months)) if r]
    df = pd.DataFrame.from_records(records)
    for col in ("connection_time", "disconnect_time"):
        df[col] = df[col].dt.tz_convert(TIMEZONE)
    duration = (df["disconnect_time"] - df["connection_time"]).dt.total_seconds() / 60
    df = df[(df["kwh_delivered"] >= MIN_KWH) & (duration >= MIN_DURATION_MIN)]
    return df.sort_values("connection_time").reset_index(drop=True)


def sessions_path(site: str, months: Iterable[str]) -> Path:
    tag = "_".join(months)
    return RESULTS_DIR / f"sessions_{site}_{tag}.parquet"


def load_sessions(
    months: Iterable[str] = ("2018-09",), site: str = "caltech/California_Garage_01", rebuild: bool = False
) -> pd.DataFrame:
    """Load the cached session table, building it from the raw files on first use."""
    months = tuple(months)
    out = sessions_path(site.replace("/", "_"), months)
    if out.exists() and not rebuild:
        return pd.read_parquet(out)
    df = build_sessions(DATA_ROOT / site, months)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    return df

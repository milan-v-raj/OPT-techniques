"""Build the cached session table from the static ACN-Data time series.

Usage: python scripts/build_sessions.py [--months 2018-09 2018-10] [--rebuild]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evsched.data import load_sessions  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", nargs="+", default=["2018-09"])
    parser.add_argument("--site", default="caltech/California_Garage_01")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    df = load_sessions(args.months, args.site, rebuild=args.rebuild)
    hours = (df["disconnect_time"] - df["connection_time"]).dt.total_seconds() / 3600
    print(f"{len(df)} sessions, {df['station_id'].nunique()} stations")
    print(f"kWh: mean {df['kwh_delivered'].mean():.2f}, median {df['kwh_delivered'].median():.2f}")
    print(f"stay (h): mean {hours.mean():.2f}, median {hours.median():.2f}")
    print(df.groupby(df["connection_time"].dt.date).size().to_string())

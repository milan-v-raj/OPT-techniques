"""Grid-aware smart EV charging scheduling on ACN-Sim (Optimization Techniques course project)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT.parent / "ACN-Data-Static-main" / "ACN-Data-Static-main" / "time series data"
RESULTS_DIR = PROJECT_ROOT / "results"
FIGURES_DIR = PROJECT_ROOT / "figures"

TIMEZONE = "America/Los_Angeles"
PERIOD = 5  # minutes per simulation step
VOLTAGE = 208  # volts, caltech_acn default
MAX_RATE = 32  # amps, BASIC EVSE max pilot
TARIFF = "sce_tou_ev_4_march_2019"

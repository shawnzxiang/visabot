"""Configuration: .env secrets, booking window, budgets, facility map."""
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BOT_DIR / ".env")

BASE = "https://ais.usvisa-info.com/en-ca/niv"

# AIS facility ids for the 7 Canadian posts (from kcajc/usvisa-ca, community-verified 94/95)
FACILITIES = {
    "Calgary": 89,
    "Halifax": 90,
    "Montreal": 91,
    "Ottawa": 92,
    "Quebec City": 93,
    "Toronto": 94,
    "Vancouver": 95,
}
FACILITY_BY_ID = {v: k for k, v in FACILITIES.items()}

# Tie-break order when two posts show the same earliest date.
# Calgary first: the DS-160 on file names Calgary, so no re-filing needed.
POST_PRIORITY = ["Calgary", "Vancouver", "Toronto", "Ottawa", "Montreal", "Quebec City", "Halifax"]

AIS_EMAIL = os.environ.get("AIS_EMAIL", "")
AIS_PASSWORD = os.environ.get("AIS_PASSWORD", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_TO = os.environ.get("ALERT_TO", GMAIL_USER)

EARLIEST_OFFSET_DAYS = int(os.environ.get("EARLIEST_OFFSET_DAYS", "5"))
LATEST_DATE = date.fromisoformat(os.environ.get("LATEST_DATE", "2026-08-21"))
TEST_MODE = os.environ.get("TEST_MODE", "true").strip().lower() not in ("false", "0", "no")

# Soft-ban budget: documented bans at ~50-110 cumulative requests/day, so stay well under.
DAILY_REQUEST_BUDGET = 40
BURST_RESERVE = 8            # routine polls stop when remaining budget < this
MAX_BOOK_ATTEMPTS_PER_DAY = 3
MAX_BOOK_ATTEMPTS_TOTAL = 6

# Duty cycle: (start_hour, end_hour, cadence_minutes); jitter +/-20% applied on top.
# Slot drops are folklore-reported to cluster evenings/late-night ET.
TIERS = [
    (17, 24, 20),   # HOT evening
    (0, 1, 20),     # HOT past midnight
    (7, 17, 60),    # DAY
    (1, 7, 180),    # NIGHT
]
JITTER_FRAC = 0.20

SOFTBAN_BACKOFF_HOURS = 8
MAINTENANCE_BACKOFF_MINUTES = 60
LOGIN_FAIL_BACKOFF_HOURS = 2

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

STATE_DIR = BOT_DIR / "state"
LOGS_DIR = BOT_DIR / "logs"
RECON_DIR = BOT_DIR / "recon"
DB_PATH = STATE_DIR / "bot.db"
LOCK_PATH = STATE_DIR / "bot.lock"


def window(today: date | None = None) -> tuple[date, date]:
    """Rolling booking window: [today + EARLIEST_OFFSET_DAYS, LATEST_DATE]."""
    today = today or date.today()
    return today + timedelta(days=EARLIEST_OFFSET_DAYS), LATEST_DATE


def window_open(today: date | None = None) -> bool:
    lo, hi = window(today)
    return lo <= hi


def in_window(d: date, today: date | None = None) -> bool:
    lo, hi = window(today)
    return lo <= d <= hi


def local_now() -> datetime:
    return datetime.now()

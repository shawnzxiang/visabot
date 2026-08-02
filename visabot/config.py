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

# The order posts are checked within each polling cycle. Calgary first: the
# DS-160 on file names Calgary, so booking there needs no re-filing.
POST_PRIORITY = ["Calgary", "Vancouver", "Toronto", "Ottawa", "Montreal", "Quebec City", "Halifax"]

AIS_EMAIL = os.environ.get("AIS_EMAIL", "")
AIS_PASSWORD = os.environ.get("AIS_PASSWORD", "")

# Primary notification channel: Pushover (https://pushover.net)
PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")

# Optional legacy secondary channel (only used if a real app password is set)
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_TO = os.environ.get("ALERT_TO", GMAIL_USER)

EARLIEST_OFFSET_DAYS = int(os.environ.get("EARLIEST_OFFSET_DAYS", "5"))
LATEST_DATE = date.fromisoformat(os.environ.get("LATEST_DATE", "2026-08-21"))
TEST_MODE = os.environ.get("TEST_MODE", "true").strip().lower() not in ("false", "0", "no")

# Polling cadence: every cycle checks ALL 7 posts back-to-back (2-5 s apart),
# then sleeps POLL_BASE_SECONDS + random(0..POLL_JITTER_SECONDS). At the default
# 3-5 min cycle that is ~360 cycles and ~2,500 days.json requests per day.
# NOTE: AIS rate-limits on cumulative request count per account and signals a
# soft-ban as HTTP 200 with an empty date list — indistinguishable from "no
# appointments". This bot does not attempt to detect that.
POLL_BASE_SECONDS = int(os.environ.get("POLL_BASE_SECONDS", "180"))
POLL_JITTER_SECONDS = int(os.environ.get("POLL_JITTER_SECONDS", "120"))
NIGHT_HOURS = (1, 6)         # 01:00-06:00 local
# Multiply the delay during NIGHT_HOURS; 1 = poll at full speed all night.
NIGHT_MULTIPLIER = float(os.environ.get("NIGHT_MULTIPLIER", "2"))

# NOT a rate limit — a runaway guard so a bug cannot hammer the site.
# Expected real usage is ~2,500-2,700 requests/day at this cadence.
DAILY_RUNAWAY_GUARD = int(os.environ.get("DAILY_RUNAWAY_GUARD", "3500"))
BURST_RESERVE = 8
MAX_BOOK_ATTEMPTS_PER_DAY = 3
MAX_BOOK_ATTEMPTS_TOTAL = 6

# Display order for --sweep, the digest and the dashboard.
ROTATION = ["Vancouver", "Calgary", "Halifax",
            "Ottawa", "Montreal", "Quebec City",
            "Toronto"]

MAINTENANCE_BACKOFF_MINUTES = 60
LOGIN_FAIL_BACKOFF_HOURS = 2

# Local web dashboard (loopback only)
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))

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

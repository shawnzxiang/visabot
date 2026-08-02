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
# Tie-break preference when two posts offer the same date (Calgary first: the
# DS-160 on file names Calgary, so booking there needs no re-filing).
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

# Polling cadence: kcajc/usvisa-ca's 180 s, plus 0-120 s jitter (it uses a fixed
# 180 s with no jitter) => 3-5 min, mean 4. ~15 checks/hour, ~360/day at full
# uptime — the same volume as that reference bot. AIS bans on cumulative count,
# so expect periodic soft-bans; the backoff ladder below absorbs them.
POLL_BASE_SECONDS = int(os.environ.get("POLL_BASE_SECONDS", "180"))
POLL_JITTER_SECONDS = int(os.environ.get("POLL_JITTER_SECONDS", "120"))
NIGHT_HOURS = (1, 6)         # 01:00-06:00 local
# Multiply the delay during NIGHT_HOURS; 1 = poll at full speed all night.
NIGHT_MULTIPLIER = float(os.environ.get("NIGHT_MULTIPLIER", "2"))

# NOT a rate limit — a runaway guard so a bug cannot hammer the site.
# Expected real usage is ~250-360 requests/day at this cadence.
DAILY_RUNAWAY_GUARD = int(os.environ.get("DAILY_RUNAWAY_GUARD", "800"))
BURST_RESERVE = 8
MAX_BOOK_ATTEMPTS_PER_DAY = 3
MAX_BOOK_ATTEMPTS_TOTAL = 6

# Soft-ban backoff ladder (seconds). Documented duration is ~5 h (range 3-24 h) but
# it sometimes lifts sooner, so probe early and escalate instead of sleeping blind.
# The streak resets the moment dates flow again.
BAN_BACKOFF_LADDER = [30 * 60, 60 * 60, 2 * 3600, 4 * 3600, 5 * 3600]

# A consulate whose calendar is normally well populated doubles as a ban canary:
# a post showing 100+ open days cannot organically drop to zero between two polls,
# so an empty list from it means we are soft-banned. Canaries sit in the normal
# rotation, so this detection costs no extra requests.
CANARY_MIN_DATES = 10

# Rotation interleaves likely-populated posts with the always-empty ones so a ban
# surfaces within a poll or two instead of a full lap.
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

"""SQLite state: request budget, availability history, events, attempts, kv, instance lock."""
import fcntl
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    ts TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE TABLE IF NOT EXISTS availability (
    ts TEXT NOT NULL,
    post TEXT NOT NULL,
    earliest_date TEXT
);
CREATE TABLE IF NOT EXISTS events (
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    code TEXT NOT NULL,
    msg TEXT
);
CREATE TABLE IF NOT EXISTS booking_attempts (
    ts TEXT NOT NULL,
    post TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT,
    outcome TEXT NOT NULL,
    evidence_path TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, db_path: Path = config.DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _midnight() -> str:
        return date.today().isoformat() + "T00:00:00"

    # --- request budget ---

    def record_request(self, kind: str) -> None:
        self.conn.execute("INSERT INTO requests VALUES (?, ?)", (self._now(), kind))
        self.conn.commit()

    def requests_today(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE ts >= ?", (self._midnight(),)
        ).fetchone()
        return row[0]

    def budget_remaining(self) -> int:
        return max(0, config.DAILY_RUNAWAY_GUARD - self.requests_today())

    def record_date_count(self, post: str, n: int) -> None:
        """Remember the most dates a post has ever shown, so it can act as a canary."""
        if n > self.peak_dates(post):
            self.kv_set(f"peak_dates_{post}", str(n))

    def peak_dates(self, post: str) -> int:
        try:
            return int(self.kv_get(f"peak_dates_{post}", "0"))
        except (TypeError, ValueError):
            return 0

    def ban_streak(self) -> int:
        try:
            return int(self.kv_get("ban_streak", "0"))
        except (TypeError, ValueError):
            return 0

    def set_ban_streak(self, n: int) -> None:
        self.kv_set("ban_streak", str(n))

    # --- availability history ---

    def record_availability(self, avail: dict) -> None:
        ts = self._now()
        self.conn.executemany(
            "INSERT INTO availability VALUES (?, ?, ?)",
            [(ts, post, d.isoformat() if d else None) for post, d in avail.items()],
        )
        self.conn.commit()

    def availability_since(self, since_iso: str):
        return self.conn.execute(
            "SELECT ts, post, earliest_date FROM availability WHERE ts >= ? ORDER BY ts",
            (since_iso,),
        ).fetchall()

    def last_seen_by_post(self):
        """Most recent non-null earliest_date ever observed, per post."""
        return self.conn.execute(
            "SELECT post, MAX(ts), earliest_date FROM availability "
            "WHERE earliest_date IS NOT NULL GROUP BY post"
        ).fetchall()

    # --- events ---

    def event(self, level: str, code: str, msg: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)", (self._now(), level, code, msg)
        )
        self.conn.commit()
        log.log(
            logging.WARNING if level in ("warn", "error") else logging.INFO,
            "%s: %s", code, msg,
        )

    def events_since(self, since_iso: str):
        return self.conn.execute(
            "SELECT ts, level, code, msg FROM events WHERE ts >= ? ORDER BY ts",
            (since_iso,),
        ).fetchall()

    # --- booking attempts ---

    def record_attempt(self, post: str, date_str: str, time_str: str | None,
                       outcome: str, evidence_path: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO booking_attempts VALUES (?, ?, ?, ?, ?, ?)",
            (self._now(), post, date_str, time_str, outcome, evidence_path),
        )
        self.conn.commit()

    def attempts_today(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM booking_attempts WHERE ts >= ? AND outcome != 'test_mode'",
            (self._midnight(),),
        ).fetchone()
        return row[0]

    def attempts_total(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM booking_attempts WHERE outcome != 'test_mode'"
        ).fetchone()
        return row[0]

    def attempts_since(self, since_iso: str):
        return self.conn.execute(
            "SELECT ts, post, date, time, outcome FROM booking_attempts WHERE ts >= ? ORDER BY ts",
            (since_iso,),
        ).fetchall()

    # --- kv ---

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def booked(self) -> bool:
        return self.kv_get("booked") == "1"

    def backoff_until(self) -> datetime | None:
        v = self.kv_get("backoff_until")
        if not v:
            return None
        until = datetime.fromisoformat(v)
        return until if until > datetime.now() else None

    def set_backoff(self, until: datetime, reason: str) -> None:
        self.kv_set("backoff_until", until.isoformat(timespec="seconds"))
        self.event("warn", "backoff", f"until {until:%Y-%m-%d %H:%M} ({reason})")


_lock_file = None  # keep a global reference so the fd lives as long as the process


def acquire_lock() -> bool:
    """Single-instance lock; returns False if another instance holds it."""
    global _lock_file
    config.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_file = open(config.LOCK_PATH, "w")
    try:
        fcntl.lockf(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False

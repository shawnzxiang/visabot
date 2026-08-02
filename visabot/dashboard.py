"""Local web dashboard: polling history, events, and .env editing.

Serves http://127.0.0.1:<DASHBOARD_PORT> bound to loopback only. Stdlib only.

Secrets are masked in API responses unless ?reveal=1 is passed. Reveal and all
mutating requests must carry the "X-Visabot: 1" header, which a cross-site page
cannot attach without a CORS preflight this server never approves; together with
the Host allowlist that keeps CSRF / DNS-rebinding pages away from credentials.

Reads .env and the SQLite DB fresh on every request, so it never needs a restart
to show current state. The poller, however, loads .env once at startup — config
saves here therefore offer to kickstart it.
"""
import json
import logging
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config

log = logging.getLogger(__name__)

ENV_PATH = config.BOT_DIR / ".env"
HTML_PATH = Path(__file__).parent / "static" / "dashboard.html"
POLLER_LABEL = "com.shawn.visabot.poller"
MASK = "••••••••"

# Order defines the config form layout. Any extra keys found in .env are
# appended so nothing becomes uneditable.
EDITABLE_KEYS = [
    "AIS_EMAIL", "AIS_PASSWORD",
    "PUSHOVER_USER_KEY", "PUSHOVER_APP_TOKEN",
    "EARLIEST_OFFSET_DAYS", "LATEST_DATE", "TEST_MODE",
    "POLL_BASE_SECONDS", "POLL_JITTER_SECONDS", "NIGHT_MULTIPLIER",
    "DAILY_RUNAWAY_GUARD", "DASHBOARD_PORT",
]
_SECRET_RE = re.compile(r"(PASSWORD|TOKEN|SECRET|_KEY)$")


def is_secret(key: str) -> bool:
    return bool(_SECRET_RE.search(key))


# --- .env read/write (comment- and order-preserving) ---

def read_env(path: Path | None = None) -> dict:
    path = path or ENV_PATH  # resolved at call time so tests can repoint ENV_PATH
    vals = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def write_env(updates: dict, path: Path | None = None) -> None:
    """Replace KEY=... lines in place, append new keys, keep comments."""
    path = path or ENV_PATH
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n")
    os.chmod(path, 0o600)


def validate(key: str, value: str) -> str | None:
    """-> error message, or None if the value is acceptable."""
    v = value.strip()
    try:
        if key in ("EARLIEST_OFFSET_DAYS",):
            assert 0 <= int(v) <= 365, "must be 0-365"
        elif key == "POLL_BASE_SECONDS":
            assert int(v) >= 60, "must be >= 60 (faster polling gets the account banned)"
        elif key == "POLL_JITTER_SECONDS":
            assert 0 <= int(v) <= 3600, "must be 0-3600"
        elif key == "NIGHT_MULTIPLIER":
            assert 1.0 <= float(v) <= 10.0, "must be 1-10 (1 = no overnight easing)"
        elif key == "DAILY_RUNAWAY_GUARD":
            assert 50 <= int(v) <= 5000, "must be 50-5000"
        elif key == "DASHBOARD_PORT":
            assert 1024 <= int(v) <= 65535, "must be 1024-65535"
        elif key == "LATEST_DATE":
            date.fromisoformat(v)
        elif key == "TEST_MODE":
            assert v.lower() in ("true", "false", "1", "0", "yes", "no"), "true or false"
        elif key == "AIS_EMAIL":
            assert "@" in v, "not an email address"
        elif is_secret(key):
            assert v, "cannot be empty"
    except AssertionError as e:
        return str(e)
    except ValueError:
        return "not a valid number/date"
    return None


def masked_config(reveal: bool = False) -> list:
    vals = read_env()
    keys = EDITABLE_KEYS + [k for k in vals if k not in EDITABLE_KEYS]
    out = []
    for k in keys:
        v = vals.get(k, "")
        secret = is_secret(k)
        out.append({
            "key": k,
            "secret": secret,
            "present": bool(v),
            "value": (MASK if (secret and v and not reveal) else v),
        })
    return out


# --- DB reads (read-only connection per request; WAL allows concurrent reads) ---

def _db():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=5)


def _kv(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def overview() -> dict:
    env = read_env()
    offset = int(env.get("EARLIEST_OFFSET_DAYS", "5") or 5)
    latest = env.get("LATEST_DATE", "2026-08-21")
    guard = int(env.get("DAILY_RUNAWAY_GUARD", str(config.DAILY_RUNAWAY_GUARD)) or 800)
    lo = (date.today() + timedelta(days=offset)).isoformat()
    o = {
        "now": datetime.now().isoformat(timespec="seconds"),
        "poller": poller_status(),
        "test_mode": env.get("TEST_MODE", "true").lower() not in ("false", "0", "no"),
        "window": [lo, latest],
        "cadence": {
            "base": int(env.get("POLL_BASE_SECONDS", "180") or 180),
            "jitter": int(env.get("POLL_JITTER_SECONDS", "120") or 120),
            "night_multiplier": float(env.get("NIGHT_MULTIPLIER", "2") or 2),
        },
        "budget": {"used": 0, "limit": guard},
        "booked": False, "backoff_until": None,
        "last_poll": None, "attempts_today": 0, "latest": [],
    }
    try:
        conn = _db()
    except sqlite3.OperationalError:
        return o
    # NB: sqlite's own context manager does NOT close the connection — without
    # closing() every request leaks an fd until the process hits EMFILE.
    with closing(conn):
        midnight = date.today().isoformat() + "T00:00:00"
        o["budget"]["used"] = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE ts >= ?", (midnight,)).fetchone()[0]
        o["attempts_today"] = conn.execute(
            "SELECT COUNT(*) FROM booking_attempts WHERE ts >= ? AND outcome != 'test_mode'",
            (midnight,)).fetchone()[0]
        o["booked"] = _kv(conn, "booked") == "1"
        o["last_poll"] = _kv(conn, "last_poll_ts")
        b = _kv(conn, "backoff_until")
        if b and datetime.fromisoformat(b) > datetime.now():
            o["backoff_until"] = b
        # bare ts comes from the row that produced MAX(ts) — SQLite guarantees it
        rows = conn.execute(
            "SELECT post, earliest_date, MAX(ts) FROM availability GROUP BY post"
        ).fetchall()
        by_post = {r[0]: r for r in rows}
        for post in config.ROTATION:
            r = by_post.get(post)
            d = r[1] if r else None
            o["latest"].append({
                "post": post, "earliest": d, "ts": r[2] if r else None,
                "in_window": bool(d and lo <= d <= latest),
            })
    return o


def history(hours: int, post: str | None) -> list:
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    q = "SELECT ts, post, earliest_date FROM availability WHERE ts >= ?"
    args = [since]
    if post and post != "All":
        q += " AND post = ?"
        args.append(post)
    q += " ORDER BY ts DESC LIMIT 2000"
    try:
        with closing(_db()) as conn:
            return [list(r) for r in conn.execute(q, args).fetchall()]
    except sqlite3.OperationalError as e:
        log.warning("history query failed: %s", e)
        return []


def events(hours: int) -> list:
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        with closing(_db()) as conn:
            return [list(r) for r in conn.execute(
                "SELECT ts, level, code, msg FROM events WHERE ts >= ? "
                "ORDER BY ts DESC LIMIT 500", (since,)).fetchall()]
    except sqlite3.OperationalError as e:
        log.warning("events query failed: %s", e)
        return []


# --- poller control ---

def poller_status() -> dict:
    try:
        r = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{POLLER_LABEL}"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {"loaded": False, "running": False, "pid": None}
    if r.returncode != 0:
        return {"loaded": False, "running": False, "pid": None}
    pid = re.search(r"pid = (\d+)", r.stdout)
    return {"loaded": True, "running": bool(pid), "pid": int(pid.group(1)) if pid else None}


def restart_poller() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{POLLER_LABEL}"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr.strip() or "ok")


# --- HTTP ---

_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "visabot-dashboard"

    def log_message(self, fmt, *args):
        log.debug("dash: " + fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _guard(self, need_header: bool) -> bool:
        if not _HOST_RE.match(self.headers.get("Host", "")):
            self._json({"error": "bad host"}, 403)
            return False
        if need_header and self.headers.get("X-Visabot") != "1":
            self._json({"error": "missing X-Visabot header"}, 403)
            return False
        return True

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        reveal = q.get("reveal", ["0"])[0] == "1"
        if not self._guard(need_header=reveal):
            return
        try:
            if url.path == "/":
                self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/overview":
                self._json(overview())
            elif url.path == "/api/history":
                self._json({"rows": history(int(q.get("hours", ["24"])[0]),
                                            q.get("post", [None])[0])})
            elif url.path == "/api/events":
                self._json({"rows": events(int(q.get("hours", ["72"])[0]))})
            elif url.path == "/api/config":
                self._json({"keys": masked_config(reveal=reveal)})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001 - keep the server alive
            log.exception("dashboard GET %s failed", self.path)
            self._json({"error": repr(e)}, 500)

    def do_POST(self):
        if not self._guard(need_header=True):
            return
        url = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 65536:
                self._json({"error": "body too large"}, 413)
                return
            body = json.loads(self.rfile.read(n) or b"{}")

            if url.path == "/api/config":
                updates = {str(k): str(v).strip()
                           for k, v in (body.get("updates") or {}).items()}
                if not updates:
                    self._json({"error": "no updates"}, 400)
                    return
                errors = {}
                for k, v in updates.items():
                    err = validate(k, v)
                    if err:
                        errors[k] = err
                if errors:
                    self._json({"errors": errors}, 400)
                    return
                write_env(updates)
                restarted, detail = (False, "not requested")
                if body.get("restart", True):
                    restarted, detail = restart_poller()
                self._json({"ok": True, "saved": sorted(updates),
                            "restarted": restarted, "detail": detail})
            elif url.path == "/api/restart":
                ok, detail = restart_poller()
                self._json({"ok": ok, "detail": detail})
            else:
                self._json({"error": "not found"}, 404)
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception("dashboard POST %s failed", self.path)
            self._json({"error": repr(e)}, 500)


def run(port: int | None = None) -> None:
    port = port or int(read_env().get("DASHBOARD_PORT", str(config.DASHBOARD_PORT)))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log.info("dashboard listening on http://127.0.0.1:%d", port)
    httpd.serve_forever()

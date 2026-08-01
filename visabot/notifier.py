"""Pushover push notifications (+ optional Gmail secondary).

Never raises out of send_alert — a notification failure must not stop the bot.
Priorities: emergency=True -> Pushover priority 2 (server re-alerts every 30 s
until acknowledged on the phone); urgent=True -> 1 (bypasses quiet hours); else 0.
"""
import hashlib
import logging
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import requests

from . import config
from .store import Store

log = logging.getLogger(__name__)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
MAX_MESSAGE = 1024   # Pushover hard cap per message
CHUNK_TARGET = 1000  # leave headroom for the "(i/n) " prefix
RATE_LIMIT = timedelta(hours=1)  # per identical subject, non-urgent only


def chunk_body(body: str, limit: int = CHUNK_TARGET) -> list[str]:
    """Split on line boundaries into <=limit-char parts (hard-split oversize lines)."""
    if len(body) <= limit:
        return [body]
    parts, cur = [], ""
    for line in body.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur += line
    if cur:
        parts.append(cur)
    return [p.rstrip("\n") or " " for p in parts]


def pushover_params(title: str, message: str, priority: int) -> dict:
    params = {
        "token": config.PUSHOVER_APP_TOKEN,
        "user": config.PUSHOVER_USER_KEY,
        "title": title[:250],
        "message": message,
        "priority": str(priority),
    }
    if priority == 2:
        # Emergency: Pushover's servers re-alert every 30 s for up to an hour
        # until the user acknowledges — survives this machine dying mid-alert.
        params["retry"] = "30"
        params["expire"] = "3600"
    return params


def _send_pushover(title: str, body: str, priority: int) -> None:
    parts = chunk_body(body)
    n = len(parts)
    for i, part in enumerate(parts, 1):
        message = part if n == 1 else f"({i}/{n}) {part}"
        # Only the first part of an emergency needs to ring through; the rest
        # would each demand their own acknowledgement, which is just noise.
        part_priority = priority if i == 1 else min(priority, 1)
        resp = requests.post(
            PUSHOVER_URL, data=pushover_params(title, message, part_priority), timeout=30
        )
        resp.raise_for_status()
        if resp.json().get("status") != 1:
            raise RuntimeError(f"pushover rejected message: {resp.text[:200]}")


def _gmail_configured() -> bool:
    return bool(
        config.GMAIL_USER
        and config.GMAIL_APP_PASSWORD
        and not config.GMAIL_APP_PASSWORD.startswith("xxxx")
    )


def _pushover_configured() -> bool:
    return bool(
        config.PUSHOVER_USER_KEY
        and config.PUSHOVER_APP_TOKEN
        and not config.PUSHOVER_USER_KEY.startswith("xxxx")
        and not config.PUSHOVER_APP_TOKEN.startswith("xxxx")
    )


def _send_gmail(subject: str, body: str, attachments: list[Path] | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = config.GMAIL_USER
    msg["To"] = config.ALERT_TO or config.GMAIL_USER
    msg["Subject"] = "[visabot] " + subject
    msg.set_content(body)
    for path in attachments or []:
        try:
            msg.add_attachment(Path(path).read_bytes(), maintype="application",
                               subtype="octet-stream", filename=Path(path).name)
        except OSError:
            log.warning("could not attach %s", path)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def send_test(store: Store) -> None:
    """Used by --test-notify; lets errors propagate so setup problems are visible."""
    if not _pushover_configured():
        raise RuntimeError("PUSHOVER_USER_KEY / PUSHOVER_APP_TOKEN not set in bot/.env")
    _send_pushover(
        "Test — visa bot can reach you",
        f"Sent {datetime.now():%Y-%m-%d %H:%M:%S} from the Mac mini.\n"
        "Tip: also enable Critical Alerts + Time Sensitive for Pushover in iOS Settings.",
        priority=0,
    )
    store.event("info", "notify_test", "pushover test sent")


def send_alert(store: Store, subject: str, body: str,
               attachments: list[Path] | None = None,
               urgent: bool = False, emergency: bool = False) -> None:
    """Rate-limited alert. urgent/emergency bypass the identical-subject rate limit."""
    key = "alert_" + hashlib.sha1(subject.encode()).hexdigest()[:12]
    if not (urgent or emergency):
        last = store.kv_get(key)
        if last and datetime.now() - datetime.fromisoformat(last) < RATE_LIMIT:
            log.info("alert suppressed (rate limit): %s", subject)
            return

    priority = 2 if emergency else 1 if urgent else 0
    sent = False
    if _pushover_configured():
        try:
            _send_pushover(subject, body, priority)
            sent = True
        except Exception as exc:  # noqa: BLE001
            store.event("error", "pushover_fail", f"{subject}: {exc}")
    if _gmail_configured():
        try:
            _send_gmail(subject, body, attachments)
            sent = True
        except Exception as exc:  # noqa: BLE001
            store.event("error", "email_fail", f"{subject}: {exc}")

    if sent:
        store.kv_set(key, datetime.now().isoformat(timespec="seconds"))
        store.event("info", "notify_sent", f"[p{priority}] {subject}")
    else:
        store.event("error", "notify_fail", f"no channel delivered: {subject}")

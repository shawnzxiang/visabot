"""Gmail alerts. Never raises out of send_alert (a mail failure must not kill the poller)."""
import hashlib
import logging
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from . import config
from .store import Store

log = logging.getLogger(__name__)

SUBJECT_PREFIX = "[visabot] "
RATE_LIMIT = timedelta(hours=1)  # per identical subject


def _send(subject: str, body: str, attachments: list[Path] | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = config.GMAIL_USER
    msg["To"] = config.ALERT_TO
    msg["Subject"] = SUBJECT_PREFIX + subject
    msg.set_content(body)
    for path in attachments or []:
        try:
            data = Path(path).read_bytes()
            msg.add_attachment(data, maintype="application", subtype="octet-stream",
                               filename=Path(path).name)
        except OSError:
            log.warning("could not attach %s", path)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def send_test(store: Store) -> None:
    """Used by --test-email; lets SMTP errors propagate so setup problems are visible."""
    _send("Test email — setup OK",
          "If you can read this, the visa bot can reach you.\n"
          f"Sent {datetime.now():%Y-%m-%d %H:%M:%S} from the Mac mini.")
    store.event("info", "email_test", "test email sent")


def send_alert(store: Store, subject: str, body: str,
               attachments: list[Path] | None = None, urgent: bool = False) -> None:
    """Rate-limited alert. urgent=True bypasses the identical-subject rate limit."""
    key = "alert_" + hashlib.sha1(subject.encode()).hexdigest()[:12]
    if not urgent:
        last = store.kv_get(key)
        if last and datetime.now() - datetime.fromisoformat(last) < RATE_LIMIT:
            log.info("alert suppressed (rate limit): %s", subject)
            return
    try:
        _send(subject, body, attachments)
        store.kv_set(key, datetime.now().isoformat(timespec="seconds"))
        store.event("info", "email_sent", subject)
    except Exception as exc:  # noqa: BLE001 - mail failure must never stop the bot
        store.event("error", "email_fail", f"{subject}: {exc}")

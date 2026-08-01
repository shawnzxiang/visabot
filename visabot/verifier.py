"""Anti-ghost-slot verification.

Every reference bot has a false-success bug (kcajc #43/#45, jeangnc #37/#48).
A booking counts only if BOTH hold:
1. the POST response contains 'Successfully Scheduled' (allowlist marker confirmed
   in two unrelated codebases), and
2. a fresh dashboard read shows the booked date.
"""
import json
import logging
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .client import AisClient
from .store import Store

log = logging.getLogger(__name__)

SUCCESS_RE = re.compile(r"successfully\s+scheduled", re.I)


def _date_variants(d: date) -> list[str]:
    out = []
    for fmt in ("%Y-%m-%d", "%d %B, %Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%d-%b-%Y"):
        s = d.strftime(fmt)
        out.append(s)
        out.append(s.lstrip("0").replace(" 0", " "))  # non-padded day variants
    return list(dict.fromkeys(out))


def _flash_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts = [el.get_text(" ", strip=True)
             for sel in (".flash", ".alert", "[role=alert]", ".error")
             for el in soup.select(sel)]
    return " | ".join(p for p in parts if p)[:500]


def verify(client: AisClient, store: Store, post: str, date_str: str,
           time_str: str, post_response) -> tuple[bool, str, str]:
    """-> (confirmed, evidence_path, detail)."""
    body = post_response.text
    evidence = client.save_debug("booking_response", body)
    marker_ok = post_response.ok and bool(SUCCESS_RE.search(body))

    d = date.fromisoformat(date_str)
    account_html = client.get_account_page()
    account_evidence = client.save_debug("post_booking_account", account_html)
    page_text = re.sub(r"\s+", " ", BeautifulSoup(account_html, "html.parser").get_text(" "))
    state_ok = any(v in page_text for v in _date_variants(d))

    confirmed = marker_ok and state_ok
    detail = (f"marker={'yes' if marker_ok else 'NO'} (HTTP {post_response.status_code}), "
              f"account shows date={'yes' if state_ok else 'NO'}")
    flash = _flash_text(body)
    if flash:
        detail += f" | page said: {flash}"

    if confirmed:
        store.kv_set("booked", "1")
        store.kv_set("booked_details", json.dumps({
            "post": post, "date": date_str, "time": time_str,
            "at": datetime.now().isoformat(timespec="seconds"),
        }))
        store.record_attempt(post, date_str, time_str, "confirmed", account_evidence)
    else:
        outcome = "ghost" if marker_ok else "failed"
        store.record_attempt(post, date_str, time_str, outcome, evidence)
    return confirmed, evidence, detail

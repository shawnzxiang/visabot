"""Parse the payment page (all 7 posts' earliest dates in one request) and pick a candidate."""
import logging
import re
import unicodedata
from datetime import date, datetime

from bs4 import BeautifulSoup

from . import config

log = logging.getLogger(__name__)

# Formats seen on AIS pages; locked down further once recon captures a real fixture.
DATE_FORMATS = ("%d %B, %Y", "%d %B %Y", "%B %d, %Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y")

NO_APPT_RE = re.compile(r"no\s+appointments?\s+available", re.I)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip()


def parse_date_cell(text: str) -> date | None:
    text = _norm(text)
    if not text or NO_APPT_RE.search(text):
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def match_post(text: str) -> str | None:
    low = _norm(text).lower()
    for post in config.FACILITIES:
        if post.lower() in low:
            return post
    if "quebec" in low:  # accent already stripped by _norm
        return "Quebec City"
    return None


def parse_payment_page(html: str) -> dict:
    """-> {post: earliest date | None} for every post found on the page."""
    soup = BeautifulSoup(html, "html.parser")
    avail: dict[str, date | None] = {}
    unparsed: list[str] = []

    # Primary shape: table rows of (location, date). Fallback: any flat td sequence
    # where a post-name cell is followed by a date cell (kcajc's legacy parser shape).
    cells = [td.get_text(" ", strip=True) for td in soup.find_all("td")]
    for i, text in enumerate(cells):
        post = match_post(text)
        if not post or post in avail and avail[post] is not None:
            continue
        nxt = cells[i + 1] if i + 1 < len(cells) else ""
        d = parse_date_cell(nxt)
        if d is None and nxt and not NO_APPT_RE.search(nxt):
            unparsed.append(f"{post!r}: {nxt!r}")
        avail[post] = d

    if unparsed:
        log.warning("payment page cells I could not parse as dates: %s", "; ".join(unparsed))
    return avail


def pick_candidate(avail: dict, today: date | None = None) -> tuple[str, date] | None:
    """Earliest in-window date wins; POST_PRIORITY breaks ties. None if nothing qualifies."""
    candidates = [
        (d, config.POST_PRIORITY.index(post) if post in config.POST_PRIORITY else 99, post)
        for post, d in avail.items()
        if d is not None and config.in_window(d, today)
    ]
    if not candidates:
        return None
    d, _, post = min(candidates)
    return post, d

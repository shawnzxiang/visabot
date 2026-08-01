"""Slot decision logic.

Note: AIS has no aggregate "earliest date at every post" endpoint for an account
that has paid but not yet booked — /schedule/{id}/payment just 302s to the booking
form. So availability is read one consulate at a time from days.json, and the
poller round-robins through the posts to spread a fixed request budget over them.
"""
import logging
from datetime import date

from . import config

log = logging.getLogger(__name__)


def parse_days(day_strings: list[str]) -> list[date]:
    out = []
    for s in day_strings:
        try:
            out.append(date.fromisoformat(s))
        except (ValueError, TypeError):
            log.warning("unparseable date from days.json: %r", s)
    return sorted(out)


def earliest_in_window(dates: list[date], today: date | None = None) -> date | None:
    """Earliest date that falls inside the rolling booking window, else None."""
    usable = [d for d in dates if config.in_window(d, today)]
    return min(usable) if usable else None


def next_post(store) -> str:
    """Round-robin over config.ROTATION, persisted across restarts."""
    order = config.ROTATION
    try:
        idx = int(store.kv_get("rotation_index", "0"))
    except ValueError:
        idx = 0
    post = order[idx % len(order)]
    store.kv_set("rotation_index", str((idx + 1) % len(order)))
    return post


def is_canary(store, post: str) -> bool:
    """True if this post has held enough dates that an empty reply is suspicious."""
    return store.peak_dates(post) >= config.CANARY_MIN_DATES


def ban_suspected(store, post: str, n_dates: int) -> bool:
    """A well-populated consulate returning nothing means we are soft-banned.

    AIS answers a soft-ban with HTTP 200 and an empty array — byte-identical to
    genuine emptiness. The tell is *which* post went empty: Vancouver holding 139
    open days cannot organically drop to zero between two polls, whereas Calgary
    is empty all the time.
    """
    return n_dates == 0 and is_canary(store, post)

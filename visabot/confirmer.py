"""Burst confirmation: days.json is authoritative; detect the soft-ban contradiction."""
import logging
from datetime import date

from . import config
from .client import AisClient

log = logging.getLogger(__name__)


class SoftBanSuspected(Exception):
    """days.json came back empty seconds after the payment page showed a date."""


def confirm(client: AisClient, post: str, today: date | None = None) -> tuple[str, str] | None:
    """-> (date_str, time_str) ready to book, or None if the slot is gone/out of window."""
    facility_id = config.FACILITIES[post]
    days = client.get_days(facility_id)
    if not days:
        # The payment page just advertised a date for this post; an empty list here is
        # the documented soft-ban signature, not "no availability".
        raise SoftBanSuspected(f"days.json empty for {post} right after payment page showed a date")

    in_window = sorted(d for d in days if config.in_window(date.fromisoformat(d), today))
    if not in_window:
        log.info("%s: days.json has %d dates but none in window (earliest %s)",
                 post, len(days), min(days))
        return None

    date_str = in_window[0]
    times = client.get_times(facility_id, date_str)
    if not times:
        log.info("%s %s: no times left (sniped)", post, date_str)
        return None
    return date_str, times[0]

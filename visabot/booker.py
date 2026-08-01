"""Booking POST with double-validation and the TEST_MODE gate."""
import logging
from datetime import date

from . import config
from .client import AisClient
from .store import Store

log = logging.getLogger(__name__)


class ValidationFailed(Exception):
    """A pre-submit sanity check failed; nothing was posted."""


def book(client: AisClient, store: Store, post: str, date_str: str, time_str: str):
    """Returns ('test_mode', evidence) | ('posted', response). Raises ValidationFailed."""
    if store.booked():
        raise ValidationFailed("kv.booked is already set")
    if store.attempts_today() >= config.MAX_BOOK_ATTEMPTS_PER_DAY:
        raise ValidationFailed("MAX_BOOK_ATTEMPTS_PER_DAY reached")
    if store.attempts_total() >= config.MAX_BOOK_ATTEMPTS_TOTAL:
        raise ValidationFailed("MAX_BOOK_ATTEMPTS_TOTAL reached")

    # Re-validate the window from scratch immediately before submitting.
    d = date.fromisoformat(date_str)
    if not config.in_window(d):
        raise ValidationFailed(f"{date_str} is outside the window {config.window()}")
    if post not in config.FACILITIES:
        raise ValidationFailed(f"unknown post {post!r}")
    facility_id = config.FACILITIES[post]

    # Fresh GET right before POST: Rails rotates the CSRF token and _yatri_session
    # together, so tokens captured earlier in the session are unusable.
    html = client.get_appointment_page()
    evidence = client.save_debug("pre_booking_appointment_page", html)
    form = client.parse_booking_form(html)

    if config.TEST_MODE:
        store.record_attempt(post, date_str, time_str, "test_mode", evidence)
        log.info("TEST_MODE: would POST %s %s %s (facility %s) with form keys %s",
                 post, date_str, time_str, facility_id, sorted(form))
        return "test_mode", evidence

    resp = client.post_booking(facility_id, date_str, time_str, form)
    return "posted", resp

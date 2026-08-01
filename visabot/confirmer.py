"""Second half of a booking burst: turn a candidate date into a bookable time slot."""
import logging

from . import config
from .client import AisClient

log = logging.getLogger(__name__)


def pick_time(client: AisClient, post: str, date_str: str) -> str | None:
    """-> a bookable time on date_str at post, or None if the slot is already gone."""
    times = client.get_times(config.FACILITIES[post], date_str)
    if not times:
        log.info("%s %s: no times returned (slot taken between our two requests)",
                 post, date_str)
        return None
    return times[0]

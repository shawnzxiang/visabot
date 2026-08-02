from datetime import date

import pytest

from visabot import config, detector
from visabot.store import Store


@pytest.fixture(autouse=True)
def pinned_window(monkeypatch):
    """Pin the window these tests assume — config reads the live .env at
    import, so the suite must not depend on the operator's settings."""
    monkeypatch.setattr(config, "EARLIEST_OFFSET_DAYS", 5)
    monkeypatch.setattr(config, "LATEST_DATE", date(2026, 8, 21))


def test_parse_days_skips_garbage():
    assert detector.parse_days(["2026-08-12", "not-a-date", "2026-08-05"]) == [
        date(2026, 8, 5), date(2026, 8, 12)
    ]


def test_earliest_in_window_picks_first_eligible():
    dates = [date(2026, 8, 3), date(2026, 8, 12), date(2026, 8, 19)]
    # window on 2026-08-01 is [08-06 .. 08-21]; 08-03 is too soon
    assert detector.earliest_in_window(dates, date(2026, 8, 1)) == date(2026, 8, 12)


def test_earliest_in_window_rejects_too_late():
    assert detector.earliest_in_window([date(2026, 9, 3)], date(2026, 8, 1)) is None


def test_earliest_in_window_respects_rolling_start():
    dates = [date(2026, 8, 12)]
    assert detector.earliest_in_window(dates, date(2026, 8, 1)) == date(2026, 8, 12)
    # by Aug 8 the window starts Aug 13, so the same date no longer qualifies
    assert detector.earliest_in_window(dates, date(2026, 8, 8)) is None


def test_rotation_cycles_all_posts_then_wraps(tmp_path):
    s = Store(tmp_path / "rot.db")
    seen = [detector.next_post(s) for _ in range(len(config.ROTATION))]
    assert seen == config.ROTATION          # every post covered exactly once
    assert detector.next_post(s) == config.ROTATION[0]   # wraps around


def test_rotation_survives_restart(tmp_path):
    db = tmp_path / "rot2.db"
    s1 = Store(db)
    detector.next_post(s1)
    first_after = detector.next_post(s1)
    s2 = Store(db)  # simulate a restart
    assert detector.next_post(s2) == config.ROTATION[2]
    assert first_after == config.ROTATION[1]

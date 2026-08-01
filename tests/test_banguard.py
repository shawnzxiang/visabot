"""Soft-ban detection via the canary trick, plus cadence sanity.

AIS answers a soft-ban with HTTP 200 and an empty array — identical to genuine
emptiness. The only way to tell them apart is which consulate went quiet.
"""
from datetime import datetime

from visabot import config, detector, scheduler
from visabot.store import Store


def test_populated_post_going_empty_is_a_ban(tmp_path):
    s = Store(tmp_path / "b.db")
    s.record_date_count("Vancouver", 139)
    assert detector.is_canary(s, "Vancouver")
    assert detector.ban_suspected(s, "Vancouver", 0)


def test_always_empty_post_is_not_a_ban_signal(tmp_path):
    s = Store(tmp_path / "b.db")
    s.record_date_count("Calgary", 0)
    assert not detector.is_canary(s, "Calgary")
    assert not detector.ban_suspected(s, "Calgary", 0)


def test_post_with_few_dates_is_not_trusted_as_canary(tmp_path):
    s = Store(tmp_path / "b.db")
    s.record_date_count("Halifax", config.CANARY_MIN_DATES - 1)
    assert not detector.ban_suspected(s, "Halifax", 0)


def test_dates_still_flowing_is_never_a_ban(tmp_path):
    s = Store(tmp_path / "b.db")
    s.record_date_count("Vancouver", 139)
    assert not detector.ban_suspected(s, "Vancouver", 5)


def test_peak_is_a_high_water_mark(tmp_path):
    s = Store(tmp_path / "b.db")
    s.record_date_count("Ottawa", 123)
    s.record_date_count("Ottawa", 4)     # a dip must not disarm the canary
    assert s.peak_dates("Ottawa") == 123
    assert detector.ban_suspected(s, "Ottawa", 0)


def test_backoff_ladder_escalates_then_caps(tmp_path):
    s = Store(tmp_path / "b.db")
    ladder = config.BAN_BACKOFF_LADDER
    waits = []
    for _ in range(len(ladder) + 3):
        streak = s.ban_streak() + 1
        s.set_ban_streak(streak)
        waits.append(ladder[min(streak - 1, len(ladder) - 1)])
    assert waits[:len(ladder)] == ladder          # escalates
    assert waits[-1] == ladder[-1]                # then holds at the cap
    assert waits == sorted(waits)                 # never goes backwards


def test_ban_streak_resets(tmp_path):
    s = Store(tmp_path / "b.db")
    s.set_ban_streak(3)
    s.set_ban_streak(0)
    assert s.ban_streak() == 0


def test_poll_cadence_is_minutes_not_hours():
    day = datetime(2026, 8, 1, 14, 0)
    delays = [scheduler.next_delay_seconds(day) for _ in range(200)]
    assert min(delays) >= config.POLL_BASE_SECONDS
    assert max(delays) <= config.POLL_BASE_SECONDS + config.POLL_JITTER_SECONDS
    assert len(set(round(d) for d in delays)) > 50   # actually jittered
    # ~7 min mean => at least 100 checks/day, an order of magnitude above the
    # old 40/day budget this replaced.
    assert 24 * 3600 / (sum(delays) / len(delays)) > 100


def test_night_is_slower_but_still_polling():
    night = datetime(2026, 8, 1, 3, 0)
    day = datetime(2026, 8, 1, 14, 0)
    assert (sum(scheduler.next_delay_seconds(night) for _ in range(50))
            > sum(scheduler.next_delay_seconds(day) for _ in range(50)))
    assert scheduler.next_delay_seconds(night) < 3600   # never idles an hour


def test_rotation_covers_every_consulate():
    assert set(config.ROTATION) == set(config.FACILITIES)


def test_rotation_interleaves_canaries_for_fast_ban_detection():
    """Populated posts must be spread out, so a ban shows up within a poll or two."""
    populated = {"Vancouver", "Ottawa", "Toronto"}
    gaps, last = [], None
    for i, post in enumerate(config.ROTATION):
        if post in populated:
            if last is not None:
                gaps.append(i - last)
            last = i
    assert max(gaps) <= 3

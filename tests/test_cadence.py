"""Cadence sanity and per-cycle polling behavior (all 7 posts, back-to-back)."""
from datetime import date, datetime, timedelta

from visabot import config, scheduler
from visabot.store import Store


class FakeClient:
    """get_days returns ISO date strings per facility id, like the real client."""

    def __init__(self, days_by_facility=None):
        self.days = days_by_facility or {}
        self.calls = []

    def ensure_session(self):
        pass

    def get_days(self, facility_id):
        self.calls.append(facility_id)
        return self.days.get(facility_id, [])


def _pin(monkeypatch):
    """Hermetic window (today+1 .. today+365) and no inter-request sleeps."""
    monkeypatch.setattr(config, "EARLIEST_OFFSET_DAYS", 1)
    monkeypatch.setattr(config, "LATEST_DATE", date.today() + timedelta(days=365))
    monkeypatch.setattr(scheduler, "_pace", lambda: None)


def test_poll_once_checks_every_post_in_priority_order(tmp_path, monkeypatch):
    _pin(monkeypatch)
    client = FakeClient()
    scheduler.poll_once(client, Store(tmp_path / "c.db"))
    assert client.calls == [config.FACILITIES[p] for p in config.POST_PRIORITY]


def test_poll_once_bursts_on_hit_then_finishes_the_cycle(tmp_path, monkeypatch):
    _pin(monkeypatch)
    hit = date.today() + timedelta(days=30)
    client = FakeClient({config.FACILITIES["Calgary"]: [hit.isoformat()]})
    bursts = []
    monkeypatch.setattr(scheduler, "run_burst",
                        lambda c, s, post, d: bursts.append((post, d)))
    scheduler.poll_once(client, Store(tmp_path / "c.db"))
    assert bursts == [("Calgary", hit)]
    assert client.calls[0] == config.FACILITIES["Calgary"]   # DS-160 post first
    assert len(client.calls) == len(config.POST_PRIORITY)    # unbooked -> full scan


def test_poll_once_stops_scanning_after_verified_booking(tmp_path, monkeypatch):
    _pin(monkeypatch)
    hit = date.today() + timedelta(days=30)
    client = FakeClient({config.FACILITIES["Calgary"]: [hit.isoformat()]})
    store = Store(tmp_path / "c.db")

    def fake_burst(c, s, post, d):
        s.kv_set("booked", "1")
    monkeypatch.setattr(scheduler, "run_burst", fake_burst)
    scheduler.poll_once(client, store)
    assert client.calls == [config.FACILITIES["Calgary"]]


def test_poll_cadence_is_minutes_not_hours():
    day = datetime(2026, 8, 1, 14, 0)
    delays = [scheduler.next_delay_seconds(day) for _ in range(200)]
    assert min(delays) >= config.POLL_BASE_SECONDS
    assert max(delays) <= config.POLL_BASE_SECONDS + config.POLL_JITTER_SECONDS
    assert len(set(round(d) for d in delays)) > 50   # actually jittered
    # >=100 cycles/day keeps every post checked many times an hour
    assert 24 * 3600 / (sum(delays) / len(delays)) > 100


def test_night_is_slower_but_still_polling(monkeypatch):
    # Pin the multiplier: with the operator's NIGHT_MULTIPLIER=1 night == day
    # and the comparison below becomes a coin flip.
    monkeypatch.setattr(config, "NIGHT_MULTIPLIER", 2.0)
    night = datetime(2026, 8, 1, 3, 0)
    day = datetime(2026, 8, 1, 14, 0)
    assert (sum(scheduler.next_delay_seconds(night) for _ in range(50))
            > sum(scheduler.next_delay_seconds(day) for _ in range(50)))
    assert scheduler.next_delay_seconds(night) < 3600   # never idles an hour


def test_night_multiplier_one_means_no_easing(monkeypatch):
    monkeypatch.setattr(config, "NIGHT_MULTIPLIER", 1.0)
    night = datetime(2026, 8, 1, 3, 0)
    hi = config.POLL_BASE_SECONDS + config.POLL_JITTER_SECONDS
    for _ in range(50):
        assert config.POLL_BASE_SECONDS <= scheduler.next_delay_seconds(night) <= hi


def test_priority_and_rotation_cover_every_consulate():
    assert set(config.POST_PRIORITY) == set(config.FACILITIES)
    assert set(config.ROTATION) == set(config.FACILITIES)

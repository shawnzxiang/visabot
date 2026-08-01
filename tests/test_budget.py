from datetime import date, timedelta

from visabot import config
from visabot.store import Store


def make_store(tmp_path):
    return Store(tmp_path / "test.db")


def test_budget_counts_only_today(tmp_path):
    s = make_store(tmp_path)
    yesterday = (date.today() - timedelta(days=1)).isoformat() + "T23:59:00"
    s.conn.execute("INSERT INTO requests VALUES (?, 'payment')", (yesterday,))
    s.conn.commit()
    assert s.requests_today() == 0
    s.record_request("payment")
    s.record_request("days")
    assert s.requests_today() == 2
    assert s.budget_remaining() == config.DAILY_REQUEST_BUDGET - 2


def test_attempt_caps_exclude_test_mode(tmp_path):
    s = make_store(tmp_path)
    s.record_attempt("Calgary", "2026-08-10", "09:00", "test_mode")
    assert s.attempts_today() == 0
    s.record_attempt("Calgary", "2026-08-10", "09:00", "failed")
    assert s.attempts_today() == 1
    assert s.attempts_total() == 1


def test_kv_and_booked(tmp_path):
    s = make_store(tmp_path)
    assert not s.booked()
    s.kv_set("booked", "1")
    assert s.booked()
    s.kv_set("schedule_id", "12345678")
    assert s.kv_get("schedule_id") == "12345678"

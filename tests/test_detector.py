from datetime import date
from pathlib import Path

from visabot import detector

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return (FIXTURES / name).read_text()


def test_parse_synthetic_payment_page():
    avail = detector.parse_payment_page(load("payment_synthetic.html"))
    assert avail["Calgary"] == date(2026, 8, 15)
    assert avail["Halifax"] is None
    assert avail["Montreal"] == date(2026, 11, 22)
    assert avail["Ottawa"] is None
    assert avail["Quebec City"] == date(2026, 8, 15)
    assert avail["Toronto"] == date(2026, 9, 3)
    assert avail["Vancouver"] == date(2026, 8, 10)


def test_pick_earliest_in_window():
    avail = detector.parse_payment_page(load("payment_synthetic.html"))
    # window on 2026-07-31 is [08-05 .. 08-21]: Vancouver 08-10 beats the 08-15 pair
    assert detector.pick_candidate(avail, date(2026, 7, 31)) == ("Vancouver", date(2026, 8, 10))


def test_priority_breaks_ties():
    avail = {"Quebec City": date(2026, 8, 15), "Calgary": date(2026, 8, 15)}
    assert detector.pick_candidate(avail, date(2026, 7, 31)) == ("Calgary", date(2026, 8, 15))


def test_no_candidate_when_all_late_or_empty():
    avail = {"Toronto": date(2026, 9, 3), "Halifax": None}
    assert detector.pick_candidate(avail, date(2026, 7, 31)) is None


def test_rolling_window_excludes_too_soon():
    # On Aug 8, window starts Aug 13 — Vancouver's Aug 10 no longer qualifies
    avail = detector.parse_payment_page(load("payment_synthetic.html"))
    assert detector.pick_candidate(avail, date(2026, 8, 8)) == ("Calgary", date(2026, 8, 15))


def test_date_formats():
    assert detector.parse_date_cell("15 August, 2026") == date(2026, 8, 15)
    assert detector.parse_date_cell("15 August 2026") == date(2026, 8, 15)
    assert detector.parse_date_cell("August 15, 2026") == date(2026, 8, 15)
    assert detector.parse_date_cell("2026-08-15") == date(2026, 8, 15)
    assert detector.parse_date_cell("No Appointments Available") is None
    assert detector.parse_date_cell("") is None

from datetime import date

from visabot import config


def test_rolling_earliest_example_from_user():
    # "say today is 8/5, then it should be 8/10 and onwards"
    lo, hi = config.window(date(2026, 8, 5))
    assert lo == date(2026, 8, 10)
    assert hi == date(2026, 8, 21)


def test_in_window_bounds():
    today = date(2026, 8, 5)
    assert not config.in_window(date(2026, 8, 9), today)
    assert config.in_window(date(2026, 8, 10), today)
    assert config.in_window(date(2026, 8, 21), today)
    assert not config.in_window(date(2026, 8, 22), today)


def test_window_closes_after_aug_16():
    assert config.window_open(date(2026, 8, 16))
    assert not config.window_open(date(2026, 8, 17))

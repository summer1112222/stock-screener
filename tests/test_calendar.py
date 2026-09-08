from datetime import date, datetime

from data.calendar import (
    is_market_session,
    is_trading_day,
    last_n_trading_days,
    next_trading_day,
    previous_trading_day,
)


def test_weekend_is_not_trading_day_and_next_day_skips_weekend():
    assert not is_trading_day("2026-09-05")
    assert next_trading_day("2026-09-04") == "2026-09-07"
    assert previous_trading_day("2026-09-07") == "2026-09-04"


def test_last_n_trading_days_returns_ordered_dates_excluding_end_when_needed():
    assert last_n_trading_days("2026-09-07", 3) == ["2026-09-03", "2026-09-04", "2026-09-07"]


def test_market_session_uses_a_share_morning_and_afternoon_windows():
    assert is_market_session(datetime(2026, 9, 8, 10, 0))
    assert is_market_session(datetime(2026, 9, 8, 13, 30))
    assert not is_market_session(datetime(2026, 9, 8, 12, 0))
    assert not is_market_session(datetime(2026, 9, 8, 15, 1))

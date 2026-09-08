from datetime import date, datetime

from data.calendar import (
    is_market_session,
    is_trading_day,
    last_n_trading_days,
    latest_trading_day,
    next_trading_day,
    previous_trading_day,
)


def test_weekend_is_not_trading_day_and_next_day_skips_weekend():
    assert not is_trading_day("2026-09-05")
    assert next_trading_day("2026-09-04") == "2026-09-07"
    assert previous_trading_day("2026-09-07") == "2026-09-04"


def test_last_n_trading_days_returns_ordered_dates_excluding_end_when_needed():
    assert last_n_trading_days("2026-09-07", 3) == ["2026-09-03", "2026-09-04", "2026-09-07"]


def test_latest_trading_day_returns_last_trading_day_at_or_before():
    assert latest_trading_day("2026-09-07") == "2026-09-07"  # 周一即交易日
    assert latest_trading_day("2026-09-05") == "2026-09-04"  # 周六→上周五
    assert latest_trading_day("2026-09-06") == "2026-09-04"  # 周日→上周五


def test_market_session_accepts_plain_date_without_crash():
    # 纯 date 输入(无时分)统一按 00:00 处理为非盘中，不抛 AttributeError
    assert is_market_session(date(2026, 9, 8)) is False


def test_market_session_uses_a_share_morning_and_afternoon_windows():
    assert is_market_session(datetime(2026, 9, 8, 10, 0))
    assert is_market_session(datetime(2026, 9, 8, 13, 30))
    assert not is_market_session(datetime(2026, 9, 8, 12, 0))
    assert not is_market_session(datetime(2026, 9, 8, 15, 1))


def test_market_session_boundaries_are_inclusive_matching_legacy_semantics():
    # 兼容旧 backtest.quality._is_in_session 的闭区间语义(930<=t<=1500)
    assert is_market_session(datetime(2026, 9, 8, 11, 30))
    assert is_market_session(datetime(2026, 9, 8, 15, 0))
    assert is_market_session(datetime(2026, 9, 8, 9, 30))
    # 边界之外非盘中
    assert not is_market_session(datetime(2026, 9, 8, 11, 31))
    assert not is_market_session(datetime(2026, 9, 8, 15, 1))
    assert not is_market_session(datetime(2026, 9, 8, 9, 29))

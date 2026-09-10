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


def test_is_market_session_defaults_to_cst_not_container_timezone(monkeypatch):
    """容器时区 UTC 时(14:07 UTC=22:07 CST 盘后),默认调用须按 CST 判盘后。

    根因:旧 datetime.now() 取容器本地(UTC)时间→14:07 落 13:00-15:00 误判盘中,
    quality 缓存 TTL=30s<67s 计算时间→缓存永不命中→每次冷算触发前端超时。
    修复后 is_market_session(None) 显式用 ZoneInfo('Asia/Shanghai') 取 CST。"""
    import data.calendar as cal
    assert cal._CST is not None, "tzdata 须可用(requirements 加 tzdata 兜底 slim 镜像)"
    real_datetime = cal.datetime

    class _UtcContainerDT(real_datetime):
        """模拟容器 UTC 时区:datetime.now(_CST) 返 CST 22:07(=UTC 14:07 盘后)。
        返 cls(...) 子类实例而非 real_datetime(...),否则 isinstance(now, 子类)
        失败(line 126 纯 date 判定误触),须保持子类身份。"""
        @classmethod
        def now(cls, tz=None):
            if tz is cal._CST:
                return cls(2026, 9, 10, 22, 7, tzinfo=tz)
            return real_datetime.now(tz)

    monkeypatch.setattr(cal, "datetime", _UtcContainerDT)
    # CST 22:07 盘后,非交易日非盘时段——必须 False(旧实现会取 UTC 14:07 误判 True)
    assert cal.is_market_session() is False

    # 对照:CST 10:00 盘中(交易日 2026-09-10 周四)须 True

    class _CstMorningDT(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is cal._CST:
                return cls(2026, 9, 10, 10, 0, tzinfo=tz)
            return real_datetime.now(tz)

    monkeypatch.setattr(cal, "datetime", _CstMorningDT)
    assert cal.is_market_session() is True

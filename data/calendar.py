"""交易日历与盘中时段工具。

统一 A 股交易日 / 自然日 / 盘中时段判定，供回测、实时筛选、主力行为序列
等模块复用，避免各处自然日与交易日混用导致日期口径不一致。

本模块不依赖行情表；交易日序列优先用内置法定节假日表从自然日推导。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# 交易日序列
# ---------------------------------------------------------------------------

#: 2026 年 A 股休市日（周末之外的法定节假日）；可随交易所公告逐年补充。
_HOLIDAYS_2026: frozenset[str] = frozenset({
    # 元旦 2026-01-01
    "2026-01-01",
    # 春节 2026-02-16 ~ 2026-02-23
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-23",
    # 清明节 2026-04-06
    "2026-04-06",
    # 劳动节 2026-05-01 ~ 2026-05-05
    "2026-05-01", "2026-05-04", "2026-05-05",
    # 端午节 2026-06-19
    "2026-06-19",
    # 中秋节 2026-09-25
    "2026-09-25",
    # 国庆节 2026-10-01 ~ 2026-10-07
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
    # 元旦 2027-01-01
    "2027-01-01",
})

#: 全年法定节假日共缓存到周日历，避免每次判定重复扫描。
_HOLIDAYS: frozenset[str] = frozenset(_HOLIDAYS_2026)


def _to_date(x: str | date | datetime) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x))


def is_trading_day(day: str | date | datetime) -> bool:
    """判断某自然日是否为 A 股交易日（周一~周五且非法定节假日）。"""
    d = _to_date(day)
    if d.weekday() >= 5:
        return False
    if d.isoformat() in _HOLIDAYS:
        return False
    return True


def _shift(day: str | date | datetime, step: int) -> str:
    d = _to_date(day)
    while True:
        d += timedelta(days=step)
        if is_trading_day(d):
            return d.isoformat()


def next_trading_day(day: str | date | datetime) -> str:
    """返回 day 之后的下一个交易日（YYYY-MM-DD），排除周末与节假日。"""
    return _shift(day, 1)


def previous_trading_day(day: str | date | datetime) -> str:
    """返回 day 之前的上一个交易日（YYYY-MM-DD）。"""
    return _shift(day, -1)


def last_n_trading_days(end: str | date | datetime, n: int) -> list[str]:
    """返回截止 end（含，按交易日序列）的最近 n 个交易日，升序（旧→新）。

    n<=0 返回空表。升序便于按 [开始, 结束] 窗口切片；调用方按最近取时自行取末元素。
    """
    out: list[str] = []
    d = _to_date(end)
    while len(out) < n:
        if is_trading_day(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    out.reverse()
    return out


def latest_trading_day(day: str | date | datetime) -> str:
    """返回 day 当天或之前最近的交易日（YYYY-MM-DD）。周末/节假日回落到前一交易日。"""
    d = _to_date(day)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d.isoformat()


# ---------------------------------------------------------------------------
# 盘中时段
# ---------------------------------------------------------------------------

#: A 股连续/集合竞价时段窗口：上午 09:30-11:30，下午 13:00-15:00。
_SESSION_START = (9, 30)
_MORNING_END = (11, 30)
_AFTERNOON_START = (13, 0)
_SESSION_END = (15, 0)


def _in_window(now: datetime, start: tuple[int, int], end: tuple[int, int]) -> bool:
    # 闭区间 [start, end]，与旧 backtest.quality._is_in_session 的 930<=t<=1500 语义对齐
    t = (now.hour, now.minute)
    return start <= t <= end


def is_market_session(now: datetime | None = None) -> bool:
    """判断当前时间是否处于 A 股盘中时段（09:30-11:30、13:00-15:00，含端点）。

    now 缺省使用 datetime.now()；仅回测等需要注入时间时显式传入。
    纯 date(无时分)输入按 00:00 处理为非盘中，不抛异常。
    """
    if now is None:
        now = datetime.now()
    if isinstance(now, date) and not isinstance(now, datetime):
        now = datetime.combine(now, datetime.min.time())
    # 非交易日的任何时刻都不算盘中
    if not is_trading_day(now):
        return False
    return _in_window(now, _SESSION_START, _MORNING_END) or _in_window(
        now, _AFTERNOON_START, _SESSION_END
    )
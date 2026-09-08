"""可复用的 pandas 技术指标纯函数。

指标层只负责从 Series 计算结果，不负责数据采集、筛选、排序或 API 输出。
各筛选与回测模块应复用这里的口径，避免 MA/RSI/量比在不同模块重复实现。
本模块仅用于历史研究和机械指标计算，不构成投资建议、买卖信号或收益承诺。
"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """简单移动平均，前 window-1 个值保留为 NaN。"""
    return series.rolling(window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """指数移动平均，使用递推（adjust=False）口径。"""
    return series.ewm(span=span, adjust=False, min_periods=1).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """按滚动平均涨跌幅计算 RSI，均值损失为 0 时归一为 RSI=100。"""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss  # loss=0 → inf
    return 100 - 100 / (1 + rs)


def rolling_return(series: pd.Series, window: int) -> pd.Series:
    """window 日收益率，使用 t / t-window - 1。"""
    return series / series.shift(window) - 1


def rolling_volatility(series: pd.Series, window: int) -> pd.Series:
    """收益率滚动样本标准差。"""
    return series.pct_change().rolling(window).std()


def volume_ratio(volume: pd.Series, window: int = 5) -> pd.Series:
    """当前成交量相对含当日成交量的 window 日均量。"""
    return volume / volume.rolling(window).mean()


def price_volume_corr(
    close: pd.Series, volume: pd.Series, window: int = 20
) -> pd.Series:
    """收盘价与成交量的滚动 Pearson 相关系数。"""
    return close.rolling(window).corr(volume)


def ma_alignment(
    close: pd.Series, windows: tuple[int, ...] = (5, 10, 20, 60)
) -> pd.DataFrame:
    """返回多窗口均线矩阵，列名为 ``ma{window}``。"""
    return pd.DataFrame({f"ma{window}": sma(close, window) for window in windows})

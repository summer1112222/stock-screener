# screener/etf_screen.py
from __future__ import annotations
import math

def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))

def valuation_percentile(value: float, history: list[float]) -> float:
    """value 在 history（升序或任意序）中的百分位，低=便宜(被低估)。
    rank = 小于等于 value 的比例（历史最高 → 近 1）; history 越大越接近 1。"""
    hi_vals = [h for h in history if h is not None and not (isinstance(h, float) and math.isnan(h))]
    if not hi_vals:
        return 0.5
    below = sum(1 for h in hi_vals if h <= value)
    return _clip(below / len(hi_vals))

def quality_score(scale_wan=0.0, turnover_rate=0.0, fee_bps=50.0, tracking_err=None) -> float:
    """质量维度: 规模(亿, 避清盘)、换手/成交额代理流动性、费率(低优)、跟踪误差(紧优)。
    权重: 规模0.4 流动性0.2 费率0.2 跟踪0.2。缺项用中性0.5 参与, 全缺→0.5。"""
    s = _clip(math.log10(scale_wan + 1) / 2.5) if scale_wan else 0.5        # 0→1亿→100亿+非线性
    liq = _clip((math.log10(turnover_rate + 0.01) + 0.5) / 1.5) if turnover_rate else 0.5
    fee = _clip(1 - (fee_bps - 10) / 80) if fee_bps else 0.5                # 10bps 满, 90bps 近 0
    tr = _clip(1 - (tracking_err or 0) / 2.0) if tracking_err is not None else 0.5
    return 0.4 * s + 0.2 * liq + 0.2 * fee + 0.2 * tr

def long_score(valuation_pct: float, quality: float, w_val=0.55, w_qual=0.45) -> float:
    denom = w_val + w_qual
    return (w_val * _clip(1.0 - valuation_pct) + w_qual * quality) / denom * 100.0
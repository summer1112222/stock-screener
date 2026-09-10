# screener/etf_screen.py
from __future__ import annotations
import math
import pandas as pd

from . import nextday as _nd
from backtest import eval as bt_eval

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


_SHORT_FACTORS_DEF = {
    "momentum_5": {"key": "momentum", "n": 5},
    "momentum_20": {"key": "momentum", "n": 20},
    "vol_corr_20": {"key": "vol_corr", "n": 20},
    "volatility_20": {"key": "volatility", "n": 20},
}


def _panel_factor(close, amount, name, spec):
    """计算单因子面板(date×code)；compute_factor 缺 amount 时对需要 amount 的
    因子返回全 NaN 空面板(不抛异常) → 视该因子不可用(avail=False)。"""
    try:
        pv = bt_eval.compute_factor(close, name, params={"n": spec["n"]}, amount=amount)
        if pv is None or pv.shape[0] == 0 or pv.isna().all().all():
            return pd.DataFrame(index=close.index, columns=close.columns), False
        return pv, True
    except Exception:
        return pd.DataFrame(index=close.index, columns=close.columns), False


def short_scores(close, amount, codes, period_ns):
    """短清单量价横截面 rank 评分：复用 eval.compute_factor 面板因子，
    逐 code 按可用因子等权重归一聚合(缺失从不伪造 0)。
    返 (scores[c]∈[0,100], details[c][factor]=分位*100或None, coverage[c]=可用权重/权重和)。"""
    factor_maps = {n: {} for n in _SHORT_FACTORS_DEF}
    avail = {n: False for n in _SHORT_FACTORS_DEF}
    for name, spec in _SHORT_FACTORS_DEF.items():
        pv, ok = _panel_factor(close, amount, name, spec)
        avail[name] = ok
        for c in codes:
            if c in pv.columns:
                val = pv[c].iloc[-1]
                factor_maps[name][c] = None if val is None or (isinstance(val, float) and math.isnan(val)) else float(val)
    # 只保留真正可用的因子名，套 nextday 加权范式(缺失从不伪造 0)
    usable = {n: {} for n in _SHORT_FACTORS_DEF if avail[n]}
    weights = {n: 1.0 for n in usable}          # 等权, 可在验证后 IC 校准
    wm = {n: weights.get(n, 1.0) for n in usable}
    pct = {n: _nd._rank_pct(factor_maps[n]) for n in usable}
    scores, details, coverage = {}, {}, {}
    for c in codes:
        present = {n: pct[n].get(c) for n in usable if pct[n].get(c) is not None}
        denom = sum(wm[n] for n in present)
        scores[c] = round((sum(wm[n] * v for n, v in present.items()) / denom * 100) if denom else 0.0, 2)
        details[c] = {n: (round(pct[n].get(c) * 100, 2) if pct[n].get(c) is not None else None) for n in usable}
        coverage[c] = round(denom / sum(wm.values()), 4)
    return scores, details, coverage
# -*- coding: utf-8 -*-
"""回测引擎：截面排名 → topN 等权 → 持有到下个调仓日 → 日度净值。

合规：历史模拟，不输出实时买卖点、不承诺收益、不自动下单。
T+1：调仓日 r 用 r 日因子排名决策，自 r+1 日起按新组合计收益。
停牌近似：当日收益 NaN 视为 0(持仓价值不变)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rebalance_dates(index: pd.Index, freq: str = "M") -> list:
    """取每期最后一个交易日作为调仓日。freq: M/W/Q(pandas3 用 ME/WE/QE，这里归一)。"""
    _map = {"M": "ME", "W": "W", "Q": "QE"}
    f = _map.get(freq, freq)
    s = pd.Series(index, index=index)
    grp = s.groupby(pd.Grouper(freq=f))
    out = [d for d in grp.last() if d in index]
    return out


def run_backtest(close: pd.DataFrame, factor: pd.DataFrame,
                 topn: int = 10, freq: str = "M",
                 benchmark: pd.Series | None = None,
                 cost_bps: float = 30.0,
                 delisted_codes: list[str] | None = None) -> dict:
    """topN 等权多组合回测。close/factor 同形(date×code)。
    返回 {equity_curve, benchmark_curve, daily_returns, turnover, rebalance_dates,
    topn, cost_bps, total_cost_drag, delisted_declared}。
    cost_bps 为双边换手成本率(基点)；turnover_d 已含买+卖两侧，drag = tov * cost_bps/10000。
    """
    idx = pd.Index(sorted(close.index.intersection(factor.index)))
    close = close.reindex(idx)
    factor = factor.reindex(idx)
    daily_ret = close.pct_change()

    rebal = rebalance_dates(idx, freq)
    if len(rebal) < 2:
        return {"equity_curve": {}, "benchmark_curve": {}, "daily_returns": {},
                "turnover": None, "rebalance_dates": rebal, "topn": topn,
                "cost_bps": float(cost_bps), "total_cost_drag": 0.0,
                "delisted_declared": len(delisted_codes or [])}

    # 每个调仓日的等权 topN 组合(用该日因子截面排名)
    weight_map = {}
    for r in rebal:
        f = factor.loc[r].dropna()
        if len(f) >= topn:
            weight_map[r] = pd.Series(1.0 / topn, index=f.nlargest(topn).index)
        else:
            weight_map[r] = pd.Series(dtype=float)

    # governing[d] = 最近且严格早于 d 的调仓日 → 实现 T+1
    rebal_set = set(rebal)
    governing = {}
    last_r = None
    for d in idx:
        if last_r is not None:
            governing[d] = last_r
        if d in rebal_set:
            last_r = d

    daily_port = []
    turnovers = []
    total_cost_drag = 0.0
    cost_rate = max(0.0, float(cost_bps)) / 10000.0
    prev_r = None
    for d in idx:
        r = governing.get(d)
        w = weight_map.get(r) if r is not None else None
        if w is None or w.empty or d == idx[0]:
            daily_port.append(0.0)
        else:
            dr = daily_ret.loc[d].reindex(w.index).fillna(0.0)
            daily_port.append(float((w * dr).sum()))
        # 换手：调仓日比较新旧权重(用上一治理调仓)
        if d in rebal_set and prev_r is not None and d in weight_map:
            a = weight_map[prev_r]
            b = weight_map[d]
            comb = a.index.union(b.index)
            tov = float((b.reindex(comb, fill_value=0)
                        - a.reindex(comb, fill_value=0)).abs().sum())
            turnovers.append(tov)
            if cost_rate > 0:
                drag = tov * cost_rate
                daily_port[-1] -= drag
                total_cost_drag += drag
        if d in rebal_set:
            prev_r = d

    nav = (1 + pd.Series(daily_port, index=idx)).cumprod()
    str_idx = [str(d.date()) for d in nav.index]
    equity = dict(zip(str_idx, nav.round(4).tolist()))

    bench_curve = {}
    if benchmark is not None:
        b = benchmark.reindex(idx).ffill()
        bnav = (1 + b.pct_change().fillna(0.0)).cumprod()
        bnav = bnav.round(4)
        bench_curve = dict(zip([str(d.date()) for d in bnav.index], bnav.tolist()))

    return {
        "equity_curve": equity,
        "benchmark_curve": bench_curve,
        "daily_returns": dict(zip(str_idx, [round(float(r), 6) for r in daily_port])),
        "turnover": round(float(np.mean(turnovers)), 4) if turnovers else None,
        "rebalance_dates": [str(d.date()) for d in rebal],
        "topn": topn,
        "cost_bps": float(cost_bps),
        "total_cost_drag": round(total_cost_drag, 6),
        "delisted_declared": len(delisted_codes or []),
    }

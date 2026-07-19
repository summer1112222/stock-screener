# -*- coding: utf-8 -*-
"""风控指标：净值序列 → 年化收益/波动/最大回撤/夏普/Sortino/Calmar/beta/alpha/VaR/CVaR。

合规：纯统计，不输出买卖点，不承诺收益。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_TRADING_DAYS = 252


def _returns_from_nav(nav: pd.Series) -> pd.Series:
    return nav.pct_change().fillna(0.0)


def risk_metrics(nav: pd.Series, benchmark: pd.Series | None = None,
                 rf: float = 0.0) -> dict:
    """nav/benchmark: 净值序列。返回风控指标字典。"""
    if nav is None or len(nav) < 2:
        return {}
    nav = nav.sort_index()
    ret = _returns_from_nav(nav)
    ann_ret = float((nav.iloc[-1] / nav.iloc[0]) ** (_TRADING_DAYS / len(nav)) - 1) \
        if len(nav) > 1 else 0.0
    ann_vol = float(ret.std(ddof=0) * np.sqrt(_TRADING_DAYS))
    excess = ret - rf / _TRADING_DAYS
    sharpe = float(excess.mean() / ann_vol * np.sqrt(_TRADING_DAYS)) if ann_vol else None
    downside = ret[ret < 0]
    ds_vol = float(downside.std(ddof=0) * np.sqrt(_TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = float(excess.mean() / ds_vol * np.sqrt(_TRADING_DAYS)) if ds_vol else None
    cummax = nav.cummax()
    dd = (nav / cummax - 1)
    max_dd = float(dd.min())
    calmar = float(ann_ret / abs(max_dd)) if max_dd else None
    var95 = float(np.percentile(ret.dropna(), 5))
    cvar95 = float(ret[ret <= var95].mean()) if (ret <= var95).any() else var95

    out = {
        "ann_return": round(ann_ret, 4),
        "ann_volatility": round(ann_vol, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "sortino": round(sortino, 4) if sortino is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
        "var95": round(var95, 4),
        "cvar95": round(cvar95, 4),
        "n_days": int(len(nav)),
    }

    if benchmark is not None and len(benchmark) >= 2:
        b = benchmark.sort_index().reindex(nav.index).ffill()
        bret = _returns_from_nav(b)
        cov = float(np.cov(ret, bret)[0, 1])
        bvar = float(bret.var(ddof=0))
        beta = float(cov / bvar) if bvar else None
        alpha = float((ret.mean() - (beta * bret.mean())) * _TRADING_DAYS) if beta is not None else None
        out["beta"] = round(beta, 4) if beta is not None else None
        out["alpha"] = round(alpha, 4) if alpha is not None else None
    return out

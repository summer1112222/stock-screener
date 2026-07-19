# -*- coding: utf-8 -*-
"""候选池：按因子对标的排序输出观察清单 + 可交易性预筛。

合规：机械筛选/排序结果，非推荐、非买卖点、不构成投资建议、不承诺收益。
spot 因子走 etf_spot/stock_spot 快照(全市场)；历史因子走 *_daily(仅已抓历史的 codes)。
可交易性预筛(tradable=true)：成交额≥阈值、排除ST、排除涨停、排除停牌。
"""
from __future__ import annotations

import pandas as pd

from data import db
from . import eval as bt_eval

# spot 排序可用字段(ETF/个股取交集)
_SPOT_RANK_FIELDS = {
    "change_pct", "turnover_amount", "turnover_rate", "latest_price",
    "total_market_cap", "circulating_market_cap", "pe", "pb",
    "amplitude", "volume_ratio",
}
_SPOT_TABLE = {"ETF": "etf_spot", "stock": "stock_spot"}


def _tradable_filter(df: pd.DataFrame, min_turnover: float,
                     limit_pct: float) -> pd.DataFrame:
    """可交易性预筛：排除 ST / 停牌 / 涨停 / 成交额不足。"""
    if df is None or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if "name" in df.columns:
        mask &= ~df["name"].astype(str).str.contains("ST", case=False, na=False)
    if "latest_price" in df.columns:
        lp = pd.to_numeric(df["latest_price"], errors="coerce")
        mask &= lp.notna() & (lp > 0)
    if "turnover_amount" in df.columns:
        mask &= pd.to_numeric(df["turnover_amount"], errors="coerce").fillna(0) >= min_turnover
    if "change_pct" in df.columns:
        mask &= pd.to_numeric(df["change_pct"], errors="coerce").fillna(-99) < limit_pct
    return df[mask]


def _zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    std = s.std(ddof=0)
    if not std:
        return s * 0
    return (s - s.mean()) / std


def _rank_multi_z(universe: str, fields: list[str], weights: list[float],
                  sort: str, limit: int, tradable: bool,
                  min_turnover: float, limit_pct: float) -> tuple[list[dict], str]:
    """多因子 z-score 合成排序(等权或自定义权重)。纯筛选排序，非评级。"""
    table = _SPOT_TABLE.get(universe)
    if not table:
        return [], f"multi_z 不支持 universe={universe}"
    rows = db.query_rows(table)
    if not rows:
        return [], f"{table} 为空，先 /api/refresh"
    df = pd.DataFrame(rows)
    if tradable:
        df = _tradable_filter(df, min_turnover, limit_pct)
    score = pd.Series(0.0, index=df.index)
    for f, w in zip(fields, weights):
        if f in df.columns:
            score = score + w * _zscore(df[f])
    df = df.assign(__v=score).dropna(subset=["__v"])
    df = df.sort_values("__v", ascending=(sort == "asc")).head(int(limit))
    out = []
    for _, r in df.iterrows():
        out.append({
            "code": r.get("code"), "name": r.get("name"),
            "factor": "multi_z", "factor_value": round(float(r["__v"]), 4),
            "close": r.get("latest_price"), "change_pct": r.get("change_pct"),
            "turnover_amount": r.get("turnover_amount"),
        })
    return out, ""


def _rank_spot(universe: str, factor: str, sort: str, limit: int,
               tradable: bool, min_turnover: float, limit_pct: float) -> tuple[list[dict], str]:
    table = _SPOT_TABLE.get(universe)
    if not table:
        return [], f"spot 模式不支持 universe={universe}"
    rows = db.query_rows(table)
    if not rows:
        return [], f"{table} 为空，先 /api/refresh(个股需代理)"
    df = pd.DataFrame(rows)
    if factor not in df.columns:
        return [], f"字段不存在: {factor}"
    if tradable:
        df = _tradable_filter(df, min_turnover, limit_pct)
    df = df.copy()
    df["__v"] = pd.to_numeric(df[factor], errors="coerce")
    df = df.dropna(subset=["__v"])
    df = df.sort_values("__v", ascending=(sort == "asc")).head(int(limit))
    out = []
    for _, r in df.iterrows():
        out.append({
            "code": r.get("code"), "name": r.get("name"),
            "factor": factor, "factor_value": round(float(r["__v"]), 4),
            "close": r.get("latest_price"), "change_pct": r.get("change_pct"),
            "turnover_amount": r.get("turnover_amount"),
            "pe": r.get("pe"), "pb": r.get("pb"),
        })
    return out, ""


def _rank_history(universe: str, codes: list[str], factor: str,
                  n: int, sort: str, limit: int) -> tuple[list[dict], str]:
    close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
    if close.empty:
        return [], "无历史数据，先 /api/backtest/fetch 拉该 codes 的历史"
    amount = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "amount")
    fac = bt_eval.compute_factor(close, factor, params={"n": n}, amount=amount)
    last = fac.iloc[-1].dropna()
    last = last.sort_values(ascending=(sort == "asc")).head(int(limit))
    last_close = close.iloc[-1]
    out = []
    for code, v in last.items():
        out.append({
            "code": code, "name": code,
            "factor": factor, "factor_value": round(float(v), 4),
            "close": round(float(last_close[code]), 4) if code in last_close and pd.notna(last_close[code]) else None,
        })
    return out, ""


def rank_candidates(universe: str, factor: str, codes: list[str] | None = None,
                    n: int = 20, sort: str = "desc", limit: int = 20,
                    tradable: bool = False, min_turnover: float = 5e7,
                    limit_pct: float = 9.9,
                    multi_fields: list[str] | None = None,
                    multi_weights: list[float] | None = None) -> dict:
    """返回 {rows, universe, factor, mode, disclaimer}。mode: spot/history。"""
    limit = max(1, min(int(limit), 500))
    history_factors = ("momentum_n", "volatility_n", "turnover_n", "activity", "momentum",
                       "reversal_5", "reversal_20", "amihud_20")
    if factor == "multi_z":
        fields = multi_fields or ["change_pct", "turnover_amount", "turnover_rate"]
        weights = multi_weights or [1.0] * len(fields)
        rows, err = _rank_multi_z(universe, fields, weights, sort, limit,
                                  tradable, min_turnover, limit_pct)
        mode = "spot"
    elif factor in history_factors:
        if not codes:
            return {"rows": [], "universe": universe, "factor": factor, "mode": "history",
                    "error": "历史因子需提供 codes(已抓历史的标的)",
                    "disclaimer": _DISCLAIMER}
        rows, err = _rank_history(universe, codes, factor, n, sort, limit)
        mode = "history"
    elif universe in _SPOT_TABLE and factor in _SPOT_RANK_FIELDS:
        rows, err = _rank_spot(universe, factor, sort, limit, tradable,
                               min_turnover, limit_pct)
        mode = "spot"
    else:
        return {"rows": [], "universe": universe, "factor": factor, "mode": "?",
                "error": f"不支持的组合 universe={universe} factor={factor}",
                "disclaimer": _DISCLAIMER}
    return {
        "rows": rows, "universe": universe, "factor": factor, "mode": mode,
        "n": n, "sort": sort, "limit": limit,
        "tradable": tradable, "min_turnover": min_turnover, "limit_pct": limit_pct,
        "error": err or None, "disclaimer": _DISCLAIMER,
    }


_DISCLAIMER = ("以下为按指定条件机械筛选/排序的观察清单，非推荐、非买卖点、"
               "不构成投资建议、不承诺收益。市场有风险，决策请独立判断，盈亏自负。")

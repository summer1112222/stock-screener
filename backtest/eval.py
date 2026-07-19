# -*- coding: utf-8 -*-
"""因子评价：从历史日线计算因子 → 前瞻收益 → IC/IR/分档多空。

合规：纯统计评价，不输出买卖点，不承诺收益。
前视守卫：因子只引用 date<=t 的数据；前瞻收益从 t 到 t+n。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from data import db
from data.history import _UNIVERSE


# universe → (table, key_col)
def _uni_meta(universe: str) -> tuple[str, str]:
    table, _, key = _UNIVERSE[universe]
    return table, key


def load_panel(universe: str, codes: list[str], start: str,
               end: str, field: str = "close") -> pd.DataFrame:
    """加载某字段的历史宽面板：index=date, columns=code/symbol/name, values=field。"""
    table, key = _uni_meta(universe)
    rows = db.query_rows(table)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if key not in df.columns or field not in df.columns or "date" not in df.columns:
        return pd.DataFrame()
    df = df[df[key].isin(codes)]
    dts = pd.to_datetime(df["date"], errors="coerce")
    df = df[(dts >= pd.to_datetime(start, errors="coerce")) &
            (dts <= pd.to_datetime(end, errors="coerce"))]
    panel = df.pivot_table(index="date", columns=key, values=field, aggfunc="last")
    panel = panel.sort_index()
    panel.index = pd.to_datetime(panel.index)
    return panel


# ------------------------------------------------------------------
# 因子计算(从 OHLCV 派生，可历史重建)
# ------------------------------------------------------------------
def compute_factor(close: pd.DataFrame, factor_key: str,
                   params: dict | None = None,
                   volume: pd.DataFrame | None = None,
                   amount: pd.DataFrame | None = None) -> pd.DataFrame:
    """按 factor_key 计算截面因子值(同形状 date×code)。
    支持: momentum_n / volatility_n / turnover_n / activity / momentum。
    前视安全：计算 t 行只用 <=t 的数据(pandas 默认即如此)。
    """
    params = params or {}
    n = int(params.get("n", 20))
    if factor_key.startswith("momentum"):
        return close.pct_change(n)
    if factor_key.startswith("volatility"):
        ret = close.pct_change()
        return ret.rolling(n).std()
    if factor_key.startswith("turnover"):
        base = amount if amount is not None else volume
        if base is None:
            return pd.DataFrame(index=close.index, columns=close.columns)
        return base.rolling(n).mean()
    if factor_key == "activity":
        if amount is None:
            return pd.DataFrame(index=close.index, columns=close.columns)
        dr = close.pct_change()
        return (amount * dr.abs()).rolling(n).mean()
    if factor_key == "momentum":
        if amount is None:
            return pd.DataFrame(index=close.index, columns=close.columns)
        dr = close.pct_change()
        return (amount * dr).rolling(n).mean()
    if factor_key.startswith("reversal"):
        m = re.search(r"_(\d+)$", factor_key)
        nn = int(m.group(1)) if m else n
        return -close.pct_change(nn)
    if factor_key.startswith("amihud"):
        m = re.search(r"_(\d+)$", factor_key)
        nn = int(m.group(1)) if m else n
        if amount is None:
            return pd.DataFrame(index=close.index, columns=close.columns)
        dr = close.pct_change()
        return (dr.abs() / amount.replace(0, np.nan)).rolling(nn).mean()
    raise ValueError(f"未知 factor_key: {factor_key}")


def forward_returns(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """t 日收盘 → t+n 日收盘 的前瞻收益。"""
    return close.shift(-n) / close - 1.0


def ic_series(factor: pd.DataFrame, fwd: pd.DataFrame,
              method: str = "pearson") -> pd.Series:
    """逐期截面 IC：因子值 与 前瞻收益 的相关。默认 pearson(无需 scipy)。"""
    ics = []
    idx = factor.index.intersection(fwd.index)
    for d in idx:
        f = factor.loc[d].dropna()
        r = fwd.loc[d].reindex(f.index).dropna()
        if len(f) < 3:
            ics.append(np.nan)
            continue
        ics.append(float(f.corr(r, method=method)))
    return pd.Series(ics, index=idx)


def ic_summary(ics: pd.Series) -> dict:
    s = ics.dropna()
    if len(s) == 0:
        return {"ic": None, "ir": None, "ic_std": None,
                "win_rate": None, "n": 0}
    mean = float(s.mean())
    std = float(s.std(ddof=0))
    return {
        "ic": mean,
        "ir": (mean / std) if std else None,
        "ic_std": std,
        "win_rate": float((s > 0).mean()),
        "n": int(len(s)),
    }


def decile_backtest(factor: pd.DataFrame, fwd: pd.DataFrame,
                    n_groups: int = 5) -> dict:
    """按因子分档，每期等权多/空，返回各组累计净值与多空净值。
    前视守卫：t 期因子对 t→t+n 收益分组。"""
    idx = factor.index.intersection(fwd.index)
    grp_rets = {g: [] for g in range(n_groups)}
    dates = []
    for d in idx:
        f = factor.loc[d].dropna()
        r = fwd.loc[d].reindex(f.index).dropna()
        common = f.index.intersection(r.index)
        if len(common) < n_groups:
            continue
        f, r = f.loc[common], r.loc[common]
        try:
            bins = pd.qcut(f, n_groups, labels=False, duplicates="drop")
        except Exception:
            continue
        if bins.nunique() < 2:
            continue
        dates.append(d)
        for g in range(n_groups):
            mask = bins == g
            if mask.any():
                grp_rets[g].append(float(r[mask].mean()))
            else:
                grp_rets[g].append(np.nan)
    if not dates:
        return {"groups": {}, "long_short": [], "dates": []}
    df = pd.DataFrame(grp_rets, index=dates)
    cum = (1 + df).cumprod()
    ls = cum[n_groups - 1] / cum[0]  # 高档/低档
    str_idx = [str(d.date()) for d in cum.index]
    return {
        "groups": {int(g): dict(zip(str_idx, cum[g].round(4))) for g in range(n_groups)},
        "long_short": dict(zip(str_idx, ls.round(4))),
        "dates": str_idx,
    }

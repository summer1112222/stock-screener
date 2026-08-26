# -*- coding: utf-8 -*-
"""因子有效性研究编排：IC、Rank IC、分层收益与样本外切分。

本模块只做历史统计，不改变筛选排序，不输出买卖点。因子值严格
使用 t 日及以前数据，前瞻收益从 t 到 t+n 日计算。
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from . import eval as bt_eval
from . import robust as bt_robust


_MIN_PERIODS = 5


def _date_text(value) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _clean_codes(codes: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(c).strip() for c in (codes or []) if str(c).strip()))


def _coverage(factor: pd.DataFrame, fwd: pd.DataFrame,
              requested_codes: list[str]) -> dict:
    common = factor.index.intersection(fwd.index)
    requested = len(requested_codes)
    factor_cells = int(factor.notna().sum().sum()) if not factor.empty else 0
    fwd_cells = int(fwd.notna().sum().sum()) if not fwd.empty else 0
    possible = max(len(common) * requested, 0)
    return {
        "requested_codes": requested,
        "history_codes": int(len(set(factor.columns) & set(requested_codes))),
        "dates": int(len(common)),
        "factor_cells": factor_cells,
        "forward_return_cells": fwd_cells,
        "factor_rate": round(factor_cells / possible, 4) if possible else 0.0,
        "forward_return_rate": round(fwd_cells / possible, 4) if possible else 0.0,
    }


def _rank_ic_series(factor: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """逐期截面 Spearman/Rank IC，不依赖 scipy。"""
    out = []
    idx = factor.index.intersection(fwd.index)
    for date in idx:
        f = factor.loc[date].dropna()
        r = fwd.loc[date].reindex(f.index).dropna()
        common = f.index.intersection(r.index)
        if len(common) < 3:
            out.append(np.nan)
            continue
        out.append(float(f.loc[common].rank().corr(r.loc[common].rank())))
    return pd.Series(out, index=idx, dtype=float)


def _ic_report(factor: pd.DataFrame, fwd: pd.DataFrame) -> dict:
    pearson = bt_eval.ic_series(factor, fwd)
    spearman = _rank_ic_series(factor, fwd)
    return {
        "pearson": bt_eval.ic_summary(pearson),
        "spearman": bt_eval.ic_summary(spearman),
        "series": {
            "pearson": {
                _date_text(k): (None if pd.isna(v) else round(float(v), 6))
                for k, v in pearson.items()
            },
            "spearman": {
                _date_text(k): (None if pd.isna(v) else round(float(v), 6))
                for k, v in spearman.items()
            },
        },
        "bootstrap": bt_robust.bootstrap_ic(spearman),
    }


def _split_report(factor: pd.DataFrame, close: pd.DataFrame,
                  fwd: pd.DataFrame, train_frac: float,
                  n_groups: int) -> dict:
    idx = factor.index.intersection(fwd.index)
    if len(idx) < 2:
        return {"cutoff": None, "summary": {"n": 0}, "test": {"n": 0}}
    frac = min(max(float(train_frac), 0.1), 0.9)
    cut_pos = min(max(int(len(idx) * frac) - 1, 0), len(idx) - 1)
    cutoff = idx[cut_pos]
    train_mask = idx <= cutoff
    test_mask = idx > cutoff
    train_f, test_f = factor.loc[idx[train_mask]], factor.loc[idx[test_mask]]
    train_r, test_r = fwd.loc[idx[train_mask]], fwd.loc[idx[test_mask]]
    train_ic = _ic_report(train_f, train_r)
    test_ic = _ic_report(test_f, test_r)
    train_decile = bt_eval.decile_backtest(train_f, train_r, n_groups)
    test_decile = bt_eval.decile_backtest(test_f, test_r, n_groups)
    return {
        "cutoff": _date_text(cutoff),
        "train_range": [_date_text(idx[0]), _date_text(cutoff)],
        "test_range": [_date_text(idx[cut_pos + 1]), _date_text(idx[-1])] if test_mask.any() else [],
        "summary": train_ic["spearman"],
        "test_summary": test_ic["spearman"],
        "train": {"summary": train_ic["spearman"], "ic": train_ic, "decile": train_decile},
        "test": {"summary": test_ic["spearman"], "ic": test_ic, "decile": test_decile},
    }


def _holding_rebalance_deciles(factor: pd.DataFrame, fwd: pd.DataFrame,
                               n_groups: int, cost_bps: float) -> dict:
    """对分层收益做等权调仓成本处理：每次调整后扣双边费用。"""
    idx = factor.index.intersection(fwd.index)
    cost = float(cost_bps or 0.0) / 10000.0
    grp_rets = {g: [] for g in range(n_groups)}
    dates = []
    prev_members = {}
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
        members = {}
        for g in range(n_groups):
            sel = set(bins[bins == g].index)
            members[g] = sel
            base = float(r[list(sel)].mean()) if sel else np.nan
            if g in prev_members:
                churn = len(sel ^ prev_members[g]) / max(len(prev_members[g]), 1)
            else:
                churn = 1.0
            grp_rets[g].append(base - 2.0 * cost * churn)
        prev_members = members
    if not dates:
        return {"groups": {}, "long_short": [], "dates": []}
    df = pd.DataFrame(grp_rets, index=dates)
    cum = (1 + df).cumprod()
    ls = cum[n_groups - 1] / cum[0]
    str_idx = [str(d.date()) for d in cum.index]
    return {
        "groups": {int(g): dict(zip(str_idx, cum[g].round(4))) for g in range(n_groups)},
        "long_short": {"cumulative": dict(zip(str_idx, ls.round(4))),
                       "final": (float(ls.iloc[-1]) if len(ls) else None)},
        "dates": str_idx,
    }


def _verdict(split: dict, cost_result: dict,
             horizon_status: str, train_frac: float,
             ic: dict) -> dict:
    if horizon_status == "insufficient_sample":
        return {"state": "insufficient_sample",
                "note": "历史样本不足，无法形成稳定统计"}
    test_summary = split.get("test", {}).get("summary", {})
    train_summary = split.get("train", {}).get("summary", {})
    test_ic = test_summary.get("ic")
    train_ic = train_summary.get("ic")
    n = int(test_summary.get("n", 0))
    if n < _MIN_PERIODS:
        return {"state": "weak_sample",
                "note": "测试集样本过少，结论不稳定"}
    if test_ic is None or train_ic is None:
        return {"state": "weak_sample", "note": "IC 数据缺失，无法判定"}
    same_sign = (test_ic >= 0) == (train_ic >= 0) and abs(test_ic) > 0.02
    ls_gross = split.get("test_decile", {}).get("long_short") or {}
    if isinstance(ls_gross, list):
        gross_final = float(ls_gross[-1]) if ls_gross else None
    else:
        gross_final = ls_gross.get("final")
    net_final = cost_result.get("long_short", {}).get("final")
    if not same_sign:
        return {"state": "unstable_oos",
                "note": "测试集 IC 与训练集不同向，样本外不稳定"}
    if gross_final is not None and net_final is not None:
        if abs(gross_final) > 0.05 and abs(net_final) / abs(gross_final) < 0.30:
            return {"state": "cost_sensitive",
                    "note": "毛收益存在但扣除成本后大幅缩小，对交易成本敏感"}
    return {"state": "validated", "note": "训练/测试方向一致，样本外稳健"}


def _horizon(factor: pd.DataFrame, close: pd.DataFrame,
             requested_codes: list[str], days: int, n_groups: int,
             train_frac: float, cost_bps: float = 0.0,
             walk_forward: bool = False) -> dict:
    fwd = bt_eval.forward_returns(close, days)
    coverage = _coverage(factor, fwd, requested_codes)
    valid_dates = factor.index.intersection(fwd.index)
    status = "ok" if len(valid_dates) >= _MIN_PERIODS else "insufficient_sample"
    split = _split_report(factor, close, fwd, train_frac, n_groups)
    ic_stats = _ic_report(factor, fwd)
    decile = bt_eval.decile_backtest(factor, fwd, n_groups)
    cost_block = {
        "enabled": cost_bps > 0,
        "cost_bps": float(cost_bps or 0.0),
        "model": "rebalance_churn_per_group",
        "decile_after_cost": _holding_rebalance_deciles(factor, fwd, n_groups, cost_bps),
    }
    if not cost_block["enabled"]:
        cost_block["decile_after_cost"] = {"groups": {}, "long_short": {}, "dates": []}
    wf_block = _walk_forward(factor, close, days, walk_forward)
    result = {
        "forward_days": int(days),
        "status": status,
        "coverage": coverage,
        "ic": ic_stats,
        "decile": decile,
        "cutoff": split.get("cutoff"),
        "train_range": split.get("train_range", []),
        "test_range": split.get("test_range", []),
        "train": split.get("train", {"summary": {"n": 0}}),
        "test": split.get("test", {"summary": {"n": 0}}),
        "cost": cost_block,
        "decile_after_cost": cost_block["decile_after_cost"],
        "walk_forward": wf_block,
        "verdict": _verdict(split, cost_block.get("decile_after_cost", {}),
                            status, train_frac, ic_stats),
    }
    return result


def _walk_forward(factor: pd.DataFrame, close: pd.DataFrame,
                  days: int, enabled: bool) -> dict:
    if not enabled:
        return {"enabled": False}
    wf = None
    try:
        wf = bt_robust.rolling_walk_forward(factor, close, n=days)
    except Exception as exc:
        wf = {"error": str(exc), "n_segments": 0}
    return {
        "enabled": True,
        "oos_ic_mean": None, "oos_ic_median": None,
        "overfit_frac": None, "segments": [],
        **(wf or {}),
    }


def run_factor_research(
    universe: str,
    codes: list[str],
    factor: str,
    start: str,
    end: str,
    forward_days: Iterable[int] = (1, 5, 10, 20),
    n: int = 20,
    n_groups: int = 5,
    train_frac: float = 0.6,
    cost_bps: float = 0.0,
    walk_forward: bool = False,
) -> dict:
    """执行一组前瞻周期的历史因子研究。
    cost_bps: 每次调仓单边成本(万分之),>0 时算成本后分层。
    walk_forward: True 时附加滚动样本外 IC。"""
    requested_codes = _clean_codes(codes)
    horizons = [int(x) for x in forward_days if int(x) > 0]
    horizons = list(dict.fromkeys(horizons))
    context = {
        "universe": universe,
        "codes": requested_codes,
        "factor": factor,
        "start": start,
        "end": end,
        "forward_days": horizons,
        "n": int(n),
        "n_groups": int(n_groups),
        "train_frac": float(train_frac),
        "cost_bps": float(cost_bps or 0.0),
        "walk_forward_enabled": bool(walk_forward),
        "source": f"{universe}_daily",
        "adjust": "qfq/local when available",
        "realtime_factors_excluded": True,
    }
    if not requested_codes:
        return {"status": "invalid_request", "message": "codes 不能为空", "context": context, "horizons": {}}
    if not horizons:
        return {"status": "invalid_request", "message": "forward_days 不能为空", "context": context, "horizons": {}}

    try:
        close = bt_eval.load_panel(universe, requested_codes, start, end, "close")
        amount = bt_eval.load_panel(universe, requested_codes, start, end, "amount")
    except Exception as exc:
        return {"status": "error", "message": f"历史面板加载失败: {exc}", "context": context, "horizons": {}}
    if close is None or close.empty:
        return {
            "status": "no_history",
            "message": "无历史数据，先 /api/backtest/fetch",
            "context": context,
            "quality": {"missing_codes": requested_codes},
            "horizons": {},
        }

    try:
        factor_values = bt_eval.compute_factor(
            close, factor, params={"n": int(n)}, amount=amount,
        )
    except Exception as exc:
        return {"status": "error", "message": f"因子计算失败: {exc}", "context": context, "horizons": {}}

    history_codes = [c for c in requested_codes if c in close.columns]
    missing_codes = [c for c in requested_codes if c not in close.columns]
    result = {
        "status": "ok",
        "message": "历史因子统计完成",
        "context": context,
        "quality": {
            "missing_codes": missing_codes,
            "history_codes": history_codes,
            "coverage_rate": round(len(history_codes) / len(requested_codes), 4),
            "low_coverage": len(history_codes) < len(requested_codes),
        },
        "horizons": {},
    }
    for days in horizons:
        result["horizons"][str(days)] = _horizon(
            factor_values, close, requested_codes, days, int(n_groups),
            float(train_frac), cost_bps=float(cost_bps or 0.0),
            walk_forward=bool(walk_forward),
        )
    if all(v["status"] == "insufficient_sample" for v in result["horizons"].values()):
        result["status"] = "insufficient_sample"
        result["message"] = "历史样本不足，无法形成稳定统计"
    return result

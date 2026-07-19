# -*- coding: utf-8 -*-
"""防过拟合：walk-forward、bootstrap IC、前视守卫、幸存者偏差告警。

合规：研究用途，不输出买卖点，不承诺收益。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import eval as bt_eval


def walk_forward(factor: pd.DataFrame, close: pd.DataFrame, n: int = 5,
                 train_frac: float = 0.6) -> dict:
    """训练段(前 train_frac)与测试段(后段)分别报 IC/IR，对比衰减。
    n = 前瞻收益天数。"""
    fwd = bt_eval.forward_returns(close, n)
    idx = factor.index
    if len(idx) < 20:
        return {"error": "样本不足(<20 期)"}
    cut = idx[int(len(idx) * train_frac)]
    train_f, test_f = factor[factor.index <= cut], factor[factor.index > cut]
    train_r, test_r = fwd[fwd.index <= cut], fwd[fwd.index > cut]
    ic_tr = bt_eval.ic_series(train_f, train_r)
    ic_te = bt_eval.ic_series(test_f, test_r)
    s_tr = bt_eval.ic_summary(ic_tr)
    s_te = bt_eval.ic_summary(ic_te)
    ic_tr_mean = s_tr.get("ic")
    ic_te_mean = s_te.get("ic")
    decay = None
    overfit = False
    if ic_tr_mean not in (None, 0) and ic_te_mean is not None:
        decay = (ic_tr_mean - ic_te_mean) / abs(ic_tr_mean)
        overfit = abs(decay) > 0.5
    return {
        "train": s_tr,
        "test": s_te,
        "ic_decay": round(decay, 4) if decay is not None else None,
        "overfit_warning": bool(overfit),
        "cutoff": str(cut),
    }


def rolling_walk_forward(factor: pd.DataFrame, close: pd.DataFrame,
                         n: int = 5, train_months: int = 12,
                         test_months: int = 2, step_months: int = 2) -> dict:
    """滚动 walk-forward：训练段 vs 测试段 IC 衰减 + 过拟合段占比。
    train_ic=0 时 decay=None（防除零）。"""
    fwd = bt_eval.forward_returns(close, n)
    idx = factor.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx)
    months = pd.PeriodIndex(idx.to_period("M")).drop_duplicates().sort_values()
    total = len(months)
    need = train_months + test_months
    if total < need:
        return {"error": f"样本不足(需≥{need}月，现有{total}月)", "n_segments": 0}

    segments = []
    oos_ics = []
    start = 0
    while start + need <= total:
        train_per = months[start:start + train_months]
        test_per = months[start + train_months:start + need]
        train_start = train_per[0].start_time
        test_start = test_per[0].start_time
        test_end = test_per[-1].end_time
        train_idx = idx[(idx >= train_start) & (idx < test_start)]
        test_idx = idx[(idx >= test_start) & (idx < test_end)]
        ic_tr = bt_eval.ic_series(factor.loc[train_idx], fwd.loc[train_idx]) if len(train_idx) else pd.Series(dtype=float)
        ic_te = bt_eval.ic_series(factor.loc[test_idx], fwd.loc[test_idx]) if len(test_idx) else pd.Series(dtype=float)
        s_tr = bt_eval.ic_summary(ic_tr)
        s_te = bt_eval.ic_summary(ic_te)
        tr_ic, te_ic = s_tr.get("ic"), s_te.get("ic")
        decay = None
        if tr_ic not in (None, 0) and te_ic is not None:
            decay = (tr_ic - te_ic) / abs(tr_ic)
        segments.append({
            "train_range": [str(train_per[0]), str(train_per[-1])],
            "test_range": [str(test_per[0]), str(test_per[-1])],
            "train_ic": tr_ic, "test_ic": te_ic,
            "decay": round(decay, 4) if decay is not None else None,
        })
        if te_ic is not None:
            oos_ics.append(te_ic)
        start += step_months

    over = [s for s in segments if s["decay"] is not None and s["decay"] > 0.5]
    overfit_frac = round(len(over) / len(segments), 4) if segments else 0.0
    return {
        "segments": segments,
        "n_segments": len(segments),
        "oos_ic_mean": round(float(np.mean(oos_ics)), 4) if oos_ics else None,
        "oos_ic_median": round(float(np.median(oos_ics)), 4) if oos_ics else None,
        "overfit_frac": overfit_frac,
    }


def bootstrap_ic(ic_series: pd.Series, n_boot: int = 1000,
                 seed: int = 42) -> dict:
    """IC 均值的 bootstrap 置信区间。"""
    s = ic_series.dropna().values
    if len(s) < 5:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": len(s)}
    rng = np.random.default_rng(seed)
    boots = rng.choice(s, size=(n_boot, len(s)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "mean": round(float(s.mean()), 4),
        "ci_low": round(float(lo), 4),
        "ci_high": round(float(hi), 4),
        "n": int(len(s)),
    }


def survivorship_note() -> str:
    """幸存者偏差近似告警(显式，不静默)。

    兼容保留：旧调用方仍可取字符串告警。新代码优先用
    ``survivorship_status()`` 拿结构化字段。
    """
    return ("universe 用当前成分近似时点成分(akshare 时点成分不全)；"
            "已退市/ST 标的可能缺失，结果存在幸存者偏差，仅供参考。")


def survivorship_status() -> dict:
    """结构化幸存者偏差告警（轻量方案：标注覆盖盲区，不建退市表）。

    合规：仅陈述覆盖盲区事实，不预测收益、不荐股。``delisted_codes``
    由调用方记账传入（见 ``backtest/engine.run_backtest``），本函数不
    维护退市清单——akshare 不提供历史退市列表，已退市标的多缺失。
    """
    return {
        "note": ("universe 用当前成分近似时点成分(akshare 时点成分不全)；"
                 "已退市/ST 标的可能缺失，结果存在幸存者偏差，仅供参考。"),
        "universe_approximation": True,
        "delisted_coverage": "akshare 不提供退市清单，已退市标的多缺失",
    }

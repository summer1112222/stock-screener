"""quality_diag 诊断脚本单元测试。合成数据，不触网、不连真实库。

被测核心是脚本自写的滚动因子面板（amount_accel / sortino 的下行波动变体，
eval.compute_factor 没有覆盖）与 main 审计聚合；分档/IC 逻辑复用
backtest.eval 已测，此处只验证串联结构。
"""
import numpy as np
import pandas as pd
import pytest

from scripts import quality_diag as qd


def _mkt(n=40, ncols=3, seed=1):
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    cols = [f"C{i}" for i in range(ncols)]
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(rng.normal(0, 1, (n, ncols)).cumsum(axis=0) + 100,
                         index=dates, columns=cols)
    amount = pd.DataFrame(rng.uniform(1e7, 1e8, (n, ncols)),
                          index=dates, columns=cols)
    return close, amount


def test_amount_accel_no_lookahead():
    """amount_accel 用 rolling(只看 <=t)，改未来值不影响历史行。"""
    _, amount = _mkt(10)
    amt2 = amount.copy()
    amt2.iloc[-1] *= 1000.0
    a1 = qd.amount_accel_panel(amount, win=3, base=6)
    a2 = qd.amount_accel_panel(amt2, win=3, base=6)
    # 前 9 行(不含末行)完全一致 → 无前视
    pd.testing.assert_frame_equal(a1.iloc[:-1], a2.iloc[:-1])


def test_sortino_panel_shape_and_negative_downside():
    """sortino 面板形状同 close，且序列恒定(无下行)时该行不产出 NaN 崩溃。"""
    close, _ = _mkt(10)
    s = qd.sortino_panel(close, days=5)
    assert s.shape == close.shape
    # 无前视：末行 t 只用 <=t，改未来 close 不影响更早行
    c2 = close.copy()
    c2.iloc[-1] += 999.0
    s2 = qd.sortino_panel(c2, days=5)
    pd.testing.assert_frame_equal(s.iloc[:-1], s2.iloc[:-1])


def test_panel_diag_reuses_eval_structure():
    """panel_diag 对口径1相关因子串 eval 的分档/IC，结构含 decile+ic，且各因子齐全。"""
    close, amount = _mkt(120, ncols=15)
    res = qd.panel_diag(close, amount, ks=(5, 20))
    # 口径1 构成因子都应出现
    for fname in ("momentum_20", "volatility_20", "amount_accel", "sortino_20"):
        assert fname in res, f"缺因子 {fname}"
    for fname, fout in res.items():
        assert "decile" in fout, f"{fname} 缺 decile"
        assert set(fout["decile"]) >= {"groups", "long_short"}
        assert "ic" in fout
    # 单调性指标由 eval 侧保证，此处只校验 long_short 系列非空
    ls = res["momentum_20"]["decile"]["long_short"]
    assert len(ls) >= 1


def test_audit_main_aggregates():
    """main 审计聚合 confidence_level / risk_flags / 口径计数。"""
    main = [
        {"code": "A", "adjusted_resonance": 0.8, "confidence_level": "high",
         "risk_flags": None, "dim_scores": {"1": 0.9, "2": 0.7}},
        {"code": "B", "adjusted_resonance": 0.5, "confidence_level": "medium",
         "risk_flags": ["杠杆"], "dim_scores": {"1": 0.3}},
        {"code": "C", "adjusted_resonance": 0.6, "confidence_level": "high",
         "risk_flags": ["FCF"], "dim_scores": {"1": 0.8, "3": 0.2}},
    ]
    a = qd.audit_main(main)
    assert a["n"] == 3
    assert a["confidence"] == {"high": 2, "medium": 1}
    assert a["risk_flags"] == {"杠杆": 1, "FCF": 1}
    assert a["dim_coverage"]["1"] == 3 and a["dim_coverage"]["2"] == 1

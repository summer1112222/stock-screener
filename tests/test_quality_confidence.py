# -*- coding: utf-8 -*-
"""优质筛选数据可信度 / 硬质量门槛 / 风险惩罚 纯函数测试。

覆盖 spec 2026-09-05 §3/§4/§5：_data_confidence、_quality_gate、
_risk_penalty、_confidence_multiplier。纯函数，不触网不依赖 DB。
"""
import pandas as pd
import pytest

from backtest import quality


def _spot_row():
    """有效 spot 行：代码/价格/成交额齐全。"""
    return {"code": "a", "name": "A", "price": 10.0, "turnover_rate": 0.05}


def _history(n, code="a"):
    """返回含 code 列的历史收盘面板，n 个有效交易日。"""
    dates = pd.bdate_range("2022-01-01", periods=n)
    return pd.DataFrame({code: [10.0] * n}, index=dates)


# ------------------------------------------------------------------
# _data_confidence
# ------------------------------------------------------------------
def test_complete_stock_has_high_confidence():
    res = quality._data_confidence(
        "a", _spot_row(), _history(60),
        {1: .8, 2: .8, 3: .8, 4: .8, 5: .8},
        behavior_days=5, fundamental_ok=True, research_count=3,
    )
    assert res["level"] == "high"
    assert res["score"] >= .75


def test_missing_history_and_fundamental_lower_confidence():
    res = quality._data_confidence(
        "a", _spot_row(), _history(10), {3: .8},
        behavior_days=1, fundamental_ok=False, research_count=0,
    )
    assert res["level"] == "low"
    assert res["components"]["history"] == 0.0
    assert res["components"]["fundamental"] == .5
    assert res["warnings"]


def test_etf_excludes_inapplicable_components():
    res = quality._data_confidence(
        "a", _spot_row(), _history(60), {1: .8, 3: .8},
        universe="etf", behavior_days=5, fundamental_ok=None, research_count=None,
    )
    assert res["components"]["fundamental"] is None
    assert res["components"]["research"] is None
    assert res["level"] == "high"


def test_flow_absent_lowers_confidence():
    res = quality._data_confidence(
        "a", _spot_row(), _history(60), {1: .8},
        behavior_days=0, fundamental_ok=False, research_count=2,
    )
    assert res["components"]["flow"] == 0.0
    assert res["components"]["history"] == 1.0
    assert res["score"] == pytest.approx(.15 + .25 + .15 + .10)  # 满分时 flow 缺席拉低
    assert res["level"] == "medium"


def test_spot_missing_price_zero():
    res = quality._data_confidence(
        "a", {"code": "a", "price": None, "turnover_rate": None}, _history(60),
        {1: .8}, behavior_days=5, fundamental_ok=True, research_count=2,
    )
    assert res["components"]["spot"] == 0.0


# ------------------------------------------------------------------
# _quality_gate
# ------------------------------------------------------------------
def test_financial_red_flags_fail_hard_gate():
    gate = quality._quality_gate(
        {}, {"score": .9, "level": "high"},
        {"goodwill_to_equity_pct": 40, "debt_ratio_latest": 60},
    )
    assert gate["hard_pass"] is False
    assert "商誉" in " ".join(gate["risk_flags"])


def test_high_debt_ratio_fails_hard_gate():
    gate = quality._quality_gate(
        {}, {"score": .9, "level": "high"},
        {"goodwill_to_equity_pct": 0, "debt_ratio_latest": 80},
    )
    assert gate["hard_pass"] is False
    assert any("负债" in x for x in gate["risk_flags"])


def test_single_day_flow_is_flagged_not_hard_reject():
    gate = quality._quality_gate({}, {"score": .8, "level": "high"}, behavior_days=1)
    assert any("脉冲" in x for x in gate["risk_flags"])


def test_low_confidence_fails_hard_gate_in_strict():
    gate = quality._quality_gate({}, {"score": .3, "level": "low"})
    assert gate["hard_pass"] is False


def test_clean_high_confidence_passes():
    gate = quality._quality_gate(
        {}, {"score": .9, "level": "high"},
        {"goodwill_to_equity_pct": 0, "debt_ratio_latest": 40, "fcf_to_netincome": 1.2},
        behavior_days=10,
    )
    assert gate["hard_pass"] is True
    assert not gate["risk_flags"]


# ------------------------------------------------------------------
# _risk_penalty / _confidence_multiplier
# ------------------------------------------------------------------
def test_risk_penalty_reduces_adjusted_score():
    gate = {"hard_pass": True, "risk_flags": ["高杠杆ROE", "高波动"]}
    assert quality._risk_penalty({}, gate, {1: .05}) > 0
    assert quality._confidence_multiplier("medium") == .85


def test_risk_penalty_maps_flags_and_caps():
    gate = {"hard_pass": True, "risk_flags": ["高杠杆ROE", "弱FCF", "高波动", "资金脉冲"]}
    p = quality._risk_penalty({}, gate, {1: .05})
    assert p == 5.0  # 1.5+1.5+2.0+1.0=6.0 被 cap 到 5.0


def test_confidence_multiplier_levels():
    assert quality._confidence_multiplier("high") == 1.0
    assert quality._confidence_multiplier("medium") == .85
    assert quality._confidence_multiplier("low") == .65

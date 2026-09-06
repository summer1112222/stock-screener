# -*- coding: utf-8 -*-
"""优质筛选数据可信度 / 硬质量门槛 / 风险惩罚 纯函数测试。

覆盖 spec 2026-09-05 §3/§4/§5：_data_confidence、_quality_gate、
_risk_penalty、_confidence_multiplier。纯函数，不触网不依赖 DB。
"""
import pandas as pd
import pytest

from backtest import quality
from data import db


# ------------------------------------------------------------------
# quality_rank 集成：strict 过滤 / 缓存键 / 降级
# ------------------------------------------------------------------
# 精简 spot mock：口径1 无历史、口径2 spot 代理、口径3 top_by_amount 降级
SPOT = [
    {"code": "000001", "name": "甲", "latest_price": 10.0, "change_pct": 2.0,
     "turnover_amount": 1e8, "turnover_rate": 3.0, "main_net_inflow": 5e7,
     "board": "银行"},
    {"code": "000002", "name": "乙", "latest_price": 8.0, "change_pct": 1.0,
     "turnover_amount": 8e7, "turnover_rate": 2.0, "main_net_inflow": 1e7,
     "board": "地产"},
]


def _mock_pipeline(monkeypatch, history=None):
    """按 test_quality.py 既有模式 mock 数据源。history=None→空历史(口径1低可信)。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel",
                        lambda *a, **k: history if history is not None else pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)  # 口径2 → spot 代理
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount",
                        lambda **kw: {"rows": [{"code": "000001", "amount": 1e9}],
                                      "total": 1})


def test_low_confidence_not_in_strict_main(monkeypatch):
    _mock_pipeline(monkeypatch, history=None)
    # 000001 confidence=0.5(medium)；min_confidence=0.6 视为低于门槛过滤出 main
    res = quality.quality_rank("stock", min_dims=2, min_confidence=0.6)
    assert res["main"] == []
    assert res["confidence_summary"]["low_excluded"] >= 1
    assert res["selection_mode"] == "degraded"
    # 低可信度标的保留在 by_dim（spec: 只进 by_dim 不进 main）
    assert any(x["code"] == "000001" for x in res["by_dim"].get(3, []))


def test_strict_off_keeps_old_inclusion(monkeypatch):
    _mock_pipeline(monkeypatch, history=None)
    res = quality.quality_rank("stock", min_dims=2, strict_quality=False)
    assert any(r["code"] == "000001" for r in res["main"])
    assert res["selection_mode"] == "loose"


def test_high_confidence_stays_in_strict_main(monkeypatch):
    dates = pd.bdate_range("2022-01-01", periods=60)
    hist = pd.DataFrame({"000001": [10.0] * 60, "000002": [9.0] * 60}, index=dates)
    _mock_pipeline(monkeypatch, history=hist)
    res = quality.quality_rank("stock", min_dims=2)
    assert any(r["code"] == "000001" for r in res["main"])
    it = next(r for r in res["main"] if r["code"] == "000001")
    assert it["data_confidence"] >= 0.50
    assert "raw_resonance" in it and "adjusted_resonance" in it
    assert it["resonance"] == it["adjusted_resonance"]


def test_new_parameters_are_in_cache_key(monkeypatch):
    _mock_pipeline(monkeypatch, history=None)
    quality._RESULT_CACHE.clear()
    quality.quality_rank("stock", strict_quality=True, risk_penalty=True)
    quality.quality_rank("stock", strict_quality=False, risk_penalty=False)
    assert len(quality._RESULT_CACHE) >= 2


def test_etf_not_penalized_for_inapplicable_components(monkeypatch):
    etf = [{"code": "510300", "name": "沪深300ETF", "latest_price": 4.0,
            "change_pct": 1.0, "turnover_amount": 1e8, "turnover_rate": 2.0,
            "main_net_inflow": 1e7}]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: etf if table == "etf_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel",
                        lambda *a, **k: pd.DataFrame())
    res = quality.quality_rank("etf", min_turnover=5e7, limit_pct=9.9)
    it = next((x for x in res["main"] if x["code"] == "510300"), None)
    assert it is not None
    assert it["confidence_level"] in ("high", "medium")


def test_api_quality_passes_confidence_params(monkeypatch):
    """/api/quality 透传 strict_quality/min_confidence/risk_penalty，且不丢免责声明。"""
    from fastapi.testclient import TestClient
    from api import server
    captured = {}

    def fake_quality_rank(**kw):
        captured.update(kw)
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": 1, "refine_status": "skip",
                "confidence_summary": {"high": 0, "medium": 0, "low": 0, "low_excluded": 0},
                "selection_mode": "strict",
                "cand_disclaimer": "多口径共振机械排序观察清单", "error": None}

    monkeypatch.setattr("backtest.quality.quality_rank", fake_quality_rank)
    client = TestClient(server.app)
    r = client.get("/api/quality?strict_quality=false&min_confidence=0.3&risk_penalty=false")
    assert r.status_code == 200
    assert captured.get("strict_quality") is False
    assert captured.get("min_confidence") == 0.3
    assert captured.get("risk_penalty") is False
    assert "cand_disclaimer" in r.json()["data"]



def _spot_row():
    """有效 spot 行：代码/价格/成交额齐全。快照价格列是 latest_price。"""
    return {"code": "a", "name": "A", "latest_price": 10.0, "turnover_rate": 0.05}


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
        "a", {"code": "a", "latest_price": None, "turnover_rate": None}, _history(60),
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


# ------------------------------------------------------------------
# 严格合格元数据（spec 2026-09-06：eligibility/exclusion_reasons/
# selection_status/eligible_count/requested_limit/selection_note/excluded_summary）
# ------------------------------------------------------------------
def test_strict_eligibility_reasons_order_and_blocking():
    """全失败输入：原因按确定性顺序，eligibility=False。"""
    ok, reasons = quality._strict_eligibility(
        hard_gate_pass=False, conf_score=0.2, min_confidence=0.5,
        risk_flags=["资金脉冲(仅单日)"], hits=1, eff_min_dims=2, resonance_raw=None)
    assert ok is False
    assert reasons == ["hard_gate", "confidence", "risk_flags",
                       "min_dims", "no_resonance"]


def test_strict_eligibility_risk_flags_non_blocking():
    """risk_flags 仅记录不阻断（2026-09-05 语义：脉冲是惩罚依据非硬拒）。"""
    ok, reasons = quality._strict_eligibility(
        hard_gate_pass=True, conf_score=0.8, min_confidence=0.5,
        risk_flags=["资金脉冲(仅单日)"], hits=3, eff_min_dims=2, resonance_raw=12.0)
    assert ok is True
    assert reasons == ["risk_flags"]


def test_strict_eligibility_clean_pass():
    ok, reasons = quality._strict_eligibility(
        hard_gate_pass=True, conf_score=0.9, min_confidence=0.5,
        risk_flags=[], hits=2, eff_min_dims=2, resonance_raw=8.0)
    assert ok is True
    assert reasons == []


def _strict_res(monkeypatch, conf_map=None, history=None, **kw):
    """集成辅助：默认 2 标的 spot mock；conf_map 可定点覆盖 _data_confidence。"""
    _mock_pipeline(monkeypatch, history=history)
    quality._RESULT_CACHE.clear()
    if conf_map:
        real = quality._data_confidence

        def fake(code, *a, **k):
            if code in conf_map:
                s, lvl = conf_map[code]
                return {"score": s, "level": lvl, "components": {}, "warnings": []}
            return real(code, *a, **k)
        monkeypatch.setattr(quality, "_data_confidence", fake)
    limit = kw.pop("limit", 5)
    return quality.quality_rank("stock", min_dims=2, limit=limit, **kw)


def test_strict_insufficient_not_padded(monkeypatch):
    """严格合格不足 limit：不补齐，selection_status=insufficient+note。"""
    res = _strict_res(monkeypatch, min_confidence=0.6)
    assert res["selection_status"] == "insufficient"
    assert res["requested_limit"] == 5
    assert res["eligible_count"] < 5
    assert len(res["main"]) <= res["eligible_count"]
    assert res["selection_note"]


def test_hard_gate_reason_isolated(monkeypatch):
    """low 级别→hard_gate 拒；score≥min_confidence 时不带 confidence 原因。"""
    res = _strict_res(monkeypatch, conf_map={"000001": (0.40, "low")},
                      min_confidence=0.3)
    it = next(x for x in res["by_dim"].get(3, []) if x["code"] == "000001")
    assert it["eligibility"] is False
    assert "hard_gate" in it["exclusion_reasons"]
    assert "confidence" not in it["exclusion_reasons"]
    assert all(r["code"] != "000001" for r in res["main"])


def test_confidence_reason_isolated(monkeypatch):
    """medium 级别过硬门槛但 score<min_confidence→仅 confidence 原因。"""
    res = _strict_res(monkeypatch, conf_map={"000001": (0.40, "medium")},
                      min_confidence=0.6)
    it = next(x for x in res["by_dim"].get(3, []) if x["code"] == "000001")
    assert it["eligibility"] is False
    assert "confidence" in it["exclusion_reasons"]
    assert "hard_gate" not in it["exclusion_reasons"]


def test_excluded_summary_counts_blocking_reasons(monkeypatch):
    res = _strict_res(monkeypatch, min_confidence=0.6)
    assert isinstance(res["excluded_summary"], dict)
    assert res["excluded_summary"].get("confidence", 0) >= 1
    # risk_flags 非阻断原因，不进排除统计
    assert "risk_flags" not in res["excluded_summary"]


def test_loose_mode_keeps_existing_behavior(monkeypatch):
    """非严格模式：main 包含语义不变，selection_status 恒 ok。"""
    res = _strict_res(monkeypatch, strict_quality=False, min_confidence=0.6)
    assert any(r["code"] == "000001" for r in res["main"])
    assert res["selection_mode"] == "loose"
    assert res["selection_status"] == "ok"
    assert res["selection_note"] is None


def test_selection_status_ok_when_enough(monkeypatch):
    dates = pd.bdate_range("2022-01-01", periods=60)
    hist = pd.DataFrame({"000001": [10.0] * 60, "000002": [9.0] * 60}, index=dates)
    res = _strict_res(monkeypatch, history=hist, limit=1)
    assert res["eligible_count"] >= 1
    assert res["selection_status"] == "ok"
    assert res["selection_note"] is None
    assert all(it["eligibility"] for it in res["main"])

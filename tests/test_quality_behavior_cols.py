# -*- coding: utf-8 -*-
"""quality 主清单附主力行为/阶段测试。mock 行为/阶段函数,不触网。
仓库根目录跑：python -m pytest tests/test_quality_behavior_cols.py -q"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from backtest import quality


def _mk_main():
    return [
        {"code": "000001", "name": "平A", "adjusted_resonance": 0.8, "warnings": []},
        {"code": "600519", "name": "贵C", "adjusted_resonance": 0.6, "warnings": []},
    ]


def test_enrich_attaches_behavior_and_phase():
    """行为列附加；main 顺序与排序不变。"""
    def _batch(codes, days):
        return {"000001": {"streak_inflow": 5, "streak_outflow": 0,
                           "cum_inflow": 1e7, "margin_accel": 1e5, "north_cum": 2e6},
                "600519": {"streak_inflow": 0, "streak_outflow": 3,
                           "cum_inflow": -5e6, "margin_accel": None, "north_cum": None}}

    def _phase(code, days):
        return {"phase": "吸筹" if code == "000001" else "出货",
                "confidence": 0.7 if code == "000001" else 0.65}

    with patch("screener.smart_money._behavior_batch", side_effect=_batch), \
         patch("screener.smart_money.main_force_phase", side_effect=_phase):
        out = quality._enrich_main_behavior(_mk_main(), "stock", days=20)
    it = out[0]
    assert it["streak_inflow"] == 5 and it["cum_net"] == 1e7
    assert it["mf_phase"] == "吸筹" and it["behavior_group"] == "高质量×资金收集"


def test_enrich_out_distribution_warns_but_no_pick_change():
    """出货+高置信只进 warnings,不剔除不扣分。"""
    def _batch(codes, days):
        return {"600519": {"streak_inflow": 0, "streak_outflow": 5,
                           "cum_inflow": -8e6, "margin_accel": None, "north_cum": None}}

    def _phase(code, days):
        return {"phase": "出货", "confidence": 0.7}

    with patch("screener.smart_money._behavior_batch", side_effect=_batch), \
         patch("screener.smart_money.main_force_phase", side_effect=_phase):
        out = quality._enrich_main_behavior(_mk_main(), "stock", days=20)
    it = next(x for x in out if x["code"] == "600519")
    assert it["mf_phase"] == "出货"
    assert any("出货" in w for w in it["warnings"])
    assert it["adjusted_resonance"] == 0.6  # 不扣分


def test_enrich_etf_skips():
    """ETF universe 不执行行为/阶段富化。"""
    with patch("screener.smart_money._behavior_batch", side_effect=AssertionError) as b, \
         patch("screener.smart_money.main_force_phase", side_effect=AssertionError):
        out = quality._enrich_main_behavior(_mk_main(), "ETF", days=20)
    assert out == _mk_main()


def test_enrich_phase_error_degrades_to_none():
    """main_force_phase 异常→ mf_phase/mf_confidence None,不崩。"""
    def _batch(codes, days):
        return {"000001": {"streak_inflow": 2, "streak_outflow": 0,
                           "cum_inflow": 3e6, "margin_accel": None, "north_cum": None},
                "600519": {}}

    def _phase(code, days):
        raise RuntimeError("boom")

    with patch("screener.smart_money._behavior_batch", side_effect=_batch), \
         patch("screener.smart_money.main_force_phase", side_effect=_phase):
        out = quality._enrich_main_behavior(_mk_main(), "stock", days=20)
    assert out[0]["mf_phase"] is None and out[0]["mf_confidence"] is None

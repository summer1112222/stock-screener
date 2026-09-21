# -*- coding: utf-8 -*-
"""macro_style 全市场风格基调状态机：纯函数单测（mock dict 直接传参，不查库不触网）。"""
import math

import pytest

from screener.macro_style import macro_style, _factor_pe, _factor_zt, _factor_margin, _factor_breadth


# 各因子映射单元的边界
def test_factor_pe_low_valu_market_is_offensive():
    # 估值分位低 → 进攻方向
    assert _factor_pe(0.0) == pytest.approx(1.0)
    assert _factor_pe(0.5) == pytest.approx(0.0)
    assert _factor_pe(1.0) == pytest.approx(-1.0)


def test_factor_zt_scaled_and_capped():
    # 涨停家数多→进攻，零→防御，超 ref 饱和到 +1
    assert _factor_zt(0) == pytest.approx(-1.0)
    assert _factor_zt(50) == pytest.approx(1.0)      # ZT_REF 处饱和
    assert _factor_zt(200) == pytest.approx(1.0)     # 远超 ref 封顶
    assert _factor_zt(25) == pytest.approx(0.0)      # 半参考 → 中性


def test_factor_margin_sign():
    # 两融扩张→进攻，收缩→防御，用 tanh 归一
    assert _factor_margin(0) == pytest.approx(0.0)
    assert _factor_margin(100) > 0
    assert _factor_margin(-100) < 0
    assert _factor_margin(1e9) == pytest.approx(1.0, abs=1e-3)   # 大额饱和到 +1


def test_factor_breadth():
    # 涨跌家数比 >0.5 → 进攻
    assert _factor_breadth(100, 100) == pytest.approx(0.0)
    assert _factor_breadth(300, 100) == pytest.approx(0.5)   # ratio 0.75 → +0.5
    assert _factor_breadth(100, 300) == pytest.approx(-0.5)  # ratio 0.25 → -0.5
    assert _factor_breadth(1000, 0) == pytest.approx(1.0)    # 全涨 → 满进攻


# 状态机整体判定
def test_attack_style():
    m = {"pe_pct": 0.2, "zt_count": 80, "margin_chg": 80.0, "up_count": 3500, "down_count": 1200}
    out = macro_style(m)
    assert out["style"] == "attack"
    assert out["score"] > 0.2
    assert out["strength"] == pytest.approx(abs(out["score"]), abs=1e-6)


def test_defense_style():
    m = {"pe_pct": 0.85, "zt_count": 10, "margin_chg": -60.0, "up_count": 1000, "down_count": 3800}
    out = macro_style(m)
    assert out["style"] == "defense"
    assert out["score"] < -0.2


def test_neutral_style():
    m = {"pe_pct": 0.5, "zt_count": 25, "margin_chg": 0.0, "up_count": 2000, "down_count": 2000}
    out = macro_style(m)
    assert out["style"] == "neutral"
    assert -0.2 <= out["score"] <= 0.2


def test_all_missing_is_neutral():
    # 全因子缺失 → 不崩、诚实 neutral、strength 0
    out = macro_style({})
    assert out["style"] == "neutral"
    assert out["score"] == 0.0
    assert out["strength"] == 0.0


def test_partial_missing_uses_available():
    # 只有涨停因子可用 → 用该因子算，不回退 0，方向正确
    out = macro_style({"zt_count": 100})
    assert out["style"] == "attack"
    assert out["score"] > 0.2


def test_breadth_zero_total_excluded():
    # up+down==0 → breadth 因子缺失排除，不除零崩
    out = macro_style({"pe_pct": 0.5, "zt_count": 50, "margin_chg": 0.0, "up_count": 0, "down_count": 0})
    # 剩下 pe(中性)+zt(+1)+margin(中性) → 进攻方向
    assert out["style"] == "attack"


def test_returns_drivers():
    out = macro_style({"pe_pct": 0.3, "zt_count": 60})
    assert "drivers" in out
    assert "pe" in out["drivers"]
    assert "zt" in out["drivers"]
    # 每个驱动带 raw/contrib/weight
    d = out["drivers"]["zt"]
    assert set(d) >= {"raw", "contrib", "weight"}
    assert d["raw"] == 60


def test_coverage_full_vs_thin_evidence():
    # 四因子齐全 → coverage 满额(1.0)，多因子共振置信高
    full = macro_style({"pe_pct": 0.2, "zt_count": 80, "margin_chg": 80.0,
                        "up_count": 3500, "down_count": 1200})
    assert full["coverage"] == pytest.approx(1.0, abs=1e-6)
    # 只剩 pe_pct 单因子(其余缺失) → coverage 明显不足(<0.5)，弱证据
    thin = macro_style({"pe_pct": 0.2})
    assert thin["coverage"] < 0.5
    # 全缺失 → coverage 0
    empty = macro_style({})
    assert empty["coverage"] == 0.0

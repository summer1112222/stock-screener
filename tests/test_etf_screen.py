# tests/test_etf_screen.py
import screener.etf_screen as es

def test_valuation_percentile_low_is_cheap():
    # 当前值处于历史低位 → 低百分位(便宜)
    assert es.valuation_percentile(10.0, [10, 12, 14, 16, 18, 20]) < 0.5
    # 当前值历史新高 → 高分位(贵)
    assert es.valuation_percentile(20.0, [10, 12, 14, 16, 18, 20]) > 0.9

def test_quality_score_prefers_large_scale_low_fee():
    good = es.quality_score(scale_wan=500, turnover_rate=2.0, fee_bps=15, tracking_err=0.2)
    bad = es.quality_score(scale_wan=2, turnover_rate=0.1, fee_bps=80, tracking_err=3.0)
    assert good > bad

def test_long_score_combines_valuation_and_quality():
    cheap_good = es.long_score(valuation_pct=0.1, quality=0.9)
    expensive_poor = es.long_score(valuation_pct=0.9, quality=0.2)
    assert cheap_good > expensive_poor
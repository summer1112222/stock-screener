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


def test_short_scores_ranks_uptrend_higher(monkeypatch):
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"000001": np.linspace(10, 16, 30), "000002": np.linspace(16, 10, 30)}, index=idx)
    amt = pd.DataFrame(1e8, index=idx, columns=up.columns)
    from backtest import eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: up if f == "close" else amt)
    scores, det, cov = es.short_scores(up, amt, ["000001", "000002"], [5, 20])
    assert scores["000001"] > scores["000002"]   # 上行强于下行
    assert "momentum_5" in det["000001"]
    assert 0 < cov["000001"] <= 1.0


def test_short_scores_missing_factor_renormalizes(monkeypatch):
    from backtest import eval as bt_eval_2
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"000001": np.linspace(10, 16, 30)}, index=idx)
    monkeypatch.setattr(bt_eval_2, "load_panel", lambda u, c, s, e, f: up if f == "close" else None)
    scores, det, cov = es.short_scores(up, None, ["000001"], [5, 20])
    assert cov["000001"] >= 0.4  # momentum/volatility 仍可用
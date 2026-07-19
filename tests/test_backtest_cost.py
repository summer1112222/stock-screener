# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import engine as bt_engine


def _synth(months=24, n_codes=20, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=months*21)
    px = 10 + np.cumsum(rng.normal(0, 0.2, (len(dates), n_codes)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=[f"c{i}" for i in range(n_codes)])
    close = close.where(close > 1, 1.0)
    factor = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    half = len(dates)//2
    factor.iloc[:half] = np.tile(np.arange(n_codes)[::-1], (half,1))
    factor.iloc[half:] = np.tile(np.arange(n_codes), (len(dates)-half,1))
    return close, factor


def test_cost_reduces_nav():
    close, factor = _synth()
    r0 = bt_engine.run_backtest(close, factor, topn=5, freq="M", cost_bps=0.0)
    r30 = bt_engine.run_backtest(close, factor, topn=5, freq="M", cost_bps=30.0)
    eq0 = pd.Series(r0["equity_curve"]).astype(float).sort_index()
    eq30 = pd.Series(r30["equity_curve"]).astype(float).sort_index()
    assert eq30.iloc[-1] < eq0.iloc[-1], "有成本时净值应低于无成本"
    assert r30["total_cost_drag"] > 0
    assert r30["cost_bps"] == 30.0

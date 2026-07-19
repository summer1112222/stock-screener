# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import eval as bt_eval


def _panels():
    rng = np.random.default_rng(5)
    dates = pd.bdate_range("2022-01-01", periods=40)
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0, 0.2, (40, 4)), axis=0),
                         index=dates, columns=[f"c{i}" for i in range(4)])
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, (40, 4)),
                         index=dates, columns=close.columns)
    return close, amount


def test_reversal_5():
    close, amount = _panels()
    r = bt_eval.compute_factor(close, "reversal_5", params={"n": 5}, amount=amount)
    expected = -close.pct_change(5)
    pd.testing.assert_series_equal(r.iloc[-1].dropna(), expected.iloc[-1].dropna())


def test_reversal_suffix_overrides_params():
    close, amount = _panels()
    # 不传 n，factor_key 后缀 5 应优先 → 等价 -pct_change(5) 而非 -pct_change(20)
    r = bt_eval.compute_factor(close, "reversal_5", params={}, amount=amount)
    expected = -close.pct_change(5)
    pd.testing.assert_series_equal(r.iloc[-1].dropna(), expected.iloc[-1].dropna())


def test_amihud_shape_and_direction():
    close, amount = _panels()
    a = bt_eval.compute_factor(close, "amihud_20", params={"n": 20}, amount=amount)
    assert a.shape == close.shape
    assert (a.dropna() >= 0).all().all()


def test_amihud_no_amount_returns_empty():
    close, _ = _panels()
    a = bt_eval.compute_factor(close, "amihud_20", params={"n": 20}, amount=None)
    assert a.shape == close.shape and a.isna().all().all()

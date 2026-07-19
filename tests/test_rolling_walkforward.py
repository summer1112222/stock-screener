# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import robust as bt_robust


def _synth(months=24, n=20):
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2022-01-01", periods=months*21)
    factor = pd.DataFrame(rng.normal(0,1,(len(dates),n)), index=dates,
                          columns=[f"c{i}" for i in range(n)])
    px = 10 + np.cumsum(rng.normal(0,0.2,(len(dates),n)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=factor.columns)
    return factor, close


def test_rolling_wf_returns_segments():
    factor, close = _synth(months=24)
    res = bt_robust.rolling_walk_forward(factor, close, n=5)
    assert "error" not in res
    assert res["n_segments"] >= 1
    assert len(res["segments"]) == res["n_segments"]
    assert res["oos_ic_median"] is None or isinstance(res["oos_ic_median"], (int,float))
    assert 0.0 <= res["overfit_frac"] <= 1.0


def test_rolling_wf_too_short():
    factor, close = _synth(months=10)
    res = bt_robust.rolling_walk_forward(factor, close, n=5)
    assert res.get("n_segments") == 0
    assert "error" in res

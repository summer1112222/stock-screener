# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import engine as bt_engine, robust as bt_robust


def test_survivorship_status_shape():
    s = bt_robust.survivorship_status()
    assert isinstance(s, dict)
    assert s["universe_approximation"] is True
    assert "delisted_coverage" in s


def test_delisted_codes_only_bookkeeping():
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2022-01-01", periods=60)
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0,0.2,(60,5)),axis=0),
                         index=dates, columns=[f"c{i}" for i in range(5)])
    factor = pd.DataFrame(rng.normal(0,1,(60,5)), index=dates, columns=close.columns)
    r0 = bt_engine.run_backtest(close, factor, topn=3, freq="M", delisted_codes=None)
    r1 = bt_engine.run_backtest(close, factor, topn=3, freq="M", delisted_codes=["c0"])
    assert r0["delisted_declared"] == 0
    assert r1["delisted_declared"] == 1
    # 算法不变：equity_curve 完全一致（delisted_codes 仅记账）
    assert r0["equity_curve"] == r1["equity_curve"]

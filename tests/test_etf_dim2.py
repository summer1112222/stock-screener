# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import quality as bt_q


def _seed_etf_daily(codes, n=60):
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2022-01-01", periods=n)
    rows = []
    for c in codes:
        px = 10 + np.cumsum(rng.normal(0, 0.3, n))
        amt = rng.uniform(1e8, 1e9, n)
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": float(px[i]), "high": float(px[i]*1.01),
                         "low": float(px[i]*0.99), "close": float(px[i]),
                         "volume": float(amt[i]/10), "amount": float(amt[i])})
    bt_q.db.upsert_rows("etf_daily", rows)


def test_etf_dim2_not_empty(monkeypatch):
    codes = ["510300", "510050"]
    _seed_etf_daily(codes)
    import data.history as hist
    def _fake_bm(code, start="19900101", end="20991231"):
        rng = np.random.default_rng(22)
        dates = pd.bdate_range("2022-01-01", periods=60)
        bpx = 3000 + np.cumsum(rng.normal(0, 10, 60))
        df = pd.DataFrame({"date": [str(d.date()) for d in dates], "close": bpx})
        return df, True, ""
    monkeypatch.setattr(hist, "fetch_benchmark_hist", _fake_bm)
    bt_q.db.upsert_rows("etf_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e8, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="etf", days=20)
    ds = res.get("dim_status", {})
    assert "2" in ds
    assert ds["2"].startswith("ok")

# -*- coding: utf-8 -*-
import pandas as pd
import screener.smart_money as sm


def test_seat_winrate(monkeypatch):
    rows = [
        {"code": "000001", "name": "A", "actor": "游资A", "date": "2026-07-01",
         "channel": "龙虎榜", "amount": 1e8},
        {"code": "000001", "name": "A", "actor": "游资A", "date": "2026-07-10",
         "channel": "龙虎榜", "amount": 2e8},
    ]
    monkeypatch.setattr(sm.db, "query_rows",
                        lambda table, where="", params=(), order_by="", limit=0:
                        rows if table == "smart_money_action" else [])
    # close 需 ≥15 行(_fwd_ret 用 iloc[pos+k] 位置索引,k=5 需 entry 后≥5 行);
    # 7/01(pos0)=10→pos5=11 ret10%; 7/10(pos9)=10→pos14=12 ret20%
    idx = pd.date_range("2026-07-01", periods=20, freq="D")
    vals = [10.0] * 20
    vals[5] = 11.0   # 7/01 entry k=5 -> +10%
    vals[14] = 12.0  # 7/10 entry k=5 -> +20%
    close = pd.DataFrame({"000001": vals}, index=idx)
    monkeypatch.setattr("backtest.signals._uni_panels",
                        lambda u, codes: (close, None))
    r = sm.seat_winrate("游资A", k=5, days=180)
    assert r["actor"] == "游资A"
    assert r["samples"] == 2
    assert r["by_k"]["5"]["win_rate"] == 1.0
    assert 0.09 <= r["by_k"]["5"]["median_ret"] <= 0.21


def test_seat_winrate_national_team(monkeypatch):
    monkeypatch.setattr(sm, "_expand_national_team", lambda: ["中央汇金"])
    monkeypatch.setattr(sm.db, "query_rows",
                        lambda table, where="", params=(), order_by="", limit=0: [])
    monkeypatch.setattr("backtest.signals._uni_panels",
                        lambda u, codes: (None, None))
    r = sm.seat_winrate("国家队", k=5, days=180)
    assert r["samples"] == 0


def test_seat_winrate_nan_no_propagate(monkeypatch):
    """停牌日 reindex 产生 NaN c0/c1;float('nan') truthy 致旧 `if c0` 失守,
    NaN 传至 np.median/ret_k5→JSONResponse(allow_nan=False)500。守卫须拦截。"""
    rows = [{"code": "000001", "name": "A", "actor": "游资A",
             "date": "2026-07-01", "channel": "龙虎榜", "amount": 1e8}]
    monkeypatch.setattr(sm.db, "query_rows",
                        lambda table, where="", params=(), order_by="", limit=0:
                        rows if table == "smart_money_action" else [])
    import numpy as np
    idx = pd.date_range("2026-07-01", periods=20, freq="D")
    vals = [10.0] * 20
    vals[0] = np.nan        # entry 当日 c0=NaN(停牌)
    vals[5] = 11.0          # k=5 c1
    close = pd.DataFrame({"000001": vals}, index=idx)
    monkeypatch.setattr("backtest.signals._uni_panels",
                        lambda u, codes: (close, None))
    r = sm.seat_winrate("游资A", k=5, days=180)
    # NaN c0 须被守卫拦,_fwd_ret 返 None→不进 rets→by_k 无 5 键(samples=0)
    # 而非 by_k["5"]["median_ret"]=nan 致序列化 500
    assert "5" not in r["by_k"]
    assert r["samples"] == 0
    for rec in r["recent"]:
        v = rec.get("ret_k5")
        assert v is None or (isinstance(v, float) and not np.isnan(v))

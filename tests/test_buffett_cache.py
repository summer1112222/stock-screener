# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
import pandas as pd
import backtest.buffett as bt_buf


def _df():
    return pd.DataFrame({"指标": ["净资产收益率"], "2023-12-31": [15.0], "2022-12-31": [12.0]})


def _set_cache(code, df, ts):
    bt_buf.db.upsert_rows("financial_abstract_cache",
        [{"code": code, "payload_json": df.to_json(orient="records", force_ascii=False), "ts": ts}])


def test_cache_hit_no_network(monkeypatch):
    code = "000001"
    bt_buf.db.init_db()
    _set_cache(code, _df(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    called = {"net": False}
    def _boom(*a, **k):
        called["net"] = True
        raise RuntimeError("should not hit net")
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract", _boom)
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None and not df.empty
    assert stale is False
    assert called["net"] is False


def test_cache_miss_then_write(monkeypatch):
    code = "600000"
    bt_buf.db.init_db()
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract", lambda symbol: _df())
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None and not df.empty
    assert stale is False
    rows = bt_buf.db.query_rows("financial_abstract_cache", where="code=?", params=(code,))
    assert rows and rows[0]["code"] == code


def test_cache_expired_refetch(monkeypatch):
    code = "000002"
    bt_buf.db.init_db()
    old_ts = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    _set_cache(code, _df(), old_ts)
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract", lambda symbol: _df())
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None
    assert stale is False


def test_ak_off_falls_back_to_stale(monkeypatch):
    code = "000003"
    bt_buf.db.init_db()
    _set_cache(code, _df(), (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"))
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None
    assert stale is True

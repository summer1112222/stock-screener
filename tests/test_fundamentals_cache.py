# tests/test_fundamentals_cache.py
# -*- coding: utf-8 -*-
"""fundamentals 完整财报按需采集+缓存测试。"""
from datetime import datetime, timedelta
from types import ModuleType

import pandas as pd

from data import fundamentals, db


def _mock_ak(sheet_fn):
    m = ModuleType("akshare")
    m.stock_balance_sheet_by_report_em = sheet_fn
    m.stock_cash_flow_sheet_by_report_em = sheet_fn
    m.stock_profit_sheet_by_report_em = sheet_fn
    return m


def _tmp_db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()


def _cashflow_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "经营活动产生的现金流量净额": 1000.0,
        "购建固定资产、无形资产及其他长期资产支付的现金": 300.0,
    }])


def test_fetch_hit_cache(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    # tdx 不可用→走 akshare 路径(本测试意图:首次 akshare 拉取入库,二次命中缓存)
    monkeypatch.setattr(fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(fundamentals, "_AK_OK", True)
    called = {"n": 0}

    def _net(symbol):
        called["n"] += 1
        return _cashflow_df()

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_net))
    df1, stale1 = fundamentals.fetch("600519", "cashflow")
    assert df1 is not None and not stale1
    df2, stale2 = fundamentals.fetch("600519", "cashflow")
    assert df2 is not None and not stale2
    assert called["n"] == 1


def test_fetch_ak_fail_returns_none(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(fundamentals, "_AK_OK", True)

    def _err(symbol):
        raise RuntimeError("em blocked")

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_err))
    df, stale = fundamentals.fetch("600519", "cashflow")
    assert df is None


def test_fetch_stale_fallback(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    old_ts = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    payload = _cashflow_df().to_json(orient="records", force_ascii=False)
    db.upsert_rows("fundamentals_cache",
                   [{"code": "600519", "source": "cashflow",
                     "payload_json": payload, "ts": old_ts}])
    monkeypatch.setattr(fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(fundamentals, "_AK_OK", True)

    def _err(symbol):
        raise RuntimeError("em blocked")

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_err))
    df, stale = fundamentals.fetch("600519", "cashflow")
    assert df is not None and stale is True


def test_ak_ok_false_skips_net(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(fundamentals, "parse_tdx_financial", lambda c: None)
    monkeypatch.setattr(fundamentals, "_AK_OK", False)
    called = {"n": 0}

    def _net(symbol):
        called["n"] += 1
        return _cashflow_df()

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_net))
    df, stale = fundamentals.fetch("600519", "cashflow")
    assert df is None and called["n"] == 0

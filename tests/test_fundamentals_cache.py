# tests/test_fundamentals_cache.py
# -*- coding: utf-8 -*-
"""fundamentals 完整财报按需采集+缓存测试。"""
import time
import threading
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


def test_parse_tdx_with_timeout_daemon(monkeypatch):
    """底层 parse 卡死 > timeout 时，守卫必须在 timeout 内返回 None。

    根因：旧实现 with ThreadPoolExecutor + shutdown(wait=True) 的 __exit__ 会
    `wait=True` 等底层卡死的 parse 线程跑完，抵消 .result(timeout) 的超时——
    pytdx 全挂时每只实际仍卡满 ~40s（_TDX_PARSE_TIMEOUT 失效），冷 quality 的
    analyze_many deadline 因 prefetch 内每只 40s 而失真，/api/quality 冷路径必超时。
    改 daemon 线程 + join(timeout) 后：到点即返 None，卡死线程后台自灭不阻塞。"""
    monkeypatch.setattr(fundamentals, "_TDX_PARSE_TIMEOUT", 0.1)
    monkeypatch.setattr(fundamentals, "parse_tdx_financial",
                        lambda code: time.sleep(0.5) or {})
    t0 = time.time()
    r = fundamentals._parse_tdx_with_timeout("600519")
    dt = time.time() - t0
    assert r is None, "超时应返 None"
    assert dt < 0.35, f"守卫应在 timeout 内返回(实际 {dt:.2f}s，旧 with 实现需 0.5s)"

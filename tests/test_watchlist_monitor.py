# tests/test_watchlist_monitor.py
# -*- coding: utf-8 -*-
"""批量自选监控测试：DB migration + watchlist 函数 + live/signals/alert 路由。
mock tdx/scan_signals/db，不触网。仓库根目录跑。"""
import sys, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from data import db as dbmod, watchlist as wl
from fastapi.testclient import TestClient
from api.server import app


def test_migrate_adds_watchlist_alert_cols():
    """旧 watchlist 表无 alert 列 → _migrate 补列后含 alert_hi/alert_lo。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE watchlist(id INTEGER PRIMARY KEY, code TEXT, "
                 "name TEXT, note TEXT, added_ts TEXT)")
    dbmod._migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    assert "alert_hi" in cols, "_migrate 未给 watchlist 补 alert_hi"
    assert "alert_lo" in cols


@pytest.fixture
def wldb(tmp_path, monkeypatch):
    """隔离 DB：patch DB_PATH 到 tmp，init_db 建新表。"""
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    return wl


@pytest.fixture
def client_wldb(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    return TestClient(app)


def test_list_codes_dedup(wldb):
    wldb.add("000001", "平安", "")
    wldb.add("510300", "沪深300ETF", "")
    wldb.add("000001", "平安2", "")  # 同 code 更新不重复
    codes = wldb.list_codes()
    assert set(codes) == {"000001", "510300"} and len(codes) == 2


def test_set_alert_and_list_items(wldb):
    wid = wldb.add("000002", "万科", "")["id"]
    ok = wldb.set_alert(wid, 10.5, 9.0)
    assert ok is True
    it = [x for x in wldb.list_items() if x["code"] == "000002"][0]
    assert it["alert_hi"] == 10.5 and it["alert_lo"] == 9.0


def test_set_alert_none_clears(wldb):
    wid = wldb.add("000003", "X", "")["id"]
    wldb.set_alert(wid, 10.0, None)
    wldb.set_alert(wid, None, None)  # 清除
    it = [x for x in wldb.list_items() if x["code"] == "000003"][0]
    assert it["alert_hi"] is None and it["alert_lo"] is None


def test_is_etf_classification(wldb):
    assert wldb._is_etf("510300") is True
    assert wldb._is_etf("159915") is True
    assert wldb._is_etf("000001") is False
    assert wldb._is_etf("600519") is False


def test_patch_watchlist_alert(client_wldb):
    """PATCH 设 alert_hi/lo，返回 updated + disclaimer + 落库。"""
    wid = wl.add("000004", "X", "")["id"]
    r = client_wldb.patch(f"/api/watchlist/{wid}", json={"alert_hi": 12.0, "alert_lo": 8.0})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["updated"] is True and d["alert_hi"] == 12.0 and d["alert_lo"] == 8.0
    assert "disclaimer" in r.json()  # 默认 _wrap 附 disclaimer
    it = [x for x in wl.list_items() if x["code"] == "000004"][0]
    assert it["alert_hi"] == 12.0


def _mock_in_session(monkeypatch, val=True):
    from backtest import quality
    monkeypatch.setattr(quality, "_is_in_session", lambda now=None: val)


def test_live_quote_batch_and_alert_hit(client_wldb, monkeypatch):
    """tdx 批量返行情 → change_pct 计算 + alert_hit 区间内为 None。"""
    wl.add("000001", "平安", "")
    wl.set_alert(wl.list_items()[0]["id"], 12.0, 9.0)  # price 11 在区间内
    wl.add("600519", "茅台", "")
    from api import server
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [
        {"code": "000001", "price": 11.0, "last_close": 10.0},
        {"code": "600519", "price": 1700.0, "last_close": 1600.0},
    ])
    _mock_in_session(monkeypatch, True)
    r = client_wldb.get("/api/watchlist/live")
    assert r.status_code == 200
    by = {x["code"]: x for x in r.json()["data"]["rows"]}
    assert by["000001"]["price"] == 11.0
    assert abs(by["000001"]["change_pct"] - 10.0) < 1e-6
    assert by["000001"]["alert_hit"] is None  # 9<11<12 区间内
    assert by["600519"]["alert_hit"] is None
    assert r.json()["data"]["in_session"] is True
    assert "cand_disclaimer" in r.json()


def test_live_alert_hi_crossed(client_wldb, monkeypatch):
    """price≥alert_hi → alert_hit='hi'。"""
    wl.add("000001", "平安", "")
    wl.set_alert(wl.list_items()[0]["id"], 10.0, 8.0)  # price 10.5≥10 → hi
    from api import server
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [
        {"code": "000001", "price": 10.5, "last_close": 9.0}])
    _mock_in_session(monkeypatch, True)
    rows = client_wldb.get("/api/watchlist/live").json()["data"]["rows"]
    assert rows[0]["alert_hit"] == "hi"


def test_live_tdx_empty_degrades_to_spot(client_wldb, monkeypatch):
    """tdx 返空 → 降级 spot latest_price + quote_source='spot陈旧'。"""
    wl.add("000001", "平安", "")
    from api import server
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [])
    with dbmod.get_conn() as conn:
        conn.execute("INSERT INTO stock_spot(code,latest_price) VALUES(?,?)", ("000001", 10.0))
        conn.commit()
    _mock_in_session(monkeypatch, False)
    r = client_wldb.get("/api/watchlist/live")
    rows = r.json()["data"]["rows"]
    assert rows[0]["price"] == 10.0
    assert rows[0]["quote_source"] == "spot陈旧"
    assert r.json()["data"]["in_session"] is False


def test_signals_split_stock_etf(client_wldb, monkeypatch):
    """watchlist 含个股+ETF → scan_signals 按 universe 分拆调用。"""
    wl.add("000001", "平安", "")
    wl.add("510300", "沪深300ETF", "")
    from backtest import signals as bt_sig
    calls = []
    def _fake_scan(universe, codes, signal_types=None, min_hits=1):
        calls.append(universe)
        return {"rows": [{"code": codes[0] if codes else "", "signals": [{"type": "ma_breakout"}]}],
                "n_scanned": len(codes), "error": None}
    monkeypatch.setattr(bt_sig, "scan_signals", _fake_scan)
    r = client_wldb.get("/api/watchlist/signals")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "stock" in calls and "etf" in calls
    unis = {row["universe"] for row in data["rows"]}
    assert unis == {"stock", "etf"}
    assert "cand_disclaimer" in r.json()


def test_signals_empty_watchlist(client_wldb):
    """空 watchlist → 空 rows 不崩。"""
    r = client_wldb.get("/api/watchlist/signals")
    assert r.status_code == 200
    assert r.json()["data"]["rows"] == []

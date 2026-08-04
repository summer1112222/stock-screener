# -*- coding: utf-8 -*-
import sqlite3
from data import watchlist, db


def _mem_conn():
    """内存 SQLite，建 watchlist 表。每次 get_conn 返回同一连接。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, name TEXT, note TEXT, added_ts TEXT)""")
    return conn


def _patch(monkeypatch):
    conn = _mem_conn()
    monkeypatch.setattr(db, "get_conn", lambda: conn, raising=False)
    # watchlist 模块内 from . import db，引用的是 data.db 同一对象
    import data.watchlist as wl
    monkeypatch.setattr(wl, "db", db, raising=False)
    return conn


def test_watchlist_add_list_remove(monkeypatch):
    _patch(monkeypatch)
    w = watchlist.add("sh600519", "贵州茅台", "测试")
    assert w["code"] == "sh600519" and w["id"] >= 1
    items = watchlist.list_items()
    assert len(items) == 1
    assert items[0]["code"] == "sh600519" and items[0]["name"] == "贵州茅台"
    assert watchlist.remove(w["id"]) is True
    assert watchlist.list_items() == []


def test_watchlist_dedup_same_code(monkeypatch):
    """同 code 再加不重复插入，更新 name/note。"""
    _patch(monkeypatch)
    a = watchlist.add("sz000001", "平安银行", "v1")
    b = watchlist.add("sz000001", "平安银行新版", "v2")
    assert a["id"] == b["id"]  # 同一行
    items = watchlist.list_items()
    assert len(items) == 1
    assert items[0]["name"] == "平安银行新版"
    assert items[0]["note"] == "v2"


def test_watchlist_order_desc(monkeypatch):
    """list 按 added_ts DESC(后加的在前)。"""
    _patch(monkeypatch)
    watchlist.add("sh600519", "A")
    watchlist.add("sz000001", "B")
    items = watchlist.list_items()
    assert items[0]["code"] == "sz000001"  # 后加在前
    assert items[1]["code"] == "sh600519"

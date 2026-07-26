# -*- coding: utf-8 -*-
"""数据健康仪表盘单测：mock db.get_conn/last_update_time + smart_money.channel_status，不触网。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from api import server


class _Row(dict):
    def __missing__(self, k): return None


class _FakeCur:
    def __init__(self, val): self._val = val
    def fetchone(self): return _Row(self._val) if isinstance(self._val, dict) else self._val
    def fetchall(self): return self._val if isinstance(self._val, list) else [self._val]


class _FakeConn:
    """按 sql 子串匹配预置结果（值为 dict→_Row，支持 row['n'] 键访问，缺键返 None）。"""
    def __init__(self, mapping): self._m = mapping
    def execute(self, sql, *params):
        for key, val in self._m.items():
            if key in sql:
                return _FakeCur(val)
        return _FakeCur(None)
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _patch_db(monkeypatch, mapping, update_time="2026-07-31 14:20:00",
              last_refresh="2026-07-31 14:20:00", channels=None):
    monkeypatch.setattr(server.db, "get_conn", lambda: _FakeConn(mapping))
    monkeypatch.setattr(server.db, "last_update_time", lambda: update_time)
    monkeypatch.setattr(server.db, "get_meta", lambda k, default="": last_refresh)
    monkeypatch.setattr(server.smart_money, "channel_status", lambda: channels or {
        "资金流": {"ok": True, "rows": 5200, "date": "2026-07-31",
                  "stale": False, "last_ok_date": "2026-07-31"}})


def _client():
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def test_health_returns_all_domains(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": {"n":100}, "MAX(date": {"d":"2026-07-31"}, "MIN(date": {"mn":"2023-01-01"}})
    r = _client().get("/api/health").json()["data"]
    dom = r["domains"]
    for k in ("spot", "history", "smart_money", "fundamentals",
              "research", "st_list", "portfolio"):
        assert k in dom and "status" in dom[k]
    assert r["overall"] in ("green", "yellow", "red")
    assert r["update_time"] == "2026-07-31 14:20:00"


def test_health_status_derivation(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": {"n":100}, "MAX(date": {"d":"2026-07-31"}},
               channels={"资金流": {"ok": False, "stale": True, "stale_date": "2026-07-29",
                                   "last_ok_date": "2026-07-29", "rows": 100}})
    sm = _client().get("/api/health").json()["data"]["domains"]["smart_money"]
    assert sm["status"] == "yellow"


def test_health_overall_worst_domain(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": {"n":0}},
               channels={"资金流": {"ok": False, "stale": True, "last_ok_date": "2026-07-29", "rows": 1}})
    r = _client().get("/api/health").json()["data"]
    assert r["overall"] == "red"


def test_health_partial_failure(monkeypatch):
    class _C:
        def execute(self, sql, *p):
            if "stock_daily" in sql:
                raise RuntimeError("history boom")
            return _FakeCur({"n":100})
        def __enter__(self): return self
        def __exit__(self,*a): pass
    monkeypatch.setattr(server.db, "get_conn", lambda: _C())
    monkeypatch.setattr(server.db, "last_update_time", lambda: "2026-07-31 14:20:00")
    monkeypatch.setattr(server.db, "get_meta", lambda k, default="": "")
    monkeypatch.setattr(server.smart_money, "channel_status", lambda: {
        "资金流": {"ok": True, "rows": 1, "date": "2026-07-31", "stale": False, "last_ok_date": "2026-07-31"}})
    dom = _client().get("/api/health").json()["data"]["domains"]
    assert dom["history"]["status"] == "red"
    assert "boom" in dom["history"].get("err", "")
    assert dom["spot"]["status"] != "red"


def test_health_smart_money_reuses_channel_status(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": {"n":10}, "MAX(date": {"d":"2026-07-31"}},
               channels={"北向": {"ok": True, "stale": True, "stale_date": "2026-07-29",
                                  "last_ok_date": "2026-07-29", "rows": 20, "date": "2026-07-29"}})
    sm = _client().get("/api/health").json()["data"]["domains"]["smart_money"]
    assert sm["channels"]["北向"]["stale"] is True
    assert sm["channels"]["北向"]["last_ok_date"] == "2026-07-29"

# tests/test_server_new_routes.py
# -*- coding: utf-8 -*-
"""新路由 smoke 测试:验证返回结构 + disclaimer。用 FastAPI TestClient。"""
from fastapi.testclient import TestClient

from api.server import app

client = TestClient(app)


def test_st_list_route(monkeypatch):
    from data import db

    def _q(table, where="", params=(), order_by="", limit=0):
        assert table == "st_list"
        return [{"code": "600250", "name": "*ST兴源", "st_type": "*ST"}]
    monkeypatch.setattr(db, "query_rows", _q)
    r = client.get("/api/st-list")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert "disclaimer" in body


def test_management_route(monkeypatch):
    from screener import smart_money as sm

    monkeypatch.setattr(sm, "top_by_amount",
                        lambda **kw: {"rows": [], "total": 0})
    r = client.get("/api/management?days=30")
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()


def test_share_unlock_route(monkeypatch):
    from screener import smart_money as sm

    monkeypatch.setattr(sm, "unlock_by_month",
                        lambda **kw: {"rows": [], "total": 0,
                                      "month": "2026-07", "total_amount": 0})
    r = client.get("/api/share-unlock?month=2026-07")
    assert r.status_code == 200
    body = r.json()
    assert "cand_disclaimer" in body
    assert body["month"] == "2026-07"


def test_research_route(monkeypatch):
    from data import research

    monkeypatch.setattr(research, "query_reports",
                        lambda **kw: {"rows": [], "total": 0})
    r = client.get("/api/research?days=30")
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()


def test_comments_route(monkeypatch):
    from data import research

    monkeypatch.setattr(research, "fetch_comments",
                        lambda code: ({"rows": []}, False))
    r = client.get("/api/comments?code=600519")
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()

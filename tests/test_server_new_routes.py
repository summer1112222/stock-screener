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


def test_market_route(monkeypatch):
    """市场温度路由返回结构 + disclaimer + 空数据降级。"""
    from data import market
    monkeypatch.setattr(market, "latest", lambda: None)
    monkeypatch.setattr(market, "trend", lambda days=30: [])
    r = client.get("/api/market")
    assert r.status_code == 200
    body = r.json()
    assert "disclaimer" in body
    assert body["data"]["latest"] is None
    assert body["data"]["trend"]["days"] == 0


def test_market_route_with_data(monkeypatch):
    from data import market
    monkeypatch.setattr(market, "latest",
                        lambda: {"date": "2026-08-08", "up_count": 200, "zt_count": 35})
    monkeypatch.setattr(market, "trend", lambda days=30: [
        {"date": "2026-08-07", "up_count": 180, "zt_count": 30, "margin_total": 1.5e12},
        {"date": "2026-08-08", "up_count": 200, "zt_count": 35, "margin_total": 1.51e12},
    ])
    r = client.get("/api/market")
    body = r.json()["data"]
    assert body["latest"]["zt_count"] == 35
    assert body["trend"]["days"] == 2
    assert body["trend"]["up_count"] == [180, 200]
    assert body["trend"]["margin_total"] == [1.5e12, 1.51e12]


def test_portfolio_alert_patch(monkeypatch):
    """PATCH /api/portfolio/{pid} 只更提醒价位 + disclaimer。"""
    from data import portfolio
    monkeypatch.setattr(portfolio, "set_alert", lambda pid, alert_hi, alert_lo: True)
    r = client.patch("/api/portfolio/3", json={"alert_hi": 12.5, "alert_lo": 9.0})
    assert r.status_code == 200
    body = r.json()
    assert "disclaimer" in body
    assert body["data"]["updated"] is True
    assert body["data"]["alert_hi"] == 12.5 and body["data"]["alert_lo"] == 9.0


def test_sm_refresh_single_channel(monkeypatch):
    """单通道刷新：channel=资金流 传 channels=["资金流"]，返回 partial=True。"""
    from api import server
    captured = {}

    def _fake_refresh(date=None, channels=None):
        captured["channels"] = channels
        return {"date": "2026-08-09", "counts": {}, "channels": {},
                "update_time": "x", "partial": True}

    monkeypatch.setattr(server.smart_money, "refresh_today", _fake_refresh)
    r = client.post("/api/smart-money/refresh?channel=资金流")
    assert r.status_code == 200
    body = r.json()
    assert captured["channels"] == ["资金流"]
    assert body["data"]["partial"] is True
    assert "cand_disclaimer" in body


def test_sm_refresh_all_no_channel(monkeypatch):
    """全量刷新：不传 channel → channels=None，无 partial 键。"""
    from api import server
    captured = {}

    def _fake_refresh(date=None, channels=None):
        captured["channels"] = channels
        return {"date": "2026-08-09", "counts": {}, "channels": {},
                "update_time": "x"}

    monkeypatch.setattr(server.smart_money, "refresh_today", _fake_refresh)
    r = client.post("/api/smart-money/refresh")
    assert r.status_code == 200
    assert captured["channels"] is None
    assert "partial" not in r.json()["data"]


def test_sm_refresh_multi_channel(monkeypatch):
    """多通道：channel=资金流,龙虎榜 → channels 列表两元素。"""
    from api import server
    captured = {}

    def _fake_refresh(date=None, channels=None):
        captured["channels"] = channels
        return {"date": "2026-08-09", "counts": {}, "channels": {},
                "update_time": "x", "partial": True}

    monkeypatch.setattr(server.smart_money, "refresh_today", _fake_refresh)
    r = client.post("/api/smart-money/refresh?channel=资金流,龙虎榜")
    assert r.status_code == 200
    assert captured["channels"] == ["资金流", "龙虎榜"]

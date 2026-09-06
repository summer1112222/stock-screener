# tests/test_sm_radar_api.py
# -*- coding: utf-8 -*-
"""主力雷达 API 测试。mock screener.smart_money.radar，不触网。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from api.server import app

client = TestClient(app)


def test_radar_ok(monkeypatch):
    from screener import smart_money as sm
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50: {
        "rows": [{"code": "000001", "name": "平A", "channel_hits": 3,
                  "daily_net": 1e7, "cum_net": 2e7, "net_intensity": 0.05,
                  "streak_inflow": 4, "streak_outflow": 0, "unlock_flag": False}],
        "total": 1, "date": "2026-09-05", "days": 5, "market": None})
    r = client.get("/api/smart-money/radar?days=5&limit=50")
    d = r.json()
    assert d["data"][0]["code"] == "000001"
    assert d["data"][0]["channel_hits"] == 3
    assert "cand_disclaimer" in d
    assert d["date"] == "2026-09-05"


def test_radar_empty(monkeypatch):
    from screener import smart_money as sm
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50: {
        "rows": [], "total": 0, "date": None, "days": 5, "market": None})
    r = client.get("/api/smart-money/radar")
    d = r.json()
    assert d["data"] == []
    assert "cand_disclaimer" in d


def test_radar_passes_market_filter(monkeypatch):
    from screener import smart_money as sm
    captured = {}
    def _radar(days=5, market=None, limit=50):
        captured.update(days=days, market=market, limit=limit)
        return {"rows": [], "total": 0, "date": None, "days": days, "market": market}
    monkeypatch.setattr(sm, "radar", _radar)
    client.get("/api/smart-money/radar?market=主板&limit=10")
    assert captured["market"] == "主板"
    assert captured["limit"] == 10

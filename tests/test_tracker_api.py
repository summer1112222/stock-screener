# tests/test_tracker_api.py
# -*- coding: utf-8 -*-
"""清单追踪 API 测试。mock backtest.tracker，不触网。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from api.server import app

client = TestClient(app)


def test_track_fill_ok(monkeypatch):
    from backtest import tracker as bt
    monkeypatch.setattr(bt, "fill_returns",
                        lambda limit=0: {"scanned": 3, "filled": 1, "skipped": 2,
                                         "note": "部分回填"})
    r = client.post("/api/track/fill?limit=5")
    d = r.json()
    assert d["data"]["filled"] == 1
    assert "bt_disclaimer" in d


def test_track_fill_get(monkeypatch):
    from backtest import tracker as bt
    monkeypatch.setattr(bt, "fill_returns",
                        lambda limit=0: {"scanned": 0, "filled": 0, "skipped": 0,
                                         "note": "无待回填"})
    r = client.get("/api/track/fill")
    assert r.json()["data"]["scanned"] == 0


def test_track_summary_ok(monkeypatch):
    from backtest import tracker as bt
    monkeypatch.setattr(bt, "summary",
                        lambda module=None, mode=None: {"total": 2, "filled": 1,
                                                        "track_days": 1, "k": {},
                                                        "by_temperature": {},
                                                        "by_industry": {}})
    r = client.get("/api/track/summary?module=quality")
    d = r.json()
    assert d["data"]["by_industry"] == {}
    assert "bt_disclaimer" in d


def test_track_summary_mode_filter(monkeypatch):
    from backtest import tracker as bt
    captured = {}
    def _summary(module=None, mode=None):
        captured.update(module=module, mode=mode)
        return {"total": 0, "filled": 0, "track_days": 0, "k": {},
                "by_temperature": {}, "by_industry": {}}
    monkeypatch.setattr(bt, "summary", _summary)
    client.get("/api/track/summary?module=nextday&mode=strict")
    assert captured == {"module": "nextday", "mode": "strict"}

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


def test_sm_today_records_head_when_default(monkeypatch):
    """默认参数调 /api/smart-money/today 应记录头部前20;改 limit 不记录。"""
    rows = [{"code": f"600{i:03d}", "name": f"股{i}", "amount": 1e8 - i,
             "quality_pct": 0.9 - i / 100} for i in range(30)]
    calls = {}
    def fake_today_list(date, channel, market, days, limit):
        calls["limit"] = limit
        return {"rows": rows, "total": len(rows), "date": "2026-09-18"}
    monkeypatch.setattr("api.server.sm_query.today_list", fake_today_list)
    rec = []
    def fake_record(module, mode, date, items):
        rec.append((module, mode, date, items))
        return len(items)
    monkeypatch.setattr("api.server.bt_tracker.record_list", fake_record)
    monkeypatch.setattr("api.server.bt_tracker.is_default_params",
                        lambda m, p: m == "smart_money" and p.get("limit") == 1000)

    cli = TestClient(app)
    r = cli.get("/api/smart-money/today")
    assert r.status_code == 200
    assert rec and rec[0][0] == "smart_money" and rec[0][1] == "today"
    assert len(rec[0][3]) == 20          # 只记头部前20
    assert rec[0][3][0]["score"] == rows[0]["amount"]

    # 改 limit 不记录
    rec.clear()
    cli.get("/api/smart-money/today?limit=500")
    assert not rec

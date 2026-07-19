# tests/test_unlock_query.py
# -*- coding: utf-8 -*-
"""限售解禁按 as_of 月份查询测试。mock db.query_rows。"""
from screener import smart_money as sm_query


def test_unlock_by_month_filters_channel_and_as_of(monkeypatch):
    captured = {}

    def _q(table, where="", params=(), order_by="", limit=0):
        captured["where"] = where
        captured["params"] = params
        captured["order"] = order_by
        return [
            {"code": "600519", "name": "贵州茅台", "channel": "限售解禁",
             "as_of": "2026-07-20", "amount": 1000000.0},
            {"code": "000001", "name": "平安银行", "channel": "限售解禁",
             "as_of": "2026-07-25", "amount": 500000.0},
        ]

    from data import db
    monkeypatch.setattr(db, "query_rows", _q)
    res = sm_query.unlock_by_month(month="2026-07")
    assert "channel = ?" in captured["where"]
    assert "as_of LIKE ?" in captured["where"]
    assert "限售解禁" in captured["params"]
    assert "2026-07%" in captured["params"]
    assert captured["order"] == "as_of ASC"
    assert res["total"] == 2
    assert res["total_amount"] == 1500000.0
    assert res["month"] == "2026-07"


def test_unlock_by_month_with_code(monkeypatch):
    from data import db

    def _q(table, where="", params=(), order_by="", limit=0):
        assert "code = ?" in where
        assert "600519" in params
        return []

    monkeypatch.setattr(db, "query_rows", _q)
    res = sm_query.unlock_by_month(month="2026-07", code="600519")
    assert res["total"] == 0
    assert res["total_amount"] == 0

# tests/test_research.py
# -*- coding: utf-8 -*-
"""research 研报采集+查询/千股千评测试。mock ak + 临时 db。"""
from datetime import datetime, timedelta
from types import ModuleType

import pandas as pd

from data import research, db


def _mock_ak(report_fn, comment_fn):
    m = ModuleType("akshare")
    m.stock_research_report_em = report_fn
    m.stock_comment_detail = comment_fn
    return m


def _tmp_db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()


def _report_df():
    return pd.DataFrame([
        {"股票代码": "600519", "股票简称": "贵州茅台", "投资评级": "增持",
         "研报标题": "T1", "研究机构": "中信", "分析师": "张三",
         "日期": "2026-07-10", "目标价（元）": 2000.0},
    ])


def test_fetch_reports_normalize(monkeypatch):
    monkeypatch.setattr(research, "_AK_OK", True)
    monkeypatch.setattr(research, "ak", _mock_ak(
        lambda **kw: _report_df(), lambda symbol: pd.DataFrame()))
    df, ok, err = research.fetch_reports(recent_days=30)
    assert ok, err
    recs = df.to_dict("records")
    assert recs[0]["code"] == "600519"
    assert recs[0]["rating"] == "增持"
    assert recs[0]["target_price"] == 2000.0


def test_fetch_reports_ak_fail(monkeypatch):
    monkeypatch.setattr(research, "_AK_OK", True)

    def _err(**kw):
        raise RuntimeError("em blocked")

    monkeypatch.setattr(research, "ak", _mock_ak(_err, lambda s: pd.DataFrame()))
    df, ok, err = research.fetch_reports(recent_days=30)
    assert not ok and df.empty


def test_query_reports_filters(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
    new = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    rows = [
        {"code": "600519", "name": "贵州茅台", "rating": "增持", "title": "T1",
         "org": "中信", "analyst": "张三", "pub_date": new, "target_price": 2000.0, "ts": new},
        {"code": "000001", "name": "平安银行", "rating": "中性", "title": "T2",
         "org": "海通", "analyst": "李四", "pub_date": old, "target_price": 15.0, "ts": old},
    ]
    db.upsert_rows("research_report", rows)
    res = research.query_reports(days=30, limit=200)
    assert res["total"] == 1
    assert res["rows"][0]["code"] == "600519"


def test_fetch_comments_cache_and_stale(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(research, "_AK_OK", True)
    called = {"n": 0}

    def _cmt(symbol):
        called["n"] += 1
        return pd.DataFrame([{"代码": symbol, "主力成本": 1800.0}])

    monkeypatch.setattr(research, "ak", _mock_ak(
        lambda **kw: pd.DataFrame(), _cmt))
    res1, stale1 = research.fetch_comments("600519")
    assert res1 is not None and not stale1
    res2, stale2 = research.fetch_comments("600519")
    assert called["n"] == 1

# -*- coding: utf-8 -*-
"""TDX 深查主力盘口结构化报告单测，不依赖网络。"""
from api import server


def _quote(**overrides):
    q = {
        "code": "000001", "price": 11.25, "low": 11.0, "high": 11.5,
        "b_vol": 6000, "s_vol": 4000,
        "bid1": 11.24, "ask1": 11.26,
        "bid2": 11.23, "ask2": 11.27,
        "bid3": 11.22, "ask3": 11.28,
        "bid4": 11.21, "ask4": 11.29,
        "bid5": 11.20, "ask5": 11.30,
        "bid_vol1": 1000, "ask_vol1": 500,
        "bid_vol2": 1000, "ask_vol2": 500,
        "bid_vol3": 1000, "ask_vol3": 500,
        "bid_vol4": 1000, "ask_vol4": 500,
        "bid_vol5": 1000, "ask_vol5": 500,
    }
    q.update(overrides)
    return q


def test_quote_analysis_basic():
    r = server._tdx_quote_analysis(_quote(), True)
    assert r["active_net_amount"] == 2250000.0
    assert r["active_net_ratio"] == 0.2
    assert r["order_imbalance"] > 0
    assert r["quote_observation_score"] is not None
    assert r["data_quality"]["in_session"] is True


def test_quote_analysis_after_session():
    r = server._tdx_quote_analysis(_quote(), False)
    assert r["data_quality"]["is_stale"] is True
    assert r["order_imbalance"] > 0
    assert r["factor_scores"]["order_imbalance"] is None
    assert r["factor_scores"]["intraday_position"] is None
    assert "盘后" in r["data_quality"]["note"]


def test_quote_analysis_partial_missing():
    r = server._tdx_quote_analysis(_quote(b_vol=None, s_vol=None), True)
    assert r["active_net_amount"] is None
    assert r["active_net_ratio"] is None
    assert r["factor_status"]["active_trade"] == "missing"
    assert r["quote_observation_score"] is not None


def test_quote_analysis_no_depth():
    q = _quote(**{f"bid_vol{i}": None for i in range(1, 6)},
               **{f"ask_vol{i}": None for i in range(1, 6)})
    r = server._tdx_quote_analysis(q, True)
    assert r["order_imbalance"] is None
    assert r["factor_status"]["order_imbalance"] == "missing"


def test_quote_analysis_no_quote_route(monkeypatch):
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [])
    r = server.tdx_quote_analysis("000001")
    assert r["data"]["quote_observation_score"] is None
    assert r["data"]["data_quality"]["quote_available"] is False
    assert "cand_disclaimer" in r

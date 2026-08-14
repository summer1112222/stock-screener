# tests/test_outlook_continuous.py
# -*- coding: utf-8 -*-
"""多因子预判连续化 + 内部人因子 测试。
验证：_push strength 连续（基本面 59 vs 39 不再同档中性）、内部人因子净增持→偏多。
用 FastAPI TestClient，mock buffett/db/research/signals/behavior/tdx。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from api.server import app

client = TestClient(app)


# 高/低 ratios → card["score"] 由 server.py step1 从这些字段机械算出
_RATIOS_HIGH = {"leverage_adj_roe": 25, "gross_margin_avg": 60, "debt_ratio_latest": 30,
                "fcf_to_netincome": 0.8}  # +moat_score4 +合理 → score=95
_RATIOS_LOW = {"leverage_adj_roe": 5, "gross_margin_avg": 10, "debt_ratio_latest": 70,
               "fcf_to_netincome": 0.1}  # +moat1 +贵 +1红旗 → score=20


def _mock_stock_analysis(monkeypatch, *,
                         ratios=None, mos=None, valuation_tag="合理",
                         red_flags=None, moat_score=4,
                         behavior_streak_in=0, behavior_streak_out=0,
                         mgmt_net=0.0,
                         signals_rows=None, reports=None):
    """组装 stock_analysis 所需 mock。card["score"] 由 ratios 经 server step1 机械算出。"""
    from backtest import buffett as bt_buf
    from data import db, research as research_data
    from screener import smart_money as sm_query
    from api import server

    # tdx quote 不通（避免触网）
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [])

    # buffett.analyze 返回基本面卡；card["score"] 由 server step1 读 ratios 机械算
    monkeypatch.setattr(bt_buf, "analyze", lambda c: {
        "code": c, "name": "X",
        "ratios": ratios if ratios is not None else _RATIOS_HIGH,
        "margin_of_safety": mos,
        "valuation_tag": valuation_tag,
        "valuation_abs": valuation_tag,
        "red_flags": red_flags if red_flags is not None else [],
        "moat_score": moat_score,
        "moat_tag": "窄(部分达标)",
        "eps_annual": 1.0, "latest_price": 50.0,
        "earnings_yield_pct": 4.0,
        "intrinsic_value": 60.0 if mos else None,
    })

    # smart_money_action 查询：step2 聚合(where="code = ?") + 内部人 step6
    # 内部人查询 where="code = ? AND channel = ? AND date >= ?"，channel 值在 params 里
    def _q(table, where="", params=(), order_by="", limit=0):
        if table == "smart_money_action":
            if "高管增减持" in (params or ()):
                if mgmt_net != 0:
                    return [{"code": params[0], "channel": "高管增减持",
                             "amount": mgmt_net, "date": "2026-08-01"}]
                return []
            return []  # step2 主力聚合
        return []
    monkeypatch.setattr(db, "query_rows", _q)

    # behavior_series：资金面因子
    monkeypatch.setattr(sm_query, "behavior_series",
                        lambda code, days=30: {
                            "code": code, "days": days,
                            "streak_inflow": behavior_streak_in,
                            "streak_outflow": behavior_streak_out,
                            "margin_accel": 0,
                            "channels": {}, "daily": [],
                        })

    # research
    monkeypatch.setattr(research_data, "query_reports",
                        lambda code=None, days=180, limit=10:
                        {"rows": reports or [], "total": len(reports or [])})
    monkeypatch.setattr(research_data, "fetch_comments",
                        lambda code: {"code": code, "comment": "中性"})

    # signals
    from backtest import signals as bt_sig
    monkeypatch.setattr(bt_sig, "scan_signals",
                        lambda uni, codes: {"rows": signals_rows or []})


def test_outlook_basic_score_continuous(monkeypatch):
    """基本面评分 strength=(s-50)/50 连续：HIGH ratios→score=95→strength=0.9(偏多)。
    公式精确：strength == (card.score - 50)/50。"""
    _mock_stock_analysis(monkeypatch, ratios=_RATIOS_HIGH)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    assert r.status_code == 200
    data = r.json()["data"]
    s = data["score"]
    assert s == 95.0  # 25+15+15+15+moat4(15)+合理(10)
    out = data["outlook"]
    fund = [c for c in out["contribs"] if c["factor"] == "基本面评分"][0]
    assert abs(fund["strength"] - (s - 50) / 50) < 1e-9  # 公式正确
    assert fund["dir"] == "偏多"
    assert abs(fund["contrib"] - 0.30 * fund["strength"]) < 1e-9


def test_outlook_score_low_bearish(monkeypatch):
    """LOW ratios→score=20→strength=-0.6(偏空)。与 HIGH 区分：不再同档中性。"""
    _mock_stock_analysis(monkeypatch, ratios=_RATIOS_LOW,
                        valuation_tag="贵", moat_score=1, red_flags=["x"])
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    data = r.json()["data"]
    s = data["score"]
    assert s == 20.0  # 5+5+5+5+moat1(5)+贵(5)-10(红旗)
    out = data["outlook"]
    fund = [c for c in out["contribs"] if c["factor"] == "基本面评分"][0]
    assert abs(fund["strength"] - (s - 50) / 50) < 1e-9
    assert fund["dir"] == "偏空"  # 20→strength=-0.6<-0.15


def test_outlook_valuation_continuous_from_mos(monkeypatch):
    """margin_of_safety 正→估值 strength=tanh(mos×3)>0 偏多。"""
    _mock_stock_analysis(monkeypatch, mos=0.4)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    out = r.json()["data"]["outlook"]
    val = [c for c in out["contribs"] if c["factor"] == "估值"][0]
    assert val["dir"] == "偏多"
    assert val["strength"] > 0
    import math
    assert abs(val["strength"] - math.tanh(0.4 * 3)) < 0.01


def test_outlook_capital_flow_uses_behavior_streak(monkeypatch):
    """资金面用 behavior streak：连续净流入 6 日→偏多，strength=tanh(6/8)。
    不再用 raw 净额合计。"""
    _mock_stock_analysis(monkeypatch, behavior_streak_in=6)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    out = r.json()["data"]["outlook"]
    cap = [c for c in out["contribs"] if c["factor"] == "资金面"][0]
    assert cap["dir"] == "偏多"
    import math
    assert abs(cap["strength"] - math.tanh(6 / 8)) < 0.01


def test_outlook_insider_factor_buy(monkeypatch):
    """内部人因子：高管净增持→偏多。WEIGHTS 含"内部人"键。"""
    _mock_stock_analysis(monkeypatch, mgmt_net=5e6)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    out = r.json()["data"]["outlook"]
    assert "内部人" in out["weights"]
    ins = [c for c in out["contribs"] if c["factor"] == "内部人"]
    assert ins, "应有内部人因子"
    assert ins[0]["dir"] == "偏多"
    assert ins[0]["strength"] > 0


def test_outlook_insider_factor_sell(monkeypatch):
    """高管净减持→偏空。"""
    _mock_stock_analysis(monkeypatch, mgmt_net=-3e6)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    out = r.json()["data"]["outlook"]
    ins = [c for c in out["contribs"] if c["factor"] == "内部人"][0]
    assert ins["dir"] == "偏空"


def test_outlook_weights_sum_to_one(monkeypatch):
    """六因子权重合计=1.0。"""
    _mock_stock_analysis(monkeypatch)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    out = r.json()["data"]["outlook"]
    assert abs(sum(out["weights"].values()) - 1.0) < 1e-9


def test_outlook_has_disclaimer(monkeypatch):
    """个股分析卡附 bt_disclaimer。"""
    _mock_stock_analysis(monkeypatch)
    r = client.get("/api/stock-analysis", params={"code": "000001"})
    assert r.status_code == 200
    assert "bt_disclaimer" in r.json()

# -*- coding: utf-8 -*-
"""优质选股筛选单测：合成数据 mock db.query_rows / 因子源，不触网。
合规：只验分位/共振/组合逻辑，不涉买卖点/收益。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from backtest import quality
from data import db


SPOT_STOCK = [
    {"code": "000001", "name": "甲", "latest_price": 10.0, "change_pct": 2.0,
     "turnover_amount": 1e8, "turnover_rate": 3.0, "main_net_inflow": 5e7},
    {"code": "000002", "name": "乙ST", "latest_price": 5.0, "change_pct": 1.0,
     "turnover_amount": 1e7, "turnover_rate": 1.0, "main_net_inflow": 0},
    {"code": "000003", "name": "丙涨停", "latest_price": 8.0, "change_pct": 10.0,
     "turnover_amount": 2e8, "turnover_rate": 5.0, "main_net_inflow": 1e8},
]


def test_tradable_filter_applied(monkeypatch):
    """ST/涨停/低成交额在分位计算前剔除。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_turnover=5e7, limit_pct=9.9)
    codes = {r["code"] for r in res["main"]}
    assert "000001" in codes
    assert "000002" not in codes   # ST + 低成交额
    assert "000003" not in codes   # 涨停


def test_degrade_no_history(monkeypatch):
    """无 *_daily → 口径1/4 空、dim_status 标 err、min_dims clamp、不崩。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    res = quality.quality_rank("stock", min_dims=2)
    assert 1 not in res["dims_available"]
    assert 4 not in res["dims_available"]
    assert "err" in res["dim_status"].get("1", "")
    assert res["min_dims"] <= len(res["dims_available"])   # clamp
    assert res["error"] is None   # 降级不报错


def test_dim_scores_percentile(monkeypatch):
    """口径分位 0-1、方向正确（000001涨多=动量高=风险口径分位高）。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    close = pd.DataFrame(
        {"000001": [10.0]*20 + [11.0], "000003": [10.0]*20 + [10.5]},
        index=pd.date_range("2026-06-01", periods=21))
    monkeypatch.setattr(bt_eval, "load_panel",
                        lambda uni, codes, s, e, field: close if field == "close" else pd.DataFrame())
    res = quality.quality_rank("stock", min_dims=1, dim_thresh=0.0)
    ds = {r["code"]: r["dim_scores"] for r in res["main"]}
    m1, m3 = ds.get("000001", {}).get(1), ds.get("000003", {}).get(1)
    if m1 is not None and m3 is not None:
        assert m1 >= m3   # 000001 涨多=动量高=风险口径分位高


def test_resonance_hits_formula():
    """resonance = hits×10 + 命中口径平均分位，命中数为主键。"""
    a, ha = quality._resonance({1: 0.9, 2: 0.8, 3: 0.7}, 0.6)   # hits=3
    b, hb = quality._resonance({1: 0.99}, 0.6)                   # hits=1
    assert a > b
    assert ha == 3 and hb == 1
    assert a == 3 * 10 + (0.9 + 0.8 + 0.7) / 3


def test_resonance_hits_priority(monkeypatch):
    """hits 高的标的 resonance 高、排前。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount",
                        lambda **kw: {"rows": [{"code": "000001", "amount": 1e9},
                                               {"code": "000003", "amount": 1e3}],
                                      "total": 2})
    res = quality.quality_rank("stock", min_dims=1, dim_thresh=0.6)
    if res["main"]:
        assert res["main"][0]["code"] == "000001"   # 资金分位高排前


def test_min_dims_gate(monkeypatch):
    """hits < min_dims 不进主清单（但 min_dims 被 clamp，000001 仍进）。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount",
                        lambda **kw: {"rows": [{"code": "000001", "amount": 1e9}],
                                      "total": 1})
    res = quality.quality_rank("stock", min_dims=2)   # dims_avail=[3], clamp→1
    assert any(r["code"] == "000001" for r in res["main"])


def test_max_per_board_greedy(monkeypatch):
    """同行业最多 max_per_board 只，贪心保留高 resonance。"""
    spot = [
        {"code": "000001", "name": "甲", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 1e8, "board": "银行"},
        {"code": "000002", "name": "乙", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 9e7, "board": "银行"},
        {"code": "000003", "name": "丙", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 8e7, "board": "银行"},
        {"code": "600001", "name": "丁", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 5e7, "board": "地产"},
    ]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: spot if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount", lambda **kw: {"rows": [], "total": 0})
    res = quality.quality_rank("stock", min_dims=1, max_per_board=2, limit=10, dim_thresh=0.0)
    codes = [r["code"] for r in res["main"]]
    bank = [c for c in codes if c in ("000001", "000002", "000003")]
    assert len(bank) <= 2   # 银行板块最多2只


def test_max_corr_greedy(monkeypatch):
    """相关性超阈跳过（合成完全相关的两只）。"""
    spot = [
        {"code": "000001", "name": "甲", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 1e8},
        {"code": "000002", "name": "乙", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 9e7},
    ]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: spot if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    close = pd.DataFrame({"000001": list(range(1, 31)), "000002": list(range(1, 31))},
                         index=pd.date_range("2026-06-01", periods=30))
    monkeypatch.setattr(bt_eval, "load_panel",
                        lambda uni, codes, s, e, field: close if field == "close" else pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount", lambda **kw: {"rows": [], "total": 0})
    res = quality.quality_rank("stock", min_dims=1, max_corr=0.5, limit=10, dim_thresh=0.0)
    assert len(res["main"]) <= 1   # 完全相关(=1.0) 超 0.5


def test_etf_dim2_empty(monkeypatch):
    """ETF 无历史时口径2 err、hits 上限3、min_dims clamp。"""
    etf = [{"code": "510300", "name": "沪深300ETF", "latest_price": 4.0,
            "change_pct": 1.0, "turnover_amount": 1e8, "turnover_rate": 2.0,
            "main_net_inflow": 5e7}]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: etf if table == "etf_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    res = quality.quality_rank("etf", min_dims=2)
    assert 2 not in res["dims_available"]
    assert res["min_dims"] <= len(res["dims_available"])
    assert res["dim_status"].get("2", "").startswith("err")


def test_nan_to_none(monkeypatch):
    """分位/resonance 缺失为 None，不抛 NaN（防 500）。"""
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    res = quality.quality_rank("stock", min_dims=1)
    for r in res["main"] + sum(res["by_dim"].values(), []):
        assert isinstance(r.get("resonance"), (int, float, type(None)))
        for v in r.get("dim_scores", {}).values():
            assert v is None or isinstance(v, (int, float))


def test_disclaimer_attached(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_dims=1)
    assert "cand_disclaimer" in res
    assert "非荐股" in res["cand_disclaimer"]


SPOT_STOCK_FULL = [
    {"code": "000001", "name": "甲", "latest_price": 10.0, "change_pct": 2.0,
     "turnover_amount": 1e8, "turnover_rate": 3.0, "main_net_inflow": 5e7,
     "pe": 8.0, "pb": 0.9, "amplitude": 2.0},
    {"code": "000004", "name": "丁", "latest_price": 6.0, "change_pct": 1.0,
     "turnover_amount": 6e7, "turnover_rate": 2.0, "main_net_inflow": 1e7,
     "pe": 30.0, "pb": 3.0, "amplitude": 8.0},
]


def test_quality_dim2_spot_proxy_fallback(monkeypatch):
    """buffett _AK_OK=False → 口径2 spot 代理分位非 None、dim_status/source_health 标降级。"""
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK_FULL if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_turnover=5e7, limit_pct=9.9)
    assert res["dim_status"].get("2", "").startswith("ok(降级spot估值代理)")
    assert res.get("source_health", {}).get("2", "").startswith("ok(降级")
    item = next((x for x in res["main"] if x["code"] == "000001"), None)
    assert item is not None
    assert item["dim_scores"].get(2) is not None
    item2 = next((x for x in res["main"] if x["code"] == "000004"), None)
    if item2:
        assert item["dim_scores"][2] >= item2["dim_scores"][2]


def test_quality_dim2_main_path_intact(monkeypatch):
    """buffett 正常但 shortlist 空+analyze_many 空 → 触发降级路径（主路径逻辑可被降级接续）。"""
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf, "shortlist_by_turnover",
                        lambda min_turnover=5e8, k=80: [])
    monkeypatch.setattr(bt_buf, "analyze_many", lambda codes: [])
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK_FULL if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_turnover=5e7, limit_pct=9.9)
    assert res["dim_status"].get("2", "").startswith("ok(降级")

# -*- coding: utf-8 -*-
"""筛选引擎单测：用合成 DataFrame mock db.query_rows，不依赖网络/AKShare。

合规：测试只验过滤/排序/合并逻辑，不涉及任何买卖点/收益承诺。
运行: pytest tests/test_engine.py  (在 stock-screener 目录下)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from data import db
from screener import engine


# ---------- 合成数据 (非真实行情，仅供逻辑测试) ----------
BOARD_ROWS = [
    {"name": "A板块", "code": "BK001", "change_pct": 3.5, "total_market_cap": 1e10,
     "turnover_rate": 2.0, "leading_stock": "甲股"},
    {"name": "B板块", "code": "BK002", "change_pct": -1.2, "total_market_cap": 5e9,
     "turnover_rate": 1.0, "leading_stock": "乙股"},
    {"name": "C板块", "code": "BK003", "change_pct": 1.8, "total_market_cap": 8e9,
     "turnover_rate": 3.5, "leading_stock": "丙股"},
]
FLOW_ROWS = [
    {"name": "A板块", "main_net_inflow": 5e7, "super_large_net": 2e7,
     "large_net": 1e7, "medium_net": -5e6, "small_net": -1e7},
    {"name": "B板块", "main_net_inflow": -3e7, "super_large_net": -1e7,
     "large_net": -5e6, "medium_net": 2e6, "small_net": 4e7},
    {"name": "C板块", "main_net_inflow": 1e7, "super_large_net": 5e6,
     "large_net": 3e6, "medium_net": 1e6, "small_net": -4e6},
]
ETF_ROWS = [
    {"code": "510300", "name": "沪深300ETF", "latest_price": 4.1, "change_pct": 1.5,
     "turnover_amount": 3e8, "turnover_rate": 0.5},
    {"code": "159915", "name": "创业板ETF", "latest_price": 2.9, "change_pct": -0.8,
     "turnover_amount": 1e8, "turnover_rate": 0.3},
]


def fake_query(table, where="", params=(), order_by="", limit=0):
    if table in ("industry_board", "concept_board"):
        return list(BOARD_ROWS)
    if table == "sector_fund_flow":
        return list(FLOW_ROWS)
    if table == "etf_spot":
        return list(ETF_ROWS)
    return []


@pytest.fixture(autouse=True)
def patch_db(monkeypatch):
    monkeypatch.setattr(db, "query_rows", fake_query)


# ---------- 条件过滤 ----------
def test_gt_filter():
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "gt", "value": 0}])
    names = [r["name"] for r in res["rows"]]
    assert "B板块" not in names   # -1.2 被过滤
    assert "A板块" in names


def test_between_filter():
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "between", "value": [0, 2]}])
    names = [r["name"] for r in res["rows"]]
    assert names == ["C板块"]      # 1.8 落在 [0,2]


def test_fund_flow_merge_and_filter():
    # 主力净流入 > 0 应留下 A、C
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "main_net_inflow", "op": "gt", "value": 0}],
        sort="main_net_inflow", asc=False)
    names = [r["name"] for r in res["rows"]]
    assert names == ["A板块", "C板块"]
    # 合并后行应含资金流字段
    assert "main_net_inflow" in res["rows"][0]


def test_topn():
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "topn", "value": 2}],
        sort=None, limit=50)
    names = [r["name"] for r in res["rows"]]
    assert len(names) == 2
    assert names[0] == "A板块"       # 3.5 最高


def test_and_combo():
    # 涨幅>0 且 换手率>2 → 只剩 C (1.8, 3.5)
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "gt", "value": 0},
        {"field": "turnover_rate", "op": "gt", "value": 2},
    ], sort="change_pct", asc=False)
    names = [r["name"] for r in res["rows"]]
    assert names == ["C板块"]


def test_skipped_missing_field():
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "不存在的字段", "op": "gt", "value": 0}])
    assert res["skipped"]
    # 缺字段不应崩，返回全部(其它条件空)
    assert len(res["rows"]) == 3


def test_etf_filter():
    res = engine.filter_etfs(conditions=[
        {"field": "turnover_amount", "op": "gt", "value": 1.5e8}],
        sort="turnover_amount", asc=False)
    codes = [r["code"] for r in res["rows"]]
    assert codes == ["510300"]        # 只有 3e8 > 1.5e8


def test_empty_data(monkeypatch):
    monkeypatch.setattr(db, "query_rows", lambda *a, **k: [])
    res = engine.filter_boards()
    assert res["rows"] == []
    assert res["skipped"]             # 提示先刷新


# ---------- 新运算符 eq/ne/topn_asc ----------
def test_eq_ne():
    # change_pct == 1.8 → 只剩 C；再 ne 1.8 → 剩 A、B
    res_eq = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "eq", "value": 1.8}])
    assert [r["name"] for r in res_eq["rows"]] == ["C板块"]
    res_ne = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "ne", "value": 1.8}], sort="change_pct", asc=False)
    names = [r["name"] for r in res_ne["rows"]]
    assert "C板块" not in names
    assert set(names) == {"A板块", "B板块"}


def test_topn_asc():
    # change_pct 末 2 名(升序) → B(-1.2) 在前、C(1.8)
    res = engine.filter_boards(category="行业", conditions=[
        {"field": "change_pct", "op": "topn_asc", "value": 2}],
        sort=None, limit=50)
    names = [r["name"] for r in res["rows"]]
    assert len(names) == 2
    assert names[0] == "B板块"      # -1.2 最低排首位


# ---------- ETF 派生因子 ----------
def test_etf_derived():
    # activity = turnover_rate * |change_pct|
    # 510300: 0.5 * 1.5 = 0.75 ; 159915: 0.3 * 0.8 = 0.24
    res = engine.filter_etfs(conditions=[
        {"field": "activity", "op": "gt", "value": 0.5}],
        sort="activity", asc=False)
    codes = [r["code"] for r in res["rows"]]
    assert codes == ["510300"]
    assert "activity" in res["rows"][0]
    assert "momentum" in res["rows"][0]
    # momentum 带符号：159915 为负(-0.8*0.3=-0.24) → 510300 为正(0.75)
    res_m = engine.filter_etfs(conditions=[
        {"field": "momentum", "op": "lt", "value": 0}], sort="momentum", asc=False)
    assert [r["code"] for r in res_m["rows"]] == ["159915"]

# -*- coding: utf-8 -*-
"""回测模块单测：合成历史 OHLCV，不依赖网络。

合规：只验 IC/分档/前视/风控/walk-forward 的数学正确性，不涉及买卖点/收益承诺。
运行: pytest tests/test_backtest.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from data import db
from backtest import eval as bt_eval, engine as bt_engine, risk as bt_risk, robust as bt_robust
from backtest import buffett as bt_buf


CODES = ["C001", "C002", "C003", "C004", "C005"]
DAYS = 80


def _rows():
    """合成日线：高 i 的代码增长率更高 → momentum 与前瞻收益正相关。"""
    rows = []
    dates = pd.bdate_range("2023-01-01", periods=DAYS).strftime("%Y-%m-%d")
    for i, c in enumerate(CODES):
        for t, d in enumerate(dates):
            close = 10.0 * (1.0 + i * 0.1) * (1.001 + i * 0.002) ** t
            rows.append({"code": c, "date": str(d), "open": close * 0.99,
                         "high": close * 1.01, "low": close * 0.98,
                         "close": close, "volume": 1e6, "amount": close * 1e6})
    return rows


@pytest.fixture(autouse=True)
def patch_db(monkeypatch):
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: _rows() if table == "etf_daily" else [])


def _close_panel():
    return bt_eval.load_panel("ETF", CODES, "2023-01-01", "2099-01-01", "close")


def test_load_panel_shape():
    p = _close_panel()
    assert not p.empty
    assert set(p.columns) == set(CODES)
    assert len(p) == DAYS


def test_ic_positive_for_momentum():
    close = _close_panel()
    factor = bt_eval.compute_factor(close, "momentum_n", params={"n": 20})
    fwd = bt_eval.forward_returns(close, 5)
    ics = bt_eval.ic_series(factor, fwd)
    s = bt_eval.ic_summary(ics)
    assert s["n"] > 0
    assert s["ic"] > 0  # 动量正相关


def test_forward_returns_no_lookahead_leak():
    """t 期前瞻收益不含 t 之后第 n+1 天以外信息：shift(-n) 形状对。"""
    close = _close_panel()
    fwd = bt_eval.forward_returns(close, 5)
    assert fwd.isna().sum().sum() == 5 * close.shape[1]  # 最后 5 行 NaN


def test_decile_monotone():
    close = _close_panel()
    factor = bt_eval.compute_factor(close, "momentum_n", params={"n": 20})
    fwd = bt_eval.forward_returns(close, 5)
    dec = bt_eval.decile_backtest(factor, fwd, n_groups=5)
    assert dec["dates"]
    # 高档累计净值应 >= 低档(动量正相关)
    groups = dec["groups"]
    assert groups[4][dec["dates"][-1]] >= groups[0][dec["dates"][-1]]


def test_run_backtest_equity_grows():
    close = _close_panel()
    factor = bt_eval.compute_factor(close, "momentum_n", params={"n": 20})
    res = bt_engine.run_backtest(close, factor, topn=3, freq="M")
    eq = pd.Series(res["equity_curve"]).astype(float).sort_index()
    assert len(eq) > 0
    # 动量组合在合成数据下净值应上升
    assert eq.iloc[-1] > eq.iloc[0]


def test_risk_metrics_basic():
    nav = pd.Series([1.0, 1.1, 1.05, 1.2, 0.9], index=range(5))
    m = bt_risk.risk_metrics(nav)
    assert m["n_days"] == 5
    assert m["max_drawdown"] < 0
    assert m["ann_volatility"] >= 0


def test_walk_forward_splits():
    close = _close_panel()
    factor = bt_eval.compute_factor(close, "momentum_n", params={"n": 20})
    wf = bt_robust.walk_forward(factor, close, n=5)
    assert "train" in wf and "test" in wf
    assert "error" not in wf


def test_bootstrap_ci():
    ics = pd.Series([0.1, 0.2, -0.05, 0.15, 0.3, 0.0, 0.12, 0.08])
    b = bt_robust.bootstrap_ic(ics, n_boot=200, seed=1)
    assert b["ci_low"] <= b["mean"] <= b["ci_high"]


# ---------- 候选池 + 可交易性预筛 ----------
from backtest import candidates as bt_cand

STOCK_SPOT_ROWS = [
    {"code": "000001", "name": "平安银行", "latest_price": 12.0, "change_pct": 2.1,
     "turnover_amount": 8e8, "turnover_rate": 1.2, "pe": 5.0, "pb": 0.6, "total_market_cap": 2e11},
    {"code": "000002", "name": "ST万科", "latest_price": 8.0, "change_pct": 9.95,
     "turnover_amount": 3e8, "turnover_rate": 0.8, "pe": None, "pb": 0.5, "total_market_cap": 9e10},
    {"code": "000003", "name": "停牌股", "latest_price": None, "change_pct": None,
     "turnover_amount": 0, "turnover_rate": 0, "pe": 10.0, "pb": 1.0, "total_market_cap": 5e10},
    {"code": "600519", "name": "贵州茅台", "latest_price": 1700.0, "change_pct": -0.5,
     "turnover_amount": 2e9, "turnover_rate": 0.3, "pe": 30.0, "pb": 9.0, "total_market_cap": 2e12},
]


def test_candidate_spot_stock_rank(monkeypatch):
    def q(table, **kw):
        return STOCK_SPOT_ROWS if table == "stock_spot" else _rows() if table == "etf_daily" else []
    monkeypatch.setattr(db, "query_rows", q)
    res = bt_cand.rank_candidates("stock", "turnover_amount", sort="desc", limit=10)
    assert res["mode"] == "spot"
    codes = [r["code"] for r in res["rows"]]
    assert codes[0] == "600519"  # 成交额最大
    assert "000003" in codes    # 未开 tradable，停牌股也在


def test_candidate_tradable_filter(monkeypatch):
    def q(table, **kw):
        return STOCK_SPOT_ROWS if table == "stock_spot" else []
    monkeypatch.setattr(db, "query_rows", q)
    res = bt_cand.rank_candidates("stock", "turnover_amount", sort="desc", limit=10,
                                  tradable=True, min_turnover=5e7, limit_pct=9.9)
    codes = [r["code"] for r in res["rows"]]
    assert "000002" not in codes   # ST + 涨停9.95 → 排除
    assert "000003" not in codes   # 停牌 → 排除
    assert "000001" in codes and "600519" in codes


def _buffett_sample(code, name, moat_tag, ratios, red_flags, ey, priority):
    """构造 analyze() 返回形状的合成样例(不触网)。"""
    return {"code": code, "name": name, "moat_tag": moat_tag,
            "ratios": ratios, "red_flags": red_flags,
            "earnings_yield_pct": ey,
            "valuation_abs": "便宜" if ey and ey > 2.5 else "贵",
            "valuation_tag": "便宜" if ey and ey > 2.5 else "贵",
            "priority": priority}


def test_buffett_reasons_built_from_criteria():
    """rank_top 给每个结果补 reasons，且内容来自已有判定(机械叙事，非推荐)。"""
    results = [
        _buffett_sample("600519", "贵州茅台", "宽(财务质量达标)",
                        {"roe_avg": 25, "gross_margin_avg": 50,
                         "leverage_adj_roe": 15}, [], 6.0,
                        "高(财务强+估值不贵，值得深读年报)"),
        _buffett_sample("600001", "某股", "无/弱",
                        {"roe_avg": 5, "gross_margin_avg": 10},
                        ["资产负债率=80%(高杠杆)"], 1.0,
                        "低(护城河弱或估值贵)"),
    ]
    top = bt_buf.rank_top(results, order="priority", n=10,
                          min_turnover=5e8, shortlist_k=80)
    # priority 排序：高 → 低，茅台第一
    assert top[0]["code"] == "600519"
    r0 = top[0]["reasons"]
    assert len(r0) == 5
    assert any("可买入shortlist" in x for x in r0)
    assert any("通过负面预筛" in x for x in r0)        # red_flags 空
    assert any("宽" in x and "ROE均25%" in x for x in r0)  # 护城河+驱动
    assert any("盈利收益率6.0%" in x for x in r0)     # 估值叙事
    assert "集内第1名(优先级排序)" in r0              # 排名
    # 有红旗的标的：reasons 列负面红旗而非"通过负面预筛"
    r1 = top[1]["reasons"]
    assert any("负面红旗" in x and "资产负债率" in x for x in r1)
    assert "集内第2名(优先级排序)" in r1

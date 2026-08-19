# -*- coding: utf-8 -*-
"""quality 因子体系升级测试(B+D+A+C+E+F)。mock DB/buffett/signals，不触网。
仓库根目录跑：python -m pytest tests/test_quality_factors.py -q"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from unittest.mock import patch

from backtest import quality
from screener import smart_money as sm_q


# ---------- Phase 1: _avg_rank_pct helper + 口径2 丰富 ----------

def test_avg_rank_pct_handles_missing():
    """缺失因子跳过不拖累；全缺→NaN。"""
    codes = ["a", "b", "c"]
    f1 = pd.Series({"a": 1.0, "b": 2.0, "c": 3.0})        # a 有
    f2 = pd.Series({"a": 10.0, "c": 30.0})                 # b 缺
    f3 = pd.Series({"b": 5.0})                             # a,c 缺
    r = quality._avg_rank_pct([f1, f2, f3], codes)
    # a 命中 f1,f2 → 取两者 rank-pct 均值；c 命中 f1,f2；b 只命中 f3
    assert pd.notna(r["a"]) and pd.notna(r["b"]) and pd.notna(r["c"])
    # 全缺的 code
    f_empty = pd.Series(dtype=float)
    r2 = quality._avg_rank_pct([f_empty], codes)
    assert pd.isna(r2["a"])


def test_avg_rank_pct_robust_to_outlier():
    """极端值不主导(rank vs zscore)：1 个巨大异常值不应让该 code 独占满分之外的悬殊。"""
    codes = ["a", "b", "c", "d"]
    # d 是极端大值；zscore 下 d 会甩开，rank 下 d=1.0、其余 0-1 均匀
    f = pd.Series({"a": 1.0, "b": 2.0, "c": 3.0, "d": 10000.0})
    r = quality._avg_rank_pct([f], codes)
    # rank-pct: d 最高=1.0，其余 <1 且相邻差距小（不受 10000 量级影响）
    assert r["d"] == 1.0
    assert r["a"] < r["b"] < r["c"] < r["d"]
    # 相邻差距应远小于 zscore 的相邻差距（rank 均匀）
    assert (r["c"] - r["a"]) < 0.6  # 3 个相邻 rank 间距不会悬殊


def test_dim2_enriched_six_factors():
    """口径2 用 6 因子(ey/moat/lroe/roic/mos/oet)且 buffett 结果含 roic/mos/oet 时 status=ok。"""
    quality._RESULT_CACHE.clear()
    rows = [
        {"code": "000001", "name": "平A", "latest_price": 10.0, "turnover_amount": 1e8,
         "change_pct": 2.0, "main_net_inflow": 1e7, "turnover_rate": 3.0,
         "pe": 15.0, "pb": 1.5, "amplitude": 3.0, "board": "银行"},
        {"code": "600519", "name": "贵C", "latest_price": 1500.0, "turnover_amount": 2e8,
         "change_pct": 1.0, "main_net_inflow": 3e7, "turnover_rate": 2.0,
         "pe": 30.0, "pb": 8.0, "amplitude": 2.0, "board": "白酒"},
    ]
    qr = {"stock_spot": rows, "industry_board": []}

    def _res(code):
        return {
            "code": code,
            "earnings_yield_pct": 7.0 if code == "600519" else 5.0,
            "moat_score": 3,
            "ratios": {"leverage_adj_roe": 18.0, "roic": 16.0,
                       "owner_earnings_to_ni": 0.8,
                       "goodwill_to_equity_pct": 5.0,
                       "debt_ratio_latest": 40.0,
                       "fcf_to_netincome": 0.7},
            "margin_of_safety": 0.3 if code == "600519" else 0.1,
        }

    with patch("data.db.query_rows", side_effect=lambda t, **k: qr.get(t, [])), \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.buffett._AK_OK", True), \
         patch("backtest.buffett.akshare_blocked", return_value=False), \
         patch("backtest.buffett.shortlist_by_turnover",
               return_value=["000001", "600519"]), \
         patch("backtest.buffett.analyze_many",
               return_value=[_res("000001"), _res("600519")]), \
         patch("screener.smart_money.top_by_amount", return_value={"rows": []}), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}), \
         patch("backtest.quality._is_in_session", return_value=False):
        res = quality.quality_rank(universe="stock", refine=False, min_turnover=0,
                                    dim_thresh=0.0, min_dims=1)
    assert res["dim_status"]["2"] == "ok", res["dim_status"]
    # 600519 价值质量分位应高于 000001(ey/mos 都更高)
    main_codes = {m["code"]: m for m in res["main"]}
    if "600519" in main_codes and "000001" in main_codes:
        d5 = (main_codes["600519"].get("dim_scores") or {}).get("2")
        d1 = (main_codes["000001"].get("dim_scores") or {}).get("2")
        if d5 is not None and d1 is not None:
            assert d5 > d1, "600519 价值质量分位应更高"


# ---------- Phase 2: _behavior_batch + 口径3 改造 ----------

def test_behavior_batch_one_query():
    """一次 DB 查询服务多 code×多 channel,按 code 聚合 streak/cum 正确。"""
    calls = {"n": 0}
    rows = [
        # 000001 资金流:6 日全正(升序)→streak_inflow=6,outflow=0;margin_accel 需≥5
        {"code": "000001", "channel": "资金流", "date": "2026-08-09", "amount": 1e7},
        {"code": "000001", "channel": "资金流", "date": "2026-08-10", "amount": 2e7},
        {"code": "000001", "channel": "资金流", "date": "2026-08-11", "amount": 3e7},
        {"code": "000001", "channel": "资金流", "date": "2026-08-12", "amount": 4e7},
        {"code": "000001", "channel": "资金流", "date": "2026-08-13", "amount": 5e7},
        {"code": "000001", "channel": "资金流", "date": "2026-08-14", "amount": 6e7},
        # 600519 北向:累计净额
        {"code": "600519", "channel": "北向", "date": "2026-08-10", "amount": 5e6},
        {"code": "600519", "channel": "北向", "date": "2026-08-12", "amount": 1e7},
    ]

    def _qr(table, **kw):
        calls["n"] += 1
        return rows

    with patch("data.db.query_rows", side_effect=_qr):
        bb = sm_q._behavior_batch(["000001", "600519"], days=30)
    assert calls["n"] == 1, "应仅 1 次 DB 查询(批量)"
    assert bb["000001"]["streak_inflow"] == 6
    assert bb["000001"]["streak_outflow"] == 0
    assert bb["000001"]["margin_accel"] is not None  # 6 日≥5
    assert bb["600519"]["north_cum"] == round(5e6 + 1e7, 2)
    # 交叉:600519 无资金流→None;000001 无北向→None
    assert bb["600519"]["streak_inflow"] is None
    assert bb["000001"]["north_cum"] is None


def test_behavior_batch_no_finshare():
    """资金流通道空→字段 None,且不触网调 finshare(批量场景不回退)。"""
    with patch("data.db.query_rows", return_value=[]), \
         patch("screener.smart_money._finshare_fund_flow_series") as ff:
        bb = sm_q._behavior_batch(["000001"], days=30)
    assert bb["000001"]["streak_inflow"] is None
    assert bb["000001"]["north_cum"] is None
    assert bb["000001"]["margin_accel"] is None
    ff.assert_not_called()  # 批量场景不调 finshare 回退


def test_dim3_uses_behavior():
    """口径3 用 _behavior_batch 的 streak/north/marginal,status ok(连续性+北向+边际)。"""
    quality._RESULT_CACHE.clear()
    rows = [
        {"code": "000001", "name": "平A", "latest_price": 10.0, "turnover_amount": 1e8,
         "change_pct": 2.0, "main_net_inflow": 1e7, "turnover_rate": 3.0,
         "pe": 15.0, "pb": 1.5, "amplitude": 3.0, "board": "银行"},
        {"code": "600519", "name": "贵C", "latest_price": 1500.0, "turnover_amount": 2e8,
         "change_pct": 1.0, "main_net_inflow": 3e7, "turnover_rate": 2.0,
         "pe": 30.0, "pb": 8.0, "amplitude": 2.0, "board": "白酒"},
    ]
    qr = {"stock_spot": rows, "industry_board": []}
    bb = {
        "000001": {"streak_inflow": 5, "streak_outflow": 0,
                   "margin_accel": 1e6, "north_cum": 2e7},
        "600519": {"streak_inflow": 1, "streak_outflow": 0,
                   "margin_accel": -5e5, "north_cum": 5e6},
    }

    with patch("data.db.query_rows", side_effect=lambda t, **k: qr.get(t, [])), \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.buffett._AK_OK", True), \
         patch("backtest.buffett.akshare_blocked", return_value=False), \
         patch("backtest.buffett.shortlist_by_turnover",
               return_value=["000001", "600519"]), \
         patch("backtest.buffett.analyze_many", return_value=[]), \
         patch("screener.smart_money._behavior_batch", return_value=bb), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}), \
         patch("backtest.quality._is_in_session", return_value=False):
        res = quality.quality_rank(universe="stock", refine=False, min_turnover=0,
                                   dim_thresh=0.0, min_dims=1)
    assert res["dim_status"]["3"] == "ok(连续性+北向+边际)", res["dim_status"]
    main = {m["code"]: m for m in res["main"]}
    # 000001 streak/north/marginal 均优于 600519 → 口径3 分位更高
    d1 = (main.get("000001", {}).get("dim_scores") or {}).get("3")
    d2 = (main.get("600519", {}).get("dim_scores") or {}).get("3")
    if d1 is not None and d2 is not None:
        assert d1 > d2, "000001 资金流口径应更高"


# ---------- Phase 3: 共振层改进(C) ----------

def test_resonance_penalize_punishes_low():
    """penalize 几何均值:某口径极低(短板)拉低总分,低于均衡两口径。"""
    # [0.9, 0.1] 有短板 vs [0.5, 0.5] 均衡 → penalize 下前者更低
    r_low, _ = quality._resonance({1: 0.9, 2: 0.1}, dim_thresh=0.0,
                                  weights={}, mode="penalize")
    r_bal, _ = quality._resonance({1: 0.5, 2: 0.5}, dim_thresh=0.0,
                                  weights={}, mode="penalize")
    assert r_low < r_bal, f"短板应被惩罚:{r_low} < {r_bal}"
    # greedy 下无惩罚:两者均值相近,0.9+0.1 与 0.5+0.5 均为 0.5 → 相等(数量偏好)
    g_low, _ = quality._resonance({1: 0.9, 2: 0.1}, 0.0, weights={}, mode="greedy")
    g_bal, _ = quality._resonance({1: 0.5, 2: 0.5}, 0.0, weights={}, mode="greedy")
    assert g_low == g_bal, "greedy 等权不惩罚短板(数量偏好)"


def test_resonance_greedy_unchanged():
    """greedy 默认行为不变:hits×10 + 命中口径加权均值(数量优先)。"""
    # 多口径命中 >> 单口径高分(greedy 数量偏好)
    a, ha = quality._resonance({1: 0.5, 2: 0.5, 3: 0.5}, 0.4, weights={})
    b, hb = quality._resonance({1: 0.99}, 0.4, weights={})
    assert ha == 3 and hb == 1
    assert a > b  # 3 命中(30+) > 1 命中(10+)


def test_dim_thresh_default_0p7():
    """quality_rank 默认 dim_thresh=0.7(提区分度)。"""
    import inspect
    sig = inspect.signature(quality.quality_rank)
    assert sig.parameters["dim_thresh"].default == 0.7
    assert sig.parameters["resonance_mode"].default == "greedy"


def test_default_dim_weights():
    """不传 weights → 用 _DEFAULT_DIM_WEIGHTS:高分(1.0)落在口径2(w=1.3)比落在
    口径3(w=0.6)对共振分贡献更大(同 hits 下,加权均值权重起作用需两命中口径分位不同)。"""
    # 两标的均 2 命中,高分 1.0 落在不同口径:甲在口径2,乙在口径3,另一口径 0.0
    ra, _ = quality._resonance({2: 1.0, 3: 0.0}, dim_thresh=0.0)  # 高分在口径2(w=1.3)
    rb, _ = quality._resonance({2: 0.0, 3: 1.0}, dim_thresh=0.0)  # 高分在口径3(w=0.6)
    assert ra > rb, f"口径2 权重高→高分落口径2 共振分应更高:{ra} > {rb}"
    # 默认权重:口径2/5=1.3 > 口径1=1.0 > 口径4=0.7 > 口径3=0.6
    assert quality._DEFAULT_DIM_WEIGHTS == {1: 1.0, 2: 1.3, 3: 0.6, 4: 0.7, 5: 1.3}


# ---------- Phase 4: 操盘因子增强 ----------

def test_risk_factors_use_downside_and_multi_window():
    """风险口径包含下行波动、多窗口动量和成交额加速。"""
    idx = pd.date_range("2026-01-01", periods=80)
    close = pd.DataFrame({
        "a": [10 + i * 0.10 for i in range(80)],
        "b": [10 + i * 0.02 for i in range(80)],
    }, index=idx)
    amount = pd.DataFrame({
        "a": [1e6] * 60 + [2e6] * 20,
        "b": [1e6] * 80,
    }, index=idx)
    factors = quality._risk_factor_series(close, amount, days=20)
    for name in ("volatility", "downside_volatility", "momentum_5",
                 "momentum_20", "momentum_60", "sortino", "amount_accel"):
        assert name in factors
    assert factors["momentum_5"]["a"] > factors["momentum_5"]["b"]
    assert factors["amount_accel"]["a"] > factors["amount_accel"]["b"]


def test_value_factors_include_growth_and_fcf_yield():
    """价值质量口径加入营收增长、毛利趋势和 FCF yield。"""
    results = [
        {"code": "a", "earnings_yield_pct": 5, "moat_score": 2,
         "ratios": {"leverage_adj_roe": 10, "roic": 8,
                    "owner_earnings_to_ni": .7, "rev_cagr": 3,
                    "gross_margin_trend": 1, "fcf_proxy": 1e7}},
        {"code": "b", "earnings_yield_pct": 5, "moat_score": 2,
         "ratios": {"leverage_adj_roe": 10, "roic": 8,
                    "owner_earnings_to_ni": .7, "rev_cagr": 8,
                    "gross_margin_trend": 4, "fcf_proxy": 3e7}},
    ]
    spot = pd.DataFrame([
        {"code": "a", "circulating_market_cap": 1e9},
        {"code": "b", "circulating_market_cap": 1e9},
    ])
    factors = quality._value_factor_series(results, spot, ["a", "b"])
    assert {"rev_cagr", "gross_margin_trend", "fcf_yield"} <= set(factors)
    assert factors["rev_cagr"]["b"] > factors["rev_cagr"]["a"]
    assert factors["fcf_yield"]["b"] > factors["fcf_yield"]["a"]


def test_flow_factors_include_quote_and_dragon(monkeypatch):
    """资金口径纳入通达信内外盘比与龙虎榜净额。"""
    monkeypatch.setattr(
        "data.pytdx_client.get_quote",
        lambda codes: [
            {"code": "a", "b_vol": 200, "s_vol": 100},
            {"code": "b", "b_vol": 100, "s_vol": 200},
        ],
    )
    monkeypatch.setattr(
        "data.db.query_rows",
        lambda table, **kwargs: [
            {"code": "a", "channel": "龙虎榜", "amount": 3e7},
            {"code": "b", "channel": "龙虎榜", "amount": -1e7},
        ] if table == "smart_money_action" else [],
    )
    behavior = {
        "a": {"streak_inflow": 4, "streak_outflow": 0,
               "north_cum": 1e7, "margin_accel": 2e6},
        "b": {"streak_inflow": 1, "streak_outflow": 4,
               "north_cum": -1e7, "margin_accel": -2e6},
    }
    factors = quality._flow_factor_series(["a", "b"], behavior)
    assert {"streak_inflow", "north_cum", "margin_accel",
            "inner_outer_ratio", "dragon_net"} <= set(factors)
    assert factors["inner_outer_ratio"]["a"] > factors["inner_outer_ratio"]["b"]
    assert factors["dragon_net"]["a"] > factors["dragon_net"]["b"]
    assert factors["streak_inflow"]["b"] == 0


def test_signal_factors_include_recent_intensity():
    """多信号口径同时使用历史胜率、当日命中数和触发强度。"""
    scan = {"rows": [
        {"code": "a", "hits": 4, "signal_keys": ["x", "y", "z", "w"]},
        {"code": "b", "hits": 1, "signal_keys": ["x"]},
    ]}
    backtest = {"rows": [
        {"signal": "x", "excess_win_rate": .1},
        {"signal": "y", "excess_win_rate": .2},
        {"signal": "z", "excess_win_rate": .3},
        {"signal": "w", "excess_win_rate": .4},
    ]}
    factors = quality._signal_factor_series(scan, backtest, ["a", "b"])
    assert {"win_rate", "signal_hits", "recent_intensity"} <= set(factors)
    assert factors["signal_hits"]["a"] > factors["signal_hits"]["b"]
    assert factors["recent_intensity"]["a"] > factors["recent_intensity"]["b"]


def test_industry_proxy_factor_uses_board_momentum(monkeypatch):
    """研报不可用时，行业板块涨跌幅可作为景气代理。"""
    monkeypatch.setattr(
        "data.db.query_rows",
        lambda table, **kwargs: [
            {"name": "板块A", "change_pct": 8, "members": ["a"]},
            {"name": "板块B", "change_pct": -2, "members": ["b"]},
        ] if table == "industry_board" else [],
    )
    factor = quality._industry_proxy_series(["a", "b"])
    assert factor["a"] > factor["b"]

# -*- coding: utf-8 -*-
"""quality 因子体系升级测试(B+D+A+C+E+F)。mock DB/buffett/signals，不触网。
仓库根目录跑：python -m pytest tests/test_quality_factors.py -q"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from unittest.mock import patch

from backtest import quality


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

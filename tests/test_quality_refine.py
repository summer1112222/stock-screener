# -*- coding: utf-8 -*-
"""quality 盘口精排测试。mock pytdx_client.get_quote，不真连网。"""
import datetime as dt
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import quality


def test_is_in_session_morning():
    # 周一 10:00 = 盘中
    mon = dt.datetime(2026, 8, 10, 10, 0)  # 2026-08-10 周一
    assert quality._is_in_session(mon) is True


def test_is_in_session_afternoon():
    tue = dt.datetime(2026, 8, 11, 14, 30)  # 周二 14:30
    assert quality._is_in_session(tue) is True


def test_is_in_session_lunch():
    mon = dt.datetime(2026, 8, 10, 12, 0)  # 午休 12:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_preopen():
    mon = dt.datetime(2026, 8, 10, 9, 0)  # 盘前 9:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_afterclose():
    mon = dt.datetime(2026, 8, 10, 16, 0)  # 盘后 16:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_weekend():
    sat = dt.datetime(2026, 8, 15, 10, 0)  # 周六
    assert quality._is_in_session(sat) is False


def _mock_quote(code):
    """构造一只 mock 五档行情：bid 略厚、主动买略多。"""
    return {
        "code": code, "price": 10.0, "last_close": 9.8, "open": 9.9,
        "high": 10.2, "low": 9.8, "vol": 10000.0, "amount": 1e7,
        "b_vol": 5500.0, "s_vol": 4500.0,
        "bid1": 9.99, "ask1": 10.01,
        "bid2": 9.98, "ask2": 10.02, "bid3": 9.97, "ask3": 10.03,
        "bid4": 9.96, "ask4": 10.04, "bid5": 9.95, "ask5": 10.05,
        "bid_vol1": 600.0, "ask_vol1": 400.0,
        "bid_vol2": 500.0, "ask_vol2": 300.0,
        "bid_vol3": 400.0, "ask_vol3": 200.0,
        "bid_vol4": 300.0, "ask_vol4": 100.0,
        "bid_vol5": 200.0, "ask_vol5": 50.0,
    }


def test_refine_intraday_factors_and_resort():
    # 000001 低 resonance(20.4) 靠高 liquidity(5x厚盘口) 翻盘；
    # 000002 高 resonance(20.5) 盘口极薄(挂单量为0, depth=None)。
    # rank pct 对 2 值对称(两轴 pct 差距均=0.5)，0.6*0.5=0.3 > 0.4*0.5=0.2 →
    # 仅当 000002 depth=None(排除 lpct→pct=0.0)时 liquidity pct 差距=1.0
    # 才足以让 0.4×liquidity 覆盖 0.6×resonance。这证明 0.4 权重真生效：
    # 若权重=0(liquidity 无效)，000002 单靠 resonance 该赢 → 测试 fail。
    pool = [
        {"code": "000001", "name": "平A", "resonance": 20.4, "hits": 2, "dim_scores": {}},
        {"code": "000002", "name": "万B", "resonance": 20.5, "hits": 2, "dim_scores": {}},
    ]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}, {"code": "000002"}])

    def fake_get_quote(codes):
        out = []
        for c in codes:
            q = _mock_quote(c)
            if c == "000001":
                # 盘口 5× 厚
                for i in range(1, 6):
                    q[f"bid_vol{i}"] *= 5
                    q[f"ask_vol{i}"] *= 5
            else:
                # 000002 盘口极薄：挂单量为 0 → depth=None → 排除 lpct
                for i in range(1, 6):
                    q[f"bid_vol{i}"] = 0.0
                    q[f"ask_vol{i}"] = 0.0
            out.append(q)
        return out

    with patch("data.pytdx_client.get_quote", side_effect=fake_get_quote):
        out_pool, status, qmap = quality._refine_by_quote(list(pool), df, in_session=True)

    assert status == "ok(盘中)"
    # 000001 流动性深度更高 → 综合分更高 → 排前
    # (单靠 resonance 000002 该赢；0.4×liquidity 覆盖了 0.6×resonance)
    assert out_pool[0]["code"] == "000001"
    # quote 字段齐
    q0 = out_pool[0]["quote"]
    assert q0["liquidity_depth"] is not None
    assert q0["bid_ask_ratio"] is not None
    assert q0["inner_outer_ratio"] is not None
    assert q0["liquidity_pct"] is not None
    assert q0["in_session"] is True
    # _refine_score 为内部键，最终 quality_rank 清理；此处精排阶段仍存在
    assert out_pool[0]["_refine_score"] >= out_pool[1]["_refine_score"]


def test_refine_afterclose_a_b_none():
    # 盘后：A(liquidity_depth)/B(bid_ask_ratio) 应置 None + note，
    # 仅 C(inner_outer_ratio) 全天有效
    pool = [{"code": "000001", "name": "平A", "resonance": 20.0, "hits": 1, "dim_scores": {}}]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}])

    with patch("data.pytdx_client.get_quote", side_effect=lambda cs: [_mock_quote(cs[0])]):
        out_pool, status, qmap = quality._refine_by_quote(list(pool), df, in_session=False)

    assert status == "ok(盘后,仅C展示)"
    q = out_pool[0]["quote"]
    assert q["liquidity_depth"] is None
    assert q["bid_ask_ratio"] is None
    assert q["inner_outer_ratio"] is not None  # C 全天有效
    assert q["liquidity_pct"] is None
    assert q["in_session"] is False
    assert "note" in q
    # 盘后不重排，无 _refine_score
    assert "_refine_score" not in out_pool[0]


def test_refine_get_quote_exception():
    # get_quote 抛异常 → refine_status=err，pool 不变
    pool = [{"code": "000001", "name": "平A", "resonance": 20.0, "hits": 1, "dim_scores": {}}]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}])

    def boom(codes):
        raise ConnectionRefusedError("tdx down")

    with patch("data.pytdx_client.get_quote", side_effect=boom):
        out_pool, status, qmap = quality._refine_by_quote(list(pool), df, in_session=True)

    assert status == "err:通达信不可用,跳过精排"
    assert qmap == {}
    # pool 不变：无 quote 字段、无 _refine_score
    assert "quote" not in out_pool[0]
    assert "_refine_score" not in out_pool[0]
    assert out_pool[0]["code"] == "000001"


def _seed_spot_rows():
    """3 只合成 spot 行，供 quality_rank 走通。"""
    return [
        {"code": "000001", "name": "平A", "latest_price": 10.0, "turnover_amount": 1e8,
         "change_pct": 2.0, "main_net_inflow": 1e7, "turnover_rate": 3.0,
         "pe": 15.0, "pb": 1.5, "amplitude": 3.0, "board": "银行"},
        {"code": "000002", "name": "万B", "latest_price": 20.0, "turnover_amount": 1.2e8,
         "change_pct": 3.0, "main_net_inflow": 2e7, "turnover_rate": 4.0,
         "pe": 18.0, "pb": 2.0, "amplitude": 4.0, "board": "地产"},
        {"code": "600519", "name": "贵C", "latest_price": 1500.0, "turnover_amount": 2e8,
         "change_pct": 1.0, "main_net_inflow": 3e7, "turnover_rate": 2.0,
         "pe": 30.0, "pb": 8.0, "amplitude": 2.0, "board": "白酒"},
    ]


def test_quality_rank_intraday_attaches_quote_and_resort():
    # 盘中：mock 全部 DB 表 + get_quote + buffett/signals 依赖
    import pandas as pd
    quality._RESULT_CACHE.clear()
    rows = _seed_spot_rows()
    qr = {"stock_spot": rows, "industry_board": []}

    def fake_query(table, **kw):
        return qr.get(table, [])

    def fake_get_quote(codes):
        return [_mock_quote(c) for c in codes]

    # 屏蔽口径1/4 对历史的依赖（返空历史）+ 口径2 buffett + 口径3 smart_money
    # _is_in_session mock 为 True → 盘中精排路径（否则测试受运行时刻影响）
    with patch("data.db.query_rows", side_effect=fake_query), \
         patch("data.pytdx_client.get_quote", side_effect=fake_get_quote), \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.buffett._AK_OK", False), \
         patch("screener.smart_money.top_by_amount", return_value={"rows": []}), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}), \
         patch("backtest.quality._is_in_session", return_value=True):
        res = quality.quality_rank(universe="stock", refine=True, refine_pool=3,
                                   min_turnover=0, dim_thresh=0.0, min_dims=1)

    assert res["refine_status"] == "ok(盘中)"
    assert len(res["main"]) >= 1
    # 每行有 quote 字段
    assert "quote" in res["main"][0]
    assert res["main"][0]["quote"]["in_session"] is True


def test_refine_quote_empty_no_crash():
    # get_quote 返空 [] → refine_status=err，pool 原样返回不崩
    pool = [{"code": "000001", "name": "x", "resonance": 20.0, "hits": 2, "dim_scores": {}}]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}])
    with patch("data.pytdx_client.get_quote", side_effect=lambda cs: []):
        out, status, qmap = quality._refine_by_quote(list(pool), df, in_session=True)
    assert status == "err:通达信不可用,跳过精排"
    assert qmap == {}
    assert out[0]["code"] == "000001"  # pool 不变

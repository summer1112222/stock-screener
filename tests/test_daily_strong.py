# -*- coding: utf-8 -*-
"""daily-strong 每日强势5步漏斗单测。mock db.query_rows，不触网。"""
import screener.daily_strong as ds


def test_nan_none():
    assert ds._nan(float("nan")) is None
    assert ds._nan(float("inf")) is None
    assert ds._nan(3.5) == 3.5
    assert ds._nan(None) is None


def test_to_f():
    assert ds._to_f("abc") is None
    assert ds._to_f(3.5) == 3.5
    assert ds._to_f(float("nan")) is None


def test_clip():
    assert ds._clip(1.5) == 1.0
    assert ds._clip(-0.5) == 0.0
    assert ds._clip(0.5) == 0.5


def test_step1_pass():
    p = {"min_change_pct": 5.0, "min_turnover": 3.0, "max_price": 50.0}
    ok = {"change_pct": 6.0, "turnover_rate": 4.0, "latest_price": 20.0}
    assert ds._step1_pass(ok, p) is True
    # 涨幅不足
    assert ds._step1_pass({**ok, "change_pct": 4.0}, p) is False
    # 换手不足
    assert ds._step1_pass({**ok, "turnover_rate": 2.0}, p) is False
    # 价过高
    assert ds._step1_pass({**ok, "latest_price": 60.0}, p) is False


def test_step2_pass():
    p = {"min_mv": 10.0, "max_mv": 200.0, "max_pe": 150.0}
    ok = {"circulating_market_cap": 100.0, "pe": 30.0, "st_type": None}
    assert ds._step2_pass(ok, p) is True
    # 市值过小
    assert ds._step2_pass({**ok, "circulating_market_cap": 5.0}, p) is False
    # 市值过大
    assert ds._step2_pass({**ok, "circulating_market_cap": 300.0}, p) is False
    # PE 过高
    assert ds._step2_pass({**ok, "pe": 200.0}, p) is False
    # 亏损(pe 空)
    assert ds._step2_pass({**ok, "pe": None}, p) is False
    # ST
    assert ds._step2_pass({**ok, "st_type": "ST"}, p) is False


import pandas as pd


def _mk_close(codes_prices: dict, n=65):
    """造 close 面板: {code: [p0..p63]} 升序。n=65 够算60MA。
    不足 n 日补 NaN(非前值)——使 dropna 反映真实历史长度,need_history 测试方生效。"""
    data = {}
    for c, pxs in codes_prices.items():
        if len(pxs) < n:
            full = list(pxs) + [float("nan")] * (n - len(pxs))
        else:
            full = list(pxs)
        data[c] = full[:n]
    df = pd.DataFrame(data)
    df.index = pd.date_range("2026-06-01", periods=n, name="date")
    return df


def test_step3_bullish_align(monkeypatch):
    # A: 5/10/20 日均线严格上行发散(价递增)
    close = _mk_close({"A": list(range(60, 125))})  # 严格递增
    amount = _mk_close({"A": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    info = ds._ma_arrange_batch("stock", ["A"])
    assert info["A"]["bullish_align"] is True
    assert info["A"]["need_history"] is False
    assert ds._step3_pass(info["A"]) is True


def test_step3_volume_breakout(monkeypatch):
    # B: 站稳60日线 + 当日量翻倍(不严格多头排列但放量突破)
    px = [100] * 65  # 平盘,close>ma60 满足
    px[-1] = 101
    close = _mk_close({"B": px})
    # 最后一天量是前20日均量2倍
    amt = [100] * 65
    amt[-1] = 250
    amount = _mk_close({"B": amt})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    info = ds._ma_arrange_batch("stock", ["B"])
    assert info["B"]["volume_breakout"] is True
    assert ds._step3_pass(info["B"]) is True


def test_step3_bearish_reject(monkeypatch):
    # C: 空头排列(价递减)
    close = _mk_close({"C": list(range(125, 60, -1))})
    amount = _mk_close({"C": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    info = ds._ma_arrange_batch("stock", ["C"])
    assert info["C"]["bearish"] is True
    assert ds._step3_pass(info["C"]) is False


def test_step3_need_history(monkeypatch):
    # D: <60 日历史
    short = _mk_close({"D": [10, 11, 12]}, n=65)  # 只3个真实值
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (short, short))
    info = ds._ma_arrange_batch("stock", ["D"])
    assert info["D"]["need_history"] is True
    assert ds._step3_pass(info["D"]) is False


def test_step4_score():
    # 量比>2.5 满分 + 涨幅<7% 满分
    s = {"volume_ratio": 3.0, "change_pct": 5.5}
    sc = ds._step4_score(s)
    assert 80 < sc <= 100
    # 涨幅>7% 扣分
    s2 = {"volume_ratio": 3.0, "change_pct": 8.0}
    assert ds._step4_score(s2) < sc
    # 量比低
    s3 = {"volume_ratio": 1.0, "change_pct": 5.5}
    assert ds._step4_score(s3) < sc


def test_step5_pass():
    spots = [
        {"code": "A", "board": "电池", "change_pct": 10.0},
        {"code": "B", "board": "电池", "change_pct": 10.0},
        {"code": "C", "board": "电池", "change_pct": 2.0},
    ]
    # 电池板块净流入排名第3(前5) + 2涨停
    sff = [{"name": "半导体", "main_net_inflow": 100},
           {"name": "军工", "main_net_inflow": 80},
           {"name": "电池", "main_net_inflow": 60}]
    ok, d = ds._step5_pass("A", spots, sff)
    assert ok is True
    assert d["board"] == "电池"
    assert d["board_rank"] == 3
    assert d["board_zt_count"] == 2


def test_step5_fail_rank():
    spots = [{"code": "A", "board": "电池", "change_pct": 10.0}]
    # 电池排名第10(>5)
    sff = [{"name": f"板块{i}", "main_net_inflow": 100 - i} for i in range(10)]
    sff.append({"name": "电池", "main_net_inflow": 1})
    ok, d = ds._step5_pass("A", spots, sff)
    assert ok is False
    assert d["board_rank"] == 11


def test_step5_no_board():
    spots = [{"code": "A", "board": None}]
    ok, d = ds._step5_pass("A", spots, [])
    assert ok is False
    assert d["board"] is None


_SPOT = [
    {"code": "A", "name": "甲", "change_pct": 6.0, "volume_ratio": 3.0,
     "turnover_rate": 5.0, "latest_price": 20.0,
     "circulating_market_cap": 100.0, "pe": 30.0, "st_type": None,
     "board": "电池"},
    {"code": "B", "name": "乙", "change_pct": 8.0, "volume_ratio": 1.0,
     "turnover_rate": 4.0, "latest_price": 30.0,
     "circulating_market_cap": 50.0, "pe": 40.0, "st_type": None,
     "board": "电池"},
    {"code": "C", "name": "丙", "change_pct": 2.0, "volume_ratio": 0.5,
     "turnover_rate": 1.0, "latest_price": 10.0,
     "circulating_market_cap": 5.0, "pe": 10.0, "st_type": "ST",
     "board": "军工"},
]
_SFF = [{"name": "电池", "main_net_inflow": 100},
        {"name": "军工", "main_net_inflow": 50}]


def _mock_orch(monkeypatch, close_map=None):
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: (_SPOT if table == "stock_spot"
                            else _SFF if table == "sector_fund_flow" else []))
    # step3: A/B 多头排列通过, C 需历史
    close = close_map or _mk_close(
        {"A": list(range(60, 125)), "B": list(range(60, 125))})
    amount = _mk_close({"A": [100] * 65, "B": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels",
                        lambda u, codes: (close, amount))
    # 涨停股: A 涨停(6.0 改 10.0 模拟) — 用 spots 内 A change_pct=6 不涨停
    # 为测 step5, 单测里直接造板块内2涨停
    ds._CACHE.clear()


def test_rank_basic(monkeypatch):
    _mock_orch(monkeypatch)
    # A: step1✓ step2✓ step3✓ step5(电池排名1但只A涨停1只<2→✗)
    # B: step1✓ step2✓ step3✓ step5(电池,1只<2→✗)
    r = ds.daily_strong_rank(limit=10, min_change_pct=5.0)
    assert r["count"] == 2  # A,B 通过粗筛
    by = {i["code"]: i for i in r["items"]}
    assert by["A"]["step1_pass"] is True
    assert by["A"]["step2_pass"] is True
    assert by["A"]["step3_pass"] is True
    assert by["A"]["step5_pass"] is False  # 涨停股不足
    # A 通过 step1/2/3 = 3 步硬剔除
    assert by["A"]["hard_pass"] >= 3


def test_rank_order_hard_pass(monkeypatch):
    _mock_orch(monkeypatch)
    r = ds.daily_strong_rank(limit=10, min_change_pct=1.0)
    # A 硬通过3步(step1/2/3) > C 硬通过0步 → A 排前
    items = r["items"]
    a_idx = next(i for i, x in enumerate(items) if x["code"] == "A")
    c_idx = next(i for i, x in enumerate(items) if x["code"] == "C")
    assert a_idx < c_idx


def test_rank_codes_limit(monkeypatch):
    _mock_orch(monkeypatch)
    r = ds.daily_strong_rank(codes=["A"], limit=10, min_change_pct=0.0)
    assert r["count"] == 1
    assert r["items"][0]["code"] == "A"


def test_rank_empty_spot(monkeypatch):
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    ds._CACHE.clear()
    r = ds.daily_strong_rank(limit=10)
    assert r["count"] == 0
    assert "先 /api/refresh" in r.get("note", "")


def test_rank_cache(monkeypatch):
    _mock_orch(monkeypatch)
    r1 = ds.daily_strong_rank(limit=10, min_change_pct=5.0)
    # 改 spot 后再调(应命中缓存,不变)
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: [])
    r2 = ds.daily_strong_rank(limit=10, min_change_pct=5.0)
    assert r2["count"] == r1["count"]


def test_route_daily_strong(monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as srv
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: (_SPOT if table == "stock_spot"
                            else _SFF if table == "sector_fund_flow" else []))
    close = _mk_close({"A": list(range(60, 125)), "B": list(range(60, 125))})
    amount = _mk_close({"A": [100] * 65, "B": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    ds._CACHE.clear()
    client = TestClient(srv.app)
    r = client.get("/api/daily-strong?limit=10&min_change_pct=5.0")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "cand_disclaimer" in body
    assert "每日强势" in body["cand_disclaimer"]
    assert body["data"]["count"] >= 0

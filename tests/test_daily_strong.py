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

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

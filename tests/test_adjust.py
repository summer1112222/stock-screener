# tests/test_adjust.py
# -*- coding: utf-8 -*-
"""前复权本地计算单测。mock xdxr 因子，手算预期 qfq，断言累乘/vol/边界。
不依赖网络。"""
import math

import pandas as pd

from data import adjust


def _raw(dates, closes, vols=None):
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": vols if vols is not None else [100] * len(dates),
    })


def test_qfq_no_event_returns_sorted_unchanged():
    raw = _raw(["2025-01-02", "2025-01-01"], [11, 10])
    out = adjust.qfq(raw, pd.DataFrame())
    assert list(out["date"]) == ["2025-01-01", "2025-01-02"]  # 排序
    assert list(out["close"]) == [10, 11]


def test_qfq_single_dividend_only_pre_event_adjusted():
    raw = _raw(["2025-06-10", "2025-06-11", "2025-06-12", "2025-06-13"],
               [10, 10, 9, 9])
    xdxr = pd.DataFrame([{"date": "2025-06-12", "category": 1,
                           "fenhong": 1, "songzhuangu": 0,
                           "peigu": 0, "peigujia": 0}])
    out = adjust.qfq(raw, xdxr)
    # prev_close=10(06-11) ratio=(10-1)/10=0.9；仅 06-12 之前的行(06-10,06-11)×0.9
    assert list(out["close"]) == [9.0, 9.0, 9, 9]


def test_qfq_two_events_cumulation_descending_apply():
    raw = _raw(["2025-01-05", "2025-03-10", "2025-06-12", "2025-09-01"],
               [10, 11, 12, 13])
    xdxr = pd.DataFrame([
        {"date": "2025-06-12", "category": 1, "fenhong": 1,
         "songzhuangu": 0, "peigu": 0, "peigujia": 0},
        {"date": "2025-09-01", "category": 1, "fenhong": 1,
         "songzhuangu": 0, "peigu": 0, "peigujia": 0},
    ])
    out = adjust.qfq(raw, xdxr)
    # D1=06-12 prev=11 ratio=10/11；D2=09-01 prev=12 ratio=11/12
    # row0(01-05): 10×(11/12)×(10/11)=100/12；row1(03-10): 11×(11/12)×(10/11)=110/12
    # row2(06-12): 12×(11/12)=11；row3(09-01): 13
    assert math.isclose(out.loc[0, "close"], 100 / 12, rel_tol=1e-9)
    assert math.isclose(out.loc[1, "close"], 110 / 12, rel_tol=1e-9)
    assert math.isclose(out.loc[2, "close"], 11.0, rel_tol=1e-9)
    assert math.isclose(out.loc[3, "close"], 13.0, rel_tol=1e-9)


def test_qfq_songzhuangu_adjusts_price_and_volume():
    # 每股送1股(songzhuangu=1.0 即每10股送10股)：价格 ÷2，量 ×2(反向÷2 前)
    raw = _raw(["2025-06-10", "2025-06-11", "2025-06-12"],
               [10, 10, 5], vols=[100, 100, 200])
    xdxr = pd.DataFrame([{"date": "2025-06-12", "category": 1,
                           "fenhong": 0, "songzhuangu": 1.0,
                           "peigu": 0, "peigujia": 0}])
    out = adjust.qfq(raw, xdxr)
    # prev_close=10 denom=1+1=2 ratio=5/10=0.5；06-10,06-11 price×0.5, vol÷2
    assert list(out["close"]) == [5.0, 5.0, 5]
    assert list(out["volume"]) == [50.0, 50.0, 200]


def test_qfq_event_out_of_range_skipped():
    # 除权日早于数据首行(无前收)→跳过，不崩
    raw = _raw(["2025-06-11", "2025-06-12"], [10, 9])
    xdxr = pd.DataFrame([{"date": "2025-06-10", "category": 1,
                           "fenhong": 1, "songzhuangu": 0,
                           "peigu": 0, "peigujia": 0}])
    out = adjust.qfq(raw, xdxr)
    assert list(out["close"]) == [10, 9]  # 无可算前收，原样


def test_qfq_category5_ignored():
    # 股本变化(category=5)不调价
    raw = _raw(["2025-06-11", "2025-06-12"], [10, 10])
    xdxr = pd.DataFrame([{"date": "2025-06-12", "category": 5,
                           "fenhong": 0, "songzhuangu": 0,
                           "peigu": 0, "peigujia": 0}])
    out = adjust.qfq(raw, xdxr)
    assert list(out["close"]) == [10, 10]


def test_get_xdxr_monkeypatch(monkeypatch):
    # get_xdxr 走 pytdx_client.get_xdxr，mock 返原始"每10股"列→归一化为每股(÷10)
    from data import pytdx_client
    monkeypatch.setattr(pytdx_client, "_TDX_OK", True)
    monkeypatch.setattr(pytdx_client, "get_xdxr", lambda c: pd.DataFrame([
        {"year": 2025, "month": 6, "day": 12, "category": 1,
         "fenhong": 36.2, "songzhuangu": 10.0, "peigu": 0.0,
         "peigujia": 0.0, "suogu": None},
    ]))
    xdxr = adjust.get_xdxr("000001")
    assert not xdxr.empty
    assert xdxr.loc[0, "date"] == "2025-06-12"
    assert xdxr.loc[0, "category"] == 1
    assert abs(xdxr.loc[0, "fenhong"] - 3.62) < 1e-9  # 36.2/10 归一化
    assert abs(xdxr.loc[0, "songzhuangu"] - 1.0) < 1e-9  # 10/10

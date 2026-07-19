# -*- coding: utf-8 -*-
"""fetch_etf_spot 新浪备援测试：东财 fund_etf_spot_em 失败时走 fund_etf_category_sina。
宿主无 akshare 时 collector.ak=None，用 ModuleType mock。"""
import pandas as pd
from types import ModuleType
from data import collector


def _sina_df():
    return pd.DataFrame([
        {"代码": "sh510300", "名称": "沪深300ETF", "最新价": 4.589, "涨跌额": 0.01,
         "涨跌幅": 0.22, "买入": 4.588, "卖出": 4.589, "昨收": 4.579, "今开": 4.58,
         "最高": 4.59, "最低": 4.57, "成交量": 5149000, "成交额": 14648790580},
        {"代码": "sz159915", "名称": "创业板ETF", "最新价": 1.234, "涨跌额": -0.01,
         "涨跌幅": -0.8, "买入": 1.233, "卖出": 1.234, "昨收": 1.244, "今开": 1.24,
         "最高": 1.25, "最低": 1.23, "成交量": 1000000, "成交额": 123400000},
    ])


def _mock_ak(em_fn, sina_fn):
    m = ModuleType("akshare")
    m.fund_etf_spot_em = em_fn
    m.fund_etf_category_sina = sina_fn
    return m


def test_etf_spot_sina_fallback(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", True)
    monkeypatch.setattr(collector, "ak", _mock_ak(
        lambda: (_ for _ in ()).throw(RuntimeError("em blocked")), _sina_df))
    df, ok, err = collector.fetch_etf_spot()
    assert ok, f"应备援成功: {err}"
    assert "新浪" in err
    recs = df.to_dict("records")
    assert len(recs) == 2
    r0 = next(r for r in recs if r["code"] == "510300")
    assert r0["name"] == "沪深300ETF"
    assert r0["latest_price"] == 4.589
    assert r0["change_pct"] == 0.22
    assert r0["turnover_amount"] == 14648790580
    assert r0["turnover_rate"] is None  # 新浪无换手率


def test_etf_spot_em_ok_no_fallback(monkeypatch):
    """东财成功时不走备援。"""
    monkeypatch.setattr(collector, "_AK_OK", True)
    em_df = pd.DataFrame([{"代码": "510300", "名称": "沪深300ETF", "最新价": 4.589,
                           "涨跌幅": 0.22, "成交额": 1.4e10, "换手率": 1.5}])
    called = {"sina": False}

    def _sina_track():
        called["sina"] = True
        return _sina_df()
    monkeypatch.setattr(collector, "ak", _mock_ak(lambda: em_df, _sina_track))
    df, ok, err = collector.fetch_etf_spot()
    assert ok and err == ""  # 东财成功，无备援标记
    assert called["sina"] is False

# tests/test_st_list.py
# -*- coding: utf-8 -*-
"""fetch_st_list 测试:st_type 由 name 前缀解析;东财失败标不可用不崩。"""
from types import ModuleType

import pandas as pd

from data import collector


def _mock_ak(st_fn):
    m = ModuleType("akshare")
    m.stock_zh_a_st_em = st_fn
    return m


def _st_df():
    return pd.DataFrame([
        {"代码": "600250", "名称": "*ST兴源", "最新价": 2.1, "涨跌幅": -1.5},
        {"代码": "000004", "名称": "ST国华", "最新价": 3.3, "涨跌幅": 0.8},
        {"代码": "000005", "名称": "世纪星源", "最新价": 1.2, "涨跌幅": 0.1},
    ])


def test_st_list_st_type_from_name(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", True)
    monkeypatch.setattr(collector, "ak", _mock_ak(lambda: _st_df()))
    df, ok, err = collector.fetch_st_list()
    assert ok, err
    recs = df.to_dict("records")
    by_code = {r["code"]: r for r in recs}
    assert by_code["600250"]["st_type"] == "*ST"
    assert by_code["000004"]["st_type"] == "ST"
    assert by_code["000005"]["st_type"] == "其他"
    assert by_code["600250"]["latest_price"] == 2.1


def test_st_list_ak_fail(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", True)

    def _err():
        raise RuntimeError("em blocked")

    monkeypatch.setattr(collector, "ak", _mock_ak(_err))
    df, ok, err = collector.fetch_st_list()
    assert not ok and df.empty
    assert "st_list" in err


def test_st_list_ak_ok_false(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", False)
    df, ok, err = collector.fetch_st_list()
    assert not ok and df.empty

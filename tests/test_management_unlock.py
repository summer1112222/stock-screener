# tests/test_management_unlock.py
# -*- coding: utf-8 -*-
"""高管增减持/限售解禁采集测试。mock ak。"""
from types import ModuleType

import pandas as pd

from data import smart_money as sm


def _mock_ak(mgmt_fn, unlock_fn):
    m = ModuleType("akshare")
    m.stock_hold_management_em = mgmt_fn
    m.stock_share_change_em = unlock_fn
    return m


def _mgmt_df():
    return pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "变动人": "李保芳",
         "变动方向": "增持", "变动金额": 5000000.0},
        {"代码": "000001", "名称": "平安银行", "变动人": "谢永林",
         "变动方向": "减持", "变动金额": 2000000.0},
    ])


def _unlock_df():
    return pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "解禁股东": "集团A",
         "解禁数量": 1000000.0, "解禁日期": "2026-07-20"},
    ])


def test_collect_management_hold(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _mock_ak(lambda: _mgmt_df(),
                                           lambda symbol: pd.DataFrame()))
    recs, ok, err = sm.collect_management_hold("2026-07-19")
    assert ok, err
    assert len(recs) == 2
    r0 = next(r for r in recs if r["code"] == "600519")
    assert r0["channel"] == "高管增减持"
    assert r0["action"] == "增持"
    assert r0["actor"] == "李保芳"
    assert r0["amount"] == 5000000.0
    assert sm.CHANNEL_STATUS["高管增减持"]["ok"] is True


def test_collect_management_hold_empty_ok(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _mock_ak(lambda: pd.DataFrame(),
                                           lambda s: pd.DataFrame()))
    recs, ok, err = sm.collect_management_hold("2026-07-19")
    assert ok and recs == []


def test_collect_management_hold_fail(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)

    def _err():
        raise RuntimeError("remotedisconnected")

    monkeypatch.setattr(sm, "ak", _mock_ak(_err, lambda s: pd.DataFrame()))
    recs, ok, err = sm.collect_management_hold("2026-07-19")
    assert not ok and recs == []
    assert "被封" in err or "不可用" in err


def test_collect_share_unlock(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _mock_ak(lambda: pd.DataFrame(),
                                           lambda symbol: _unlock_df()))
    recs, ok, err = sm.collect_share_unlock("2026-07-19")
    assert ok, err
    assert len(recs) == 1
    r0 = recs[0]
    assert r0["channel"] == "限售解禁"
    assert r0["action"] == "解禁"
    assert r0["as_of"] == "2026-07-20"
    assert r0["amount"] == 1000000.0


def test_refresh_today_plan_has_new_channels():
    """refresh_today plan 含两新通道(检查 plan 构造,不实际跑)。"""
    import inspect
    src = inspect.getsource(sm.refresh_today)
    assert "collect_management_hold" in src
    assert "collect_share_unlock" in src

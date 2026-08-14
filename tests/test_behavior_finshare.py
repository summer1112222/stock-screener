# -*- coding: utf-8 -*-
"""behavior_series finshare 按需补测试。

合成数据 mock db.query_rows(表) 与 finshare.get_money_flow_stock(按需补)，
不触网。覆盖：表有资金流→用表不调finshare；表无资金流+finshare有→补(标source)；
表空+finshare有→补；表空+finshare不可用→提示空；finshare序列截窗+streak/cum 一致；
finshare 异常→优雅 None 不崩。
"""
import sys
import types
from datetime import datetime, timedelta

import pandas as pd
import pytest

from screener import smart_money as sm


def _d(ago: int) -> str:
    """ago 天前的 YYYY-MM-DD（合成近期日期，配合 behavior_series 的近 days 日截窗）。"""
    return (datetime.now() - timedelta(days=ago)).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """拦截 db.query_rows 默认返回空；各测内再覆盖。不动 finshare（各测自处理）。"""
    monkeypatch.setattr(sm.db, "query_rows", lambda *a, **k: [])


def _fake_finshare(monkeypatch, rows, exc=None):
    """注入假 finshare；exc 非 None 时 get_money_flow_stock 抛 exc。rows=[(date,main_net)]。"""
    fake = types.ModuleType("finshare")
    if exc is not None:
        def _boom(code=None):
            raise exc
        fake.get_money_flow_stock = _boom
    else:
        fake.get_money_flow_stock = lambda code=None: pd.DataFrame(
            rows, columns=["trade_time", "main_net"])
    monkeypatch.setitem(sys.modules, "finshare", fake)


def test_table_has_fundflow_uses_table(monkeypatch):
    """表有资金流记录 → 用表，不调 finshare（无 source 标，值为表值）。"""
    monkeypatch.setattr(sm.db, "query_rows", lambda *a, **k: [
        {"code": "600519", "date": _d(1), "channel": "资金流", "amount": 1.0e8},
        {"code": "600519", "date": _d(2), "channel": "资金流", "amount": -0.5e8},
    ])
    _fake_finshare(monkeypatch, [(_d(1), 9.9e8)])  # 故意不同，不应被用
    r = sm.behavior_series("600519", days=30)
    assert len(r["daily"]) == 2
    assert {d["amount"] for d in r["daily"]} == {1.0e8, -0.5e8}
    assert "source" not in r["channels"]["资金流"]   # 表路径无 finshare 标


def test_table_no_fundflow_finshare_fills(monkeypatch):
    """表有北向无资金流 → finshare 补资金流，source=finshare，顶层取 finshare 序列。"""
    monkeypatch.setattr(sm.db, "query_rows", lambda *a, **k: [
        {"code": "600519", "date": _d(1), "channel": "北向", "amount": 3.0e8},
    ])
    _fake_finshare(monkeypatch, [(_d(1), 1.2e8), (_d(2), -0.4e8)])
    r = sm.behavior_series("600519", days=30)
    ff = r["channels"]["资金流"]
    assert ff["source"] == "finshare"
    assert len(ff["daily"]) == 2
    assert r["daily"] == ff["daily"]              # 顶层取资金流口径
    assert "北向" in r["channels"]                # 北向仍来自表


def test_table_empty_finshare_fills(monkeypatch):
    """表全空 + finshare 有 → 补资金流，note 空，daily 非空。"""
    _fake_finshare(monkeypatch, [(_d(1), 1.0e8), (_d(2), 2.0e8), (_d(3), -1.0e8)])
    r = sm.behavior_series("600519", days=30)
    assert r["channels"]["资金流"]["source"] == "finshare"
    assert len(r["daily"]) == 3
    assert r["note"] == ""                        # 有数据不提示


def test_table_empty_finshare_unavailable_prompt(monkeypatch):
    """表空 + finshare 不可用 → 提示，channels 空，daily=[]。"""
    monkeypatch.setitem(sys.modules, "finshare", None)  # import 失 → 备援 None
    r = sm.behavior_series("600519", days=30)
    assert r["channels"] == {}
    assert r["daily"] == []
    assert "无主力动向" in r["note"]


def test_finshare_window_truncation(monkeypatch):
    """finshare 序列截近 days 日：窗口外日期被截。"""
    rows = [(_d(ago), float(ago) * 1e8) for ago in range(1, 41)]  # 40 天
    _fake_finshare(monkeypatch, rows)
    r = sm.behavior_series("600519", days=15)
    ff = r["channels"]["资金流"]
    assert ff["n_days"] <= 15
    cutoff = _d(15)
    assert all(d["date"] >= cutoff for d in ff["daily"])


def test_finshare_streak_cum_consistent(monkeypatch):
    """finshare 补的 streak/cum 与 _streak 直算一致。"""
    amts = [1e8, 1e8, -1e8, 2e8, 2e8]  # 末尾连续正 2
    rows = [(_d(i + 1), a) for i, a in enumerate(amts)]
    _fake_finshare(monkeypatch, rows)
    r = sm.behavior_series("600519", days=30)
    ff = r["channels"]["资金流"]
    si, so = sm._streak(amts)
    assert ff["streak_inflow"] == si
    assert ff["streak_outflow"] == so
    assert ff["cum_inflow"] == round(sum(amts), 2)


def test_finshare_exception_no_crash(monkeypatch):
    """finshare get_money_flow_stock 抛异常 → 优雅 None，不崩，提示。"""
    _fake_finshare(monkeypatch, [], exc=RuntimeError("boom"))
    r = sm.behavior_series("600519", days=30)
    assert r["channels"] == {}
    assert "无主力动向" in r["note"]

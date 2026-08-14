# -*- coding: utf-8 -*-
"""龙虎榜 finshare 备援测试。

合成数据 mock ak.stock_lhb_detail_em(东财主源) 与 finshare.get_lhb(备援)，
不触网。覆盖：东财命中→用东财；东财异常→finshare 备援(fs_code 归一/name 空)；
东财空+finshare 无当日→成功空；东财异常+finshare 不可用→False；fs_code 归一。
合规相关：备援记录 channel=龙虎榜、source=finshare 诚实标注。
"""
import sys
import types

import pandas as pd
import pytest

from data import smart_money as sm

DATE = "2026-08-07"
DASH = "20260807"


@pytest.fixture(autouse=True)
def _reset_status():
    """每测前重置龙虎榜通道状态，防串扰。"""
    sm.CHANNEL_STATUS["龙虎榜"] = {"ok": False, "source": "", "err": "未采集", "at": ""}
    yield
    sm.CHANNEL_STATUS["龙虎榜"] = {"ok": False, "source": "", "err": "未采集", "at": ""}


def _fake_finshare(monkeypatch, rows):
    """注入假 finshare 模块，get_lhb 返回合成 DataFrame。"""
    fake = types.ModuleType("finshare")
    fake.get_lhb = lambda: pd.DataFrame(rows)
    monkeypatch.setitem(sys.modules, "finshare", fake)


def _em_ns(df):
    """构造假 ak 模块，stock_lhb_detail_em 返回 df。"""
    return types.SimpleNamespace(stock_lhb_detail_em=lambda **kw: df)


def _em_boom(err=ConnectionError("RemoteDisconnected")):
    """东财主源抛异常（被封）。"""
    def _f(**kw):
        raise err
    return types.SimpleNamespace(stock_lhb_detail_em=_f)


def test_em_hit_uses_em(monkeypatch):
    """东财主源有数据 → 用东财记录（name 来自东财），不走 finshare。"""
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _em_ns(pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台",
         "龙虎榜净买额": 1.2e8, "上榜原因": "日涨幅偏离值"},
    ])))
    # finshare 故意塞了别的票；东财命中即 return，不应被用
    _fake_finshare(monkeypatch, [dict(fs_code="000004.SZ", trade_date=DASH,
                                      net_buy_amount=5e7, reason="龙虎榜")])
    recs, ok, err = sm.collect_dragon_tiger(DATE)
    assert ok and len(recs) == 1
    assert recs[0]["code"] == "600519"
    assert recs[0]["name"] == "贵州茅台"          # 东财 name
    assert recs[0]["amount"] == 1.2e8
    assert sm.CHANNEL_STATUS["龙虎榜"]["source"] == "东财"


def test_em_fail_finshare_fallback(monkeypatch):
    """东财异常(被封) → finshare 备援：fs_code 归一、name 留空、净额取值。"""
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _em_boom())
    _fake_finshare(monkeypatch, [
        dict(fs_code="000004.SZ", trade_date=DASH, close_price=0.51,
             change_rate=10.0, net_buy_amount=5e7, buy_amount=8e7,
             sell_amount=3e7, turnover_rate=2.76, reason="日振幅值"),
        dict(fs_code="SH600519", trade_date=DASH, net_buy_amount=1.2e8,
             reason="龙虎榜"),
    ])
    recs, ok, err = sm.collect_dragon_tiger(DATE)
    assert ok, f"应备援成功: {err}"
    assert len(recs) == 2
    assert {r["code"] for r in recs} == {"000004", "600519"}   # fs_code 归一
    assert all(r["channel"] == "龙虎榜" for r in recs)
    assert all(r["name"] is None for r in recs)                 # finshare 无 name
    assert recs[0]["amount"] == 5e7
    assert sm.CHANNEL_STATUS["龙虎榜"]["source"] == "finshare"  # 诚实标备援源


def test_em_empty_finshare_other_day_ok(monkeypatch):
    """东财空 + finshare 仅有前一日 → 当日真无上榜，成功空。"""
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _em_ns(pd.DataFrame()))       # 东财当日空
    _fake_finshare(monkeypatch, [dict(fs_code="000004.SZ",
                                      trade_date="20260806",   # 前一日
                                      net_buy_amount=5e7, reason="x")])
    recs, ok, err = sm.collect_dragon_tiger(DATE)
    assert ok and recs == []                                    # 不算错


def test_em_empty_finshare_hit_uses_finshare(monkeypatch):
    """东财空(未更新) + finshare 当日有 → 用 finshare 补。"""
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _em_ns(pd.DataFrame()))
    _fake_finshare(monkeypatch, [dict(fs_code="sz000001", trade_date=DASH,
                                      net_buy_amount=3.3e8, reason="龙虎榜")])
    recs, ok, err = sm.collect_dragon_tiger(DATE)
    assert ok and len(recs) == 1
    assert recs[0]["code"] == "000001"                           # sz 前缀归一
    assert sm.CHANNEL_STATUS["龙虎榜"]["source"] == "finshare"


def test_both_fail_false(monkeypatch):
    """东财异常 + finshare 不可用 → False，err 诚实。"""
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _em_boom())
    # finshare 注入 None → import 失 → 备援优雅失败
    monkeypatch.setitem(sys.modules, "finshare", None)
    recs, ok, err = sm.collect_dragon_tiger(DATE)
    assert not ok and recs == []
    assert err and "龙虎榜" in err     # 主源+备援均败，err 非空诚实标注


def test_ak_off_finshare_fallback(monkeypatch):
    """akshare 不可用(_AK_OK=False) → 跳过东财直接 finshare 备援。"""
    monkeypatch.setattr(sm, "_AK_OK", False)
    monkeypatch.setattr(sm, "ak", None)
    _fake_finshare(monkeypatch, [dict(fs_code="600519.SH", trade_date=DASH,
                                      net_buy_amount=1.2e8, reason="龙虎榜")])
    recs, ok, err = sm.collect_dragon_tiger(DATE)
    assert ok and len(recs) == 1 and recs[0]["code"] == "600519"
    assert sm.CHANNEL_STATUS["龙虎榜"]["source"] == "finshare"


@pytest.mark.parametrize("inp,exp", [
    ("000004.SZ", "000004"),
    ("SH600519", "600519"),
    ("sz000004", "000004"),
    ("002966", "002966"),
    ("", ""),
    (None, ""),
])
def test_fs_code_norm(inp, exp):
    assert sm._fs_code_norm(inp) == exp

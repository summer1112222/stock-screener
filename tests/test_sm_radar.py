# -*- coding: utf-8 -*-
"""主力多通道雷达测试。mock data.db.query_rows，不触网。
仓库根目录跑：python -m pytest tests/test_sm_radar.py -q"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from datetime import datetime

from screener import smart_money as sm_q

import pytest


@pytest.fixture(autouse=True)
def _clear_radar_cache():
    sm_q._RADAR_CACHE.clear()
    yield
    sm_q._RADAR_CACHE.clear()


def _mk_rows():
    return [
        # 000001 三路资金共振
        {"code": "000001", "name": "平A", "channel": "资金流", "date": "2026-09-05", "amount": 5e6},
        {"code": "000001", "channel": "龙虎榜", "date": "2026-09-05", "amount": 2e6},
        {"code": "000001", "channel": "北向", "date": "2026-09-04", "amount": 3e6},
        # 600519 两路：资金流正 + 高管增持(股数，不进金额)
        {"code": "600519", "name": "贵C", "channel": "资金流", "date": "2026-09-05", "amount": 1e7},
        {"code": "600519", "channel": "高管增减持", "date": "2026-09-05", "action": "增持", "amount": 1000},
        # 000002 单路资金流 + 未来解禁(封顶今天，不计 hits)
        {"code": "000002", "name": "万B", "channel": "资金流", "date": "2026-09-05", "amount": -2e6},
        {"code": "000002", "channel": "限售解禁", "date": "2026-10-01", "as_of": "2026-10-01", "amount": 9e8},
    ]


def _mk_spot():
    return [
        {"code": "000001", "turnover_amount": 1e9},
        {"code": "600519", "turnover_amount": 2e9},
        {"code": "000002", "turnover_amount": 5e8},
    ]


def _mock_db(table, **k):
    if table == "smart_money_action":
        return _mk_rows()
    if table == "stock_spot":
        return _mk_spot()
    return []


def test_radar_counts_channel_hits_and_excludes_unlock():
    """000001 三路资金正向共振=3 hits；000002 资金流出+未来解禁=0 hits 且 unlock_flag。"""
    with patch("data.db.query_rows", side_effect=_mock_db):
        res = sm_q.radar(days=5, limit=50)
    rows = {r["code"]: r for r in res["rows"]}
    assert rows["000001"]["channel_hits"] == 3
    assert rows["600519"]["channel_hits"] == 2  # 资金流 + 高管增持
    assert rows["000002"]["channel_hits"] == 0
    assert rows["000002"]["unlock_flag"] is True
    assert rows["000002"]["unlock_as_of"] == "2026-10-01"


def test_radar_high_mgmt_does_not_mix_amount():
    """高管增持的 amount(股) 不应进 cum_net/intensity。600519 cum_net 只算资金流 1e7。"""
    with patch("data.db.query_rows", side_effect=_mock_db):
        res = sm_q.radar(days=5, limit=50)
    r = next(x for x in res["rows"] if x["code"] == "600519")
    assert r["cum_net"] == 1e7


def test_radar_date_capped_at_today():
    """未来解禁日期不得劫持窗口 end=latest 实盘日(2026-09-05)。"""
    with patch("data.db.query_rows", side_effect=_mock_db):
        res = sm_q.radar(days=5)
    assert res["date"] == "2026-09-05"
    assert res["date"] <= datetime.now().strftime("%Y-%m-%d")


def test_radar_sorts_by_resonance():
    """排序主键 (resonance DESC, None last)。000001 三通道共振最高排第一。"""
    with patch("data.db.query_rows", side_effect=_mock_db):
        res = sm_q.radar(days=5, limit=50)
    rows = res["rows"]
    assert rows[0]["code"] == "000001"
    resonances = [r["resonance"] for r in rows if r["resonance"] is not None]
    assert resonances == sorted(resonances, reverse=True)
    r600 = next(r for r in rows if r["code"] == "600519")
    r002 = next(r for r in rows if r["code"] == "000002")
    # 三通道共振 > 双通道(资金流+高管) > 单通道流出
    assert rows[0]["resonance"] > r600["resonance"] > r002["resonance"]
    # 保留 channel_hits 诊断字段：000001 三路资金正向
    assert rows[0]["channel_hits"] == 3
    assert rows[0]["channel_hits"] > r600["channel_hits"]


def test_radar_empty_returns_struct():
    """空表/无记录 → 空 rows + date None，不崩。"""
    with patch("data.db.query_rows", return_value=[]):
        res = sm_q.radar(days=5)
    assert res["rows"] == [] and res["total"] == 0 and res["date"] is None


def _mk_rows_lowliq():
    return [
        {"code": "300001", "name": "低流", "channel": "资金流", "date": "2026-09-05", "amount": 1e7},
    ]


def _mk_spot_lowliq():
    return [{"code": "300001", "turnover_amount": 1e6}]  # < min_turnover 默认 5e7


def _mock_db_lowliq(table, **k):
    if table == "smart_money_action":
        return _mk_rows_lowliq()
    if table == "stock_spot":
        return _mk_spot_lowliq()
    return []


def test_radar_min_turnover_flags_low_liq():
    """成交额 < min_turnover(默认5e7) 的 code：resonance=None + low_liq=True，不参与共振排序。"""
    with patch("data.db.query_rows", side_effect=_mock_db_lowliq):
        res = sm_q.radar(days=5, limit=50)
    assert len(res["rows"]) == 1
    r = res["rows"][0]
    assert r["low_liq"] is True
    assert r["resonance"] is None


def test_radar_cache_hits_second_call():
    """同参数二次调用命中 30s 缓存，不重查 db(action+spot 共 2 表)。"""
    calls = {"n": 0}

    def counting_db(table, **k):
        calls["n"] += 1
        return _mock_db(table, **k)

    with patch("data.db.query_rows", side_effect=counting_db):
        sm_q.radar(days=5, limit=50)
        n1 = calls["n"]
        sm_q.radar(days=5, limit=50)
        n2 = calls["n"]
    assert n1 == 3  # 最新日期 + action 窗口 + stock_spot 共 3 查
    assert n2 == n1  # 二次命中缓存，不重查 db


# ---- radar_resonance_for 触发原语（spec 2026-09-16 Task 1）----

import screener.smart_money as sm


def _radar_row(code, turnover, channels, low_liq=False, asof="2026-09-15"):
    """构造 radar() 单行返回结构（复用 radar 内部 _turnover/channels/net 字段）。"""
    return {"code": code, "name": code, "_turnover": turnover,
            "channels": channels, "low_liq": low_liq, "data_asof": asof,
            "channel_hits": 0, "resonance": None, "net_intensity": None,
            "cum_net": 0.0, "unlock_flag": False}


def _ch(net):
    """构造单通道 dict，net 为累计净额。"""
    return {"net": net, "daily": {}, "latest_date": None, "positive": net > 0}


def test_radar_resonance_for_in_count_strength_floor(monkeypatch):
    # 资金流 net=200万, turnover=1亿 → intensity=0.002 > 0.001 → 计入 in
    # 龙虎榜 net=5万, turnover=1亿 → intensity=0.0005 < 0.001 → 不计
    rows = [_radar_row("600519", 1e8, {
        "资金流": _ch(2e6), "龙虎榜": _ch(5e4), "北向": _ch(-3e6)})]
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": rows})
    out = sm.radar_resonance_for(["600519"], days=5)
    assert out["600519"]["in_count"] == 1   # 仅资金流过门槛
    assert out["600519"]["out_count"] == 1  # 北向 -3e6/1e8=-0.03 < -0.001
    assert out["600519"]["has_data"] is True
    assert out["600519"]["low_liq"] is False


def test_radar_resonance_for_low_liq_not_counted(monkeypatch):
    # low_liq=True → 通道一律不计入（哪怕 net/turnover 数值大）
    rows = [_radar_row("000001", 1e4, {"资金流": _ch(1e6)}, low_liq=True)]
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": rows})
    out = sm.radar_resonance_for(["000001"], days=5)
    assert out["000001"]["in_count"] == 0
    assert out["000001"]["out_count"] == 0
    assert out["000001"]["has_data"] is True
    assert out["000001"]["low_liq"] is True


def test_radar_resonance_for_no_data(monkeypatch):
    # 候选 code 不在 radar 返回中 → has_data=False
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": []})
    out = sm.radar_resonance_for(["300999"], days=5)
    assert out["300999"]["has_data"] is False
    assert out["300999"]["in_count"] == 0
    assert out["300999"]["out_count"] == 0
    assert out["300999"]["asof"] is None


def test_radar_resonance_for_mgmt_confirm_separate(monkeypatch):
    # 高管增持正向 → mgmt_confirm=True，但不并入 in_count
    rows = [_radar_row("600519", 1e8, {
        "资金流": _ch(2e6),
        "高管增减持": {"net": None, "daily": {}, "latest_date": None, "positive": True}})]
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": rows})
    out = sm.radar_resonance_for(["600519"], days=5)
    assert out["600519"]["in_count"] == 1   # 仅资金流
    assert out["600519"]["mgmt_confirm"] is True

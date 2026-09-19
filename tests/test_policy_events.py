# -*- coding: utf-8 -*-
"""policy_events 事件型催化标注层测试。纯 mock 不触网。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener import policy_events as pe


def test_event_hit_boards():
    """白色家电 命中房地产信贷事件(地产链后周期) → 返回含 id/date/title 的事件。"""
    evs = pe.event_hit("白色家电")
    assert evs, "白色家电应命中地产链事件"
    for e in evs:
        assert e["id"] and e["date"] and e["title"]
    assert any(e["id"].startswith("property_") for e in evs)


def test_event_hit_no_match_empty():
    assert pe.event_hit("猪肉") == []   # industry_board 存在但不在任何事件
    assert pe.event_hit("") == []


def test_event_hit_as_of_before_date():
    """事件 date=2026-08-28；as_of 早于它 → 不命中。"""
    assert pe.event_hit("银行", as_of="2026-01-01") == []


def test_event_hit_as_of_after_date():
    evs = pe.event_hit("银行", as_of="2026-09-10")
    assert any(e["id"] == "pbc_notice_22_2026-08" for e in evs)


def test_attach_policy_events_dedup():
    """多板块命中同事件 → policy_event 去重；无命中 → []。"""
    rows = [{"code": "a"}, {"code": "z"}]
    mm = {"白色家电": {"a"}, "家用电器": {"a"}}   # 两板块可命中重叠事件
    pe.attach_policy_events(rows, mm)
    evs = rows[0]["policy_event"]
    ids = [e["id"] for e in evs]
    assert len(ids) == len(set(ids))             # 去重
    assert rows[1]["policy_event"] == []          # z 无板块 → 空


def test_events_boards_aligned_to_industry():
    """事件表结构可读性：每事件有 date/title/非空板块列表。"""
    for eid, ev in pe.POLICY_EVENTS.items():
        assert ev.get("boards"), eid
        assert all(isinstance(b, str) and b for b in ev["boards"])
        assert ev.get("date") and ev.get("title")

# -*- coding: utf-8 -*-
"""policy_events 事件型催化标注层测试。纯 mock 不触网。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
import pytest
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


def test_event_hit_new_events_2026_09():
    """2026-09-20 新增事件：城市更新(水泥) / 养老大健康(医疗器械) / 智能家居(小家电) / 房车(乘用车)。"""
    assert any(e["id"].startswith("urban_renewal_") for e in pe.event_hit("水泥"))
    assert any(e["id"].startswith("aging_healthcare_") for e in pe.event_hit("医疗器械"))
    assert any(e["id"].startswith("smart_home_promo_") for e in pe.event_hit("小家电"))
    assert any(e["id"].startswith("rv_consumption_") for e in pe.event_hit("乘用车"))


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


# ===== B3 催化权重（2026-09-20）：宏观风格 × 事件时效 =====

def test_event_weight_timeliness_decay():
    """时效衰减：越新越强；date 当日(age0)→1.0，7 天→~0.37(半衰期)，越久越低。"""
    assert pe.event_weight("2026-09-01", ["汽车"], as_of="2026-09-01") == pytest.approx(1.0)
    assert pe.event_weight("2026-09-01", ["汽车"], as_of="2026-09-08") == pytest.approx(math.exp(-1), abs=1e-3)
    assert pe.event_weight("2026-09-01", ["汽车"], as_of="2026-09-15") == pytest.approx(math.exp(-2), abs=1e-3)


def test_event_weight_style_fit():
    """风格调制：attack 放大 offensive 板块事件、defense 放大 defensive；不对口压制；无 style=1.0。"""
    assert pe.event_weight("2026-09-01", ["汽车"], style={"style": "attack"}) == pytest.approx(1.5)   # 汽车→offensive
    assert pe.event_weight("2026-09-01", ["汽车"], style={"style": "defense"}) == pytest.approx(0.8) # 风格不对口
    assert pe.event_weight("2026-09-01", ["食品饮料"], style={"style": "defense"}) == pytest.approx(1.5)  # 食品饮料→defensive
    assert pe.event_weight("2026-09-01", ["汽车"], style=None) == pytest.approx(1.0)
    assert pe.event_weight("2026-09-01", ["汽车"], style={"style": "neutral"}) == pytest.approx(1.0)


def test_event_weight_combined_style_and_time():
    """风格×时效复合：7 天后 offensive 事件 in attack → 1.5×exp(-1)≈0.5519。"""
    w = pe.event_weight("2026-09-01", ["汽车"], as_of="2026-09-08", style={"style": "attack"})
    assert w == pytest.approx(1.5 * math.exp(-1), abs=1e-3)
    assert 0.0 <= w          # 只裁下限(放大可 >1,此处时效衰减后仍 <1)


def test_event_hit_returns_weight_and_age():
    """event_hit 带 as_of+style：命中消费事件(汽车)附 B3 权重与时效天数，只标注不进排序。"""
    evs = pe.event_hit("汽车", as_of="2026-09-10", style={"style": "attack"})
    assert evs, "汽车应命中促消费事件"
    e = evs[0]
    assert "weight" in e and "age_days" in e
    assert e["age_days"] == 9                       # 09-01 → 09-10
    assert e["weight"] == pytest.approx(math.exp(-9 / 7) * 1.5, abs=1e-3)


def test_attach_policy_events_threads_style():
    """attach 透传 as_of+style：policy_event 带 B3 权重；无 style → weight=1.0(无时效)。"""
    rows = [{"code": "a"}, {"code": "z"}]
    mm = {"汽车": {"a"}, "家用电器": {"a"}}          # 促消费事件含两者,去重后仍 1 条
    pe.attach_policy_events(rows, mm, as_of="2026-09-10", style={"style": "attack"})
    evs = rows[0]["policy_event"]
    assert evs and all("weight" in e and "age_days" in e for e in evs)
    # 无 style/as_of(旧调用) → 事件仍返回但 weight=1.0,不破坏旧测试
    pe.attach_policy_events(rows, mm)
    assert all(e.get("weight") == 1.0 and e.get("age_days") is None for e in rows[0]["policy_event"])

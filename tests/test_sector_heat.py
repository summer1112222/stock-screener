# -*- coding: utf-8 -*-
"""sector_heat 行业景气加成层测试。纯 mock 三源(板块资金流/行业板手脚)不触网。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from screener import sector_heat as sh


def test_policy_hit():
    b = sh.board_heat  # 触发模块 import
    assert sh.policy_hit("先进制造") == 0.05
    assert sh.policy_hit("白酒") == 0.0
    assert sh.policy_hit("") == 0.0


def test_rank_pct_order():
    s = pd.Series({"a": 1.0, "b": 3.0, "c": 2.0})
    r = sh.rank_pct(s)
    assert r["b"] > r["c"] > r["a"]
    assert float(r["b"]) == 1.0 and float(r["a"]) == 0.0
    assert sh.rank_pct(pd.Series(dtype=float)).empty
    assert float(sh.rank_pct(pd.Series({"z": 5.0}))["z"]) == 0.5


def test_board_heat_weights():
    """0.6*资金分位 + 0.4*上涨宽度分位；width 用 up/(up+down)。"""
    ff = [
        {"name": "先进制造", "main_net_inflow": 3e8},
        {"name": "白酒", "main_net_inflow": -1e8},
        {"name": "医药", "main_net_inflow": 1e8},
    ]
    br = [
        {"name": "先进制造", "up_count": 60, "down_count": 40, "constituent_count": 100},
        {"name": "白酒", "up_count": 5, "down_count": 95, "constituent_count": 100},
        {"name": "医药", "up_count": 30, "down_count": 70, "constituent_count": 100},
    ]
    h = sh.board_heat(ff, br)
    # 先进制造 资金最高(1.0)+宽度0.6=(1.0) → heat 最高；白酒最低
    assert h["先进制造"] == max(h.values())
    assert h["白酒"] == min(h.values())
    for v in h.values():
        assert 0.0 <= v <= 1.0


def test_pick_board_highest_heat():
    mm = {"先进制造": {"a", "b"}, "医药": {"a"}}
    heat = {"先进制造": 0.8, "医药": 0.4}
    assert sh.pick_board("a", mm, heat) == "先进制造"  # 多命中取 heat 最高
    assert sh.pick_board("c", mm, heat) is None


def test_attach_sector_heat():
    """attach 原地加 sector_heat/policy_hit；未命中板块→sector_heat=None。"""
    rows = [{"code": "a"}, {"code": "c"}]
    ff = [{"name": "先进制造", "main_net_inflow": 3e8}]
    br = [{"name": "先进制造", "up_count": 60, "down_count": 40}]
    mm = {"先进制造": {"a"}}
    sh.attach_sector_heat(rows, ff, br, mm)
    r0 = rows[0]
    assert r0["policy_hit"] == 0.05            # 先进制造 命中政策
    assert r0["sector_heat"] is not None and 0.0 <= r0["sector_heat"] <= 1.0
    assert rows[1]["policy_hit"] == 0.0
    assert rows[1]["sector_heat"] is None      # 无板块 → None


def test_quality_refine_applies_sector_heat():
    """精排加成：res 打平时 heat/policy 破平(高景气+政策方列首)，item 附字段。"""
    from backtest import quality
    pool = [
        {"code": "a", "resonance": 0.6},  # 先进制造(heat最高)+政策 → 应列首
        {"code": "b", "resonance": 0.6},  # 白酒(heat最低)
        {"code": "c", "resonance": 0.0},  # 医药(heat中)
    ]
    ff = [{"name": "先进制造", "main_net_inflow": 3e8},
          {"name": "白酒", "main_net_inflow": -1e8},
          {"name": "医药", "main_net_inflow": 1e8}]
    br = [{"name": "先进制造", "up_count": 60, "down_count": 40},
          {"name": "白酒", "up_count": 5, "down_count": 95},
          {"name": "医药", "up_count": 30, "down_count": 70}]
    mm = {"先进制造": {"a"}, "白酒": {"b"}, "医药": {"c"}}
    out = quality._apply_sector_heat(pool, ff, br, mm, in_session=True)
    assert out[0]["code"] == "a", f"景气+政策应列首: {[x['code'] for x in out]}"
    merged = {x["code"]: x for x in out}
    assert merged["a"]["sector_heat"] == 1.0
    assert merged["a"]["policy_hit"] == 0.05
    assert merged["b"]["policy_hit"] == 0.0
    # 盘后：A 流动性失效，仅 res+heat+policy，仍保持 a 首
    out2 = quality._apply_sector_heat(pool, ff, br, mm, in_session=False)
    assert out2[0]["code"] == "a"


def test_top_by_amount_annotates_sector(monkeypatch):
    """主力池经 _sector_ctx 标注管线：高景气+政策行附 sector_heat/policy_hit。"""
    from screener import smart_money as sm
    import data.db as db

    rows = [{"code": "a", "name": "甲", "market": "sh", "amount": 1e7, "count": 1},
            {"code": "z", "name": "乙", "market": "sz", "amount": 2e7, "count": 1}]
    ff = [{"name": "先进制造", "main_net_inflow": 3e8}]
    br = [{"name": "先进制造", "up_count": 60, "down_count": 40}]
    mm = {"先进制造": {"a"}}

    monkeypatch.setattr(db, "query_rows", lambda *a, **k: rows)
    monkeypatch.setattr(sm, "_attach_intensity", lambda p: p)
    monkeypatch.setattr(sm, "_sector_ctx",
                        lambda p: sh.attach_sector_heat(p, ff, br, mm))

    out = sm.top_by_amount(days=5)
    merged = {x["code"]: x for x in out["rows"]}
    assert "sector_heat" in merged["a"] and merged["a"]["policy_hit"] == 0.05
    assert merged["z"]["sector_heat"] is None      # 无板块 → 诚实缺失
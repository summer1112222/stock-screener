# -*- coding: utf-8 -*-
"""排序因子诊断单测：合成 list_track 行，不触网。
合规：只算历史收益统计事实，不预测不荐股。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
import pytest
from scripts import diagnose_ranking as dr

def _row(rank, score, ret_k1, ret_k3, factor=None, quality_pct=None, sector_heat=None):
    meta = {"factor_scores": factor or {}}
    if quality_pct is not None: meta["quality_pct"] = quality_pct
    if sector_heat is not None: meta["sector_heat"] = sector_heat
    return {"code": f"6{rank:05d}", "date": "2026-09-18", "rank": rank, "score": score,
            "ret_k1": ret_k1, "ret_k3": ret_k3, "meta_json": json.dumps(meta)}

ROWS = [
    _row(1, 0.9, 0.05, 0.12, {"mom_5_1": 0.9, "rel_strength": 0.8}, quality_pct=0.8, sector_heat=0.7),
    _row(2, 0.7, 0.03, 0.08, {"mom_5_1": 0.7, "rel_strength": 0.6}, quality_pct=0.6, sector_heat=0.5),
    _row(3, 0.5, 0.01, 0.04, {"mom_5_1": 0.5, "rel_strength": 0.4}, quality_pct=0.4, sector_heat=0.3),
    _row(4, 0.3, -0.01, -0.02, {"mom_5_1": 0.3, "rel_strength": 0.2}, quality_pct=0.2, sector_heat=0.1),
    _row(5, 0.1, -0.03, -0.08, {"mom_5_1": 0.1, "rel_strength": 0.0}, quality_pct=0.0, sector_heat=0.0),
]


def test_head_vs_all():
    """头部 top2 的 ret_k1/k3 中位数应高于整体(合成数据头部更强)。"""
    out = dr.head_vs_all(ROWS, heads=(2,))
    assert out["top2"]["ret_k1"]["median"] > out["all"]["ret_k1"]["median"]


def test_factor_deciles_monotonic():
    """mom_5_1 高分档 ret_k3 中位数应单调递增(合成数据)。"""
    out = dr.factor_deciles(ROWS, ("mom_5_1",))
    d = out["mom_5_1"]["deciles"]
    vals = [v[1]["median"] for v in sorted(d.items(), key=lambda kv: kv[0])]
    assert vals == sorted(vals) and vals[0] < vals[-1]


def test_penetration_layers():
    """quality_pct/sector_heat 分档: 高分组 ret_k3 中位数高于低分组。"""
    out = dr.penetration_layers(ROWS, ("quality_pct", "sector_heat"))
    for k in ("quality_pct", "sector_heat"):
        hi = out[k]["high"]["median"]
        lo = out[k]["low"]["median"]
        assert hi > lo


def test_quality_structure_counts_daily():
    """按 date 统计每日记录数(通过率代理)。"""
    out = dr.quality_structure(ROWS)
    assert out["2026-09-18"]["n"] == 5
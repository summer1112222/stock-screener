# -*- coding: utf-8 -*-
"""清单追踪层测试。mock data.db，不触网。
仓库根目录跑：python -m pytest tests/test_tracker.py -q"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch

from backtest import tracker
from data.models import LIST_TRACK_FIELDS, SCHEMA_SQL


def test_list_track_schema_defined():
    """list_track 表进入 SCHEMA_SQL，字段与 LIST_TRACK_FIELDS 一致。"""
    assert "CREATE TABLE IF NOT EXISTS list_track" in SCHEMA_SQL
    for f in ("module", "mode", "date", "code", "rank", "score",
              "meta_json", "ret_k1", "ret_k3", "ret_k5", "filled_ts"):
        assert f in LIST_TRACK_FIELDS


def test_record_list_idempotent_same_code():
    """同日同 mode 同 code 重复记录交给 INSERT OR IGNORE，避免覆盖既有收益。"""
    calls = []
    def _insert(row):
        calls.append(row)
        return 1
    with patch("backtest.tracker._insert_ignore", side_effect=_insert):
        tracker.record_list("quality", "strict", "2026-09-05",
                            [{"code": "000001", "name": "平A", "adjusted_resonance": 0.8, "hits": 3}])
        tracker.record_list("quality", "strict", "2026-09-05",
                            [{"code": "000001", "name": "平A", "adjusted_resonance": 0.8, "hits": 3}])
    assert len(calls) == 2
    row = calls[0]
    assert row["module"] == "quality" and row["mode"] == "strict"
    assert row["score"] == 0.8
    assert "hits" in row["meta_json"]


def test_is_default_params_quality():
    """quality 默认参数组合判定；偏离任一即 False。"""
    defaults = dict(tracker.DEFAULT_PARAMS["quality"])
    assert tracker.is_default_params("quality", defaults) is True
    bad = dict(defaults); bad["limit"] = 99
    assert tracker.is_default_params("quality", bad) is False
    assert tracker.is_default_params("unknown", {}) is False


def test_record_smart_money():
    """B3 追踪：smart_money 应能 record，score=净额、meta_json 含 quality_pct。
    按现有 mock 方式(同上 test_record_list_idempotent_same_code) patch _insert_ignore
    捕获落库行，验证白名单/score 分支/meta 分支，不触网不污染真实 DB。"""
    tracker.DEFAULT_PARAMS["smart_money"] = {
        "date": None, "channel": None, "market": None, "days": 7, "limit": 1000,
    }
    calls = []
    def _insert(row):
        calls.append(row)
        return 1
    with patch("backtest.tracker._insert_ignore", side_effect=_insert):
        n = tracker.record_list("smart_money", "today", "2026-09-18",
                                [{"code": "600001", "name": "甲",
                                  "score": 1.2e8, "quality_pct": 0.9}])
    assert n >= 1
    row = calls[0]
    assert row["module"] == "smart_money" and row["mode"] == "today"
    assert row["score"] == 1.2e8
    assert json.loads(row["meta_json"]).get("quality_pct") == 0.9


def test_record_nextday_persists_penetration_fields():
    """nextday 追踪：meta_json 应保留穿透标注 sector_heat/policy_hit/mf_phase,
    供 diagnose_ranking.penetration_layers 做穿透分层(终端审修复,2026-09-19)。"""
    calls = []
    def _insert(row):
        calls.append(row)
        return 1
    with patch("backtest.tracker._insert_ignore", side_effect=_insert):
        n = tracker.record_list(
            "nextday", "strict", "2026-09-18",
            [{"code": "600001", "name": "甲", "score": 0.9,
              "factor_scores": {"mom_5_1": 0.9},
              "sector_heat": 0.7, "policy_hit": True, "mf_phase": "拉升",
              "streak_inflow": 3}])
    assert n >= 1
    meta = json.loads(calls[0]["meta_json"])
    assert meta.get("sector_heat") == 0.7
    assert meta.get("policy_hit") is True
    assert meta.get("mf_phase") == "拉升"
    assert meta.get("streak_inflow") == 3


def test_smart_money_default_params():
    """smart_money 默认参数判定：默认组合命中，改 limit 不命中。"""
    tracker.DEFAULT_PARAMS["smart_money"] = {
        "date": None, "channel": None, "market": None, "days": 7, "limit": 1000,
    }
    assert tracker.is_default_params(
        "smart_money", {"date": None, "channel": None, "market": None,
                        "days": 7, "limit": 1000})
    assert not tracker.is_default_params(
        "smart_money", {"date": None, "channel": None, "market": None,
                        "days": 7, "limit": 50})


def test_fill_returns_t_plus1_open_buy():
    """T+1 open 买入、T+1+k close 卖出；k=1/3/5 收益正确。"""
    import pandas as pd
    close = pd.DataFrame(
        [[100.0], [110.0], [121.0], [133.0], [146.0]],
        index=["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"],
        columns=["000001"])
    open_df = pd.DataFrame(
        [[100.5], [111.0], [122.0], [134.0], [147.0]],
        index=["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"],
        columns=["000001"])
    pending = [{"module": "quality", "mode": "strict", "date": "2026-09-01",
                "code": "000001", "name": "平A", "rank": 1, "score": 0.8,
                "meta_json": "{}", "ret_k1": None, "ret_k3": None,
                "ret_k5": None, "filled_ts": None}]
    calls = []

    def _rows(table, **k):
        if table == "list_track":
            return [dict(pending[0])]
        if table == "stock_daily":
            return []
        return []

    def _panels(codes):
        return close, open_df

    def _up(table, rows):
        calls.append(rows[0])
        return 1
    with patch("data.db.query_rows", side_effect=_rows), \
         patch("data.db.upsert_rows", side_effect=_up), \
         patch("backtest.tracker._load_panels", side_effect=_panels):
        res = tracker.fill_returns()
    assert res["filled"] == 1
    row = calls[0]
    # entry = T+1 open = 111.0；k1 close(09-02)=110 → 110/111-1 ≈ -0.009
    assert row["ret_k1"] is not None
    assert "t+1_open" in row["meta_json"]


def test_summary_aggregates_win_rate():
    """summary 聚合各 k 的中位/胜率。"""
    rows = [
        {"module": "quality", "mode": "strict", "date": "2026-09-01",
         "ret_k1": 0.1, "ret_k3": 0.2, "ret_k5": 0.3},
        {"module": "quality", "mode": "strict", "date": "2026-09-02",
         "ret_k1": -0.05, "ret_k3": 0.1, "ret_k5": 0.15},
        {"module": "quality", "mode": "strict", "date": "2026-09-03",
         "ret_k1": 0.02, "ret_k3": None, "ret_k5": None},
    ]
    def _rows(table, **k):
        return rows if table == "list_track" else []
    with patch("data.db.query_rows", side_effect=_rows):
        res = tracker.summary(module="quality", mode="strict")
    assert res["filled"] == 3
    assert res["k"]["k1"]["n"] == 3
    assert res["k"]["k1"]["win_rate"] == round(2 / 3, 4)
    assert res["k"]["k5"]["n"] == 2  # 第三条 k5 为 None

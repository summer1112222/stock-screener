# -*- coding: utf-8 -*-
"""主力多通道雷达测试。mock data.db.query_rows，不触网。
仓库根目录跑：python -m pytest tests/test_sm_radar.py -q"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from datetime import datetime

from screener import smart_money as sm_q


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


def test_radar_sorts_by_hits_then_intensity():
    """排序主键 (hits DESC, net_intensity DESC)。"""
    with patch("data.db.query_rows", side_effect=_mock_db):
        res = sm_q.radar(days=5, limit=50)
    hits = [r["channel_hits"] for r in res["rows"]]
    assert hits == sorted(hits, reverse=True)
    assert res["rows"][0]["code"] == "000001"


def test_radar_empty_returns_struct():
    """空表/无记录 → 空 rows + date None，不崩。"""
    with patch("data.db.query_rows", return_value=[]):
        res = sm_q.radar(days=5)
    assert res["rows"] == [] and res["total"] == 0 and res["date"] is None

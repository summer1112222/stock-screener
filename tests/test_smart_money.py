# -*- coding: utf-8 -*-
"""主力动向单测：合成数据 mock db.query_rows，不依赖网络/AKShare。
合规：只验归类/聚合逻辑，不涉买卖点/收益。运行: pytest tests/test_smart_money.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from data import db
from data.models import SCHEMA_SQL, TABLE_FIELDS, SMART_MONEY_FIELDS
from data import smart_money as sm
from screener import smart_money as smq


def test_table_in_schema():
    assert "smart_money_action" in SCHEMA_SQL


def test_table_fields_registered():
    assert "smart_money_action" in TABLE_FIELDS
    assert TABLE_FIELDS["smart_money_action"] is SMART_MONEY_FIELDS


def test_init_creates_table(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    assert db.query_rows("smart_money_action") == []


# ---------- collector 测试（注入 fake ak，不依赖真实 akshare） ----------
class _FakeAk:
    """最小 fake akshare：把传入的方法挂成属性。"""
    def __init__(self, **methods):
        self.__dict__.update(methods)


def _patch_ak(monkeypatch, **methods):
    monkeypatch.setattr(sm, "ak", _FakeAk(**methods))
    monkeypatch.setattr(sm, "_AK_OK", True)


def test_fund_flow_nan_to_none(monkeypatch):
    """资金流复用 stock_spot.main_net_inflow；None/NaN 行跳过不写废行，只入有净额的票。
    全 None 时 ok=false 带明确 err（防 ok 误报、防前端 11059 行 '—'）。"""
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: [
        {"code": "000001", "name": "平安银行", "main_net_inflow": 1.5e8},
        {"code": "000002", "name": "万科A", "main_net_inflow": float("nan")},
    ] if table == "stock_spot" else [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok
    assert len(recs) == 1                       # NaN 行跳过，不写 amount=null 废行
    assert recs[0]["channel"] == "资金流"
    assert recs[0]["market"] == "股票"
    assert recs[0]["actor"] == ""               # 无 actor 通道用空串，UNIQUE 去重生效
    assert recs[0]["action"] == "净买入"
    assert recs[0]["amount"] == 1.5e8


def test_fund_flow_all_none_marks_unavailable(monkeypatch):
    """spot 全无 main_net_inflow（字段缺失/东财被封）→ 0 行 + ok=false + 明确 err，不误报。"""
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: [
        {"code": "000001", "name": "甲", "main_net_inflow": None},
        {"code": "000002", "name": "乙", "main_net_inflow": None},
    ] if table == "stock_spot" else [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok is False
    assert recs == []
    assert "东财个股资金流被封" in err


def test_dragon_tiger_seat_level(monkeypatch):
    _patch_ak(monkeypatch,
              stock_lhb_detail_em=lambda **kw: pd.DataFrame({
                  "代码": ["000001"], "名称": ["平安银行"]}),
              stock_lhb_stock_detail_em=lambda symbol: pd.DataFrame({
                  "席位名称": ["机构专用", "华泰证券股份有限公司"],
                  "买入额": [1e7, 2e7],
                  "卖出额": [0, 5e6],
              }))
    recs, ok, err = sm.collect_dragon_tiger("2026-07-14")
    assert ok
    assert len(recs) == 2
    assert recs[0]["channel"] == "龙虎榜"
    assert recs[0]["action"] == "上榜"
    assert "机构专用" in {r["actor"] for r in recs}
    inst = [r for r in recs if r["actor"] == "机构专用"][0]
    assert inst["amount"] == 1e7            # 买入 - 卖出


def test_collect_no_spot_returns_empty_not_raise(monkeypatch):
    """无 stock_spot 时资金流通道返回空不崩（不再触东财个股资金流接口）。"""
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok is False
    assert recs == []
    assert "无 stock_spot" in err


def test_upsert_dedup_empty_actor_on_refresh(tmp_path, monkeypatch):
    """C1 回归：无 actor 通道用空串，重刷同日 INSERT OR REPLACE 命中 UNIQUE 去重，
    不静默重复（SQLite NULL≠NULL 是原 bug 根源）。资金流复用 stock_spot。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    _orig_qr = db.query_rows

    def _qr(table, **kw):
        if table == "stock_spot":
            return [{"code": "000001", "name": "平安银行", "main_net_inflow": 1.5e8},
                    {"code": "000002", "name": "万科A", "main_net_inflow": 2e7}]
        return _orig_qr(table, **kw)
    monkeypatch.setattr(db, "query_rows", _qr)
    recs, ok, _ = sm.collect_fund_flow("2026-07-14")
    assert ok and len(recs) == 2
    assert all(r["actor"] == "" for r in recs)        # 空串，非 None
    # upsert_rows 不经 mock（直接走真实 conn）
    n1 = db.upsert_rows("smart_money_action", recs)
    n2 = db.upsert_rows("smart_money_action", recs)   # 模拟重刷同日
    assert n2 == 2                                     # INSERT OR REPLACE 仍计 2 行
    rows = _orig_qr("smart_money_action")
    assert len(rows) == 2                              # 但库里只 2 行，无静默重复




def test_friendly_err_normalizes():
    """err 文案归一：NoneType/下标/RemoteDisconnected 不裸露 akshare traceback。"""
    e1 = sm._friendly_err("龙虎榜", RuntimeError("'NoneType' object is not subscriptable"))
    assert "接口不可用" in e1 and "NoneType" not in e1
    e2 = sm._friendly_err("北向", RuntimeError("('Connection aborted.', RemoteDisconnected('...'))"))
    assert "接口不可用" in e2 and "东财被封" in e2
    e3 = sm._friendly_err("资金流", ValueError("参数错误"))
    assert "资金流: 参数错误" in e3   # 非已知模式保留原文


# ---------- 查询层测试（fake 真正解析 where，验证 SQL 过滤构造） ----------
SM_ROWS = [
    {"date": "2026-07-14", "code": "000001", "name": "甲", "market": "股票",
     "channel": "龙虎榜", "actor": "机构专用", "action": "上榜", "amount": 1e7, "as_of": None},
    {"date": "2026-07-14", "code": "000002", "name": "乙", "market": "股票",
     "channel": "资金流", "actor": None, "action": "净买入", "amount": 2e7, "as_of": None},
    {"date": "2026-07-13", "code": "000001", "name": "甲", "market": "股票",
     "channel": "龙虎榜", "actor": "中央汇金", "action": "上榜", "amount": 5e6, "as_of": None},
    {"date": "2026-07-13", "code": "510300", "name": "沪深300ETF", "market": "ETF",
     "channel": "资金流", "actor": None, "action": "净买入", "amount": 3e7, "as_of": None},
]


def _filter_rows(rows, where, params):
    """解析 "col op ? AND ..." 真过滤，让测试实打实验证 where 构造。"""
    if not where:
        return list(rows)
    import re
    conds = re.findall(r'(\w+)\s*(=|>=|<=|>|<)\s*\?', where)
    out = []
    for r in rows:
        keep = True
        for i, (col, op) in enumerate(conds):
            val = params[i] if i < len(params) else None
            rv = r.get(col)
            if op == "=":
                if str(rv) != str(val): keep = False; break
            elif op == ">=":
                if rv is None or str(rv) < str(val): keep = False; break
            elif op == "<=":
                if rv is None or str(rv) > str(val): keep = False; break
            elif op == ">":
                if rv is None or str(rv) <= str(val): keep = False; break
            elif op == "<":
                if rv is None or str(rv) >= str(val): keep = False; break
        if keep:
            out.append(r)
    return out


def _fake_query(table, where="", params=(), order_by="", limit=0):
    return _filter_rows(SM_ROWS, where, params)


@pytest.fixture
def sm_db(monkeypatch):
    monkeypatch.setattr(db, "query_rows", _fake_query)


def test_today_list_filters_by_channel(sm_db):
    res = smq.today_list("2026-07-14", channel="龙虎榜")
    assert {r["channel"] for r in res["rows"]} == {"龙虎榜"}
    assert all(r["date"] == "2026-07-14" for r in res["rows"])


def test_by_actor_national_team_keyword(sm_db):
    res = smq.by_actor("国家队", days=30)
    assert "中央汇金" in {r["actor"] for r in res["rows"]}
    assert res["summary"]["出现次数"] >= 1


def test_top_by_amount_desc(sm_db, monkeypatch):
    # top_by_amount 用 datetime.now() 相对窗口；固定"今"=2026-07-17，使 SM_ROWS
    # 的 2026-07-13/14 都落 days=5 窗口内，防真实时钟漂移致 ETF 行被排除而测试失效。
    import datetime as _dt
    class _FixedDt:
        @staticmethod
        def now():
            return _dt.datetime(2026, 7, 17)
    monkeypatch.setattr(smq, "datetime", _FixedDt)
    res = smq.top_by_amount(days=5, limit=10)
    amts = [r["amount"] for r in res["rows"]]
    assert amts == sorted(amts, reverse=True)
    assert any(r["market"] == "ETF" for r in res["rows"])   # ETF actor 空不报错

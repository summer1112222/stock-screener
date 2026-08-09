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


def _skip_ths(monkeypatch, err: str = "test-skip-ths"):
    """collect_fund_flow 走 THS 优先；测试 monkeypatch 它返回空，强制走 spot 兜底，
    避免单测触网。"""
    monkeypatch.setattr(sm, "_fetch_ths_individual_fund_flow",
                        lambda *a, **kw: ([], False, err))


def test_fund_flow_nan_to_none(monkeypatch):
    """资金流 spot 兜底路径：复用 stock_spot.main_net_inflow；None/NaN 行跳过不写废行，
    只入有净额的票。全 None 时 ok=false 带明确 err（防 ok 误报、防前端 11059 行 '—'）。"""
    _skip_ths(monkeypatch)   # THS 优先但测试跳过，强制走 spot 兜底
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: [
        {"code": "000001", "name": "平安银行", "main_net_inflow": 1.5e8},
        {"code": "000002", "name": "万科A", "main_net_inflow": float("nan")},
    ] if table == "stock_spot" else [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok
    assert len(recs) == 1                       # NaN 行跳过，不写 amount=null 废行
    assert recs[0]["channel"] == "资金流"
    assert recs[0]["market"] == "股票"
    assert recs[0]["actor"] == "主力资金"               # 无 actor 通道用空串，UNIQUE 去重生效
    assert recs[0]["action"] == "净买入"
    assert recs[0]["amount"] == 1.5e8


def test_fund_flow_ths_path_preferred(monkeypatch):
    """THS 直取优先：_fetch_ths 返回有效行时 collect_fund_flow 用 THS 数据，
    不读 spot（source 标同花顺）。"""
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "_fetch_ths_individual_fund_flow",
                        lambda *a, **kw: ([{"code": "600519", "name": "贵州茅台",
                                             "amount": 8.22e7}], True, ""))
    monkeypatch.setattr(db, "query_rows", lambda *a, **kw: [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok, err
    assert len(recs) == 1
    assert recs[0]["code"] == "600519"
    assert recs[0]["amount"] == 8.22e7
    assert recs[0]["actor"] == "主力资金"
    assert sm.CHANNEL_STATUS["资金流"]["source"] == "同花顺"


def test_parse_cn_amount():
    """THS 中文金额字符串解析：万/亿/负数/纯数字/无效。"""
    assert sm._parse_cn_amount("822.74万") == 822.74e4
    assert sm._parse_cn_amount("1.63亿") == 1.63e8
    assert sm._parse_cn_amount("-3172.81万") == -3172.81e4
    assert sm._parse_cn_amount("5300.0") == 5300.0
    assert sm._parse_cn_amount("--") is None
    assert sm._parse_cn_amount(None) is None
    assert sm._parse_cn_amount("abc") is None


def test_fund_flow_all_none_marks_unavailable(monkeypatch):
    """spot 全无 main_net_inflow（字段缺失/东财被封）→ 0 行 + ok=false + 明确 err，不误报。"""
    _skip_ths(monkeypatch)
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: [
        {"code": "000001", "name": "甲", "main_net_inflow": None},
        {"code": "000002", "name": "乙", "main_net_inflow": None},
    ] if table == "stock_spot" else [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok is False
    assert recs == []
    assert err   # 明确非空 err


def test_dragon_tiger_per_stock(monkeypatch):
    """akshare 1.18：龙虎榜改用主榜单'龙虎榜净买额'出个股级记录（不再逐股拉席位明细，
    因 stock_lhb_stock_detail_em 现需 date+flag 逐股两次请求、东财反爬下太慢）。
    日期需无破折号 YYYYMMDD；collector 内部把 'YYYY-MM-DD' 归一为无破折号调用。"""
    calls = {}
    def _lhb(**kw):
        calls.update(kw)
        return pd.DataFrame({"代码": ["000001"], "名称": ["平安银行"],
                             "龙虎榜净买额": [1.2e8], "上榜原因": ["日跌幅偏离值达7%"]})
    _patch_ak(monkeypatch, stock_lhb_detail_em=_lhb)
    recs, ok, err = sm.collect_dragon_tiger("2026-07-14")
    assert ok, err
    assert calls["start_date"] == "20260714"   # 带破折号入参被归一为无破折号
    assert len(recs) == 1
    r0 = recs[0]
    assert r0["channel"] == "龙虎榜"
    assert r0["action"] == "上榜"
    assert r0["code"] == "000001"
    assert r0["actor"] == "日跌幅偏离值达7%"
    assert r0["amount"] == 1.2e8


def test_collect_no_spot_returns_empty_not_raise(monkeypatch):
    """无 stock_spot 时资金流通道返回空不崩（THS 亦失败 → 两路均空，ok=false 带明确 err）。"""
    _skip_ths(monkeypatch)
    monkeypatch.setattr(db, "query_rows", lambda table, **kw: [])
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok is False
    assert recs == []
    assert err


def test_upsert_dedup_empty_actor_on_refresh(tmp_path, monkeypatch):
    """C1 回归：无 actor 通道用空串，重刷同日 INSERT OR REPLACE 命中 UNIQUE 去重，
    不静默重复（SQLite NULL≠NULL 是原 bug 根源）。资金流复用 stock_spot。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    _skip_ths(monkeypatch)   # 强制走 spot 兜底，验证 spot 路径的 UNIQUE 去重
    _orig_qr = db.query_rows

    def _qr(table, **kw):
        if table == "stock_spot":
            return [{"code": "000001", "name": "平安银行", "main_net_inflow": 1.5e8},
                    {"code": "000002", "name": "万科A", "main_net_inflow": 2e7}]
        return _orig_qr(table, **kw)
    monkeypatch.setattr(db, "query_rows", _qr)
    recs, ok, _ = sm.collect_fund_flow("2026-07-14")
    assert ok and len(recs) == 2
    assert all(r["actor"] == "主力资金" for r in recs)  # 资金流 actor=主力资金,UNIQUE 去重生效
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


def test_northbound_fallback_acc_flow(monkeypatch, tmp_path):
    """主源抛 NoneType 崩 → 备援2 十大成交股出记录。
    沪/深两通各返回不同 code 1 行，验证双 symbol 循环不被改坏。
    action="上榜"；source 标北向十大成交股(盘后)。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    def _boom(**kw):
        raise RuntimeError("'NoneType' object is not subscriptable")
    def _acc_flow(symbol="沪股通"):
        if symbol == "沪股通":
            return pd.DataFrame({"股票代码": ["600519"],
                                 "股票简称": ["贵州茅台"],
                                 "净买额": [3.2e8]})
        if symbol == "深股通":
            return pd.DataFrame({"股票代码": ["000001"],
                                 "股票简称": ["平安银行"],
                                 "净买额": [1.1e8]})
        return pd.DataFrame()
    def _net_flow(symbol="北向"):
        return pd.DataFrame({"日期": ["2026-07-25"], "当日资金流入": [5e8]})
    _patch_ak(monkeypatch,
              stock_hsgt_individual_em=_boom,
              stock_hsgt_hold_stock_em=_boom,
              stock_hsgt_north_acc_flow_in=_acc_flow,
              stock_hsgt_north_net_flow_in=_net_flow)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    recs, ok, err = sm.collect_northbound("2026-07-25")
    assert ok, err
    assert len(recs) == 2
    assert all(r["channel"] == "北向" for r in recs)
    assert all(r["actor"] == "北向资金" for r in recs)
    assert all(r["action"] == "上榜" for r in recs)
    r_sh = next(r for r in recs if r["code"] == "600519")
    assert r_sh["amount"] == 3.2e8
    assert r_sh["action"] == "上榜"
    assert r_sh["actor"] == "北向资金"
    r_sz = next(r for r in recs if r["code"] == "000001")
    assert r_sz["amount"] == 1.1e8
    assert r_sz["action"] == "上榜"
    assert r_sz["actor"] == "北向资金"
    assert sm.CHANNEL_STATUS["北向"]["source"] == "北向十大成交股(盘后)"


def test_northbound_degrade_to_total(monkeypatch, tmp_path):
    """备援2 也失败/空 → 降级3 总额 1 条，actor="北向总额"。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    def _boom(**kw):
        raise RuntimeError("NoneType")
    def _acc_flow(symbol="沪股通"):
        return pd.DataFrame()
    def _net_flow(symbol="北向"):
        return pd.DataFrame({"日期": ["2026-07-25"], "当日资金流入": [5e8]})
    _patch_ak(monkeypatch,
              stock_hsgt_individual_em=_boom,
              stock_hsgt_hold_stock_em=_boom,
              stock_hsgt_north_acc_flow_in=_acc_flow,
              stock_hsgt_north_net_flow_in=_net_flow)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    recs, ok, err = sm.collect_northbound("2026-07-25")
    assert ok, err
    assert len(recs) == 1
    assert recs[0]["actor"] == "北向总额"
    assert recs[0]["action"] == "净买入"
    assert recs[0]["amount"] == 5e8
    assert sm.CHANNEL_STATUS["北向"]["source"] == "北向总额(盘后)"


def _mock_other_channels(monkeypatch, ok=True):
    """把非测试目标的 5 通道 mock 成快速返回，避免 refresh_today 触网。
    龙虎榜/十大股东/高管/限售/北向 默认 ok 空；调用方可覆盖其中之一。"""
    for name in ("collect_dragon_tiger", "collect_holders",
                 "collect_management_hold", "collect_share_unlock",
                 "collect_northbound"):
        monkeypatch.setattr(sm, name, lambda d, _n=name: ([], ok, "skip"))


def test_stale_degradation_keeps_old_data(monkeypatch, tmp_path):
    """通道拉取失败 + DB 有 3 日前旧数据 → stale=True 保留旧、last_ok_date 正确、
    meta 写入；refresh_today 跳过 upsert（不入库新行）。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    old = [{"date": "2026-07-22", "code": "000001", "name": "甲", "market": "股票",
            "channel": "资金流", "actor": "", "action": "净买入", "amount": 1.5e8,
            "as_of": None, "ts": "2026-07-22 10:00:00"}]
    db.upsert_rows("smart_money_action", old)
    _mock_other_channels(monkeypatch)   # 其余 5 通道 ok 空，不触网
    monkeypatch.setattr(sm, "collect_fund_flow",
                        lambda d: ([], False, "资金流: THS 与 spot 均无净额"))
    metas = {}
    monkeypatch.setattr(db, "set_meta", lambda k, v: metas.update({k: v}))
    monkeypatch.setattr(db, "get_meta", lambda k, default="": metas.get(k, default))
    report = sm.refresh_today("2026-07-25")
    ch = report["channels"]["资金流"]
    assert ch["ok"] is True and ch.get("stale") is True
    assert ch["rows"] == 1
    assert ch["err"].startswith("回退至 2026-07-22")
    assert "sm_stale_资金流" in metas


def test_three_state_channel_light(monkeypatch, tmp_path):
    """三态：黄(资金流有旧+采集失败→stale)/灰(北向无旧+失败)。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.upsert_rows("smart_money_action",
                   [{"date": "2026-07-22", "code": "000001", "name": "甲",
                     "market": "股票", "channel": "资金流", "actor": "",
                     "action": "净买入", "amount": 1e8, "as_of": None, "ts": ""}])
    _mock_other_channels(monkeypatch)
    monkeypatch.setattr(sm, "collect_fund_flow", lambda d: ([], False, "fail"))
    monkeypatch.setattr(db, "set_meta", lambda k, v: None)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    sm.refresh_today("2026-07-25")
    st = sm.channel_status()["资金流"]
    assert st["ok"] is True and st.get("stale") is True          # 黄
    # 灰：北向覆盖为失败（无旧数据）
    monkeypatch.setattr(sm, "collect_northbound", lambda d: ([], False, "全失败"))
    sm.CHANNEL_STATUS["北向"] = {"ok": False, "source": "", "err": "未采集", "at": ""}
    sm.refresh_today("2026-07-25")
    st_nb = sm.channel_status()["北向"]
    assert st_nb["ok"] is False and not st_nb.get("stale")       # 灰


def test_holders_seed_union(monkeypatch, tmp_path):
    """候选 = 成交额前200 ∪ 种子；覆盖仅种子命中、shortlist 外的国家队小盘股。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.upsert_rows("stock_spot", [
        {"code": "600519", "name": "贵州茅台", "turnover_amount": 1e9},
        {"code": "601318", "name": "中国平安", "turnover_amount": 8e8}])
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "NATIONAL_TEAM_HOLDINGS_SEED", {"600999": "测试股"})
    monkeypatch.setattr(sm, "_load_seed", lambda: set(["600999"]))
    monkeypatch.setattr(sm, "_save_seed", lambda codes: None)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    pulled = []
    def _gdfx(symbol, date):
        pulled.append(symbol)
        return pd.DataFrame({"股东名称": ["中央汇金"]})
    _patch_ak(monkeypatch, stock_gdfx_free_top_10_em=_gdfx)
    recs, ok, err = sm.collect_holders("2026-07-25")
    assert ok, err
    assert any("600999" in p for p in pulled)
    assert any(r["actor"] == "中央汇金" for r in recs)


def test_holders_seed_learning(monkeypatch, tmp_path):
    """成功拉取后新命中 code 并入种子、落 meta。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    saved = {}
    monkeypatch.setattr(db, "set_meta", lambda k, v: saved.update({k: v}))
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "NATIONAL_TEAM_HOLDINGS_SEED", {"600999": "测试股"})
    monkeypatch.setattr(sm, "_load_seed", lambda: set(["600999"]))
    monkeypatch.setattr(db, "query_rows", lambda table, **kw:
        [{"code": "600519", "name": "茅台", "turnover_amount": 1e9}] if table=="stock_spot" else [])
    def _gdfx(symbol, date):
        return pd.DataFrame({"股东名称": ["中国证券金融"]})
    _patch_ak(monkeypatch, stock_gdfx_free_top_10_em=_gdfx)
    recs, ok, err = sm.collect_holders("2026-07-25")
    assert ok
    assert "nt_holdings_seed" in saved
    assert "600519" in saved["nt_holdings_seed"]


def test_today_list_defaults_to_latest_window(sm_db):
    """date 省略时默认取最新日期往前7日窗口(平衡数据量与速度)。SM_ROWS 最新 2026-07-14。"""
    res = smq.today_list()
    assert res["date"] == "2026-07-14"          # 响应回传实际最新日期
    assert all(r["date"] in ("2026-07-13", "2026-07-14") for r in res["rows"])
    assert res["total"] == 4                     # 7日窗口含 SM_ROWS 全部4行


def test_today_list_days_window(sm_db):
    """days=1 仅最新日;days=7 含近7日(SM_ROWS 全4行)。"""
    assert smq.today_list(days=1)["total"] == 2  # 仅 2026-07-14 2行
    assert smq.today_list(days=7)["total"] == 4  # 07-13+07-14 共4行


def test_refresh_single_channel_skips_others(monkeypatch, tmp_path):
    """单通道刷新：channels=["资金流"] 只跑资金流采集，其余5通道 skipped=True
    不调用采集函数；partial=True 且不刷新全局 update_time（避免误导全量已更新）。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(db, "set_meta", lambda k, v: None)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    called = []
    monkeypatch.setattr(sm, "collect_fund_flow",
                        lambda d: (called.append("资金流") or
                         ([{"date": d, "code": "000001", "name": "甲",
                            "market": "股票", "channel": "资金流", "actor": "",
                            "action": "净买入", "amount": 1.5e8, "as_of": None,
                            "ts": ""}], True, "")))
    for name in ("collect_dragon_tiger", "collect_holders",
                 "collect_management_hold", "collect_share_unlock",
                 "collect_northbound"):
        monkeypatch.setattr(sm, name,
                            lambda d, _n=name: (called.append(_n) or ([], True, "skip")))
    report = sm.refresh_today("2026-07-25", channels=["资金流"])
    assert called == ["资金流"]                       # 只跑了资金流
    assert report["channels"]["资金流"]["ok"] is True
    assert not report["channels"]["资金流"].get("skipped")
    assert report["channels"]["资金流"]["rows"] == 1
    assert report.get("partial") is True               # 单通道标 partial
    for other in ("龙虎榜", "十大股东", "高管增减持", "限售解禁", "北向"):
        ch = report["channels"][other]
        assert ch.get("skipped") is True, other
        assert ch["rows"] == 0


def test_refresh_all_channels_no_partial(monkeypatch, tmp_path):
    """全量刷新(channels=None)：所有通道都跑、无 skipped、无 partial 键。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(db, "set_meta", lambda k, v: None)
    monkeypatch.setattr(db, "get_meta", lambda k, default="": "")
    for name in ("collect_fund_flow", "collect_dragon_tiger", "collect_holders",
                 "collect_management_hold", "collect_share_unlock", "collect_northbound"):
        monkeypatch.setattr(sm, name, lambda d: ([], True, ""))
    report = sm.refresh_today("2026-07-25")
    assert "partial" not in report
    assert all(not v.get("skipped") for v in report["channels"].values())

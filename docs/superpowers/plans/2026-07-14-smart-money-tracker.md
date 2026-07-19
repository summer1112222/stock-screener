# 主力资金动向跟踪（P1）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一张 `smart_money_action` 底表 + 4 通道 collector（龙虎榜/十大股东/北向/资金流）+ `today`/`refresh`/`channels` 路由 + 前端当日清单页签，提供"每日主力动向观察清单"。

**Architecture:** 采集层 `data/smart_money.py`（每通道 `(records,ok,err)`，东财优先+备援+失败标不可用不崩，复用 `collector._to_records`/`_install_http_patch`）→ 查询层 `screener/smart_money.py`（只读 `db.query_rows`，不触网）→ `api/server.py` 路由用 `_wrap`+`cand_disclaimer`。`smart_money_action` 是唯一新增表。

**Tech Stack:** Python 3 / FastAPI / pandas / akshare / SQLite（与现有项目一致，无新依赖）。

## Global Constraints

- 合规：措辞统一"动向/动作/净额/观察清单/排序"，**禁**"推荐/买入信号/卖点/强势股"；清单/排序类附 `cand_disclaimer`。
- NaN→None 必须用 `df.astype(object).where(pd.notna(df), None)`，**不能**用 `df.where(pd.notna(df), None)`（float64 列 None→NaN，JSONResponse `allow_nan=False` 会 500）。
- 每个 collector 返回 `(records, ok, err)`，异常不抛崩；东财 `RemoteDisconnected`/502/503/504 自动受 `collector._install_http_patch()` 全局退避重试保护（已存在，勿重装）。
- `CHANNEL_STATUS` 不静默：任何通道 `ok=False` 显式返回 `err` 串。
- 单测放 `tests/`，合成数据 mock `db.query_rows`/`db.upsert_rows`，不触网。
- 日期格式 `YYYY-MM-DD`（带破折号）；入表统一带破折号。

参考 spec：`docs/superpowers/specs/2026-07-14-smart-money-tracker-design.md`

---

## File Structure

| 文件 | 责任 |
|---|---|
| `data/models.py` | 加 `smart_money_action` 到 `SCHEMA_SQL` + `SMART_MONEY_FIELDS` + `TABLE_FIELDS` |
| `data/smart_money.py` | 新建。4 通道 collector + `refresh_today` + `CHANNEL_STATUS`/`NATIONAL_TEAM` 常量 |
| `screener/smart_money.py` | 新建。`today_list` / `by_actor` / `top_by_amount` / `_expand_national_team`（只读 db） |
| `api/server.py` | 加 3 路由 + import |
| `web/index.html` | 加"主力动向"页签（当日清单 + 通道状态灯） |
| `tests/test_smart_money.py` | 新建。9 条单测 |

---

## Task 1: 底表 schema + 规范字段集

**Files:**
- Modify: `data/models.py`
- Test: `tests/test_smart_money.py`（新建）

**Interfaces:**
- Produces: 表名 `smart_money_action`；`SMART_MONEY_FIELDS`；`TABLE_FIELDS["smart_money_action"]`（供 `db.upsert_rows` 取列）。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_smart_money.py`：

```python
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


def test_table_in_schema():
    assert "smart_money_action" in SCHEMA_SQL

def test_table_fields_registered():
    assert "smart_money_action" in TABLE_FIELDS
    assert TABLE_FIELDS["smart_money_action"] is SMART_MONEY_FIELDS

def test_init_creates_table(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    assert db.query_rows("smart_money_action") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: FAIL（`SMART_MONEY_FIELDS` 未定义 / 表不在 schema）

- [ ] **Step 3: 实现** — `data/models.py`，在 `BOARD_DAILY_FIELDS` 之后加：

```python
# 主力动向记录规范字段集(游资/国家队/外资/资金流四通道统一入表)
SMART_MONEY_FIELDS = {
    "date", "code", "name", "market", "channel", "actor",
    "action", "amount", "rank", "as_of", "raw", "ts",
}
```

在 `SCHEMA_SQL` 字符串末尾（`board_daily` 表之后、闭合 `"""` 之前）加：

```sql

CREATE TABLE IF NOT EXISTS smart_money_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    market TEXT NOT NULL,
    channel TEXT NOT NULL,
    actor TEXT,
    action TEXT,
    amount REAL,
    rank INTEGER,
    as_of TEXT,
    raw TEXT,
    ts TEXT,
    UNIQUE(date, code, channel, actor, action)
);
CREATE INDEX IF NOT EXISTS idx_sm_date  ON smart_money_action(date);
CREATE INDEX IF NOT EXISTS idx_sm_code  ON smart_money_action(code);
CREATE INDEX IF NOT EXISTS idx_sm_actor ON smart_money_action(actor);
```

在 `TABLE_FIELDS` dict 末尾加：

```python
    "smart_money_action": SMART_MONEY_FIELDS,
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add data/models.py tests/test_smart_money.py
git commit -m "feat: add smart_money_action table + field set"
```

---

## Task 2: 采集层骨架 + 常量 + 4 通道 collector

**Files:**
- Create: `data/smart_money.py`
- Test: `tests/test_smart_money.py`（追加）

**Interfaces:**
- Consumes: `data.collector._to_records`（NaN→None）、`data.db.upsert_rows`/`get_meta`/`set_meta`/`stamp_update_time`/`query_rows`。
- Produces: `collect_dragon_tiger(date)->(list,bool,str)`、`collect_holders(date)`、`collect_northbound(date)`、`collect_fund_flow(date)`；常量 `CHANNEL_STATUS`、`NATIONAL_TEAM`；`refresh_today(date)->dict`。每条 record 是 dict，键 = `SMART_MONEY_FIELDS` 子集（`db.upsert_rows` 按 `TABLE_FIELDS` 取列，缺列置 None）。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_smart_money.py`：

```python
from data import smart_money as sm


def test_fund_flow_nan_to_none(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(
        ak, "stock_individual_fund_flow_rank",
        lambda **kw: pd.DataFrame({
            "代码": ["000001", "000002"],
            "名称": ["平安银行", "万科A"],
            "主力净流入净额": [1.5e8, float("nan")],
        }))
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok
    assert len(recs) == 2
    assert recs[1]["amount"] is None          # NaN→None，防 JSON 500
    assert recs[0]["channel"] == "资金流"
    assert recs[0]["market"] == "股票"
    assert recs[0]["actor"] is None
    assert recs[0]["action"] == "净买入"


def test_dragon_tiger_seat_level(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "stock_lhb_detail_em",
                        lambda **kw: pd.DataFrame({
                            "代码": ["000001"], "名称": ["平安银行"]}))
    monkeypatch.setattr(ak, "stock_lhb_stock_detail_em",
                        lambda symbol: pd.DataFrame({
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


def test_collect_exception_returns_empty_not_raise(monkeypatch):
    import akshare as ak
    def boom(**kw):
        raise RuntimeError("RemoteDisconnected")
    monkeypatch.setattr(ak, "stock_individual_fund_flow_rank", boom)
    recs, ok, err = sm.collect_fund_flow("2026-07-14")
    assert ok is False
    assert recs == []
    assert "RemoteDisconnected" in err
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: FAIL（`data.smart_money` 不存在）

- [ ] **Step 3: 实现** — 新建 `data/smart_money.py`：

```python
# -*- coding: utf-8 -*-
"""主力动向采集层：龙虎榜席位 / 十大流通股东 / 陆股通 / 个股资金流。

合规：本层只采集公开龙虎榜/股东/资金流数据，归类为"主力动向观察清单"，
      不荐股、不输出买卖点、不承诺收益。游资席位名/国家队持仓为公开事实陈述。
稳定性：每个 collector 返回 (records, ok, err)，网络/接口异常不抛崩。
NaN→None：出口统一经 _clean/_to_float 处理，确保 float NaN→None。
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover
    ak = None  # type: ignore
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db, collector  # noqa: F401  (import collector 触发 _install_http_patch 全局 UA 保护)

# 国家队关键字（查询层 by_actor("国家队") 展开为 LIKE 多名匹配）
NATIONAL_TEAM = ["中国证券金融", "中央汇金", "全国社保基金",
                 "中证金融", "梧桐树", "国家外汇管理局"]

# 各通道最近一次状态，前端据此灰掉不可用通道
CHANNEL_STATUS = {
    "龙虎榜": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "十大股东": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "北向": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "资金流": {"ok": False, "source": "", "err": "未采集", "at": ""},
}


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _set_status(channel: str, ok: bool, source: str, err: str) -> None:
    CHANNEL_STATUS[channel] = {"ok": ok, "source": source,
                               "err": err, "at": _now_ts()}


def _clean(v):
    """标量 NaN→None，便于 json 化。"""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _first_col(df: pd.DataFrame, candidates: list[str]) -> str:
    """从候选列名取第一个命中的实际列名；取不到回候选首项（r.get 自然得 None）。"""
    cols = set(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return candidates[0]


def _rec(date, code, name, market, channel, actor, action,
         amount, rank=None, as_of=None, raw=None) -> dict:
    """构造一条 smart_money_action 记录（amount NaN→None）。"""
    a = None if amount is None else (_to_float(amount))
    return {
        "date": date, "code": None if code is None else str(code),
        "name": name, "market": market, "channel": channel,
        "actor": actor, "action": action, "amount": a, "rank": rank,
        "as_of": as_of,
        "raw": json.dumps(raw, ensure_ascii=False) if raw else None,
        "ts": _now_ts(),
    }


# ------------------------------------------------------------------
# 资金流通道（无 actor，最简）
# ------------------------------------------------------------------
def collect_fund_flow(date: str) -> tuple[list[dict], bool, str]:
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        df = ak.stock_individual_fund_flow_rank(indicator="今日")
    except Exception as e:
        _set_status("资金流", False, "", str(e))
        return [], False, f"资金流: {e}"
    if df is None or df.empty:
        _set_status("资金流", False, "", "空结果")
        return [], False, "资金流: 空结果"
    col_code = _first_col(df, ["代码", "code"])
    col_name = _first_col(df, ["名称", "name"])
    col_amt = _first_col(df, ["主力净流入净额", "主力净流入", "主力净流入额"])
    recs = []
    for _, r in df.iterrows():
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "资金流", None, "净买入", r.get(col_amt),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("资金流", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 北向（陆股通）
# ------------------------------------------------------------------
def collect_northbound(date: str) -> tuple[list[dict], bool, str]:
    if not _AK_OK:
        return [], False, _AK_ERR
    df = None
    src = ""
    try:
        df = ak.stock_hsgt_individual_em(stock="北向资金"); src = "东财"
    except Exception:
        pass
    if df is None:
        try:
            df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行"); src = "东财"
        except Exception as e:
            _set_status("北向", False, "", str(e))
            return [], False, f"北向: 东财被封且无备援: {e}"
    if df is None or df.empty:
        _set_status("北向", False, "", "空结果")
        return [], False, "北向: 空结果"
    col_code = _first_col(df, ["股票代码", "代码", "code"])
    col_name = _first_col(df, ["股票简称", "名称", "name"])
    col_amt = _first_col(df, ["持股数量变化", "增持市值", "净买额", "今日增持市值"])
    recs = []
    for _, r in df.iterrows():
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "北向", None, "净买入", r.get(col_amt),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("北向", True, src, "")
    return recs, True, ""


# ------------------------------------------------------------------
# 龙虎榜（逐股×每席位）
# ------------------------------------------------------------------
def collect_dragon_tiger(date: str) -> tuple[list[dict], bool, str]:
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        stocks = ak.stock_lhb_detail_em(start_date=date, end_date=date)
    except Exception as e:
        _set_status("龙虎榜", False, "", str(e))
        return [], False, f"龙虎榜(列表): 东财被封，席位名无备援源: {e}"
    if stocks is None or stocks.empty:
        _set_status("龙虎榜", True, "", "当日无上榜票")
        return [], True, ""   # 当日无上榜票不算错
    col_code = _first_col(stocks, ["代码", "code"])
    col_name = _first_col(stocks, ["名称", "name"])
    recs = []
    partial_err = ""
    for _, s in stocks.iterrows():
        code, name = s.get(col_code), s.get(col_name)
        try:
            det = ak.stock_lhb_stock_detail_em(symbol=str(code))
        except Exception as e:
            partial_err = f"龙虎榜(席位 {code}): {e}"
            continue
        if det is None or det.empty:
            continue
        col_seat = _first_col(det, ["席位名称", "营业部名称", "席位"])
        col_buy = _first_col(det, ["买入额", "买入金额"])
        col_sell = _first_col(det, ["卖出额", "卖出金额"])
        for _, r in det.iterrows():
            buy = _to_float(r.get(col_buy))
            sell = _to_float(r.get(col_sell))
            amt = None if (buy is None or sell is None) else (buy - sell)
            recs.append(_rec(date, code, name, "股票", "龙虎榜",
                            r.get(col_seat), "上榜", amt,
                            raw={k: _clean(v) for k, v in r.items()}))
    if not recs and partial_err:
        _set_status("龙虎榜", False, "", partial_err)
        return [], False, partial_err
    _set_status("龙虎榜", True, "东财", partial_err)
    return recs, True, ("(部分席位失败) " + partial_err if partial_err else "")


# ------------------------------------------------------------------
# 十大流通股东（季频，国家队关键字命中靠查询层 LIKE）
# ------------------------------------------------------------------
def collect_holders(date: str) -> tuple[list[dict], bool, str]:
    if not _AK_OK:
        return [], False, _AK_ERR
    last = db.get_meta("holders_last_as_of", "")
    if last:
        try:
            last_d = datetime.strptime(last, "%Y-%m-%d")
            if (datetime.now() - last_d).days < 60:
                _set_status("十大股东", True, "", "未到披露窗口，跳过")
                return [], True, "(未到季报披露窗口，跳过)"
        except Exception:
            pass
    spots = db.query_rows("stock_spot", limit=0)
    if not spots:
        _set_status("十大股东", False, "", "无 stock_spot")
        return [], False, "十大股东: 无 stock_spot，先 /api/refresh 拉个股 spot"
    as_of = date
    recs = []
    for sp in spots[:2000]:   # 控量，避免上千次请求把东财打爆
        code = sp.get("code")
        try:
            df = ak.stock_gdfx_free_top_10(symbol=str(code))
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_holder = _first_col(df, ["股东名称", "股东"])
        for _, r in df.iterrows():
            holder = r.get(col_holder)
            if holder is None:
                continue
            recs.append(_rec(date, code, sp.get("name"), "股票", "十大股东",
                            holder, "持仓", None, as_of=as_of,
                            raw={k: _clean(v) for k, v in r.items()}))
    if recs:
        db.set_meta("holders_last_as_of", as_of)
    _set_status("十大股东", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 编排
# ------------------------------------------------------------------
def refresh_today(date: str | None = None) -> dict:
    """串行跑 4 通道 → upsert smart_money_action → 写 meta + CHANNEL_STATUS。
    单通道崩不影响其他通道。"""
    db.init_db()
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    report = {"date": date, "counts": {}, "channels": {}}
    plan = [("资金流", collect_fund_flow), ("北向", collect_northbound),
            ("龙虎榜", collect_dragon_tiger), ("十大股东", collect_holders)]
    for ch, fn in plan:
        try:
            recs, ok, err = fn(date)
        except Exception as e:   # 双保险：collect 内部已 try，这里再兜
            recs, ok, err = [], False, f"{ch}: 未捕获异常 {e}"
            _set_status(ch, False, "", str(e))
        n = db.upsert_rows("smart_money_action", recs) if (ok and recs) else 0
        st = CHANNEL_STATUS.get(ch, {})
        report["counts"][ch] = n
        report["channels"][ch] = {"ok": ok, "rows": n, "err": err, "at": st.get("at", "")}
    report["update_time"] = db.stamp_update_time()
    return report
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: PASS（6 passed：3 schema + 3 collector）

- [ ] **Step 5: 提交**

```bash
git add data/smart_money.py tests/test_smart_money.py
git commit -m "feat: 4-channel smart-money collectors + refresh orchestration"
```

---

## Task 3: 查询层（不触网）

**Files:**
- Create: `screener/smart_money.py`
- Test: `tests/test_smart_money.py`（追加）

**Interfaces:**
- Consumes: `db.query_rows("smart_money_action", where, params, order_by, limit)`、`data.smart_money.NATIONAL_TEAM`。
- Produces: `today_list(date, channel, market)->dict`、`by_actor(actor, days)->dict`、`top_by_amount(days, market, channel, limit)->dict`、`_expand_national_team()->list[str]`。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_smart_money.py`：

```python
from screener import smart_money as smq


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


@pytest.fixture
def sm_db(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, where="", params=(), order_by="", limit=0: list(SM_ROWS))


def test_today_list_filters_by_channel(sm_db):
    res = smq.today_list("2026-07-14", channel="龙虎榜")
    assert {r["channel"] for r in res["rows"]} == {"龙虎榜"}


def test_by_actor_national_team_keyword(sm_db):
    res = smq.by_actor("国家队", days=30)
    assert "中央汇金" in {r["actor"] for r in res["rows"]}
    assert res["summary"]["出现次数"] >= 1


def test_top_by_amount_desc(sm_db):
    res = smq.top_by_amount(days=5, limit=10)
    amts = [r["amount"] for r in res["rows"]]
    assert amts == sorted(amts, reverse=True)
    assert any(r["market"] == "ETF" for r in res["rows"])   # ETF actor 空不报错
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: FAIL（`screener.smart_money` 不存在）

- [ ] **Step 3: 实现** — 新建 `screener/smart_money.py`：

```python
# -*- coding: utf-8 -*-
"""主力动向查询/聚合层（不触网，纯 db.query_rows）。

合规：输出"主力动向观察清单/机械归类"，不荐股、不输出买卖点、不承诺收益。
依赖 smart_money_action 表（由 data/smart_money.refresh_today 写入）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from data import db, smart_money as sm_data

_NATIONAL_TEAM_KEYWORD = "国家队"


def _expand_national_team() -> list[str]:
    return list(sm_data.NATIONAL_TEAM)


def today_list(date: str | None = None, channel: str | None = None,
               market: str | None = None) -> dict:
    """用法 A：某日主力动向清单，按 amount 降序。"""
    where, params = [], []
    if date:
        where.append("date = ?"); params.append(date)
    if channel:
        where.append("channel = ?"); params.append(channel)
    if market:
        where.append("market = ?"); params.append(market)
    w = " AND ".join(where) if where else ""
    rows = db.query_rows("smart_money_action", where=w, params=tuple(params),
                         order_by="amount DESC", limit=0)
    return {"rows": rows, "total": len(rows)}


def by_actor(actor: str, days: int = 30) -> dict:
    """用法 B：某席位/股东 N 日内动向记录 + 汇总。
    actor 传席位名/股东名子串，或保留词"国家队"（展开为 LIKE 多名匹配）。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.query_rows("smart_money_action",
                         where="date >= ?", params=(since,),
                         order_by="date DESC", limit=0)
    if actor == _NATIONAL_TEAM_KEYWORD:
        keys = _expand_national_team()
        filtered = [r for r in rows if r.get("actor")
                    and any(k in r["actor"] for k in keys)]
    else:
        filtered = [r for r in rows if r.get("actor") and actor in r["actor"]]
    amt = 0.0
    for r in filtered:
        a = r.get("amount")
        if a is not None:
            amt += a
    return {"rows": filtered,
            "summary": {"出现次数": len(filtered), "累计净额": amt}}


def top_by_amount(days: int = 5, market: str | None = None,
                  channel: str | None = None, limit: int = 30) -> dict:
    """用法 C：按 N 日累计主力净额排序的观察池（group by code 降序）。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where, params = ["date >= ?"], [since]
    if market:
        where.append("market = ?"); params.append(market)
    if channel:
        where.append("channel = ?"); params.append(channel)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="", limit=0)
    agg: dict[str, dict] = {}
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        cur = agg.setdefault(code, {"code": code, "name": r.get("name"),
                                    "market": r.get("market"),
                                    "amount": 0.0, "count": 0})
        a = r.get("amount")
        if a is not None:
            cur["amount"] += a
        cur["count"] += 1
    pool = sorted(agg.values(), key=lambda x: x["amount"], reverse=True)[:limit]
    return {"rows": pool, "total": len(pool)}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_smart_money.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add screener/smart_money.py tests/test_smart_money.py
git commit -m "feat: smart-money query layer (today/by_actor/top_by_amount)"
```

---

## Task 4: API 路由

**Files:**
- Modify: `api/server.py`

**Interfaces:**
- Consumes: `data.smart_money.refresh_today`/`CHANNEL_STATUS`、`screener.smart_money.today_list`。
- Produces: `GET /api/smart-money/today`、`POST /api/smart-money/refresh`、`GET /api/smart-money/channels`。

- [ ] **Step 1: 实现** — `api/server.py`。把原 import 行：

```python
from data import collector, db, history, portfolio
from screener import engine
```

改为：

```python
from data import collector, db, history, portfolio, smart_money
from screener import engine, smart_money as sm_query
```

在 `portfolio` 路由块之后加：

```python
# ------------------------------------------------------------------
# 主力动向（游资/国家队/外资/资金流）—— 观察清单，非荐股
# ------------------------------------------------------------------
SM_CAND_DISCLAIMER = "主力动向观察清单，机械归类，非荐股非买卖信号，盈亏自负。"


@app.get("/api/smart-money/today")
def sm_today(date: str | None = Query(None),
             channel: str | None = Query(None),
             market: str | None = Query(None)):
    res = sm_query.today_list(date, channel, market)
    return _wrap(res["rows"], {
        "total": res["total"], "date": date,
        "cand_disclaimer": SM_CAND_DISCLAIMER})


@app.post("/api/smart-money/refresh")
def sm_refresh():
    report = smart_money.refresh_today()
    return _wrap(report, {"cand_disclaimer": SM_CAND_DISCLAIMER})


@app.get("/api/smart-money/channels")
def sm_channels():
    return _wrap(smart_money.CHANNEL_STATUS)
```

- [ ] **Step 2: 验证路由注册**

Run: `python -c "from api.server import app; print(sorted(r.path for r in app.routes if 'smart-money' in getattr(r,'path',''))))"`
Expected: 输出含 `/api/smart-money/today`、`/api/smart-money/refresh`、`/api/smart-money/channels`。

- [ ] **Step 3: 提交**

```bash
git add api/server.py
git commit -m "feat: smart-money API routes (today/refresh/channels)"
```

---

## Task 5: 前端页签

**Files:**
- Modify: `web/index.html`

- [ ] **Step 1: 实现** — 在页签导航加项、加内容容器、加 JS：

页签导航处加：
```html
<button class="tab" onclick="showTab('smart-money')">主力动向</button>
```

内容容器处加：
```html
<section id="tab-smart-money" class="tab-panel" style="display:none">
  <div style="margin-bottom:8px">
    <span id="sm-channels"></span>
    <button onclick="smRefresh()">刷新主力动向</button>
  </div>
  <table id="sm-table"><thead><tr>
    <th>日期</th><th>代码</th><th>名称</th><th>市场</th><th>通道</th>
    <th>席位/股东</th><th>动作</th><th>净额(元)</th>
  </tr></thead><tbody></tbody></table>
  <p class="disclaimer" id="sm-disclaimer"></p>
</section>
```

JS 处加：
```javascript
function showTab(id) {
  document.querySelectorAll('.tab-panel').forEach(p => p.style.display='none');
  document.getElementById('tab-'+id).style.display='block';
  if (id === 'smart-money') { smLoadChannels(); smLoad(); }
}
async function smLoadChannels() {
  const r = await fetch('/api/smart-money/channels').then(x=>x.json());
  document.getElementById('sm-channels').innerHTML =
    Object.entries(r.data).map(([k,v]) => {
      const color = v.ok ? 'green' : 'gray';
      const title = v.ok ? (k+': '+(v.source||'ok')) : (k+': '+v.err);
      return '<span style="color:'+color+';margin-right:10px" title="'+title+'">●'+k+'</span>';
    }).join('');
}
async function smRefresh() {
  await fetch('/api/smart-money/refresh',{method:'POST'});
  smLoadChannels(); smLoad();
}
async function smLoad() {
  const r = await fetch('/api/smart-money/today').then(x=>x.json());
  const rows = r.data || [];
  document.querySelector('#sm-table tbody').innerHTML = rows.map(x =>
    '<tr><td>'+(x.date||'')+'</td><td>'+(x.code||'')+'</td><td>'+(x.name||'')+'</td>'+
    '<td>'+(x.market||'')+'</td><td>'+(x.channel||'')+'</td><td>'+(x.actor||'')+'</td>'+
    '<td>'+(x.action||'')+'</td><td>'+(x.amount==null?'-':(+x.amount).toLocaleString())+'</td></tr>'
  ).join('');
  document.getElementById('sm-disclaimer').textContent = r.cand_disclaimer || '';
}
```

- [ ] **Step 2: 手测** — 启动服务、切"主力动向"页签、点刷新：

Run: `uvicorn api.server:app --reload --port 8000`，浏览器开 `http://localhost:8000/web/index.html`，切"主力动向"页签。
Expected: 4 个通道状态灯（未采集为灰），刷新后清单表出当日行、底部 disclaimer 显示。

- [ ] **Step 3: 提交**

```bash
git add web/index.html
git commit -m "feat: smart-money web tab (daily list + channel status lights)"
```

---

## Task 6: 全量单测 + 收尾

- [ ] **Step 1: 全量单测**

Run: `python -m pytest tests/ -q`
Expected: 全绿（原有 + 新增 9 条 smart_money 测试）。

- [ ] **Step 2: 核对 CLAUDE.md 改动检查清单**

- `smart_money_action` 表 → 已同步 `models.SCHEMA_SQL` + `SMART_MONEY_FIELDS` + `TABLE_FIELDS`（全新表，无需 `_BOARD_MIGRATIONS`）。
- 4 采集源 → 复用 `(records, ok, err)` + NaN→None + 异常不崩；东财域名受 `collector._install_http_patch` 全局保护（`data/smart_money.py` 顶部 `from . import collector` 触发）。
- 3 路由 → 用 `_wrap()`，`today`/`refresh` 附 `cand_disclaimer`。
- 前端 → 列名中性（"动作/净额(元)"）。

- [ ] **Step 3: 更新 CLAUDE.md** — 把 `data/smart_money.py`、`screener/smart_money.py` 加进架构小节，路由速查加 3 条 smart-money 路由，一并提交。

```bash
git add CLAUDE.md
git commit -m "docs: note smart_money module + routes in CLAUDE.md"
```

---

## Self-Review

**1. Spec 覆盖**：§3 schema→Task1 ✓；§4 四通道+数据流→Task2 ✓（龙虎榜逐股×席位、十大股东季频节流、北向/资金流）；§5 P1 路由→Task4 ✓；§6 前端→Task5 ✓；§7 错误处理（单通道崩不影响/NaN→None/CHANNEL_STATUS 不静默/partial）→Task2 `_set_status`+`refresh_today` try 兜 ✓；§8 六类场景→Task1(3)+Task2(3)+Task3(3)=9 条覆盖 ✓；P2/P3 查询层已实现（Task3），路由按 spec 留下期 ✓。

**2. Placeholder 扫描**：无 TBD/TODO；akshare 列名用 `_first_col` 容错候选（与现有 `BOARD_ALIASES` 同思路，非占位）。

**3. 类型一致**：`collect_*` 返回 `tuple[list[dict],bool,str]`；`refresh_today` 解包 `(recs,ok,err)=fn(date)` ✓；`today_list/by_actor/top_by_amount` 签名 Task3 定义、Task4 `sm_query.today_list(date,channel,market)` 调用一致 ✓；`CHANNEL_STATUS` 键名"龙虎榜/十大股东/北向/资金流" Task2 定义、Task5 前端 `Object.entries(r.data)` 渲染一致 ✓。

**落地注记（非占位，是已知约束）**：akshare 龙虎榜/北向/十大股东东财接口在当前出口 IP 大概率被封；本计划按"东财优先→备援→全失败标 ok=False 返 err 不崩"实现，通道不可用时前端灰灯，不阻塞其他通道。十大股东覆盖率受 `spots[:2000]` 限量 + 季频节流约束。

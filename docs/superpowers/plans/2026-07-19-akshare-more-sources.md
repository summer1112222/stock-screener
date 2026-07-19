# AKShare 扩展数据源 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 AKShare 补 4 类数据源(ST 全名单 / 完整三大财报 / 高管增减持+限售解禁 / 研报评级+千股千评),沿用现有采集约定,增强 buffett FCF 精度。

**Architecture:** 列表型接口进 `refresh_all`/`refresh_today` 全量抓;per-code 明细型按需拉取+7天缓存(同 buffett 模式)。高管增减持/限售解禁并入 `smart_money_action`(复用 channel,零加列)。完整财报仅供 buffett 内部 FCF 升级,不暴露对外路由。

**Tech Stack:** Python 3 / FastAPI / pandas / AKShare / SQLite / pytest

## Global Constraints

- **合规**:所有新路由走 `_wrap()` + 对应 disclaimer;ST 默认 disclaimer,高管增减持/限售解禁复用 `SM_CAND_DISCLAIMER`,研报/千股千评用"机构视角机械汇总,非荐股非买卖信号,盈亏自负"。措辞用"筛选/排序/观察清单",禁"推荐/买入/卖出"。
- **稳定性**:每个采集函数返回 `(df|records, ok, err)`,异常不抛崩;东财域名受 `collector._install_http_patch()` 保护;被封标不可用不写 null 废行。
- **NaN→None**:列表型经 `_to_records`(`df.astype(object).where(pd.notna(df), None)`),per-code raw 经 `_clean`。
- **DB 迁移**:新表 `CREATE TABLE IF NOT EXISTS`,`_migrate` 不加条目;`smart_money_action` 零新列。
- **akshare 列名漂移**:用 `_first_col` 候选名容错,单测 mock 验证。
- **测试**:mock akshare(`monkeypatch.setattr(mod,"ak",mock_module)`),不触网;碰 db 的测试 `monkeypatch` 临时 `DB_PATH`。

## File Structure

- `data/models.py` — 新增 3 表 schema + 字段集 + 别名映射
- `data/fundamentals.py`(新) — 完整三大财报按需采集+缓存,导出 `fetch(code, source)` + 缓存工具
- `data/research.py`(新) — 研报采集+查询,千股千评按需(复用 fundamentals 缓存工具)
- `data/collector.py` — 加 `fetch_st_list()` + `refresh_all` 扩
- `data/smart_money.py` — 加 `collect_management_hold`/`collect_share_unlock` + `CHANNEL_STATUS` + `refresh_today` plan
- `screener/smart_money.py` — 加 `unlock_by_month(month, code)`
- `api/server.py` — 加 5 条路由
- `backtest/buffett.py` — `analyze()` FCF 升级 + `_pick_col_sum`
- `web/index.html` — buffett `fcf_source` 标签 + 通道循环
- `tests/` — 5 个测试文件
- `CLAUDE.md` — 路由速查 + 检查清单更新

---

### Task 1: models.py 新增表 schema + 字段集 + 别名

**Files:**
- Modify: `data/models.py`
- Test: `tests/test_models_new_tables.py`

**Interfaces:**
- Produces: `ST_LIST_FIELDS`, `RESEARCH_REPORT_FIELDS`, `FUNDAMENTALS_CACHE_FIELDS`, `ST_LIST_ALIASES`, `RESEARCH_REPORT_ALIASES`;SCHEMA_SQL 含 `st_list`/`research_report`/`fundamentals_cache` 三表;`TABLE_FIELDS` 含三表。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_new_tables.py
# -*- coding: utf-8 -*-
from data import models


def test_st_list_fields():
    assert models.ST_LIST_FIELDS == {"code", "name", "st_type",
                                    "latest_price", "change_pct"}


def test_research_report_fields():
    assert models.RESEARCH_REPORT_FIELDS == {
        "code", "name", "rating", "title", "org",
        "analyst", "pub_date", "target_price", "ts"}


def test_fundamentals_cache_fields():
    assert models.FUNDAMENTALS_CACHE_FIELDS == {"code", "source",
                                                "payload_json", "ts"}


def test_schema_has_new_tables():
    assert "CREATE TABLE IF NOT EXISTS st_list" in models.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS research_report" in models.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS fundamentals_cache" in models.SCHEMA_SQL


def test_table_fields_registered():
    assert "st_list" in models.TABLE_FIELDS
    assert "research_report" in models.TABLE_FIELDS
    assert "fundamentals_cache" in models.TABLE_FIELDS


def test_st_list_aliases():
    assert models.ST_LIST_ALIASES["代码"] == "code"
    assert models.ST_LIST_ALIASES["涨跌幅"] == "change_pct"


def test_research_report_aliases():
    assert models.RESEARCH_REPORT_ALIASES["投资评级"] == "rating"
    assert models.RESEARCH_REPORT_ALIASES["目标价"] == "target_price"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_models_new_tables.py -v`
Expected: FAIL("模块无属性 ST_LIST_FIELDS"等)

- [ ] **Step 3: 实现**

在 `data/models.py` 适当位置(STOCK_SPOT_ALIASES 之后、SCHEMA_SQL 之前)加:

```python
# ST 全名单规范字段
ST_LIST_FIELDS = {
    "code", "name", "st_type", "latest_price", "change_pct",
}

# 研报评级规范字段(不含 id,靠 UNIQUE 去 REPLACE)
RESEARCH_REPORT_FIELDS = {
    "code", "name", "rating", "title", "org",
    "analyst", "pub_date", "target_price", "ts",
}

# 完整财报+千股千评缓存字段(多 source,7 天 TTL)
FUNDAMENTALS_CACHE_FIELDS = {"code", "source", "payload_json", "ts"}

# ST 全名单 AKShare 列名别名
ST_LIST_ALIASES = {
    "代码": "code", "code": "code",
    "名称": "name", "name": "name",
    "涨跌幅": "change_pct", "change_pct": "change_pct",
    "最新价": "latest_price", "latest_price": "latest_price",
}

# 研报评级 AKShare 列名别名
RESEARCH_REPORT_ALIASES = {
    "代码": "code", "股票代码": "code", "code": "code",
    "名称": "name", "股票简称": "name", "name": "name",
    "评级": "rating", "投资评级": "rating", "rating": "rating",
    "研报标题": "title", "标题": "title", "title": "title",
    "机构": "org", "研究机构": "org", "org": "org",
    "研究员": "analyst", "分析师": "analyst", "analyst": "analyst",
    "日期": "pub_date", "研报日期": "pub_date", "pub_date": "pub_date",
    "目标价": "target_price", "目标价（元）": "target_price",
}
```

在 `SCHEMA_SQL` 末尾(`financial_abstract_cache` 表之后)加:

```sql

-- ST 全名单快照(同 spot 模式,code 主键覆盖)
CREATE TABLE IF NOT EXISTS st_list (
    code TEXT PRIMARY KEY,
    name TEXT,
    st_type TEXT,
    latest_price REAL,
    change_pct REAL
);

-- 研报评级(列表型,多机构同日;靠 UNIQUE 去 REPLACE)
CREATE TABLE IF NOT EXISTS research_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT, name TEXT,
    rating TEXT,
    title TEXT,
    org TEXT,
    analyst TEXT,
    pub_date TEXT,
    target_price REAL,
    ts TEXT,
    UNIQUE(code, pub_date, org, title)
);

-- 完整三大财报+千股千评按需缓存(多 source,7 天 TTL)
CREATE TABLE IF NOT EXISTS fundamentals_cache (
    code TEXT, source TEXT,
    payload_json TEXT,
    ts TEXT,
    PRIMARY KEY (code, source)
);
```

在 `TABLE_FIELDS` dict 末尾加:

```python
    "st_list": ST_LIST_FIELDS,
    "research_report": RESEARCH_REPORT_FIELDS,
    "fundamentals_cache": FUNDAMENTALS_CACHE_FIELDS,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_models_new_tables.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add data/models.py tests/test_models_new_tables.py
git commit -m "feat(models): 新增 st_list/research_report/fundamentals_cache 三表 schema"
```

---

### Task 2: data/fundamentals.py 完整三大财报按需采集+缓存

**Files:**
- Create: `data/fundamentals.py`
- Test: `tests/test_fundamentals_cache.py`

**Interfaces:**
- Consumes: `data.db`(`query_rows`/`upsert_rows`/`init_db`),`data.collector._install_http_patch`(import 触发全局 UA 保护),akshare
- Produces: `fetch(code, source) -> (DataFrame|None, stale_bool)`;缓存工具 `_cache_get(code, source, allow_stale)`/`_cache_set(code, source, df)`/`_strip_prefix(code)`/`_AK_TIMEOUT`/`_CACHE_TTL_DAYS`(Task 3 复用)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fundamentals_cache.py
# -*- coding: utf-8 -*-
"""fundamentals 完整财报按需采集+缓存测试。
宿主无 akshare 时 _AK_OK=False,用 monkeypatch 注入 mock ak + 临时 db。"""
from datetime import datetime, timedelta
from types import ModuleType

import pandas as pd

from data import fundamentals, db


def _mock_ak(sheet_fn):
    m = ModuleType("akshare")
    m.stock_balance_sheet_by_report_em = sheet_fn
    m.stock_cash_flow_sheet_by_report_em = sheet_fn
    m.stock_profit_sheet_by_report_em = sheet_fn
    return m


def _tmp_db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()


def _cashflow_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "经营活动产生的现金流量净额": 1000.0,
        "购建固定资产、无形资产及其他长期资产支付的现金": 300.0,
    }])


def test_fetch_hit_cache(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(fundamentals, "_AK_OK", True)
    called = {"n": 0}

    def _net(symbol):
        called["n"] += 1
        return _cashflow_df()

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_net))
    df1, stale1 = fundamentals.fetch("600519", "cashflow")
    assert df1 is not None and not stale1
    df2, stale2 = fundamentals.fetch("600519", "cashflow")
    assert df2 is not None and not stale2
    assert called["n"] == 1  # 第二次走缓存


def test_fetch_ak_fail_returns_none(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(fundamentals, "_AK_OK", True)

    def _err(symbol):
        raise RuntimeError("em blocked")

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_err))
    df, stale = fundamentals.fetch("600519", "cashflow")
    assert df is None  # 无缓存且拉取失败


def test_fetch_stale_fallback(monkeypatch, tmp_path):
    """有过期缓存时,拉取失败返回 stale 缓存。"""
    _tmp_db(monkeypatch, tmp_path)
    old_ts = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    payload = _cashflow_df().to_json(orient="records", force_ascii=False)
    db.upsert_rows("fundamentals_cache",
                   [{"code": "600519", "source": "cashflow",
                     "payload_json": payload, "ts": old_ts}])
    monkeypatch.setattr(fundamentals, "_AK_OK", True)

    def _err(symbol):
        raise RuntimeError("em blocked")

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_err))
    df, stale = fundamentals.fetch("600519", "cashflow")
    assert df is not None and stale is True


def test_ak_ok_false_skips_net(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(fundamentals, "_AK_OK", False)
    called = {"n": 0}

    def _net(symbol):
        called["n"] += 1
        return _cashflow_df()

    monkeypatch.setattr(fundamentals, "ak", _mock_ak(_net))
    df, stale = fundamentals.fetch("600519", "cashflow")
    assert df is None and called["n"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_fundamentals_cache.py -v`
Expected: FAIL(模块不存在 / ImportError)

- [ ] **Step 3: 实现 data/fundamentals.py**

```python
# -*- coding: utf-8 -*-
"""完整三大财报按需采集+缓存(同 buffett financial_abstract_cache 模式,多 source)。

合规:本层只采集公开财务数据,不做选股/评级/买卖点逻辑。
稳定性:fetch(code, source) 返回 (df, stale);_AK_OK=False 或单只超时(20s)降级
       返回过期缓存(stale=True),防 quality 逐只拉卡死。
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from types import ModuleType

import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover
    ak = ModuleType("akshare")
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db, collector  # noqa: F401  (import collector 触发 _install_http_patch)

_AK_TIMEOUT = 20
_CACHE_TTL_DAYS = 7


def _strip_prefix(code: str) -> str:
    c = str(code).strip()
    return c[2:] if c[:2].lower() in ("sh", "sz", "bj") else c


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cache_get(code: str, source: str, allow_stale: bool = False):
    """返回 (df_or_None, status)。status ∈ hit/stale/miss。"""
    rows = db.query_rows("fundamentals_cache",
                         where="code=? AND source=?", params=(code, source))
    if not rows:
        return None, "miss"
    r = rows[0]
    payload, ts = r.get("payload_json"), r.get("ts")
    if not payload or not ts:
        return None, "miss"
    try:
        df = pd.read_json(io.StringIO(payload))
    except Exception:
        return None, "miss"
    try:
        age = datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None, "miss"
    if age.days <= _CACHE_TTL_DAYS:
        return df, "hit"
    if allow_stale:
        return df, "stale"
    return None, "stale"


def _cache_set(code: str, source: str, df: pd.DataFrame) -> None:
    payload = df.to_json(orient="records", force_ascii=False)
    db.upsert_rows("fundamentals_cache",
                   [{"code": code, "source": source,
                     "payload_json": payload, "ts": _now_ts()}])


def _fetch_net(code: str, source: str):
    c = _strip_prefix(code)
    if source == "balance":
        return ak.stock_balance_sheet_by_report_em(symbol=c)
    if source == "cashflow":
        return ak.stock_cash_flow_sheet_by_report_em(symbol=c)
    if source == "profit":
        return ak.stock_profit_sheet_by_report_em(symbol=c)
    return None


def fetch(code: str, source: str) -> tuple[pd.DataFrame | None, bool]:
    """返回 (df, stale)。缓存7天 TTL;_AK_OK=False 或单只超时(20s)降级返回过期缓存。"""
    df, status = _cache_get(code, source, allow_stale=False)
    if status == "hit":
        return df, False
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, code, source).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(code, source, net)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(code, source, allow_stale=True)
    if df_s is not None:
        return df_s, True
    return None, False
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_fundamentals_cache.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add data/fundamentals.py tests/test_fundamentals_cache.py
git commit -m "feat(fundamentals): 完整三大财报按需采集+缓存(同 buffett 模式)"
```

---

### Task 3: data/research.py 研报采集+查询+千股千评

**Files:**
- Create: `data/research.py`
- Test: `tests/test_research.py`

**Interfaces:**
- Consumes: `data.db`,`data.collector._install_http_patch`,`data.fundamentals`(`_cache_get`/`_cache_set`/`_strip_prefix`/`_AK_TIMEOUT`/`_AK_OK`),`data.models.RESEARCH_REPORT_ALIASES`,akshare
- Produces: `fetch_reports(recent_days) -> (df, ok, err)`(进 refresh_all);`query_reports(code, days, limit) -> dict`;`fetch_comments(code) -> (dict|None, stale)`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_research.py
# -*- coding: utf-8 -*-
"""research 研报采集+查询/千股千评测试。mock ak + 临时 db。"""
from datetime import datetime, timedelta
from types import ModuleType

import pandas as pd

from data import research, db


def _mock_ak(report_fn, comment_fn):
    m = ModuleType("akshare")
    m.stock_research_report_em = report_fn
    m.stock_comment_detail = comment_fn
    return m


def _tmp_db(monkeypatch, tmp_path):
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()


def _report_df():
    return pd.DataFrame([
        {"股票代码": "600519", "股票简称": "贵州茅台", "投资评级": "增持",
         "研报标题": "T1", "研究机构": "中信", "分析师": "张三",
         "日期": "2026-07-10", "目标价（元）": 2000.0},
    ])


def test_fetch_reports_normalize(monkeypatch):
    monkeypatch.setattr(research, "_AK_OK", True)
    monkeypatch.setattr(research, "ak", _mock_ak(
        lambda **kw: _report_df(), lambda symbol: pd.DataFrame()))
    df, ok, err = research.fetch_reports(recent_days=30)
    assert ok, err
    recs = df.to_dict("records")
    assert recs[0]["code"] == "600519"
    assert recs[0]["rating"] == "增持"
    assert recs[0]["target_price"] == 2000.0


def test_fetch_reports_ak_fail(monkeypatch):
    monkeypatch.setattr(research, "_AK_OK", True)

    def _err(**kw):
        raise RuntimeError("em blocked")

    monkeypatch.setattr(research, "ak", _mock_ak(_err, lambda s: pd.DataFrame()))
    df, ok, err = research.fetch_reports(recent_days=30)
    assert not ok and df.empty


def test_query_reports_filters(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
    new = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    rows = [
        {"code": "600519", "name": "贵州茅台", "rating": "增持", "title": "T1",
         "org": "中信", "analyst": "张三", "pub_date": new, "target_price": 2000.0, "ts": new},
        {"code": "000001", "name": "平安银行", "rating": "中性", "title": "T2",
         "org": "海通", "analyst": "李四", "pub_date": old, "target_price": 15.0, "ts": old},
    ]
    db.upsert_rows("research_report", rows)
    res = research.query_reports(days=30, limit=200)
    assert res["total"] == 1
    assert res["rows"][0]["code"] == "600519"


def test_fetch_comments_cache_and_stale(monkeypatch, tmp_path):
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(research, "_AK_OK", True)
    called = {"n": 0}

    def _cmt(symbol):
        called["n"] += 1
        return pd.DataFrame([{"代码": symbol, "主力成本": 1800.0}])

    monkeypatch.setattr(research, "ak", _mock_ak(
        lambda **kw: pd.DataFrame(), _cmt))
    res1, stale1 = research.fetch_comments("600519")
    assert res1 is not None and not stale1
    res2, stale2 = research.fetch_comments("600519")
    assert called["n"] == 1  # 第二次走缓存
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_research.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 data/research.py**

```python
# -*- coding: utf-8 -*-
"""研报评级+千股千评采集层。

合规:本层只采集公开机构研报/千股千评,归类为"机构视角机械汇总",
     不荐股、不输出买卖点、不承诺收益。
稳定性:fetch_reports 返回 (df, ok, err),异常不抛崩;fetch_comments 按需+缓存。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from types import ModuleType

import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover
    ak = ModuleType("akshare")
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db, collector  # noqa: F401
from .models import RESEARCH_REPORT_ALIASES
from .fundamentals import _cache_get, _cache_set, _strip_prefix, _AK_TIMEOUT


def _to_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def fetch_reports(recent_days: int = 30) -> tuple[pd.DataFrame, bool, str]:
    """研报(列表型,进 refresh_all)。stock_research_report_em 按日期范围取近 N 日。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=recent_days)).strftime("%Y%m%d")
    try:
        df = ak.stock_research_report_em(start_date=start, end_date=end)
        norm = _normalize_report(df)
        if not norm.empty:
            norm["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return norm, True, ""
    except Exception as e:
        return pd.DataFrame(), False, f"research: {e}"


def _normalize_report(df: pd.DataFrame) -> pd.DataFrame:
    """RESEARCH_REPORT_ALIASES 归一,只保留规范字段,缺列补 None。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {}
    for col in df.columns:
        key = RESEARCH_REPORT_ALIASES.get(col)
        if key and key not in rename.values():
            rename[col] = key
    df = df.rename(columns=rename)
    keep = [v for v in RESEARCH_REPORT_ALIASES.values() if v in df.columns
            and v != "ts"]
    return df[keep].copy() if keep else pd.DataFrame()


def query_reports(code: str | None = None, days: int = 30,
                  limit: int = 200) -> dict:
    """从 research_report 表查近 N 日研报;code 非空则按 code 过滤。pub_date 降序。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where, params = ["pub_date >= ?"], [since]
    if code:
        where.append("code = ?")
        params.append(code)
    rows = db.query_rows("research_report", where=" AND ".join(where),
                         params=tuple(params), order_by="pub_date DESC",
                         limit=limit)
    return {"rows": rows, "total": len(rows)}


def fetch_comments(code: str) -> tuple[dict | None, bool]:
    """千股千评(per-code 按需+缓存,source=comments)。超时降级返回 stale 缓存。"""
    code = _strip_prefix(code)
    cached, status = _cache_get(code, "comments", allow_stale=False)
    if status == "hit":
        return {"rows": cached.to_dict("records")}, False
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                df = ex.submit(lambda: ak.stock_comment_detail(symbol=code)
                               ).result(timeout=_AK_TIMEOUT)
            if df is not None and not df.empty:
                _cache_set(code, "comments", df)
                return {"rows": _to_records(df)}, False
        except (FuturesTimeout, Exception):
            pass
    cached_s, _ = _cache_get(code, "comments", allow_stale=True)
    if cached_s is not None:
        return {"rows": cached_s.to_dict("records")}, True
    return None, False
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_research.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add data/research.py tests/test_research.py
git commit -m "feat(research): 研报评级采集+查询+千股千评按需缓存"
```

---

### Task 4: data/collector.py 加 fetch_st_list + refresh_all

**Files:**
- Modify: `data/collector.py`
- Test: `tests/test_st_list.py`

**Interfaces:**
- Consumes: `data.models.ST_LIST_ALIASES`,`_normalize`,`_to_records`,`ak`
- Produces: `fetch_st_list() -> (df, ok, err)`;`refresh_all` counts 含 `st_list`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_st_list.py
# -*- coding: utf-8 -*-
"""fetch_st_list 测试:st_type 由 name 前缀解析;东财失败标不可用不崩。"""
from types import ModuleType

import pandas as pd

from data import collector


def _mock_ak(st_fn):
    m = ModuleType("akshare")
    m.stock_zh_a_st_em = st_fn
    return m


def _st_df():
    return pd.DataFrame([
        {"代码": "600250", "名称": "*ST兴源", "最新价": 2.1, "涨跌幅": -1.5},
        {"代码": "000004", "名称": "ST国华", "最新价": 3.3, "涨跌幅": 0.8},
        {"代码": "000005", "名称": "世纪星源", "最新价": 1.2, "涨跌幅": 0.1},
    ])


def test_st_list_st_type_from_name(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", True)
    monkeypatch.setattr(collector, "ak", _mock_ak(lambda: _st_df()))
    df, ok, err = collector.fetch_st_list()
    assert ok, err
    recs = df.to_dict("records")
    by_code = {r["code"]: r for r in recs}
    assert by_code["600250"]["st_type"] == "*ST"
    assert by_code["000004"]["st_type"] == "ST"
    assert by_code["000005"]["st_type"] == "其他"
    assert by_code["600250"]["latest_price"] == 2.1


def test_st_list_ak_fail(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", True)

    def _err():
        raise RuntimeError("em blocked")

    monkeypatch.setattr(collector, "ak", _mock_ak(_err))
    df, ok, err = collector.fetch_st_list()
    assert not ok and df.empty
    assert "st_list" in err


def test_st_list_ak_ok_false(monkeypatch):
    monkeypatch.setattr(collector, "_AK_OK", False)
    df, ok, err = collector.fetch_st_list()
    assert not ok and df.empty
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_st_list.py -v`
Expected: FAIL(`fetch_st_list` 未定义)

- [ ] **Step 3: 实现**

在 `data/collector.py` 顶部 import 改:
```python
from .models import (BOARD_ALIASES, FUND_FLOW_ALIASES, ETF_ALIASES,
                     STOCK_SPOT_ALIASES, ST_LIST_ALIASES)
```

在 `fetch_stock_spot` 之后加:
```python
def fetch_st_list() -> tuple[pd.DataFrame, bool, str]:
    """ST/*ST 全名单(东财 stock_zh_a_st_em)。无 THS 备援,被封标不可用不崩。
    st_type 由 name 前缀解析。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_zh_a_st_em()
        norm = _normalize(df, ST_LIST_ALIASES)
        if not norm.empty:
            def _t(n):
                s = str(n or "")
                return "*ST" if s.startswith("*ST") else "ST" if s.startswith("ST") else "其他"
            norm["st_type"] = norm["name"].map(_t)
            return norm, True, ""
        return pd.DataFrame(), False, "st_list: 空结果"
    except Exception as e:
        return pd.DataFrame(), False, f"st_list: {e}"
```

在 `refresh_all` 的 `fetch_stock_spot` 块之后、`if n_ok > 0...` 之前加:
```python
    # ST 全名单(东财 stock_zh_a_st_em)
    df, ok, err = fetch_st_list()
    if ok:
        n = db.upsert_rows("st_list", _to_records(df))
        report["counts"]["st_list"] = n
        n_ok += 1
    else:
        report["errors"].append(err)
        report["counts"]["st_list"] = 0
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_st_list.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add data/collector.py tests/test_st_list.py
git commit -m "feat(collector): ST 全名单采集 + refresh_all 集成"
```

---

### Task 5: data/smart_money.py 加高管增减持/限售解禁通道

**Files:**
- Modify: `data/smart_money.py`
- Test: `tests/test_management_unlock.py`

**Interfaces:**
- Consumes: `_AK_OK`,`_first_col`,`_rec`,`_set_status`,`_friendly_err`,`_clean`,akshare
- Produces: `collect_management_hold(date)`/`collect_share_unlock(date)`;`CHANNEL_STATUS` 含两通道;`refresh_today` plan 含两通道

- [ ] **Step 1: 写失败测试**

```python
# tests/test_management_unlock.py
# -*- coding: utf-8 -*-
"""高管增减持/限售解禁采集测试。mock ak。"""
from types import ModuleType

import pandas as pd

from data import smart_money as sm


def _mock_ak(mgmt_fn, unlock_fn):
    m = ModuleType("akshare")
    m.stock_hold_management_em = mgmt_fn
    m.stock_share_change_em = unlock_fn
    return m


def _mgmt_df():
    return pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "变动人": "李保芳",
         "变动方向": "增持", "变动金额": 5000000.0},
        {"代码": "000001", "名称": "平安银行", "变动人": "谢永林",
         "变动方向": "减持", "变动金额": 2000000.0},
    ])


def _unlock_df():
    return pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "解禁股东": "集团A",
         "解禁数量": 1000000.0, "解禁日期": "2026-07-20"},
    ])


def test_collect_management_hold(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _mock_ak(lambda: _mgmt_df(),
                                           lambda symbol: pd.DataFrame()))
    recs, ok, err = sm.collect_management_hold("2026-07-19")
    assert ok, err
    assert len(recs) == 2
    r0 = next(r for r in recs if r["code"] == "600519")
    assert r0["channel"] == "高管增减持"
    assert r0["action"] == "增持"
    assert r0["actor"] == "李保芳"
    assert r0["amount"] == 5000000.0
    assert sm.CHANNEL_STATUS["高管增减持"]["ok"] is True


def test_collect_management_hold_empty_ok(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _mock_ak(lambda: pd.DataFrame(),
                                           lambda s: pd.DataFrame()))
    recs, ok, err = sm.collect_management_hold("2026-07-19")
    assert ok and recs == []  # 当日无增减持不算错


def test_collect_management_hold_fail(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)

    def _err():
        raise RuntimeError("remotedisconnected")

    monkeypatch.setattr(sm, "ak", _mock_ak(_err, lambda s: pd.DataFrame()))
    recs, ok, err = sm.collect_management_hold("2026-07-19")
    assert not ok and recs == []
    assert "被封" in err or "不可用" in err


def test_collect_share_unlock(monkeypatch):
    monkeypatch.setattr(sm, "_AK_OK", True)
    monkeypatch.setattr(sm, "ak", _mock_ak(lambda: pd.DataFrame(),
                                           lambda symbol: _unlock_df()))
    recs, ok, err = sm.collect_share_unlock("2026-07-19")
    assert ok, err
    assert len(recs) == 1
    r0 = recs[0]
    assert r0["channel"] == "限售解禁"
    assert r0["action"] == "解禁"
    assert r0["as_of"] == "2026-07-20"
    assert r0["amount"] == 1000000.0


def test_refresh_today_plan_has_new_channels():
    """refresh_today plan 含两新通道(检查 plan 构造,不实际跑)。"""
    import inspect
    src = inspect.getsource(sm.refresh_today)
    assert "collect_management_hold" in src
    assert "collect_share_unlock" in src
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_management_unlock.py -v`
Expected: FAIL(`collect_management_hold` 未定义 / `高管增减持` 不在 CHANNEL_STATUS)

- [ ] **Step 3: 实现**

在 `data/smart_money.py` 的 `CHANNEL_STATUS` dict 加两键(在"资金流"后):
```python
    "高管增减持": {"ok": False, "source": "", "err": "未采集", "at": ""},
    "限售解禁": {"ok": False, "source": "", "err": "未采集", "at": ""},
```

在 `collect_holders` 之后、`refresh_today` 之前加:
```python
# ------------------------------------------------------------------
# 高管增减持(按日期全市场)
# ------------------------------------------------------------------
def collect_management_hold(date: str) -> tuple[list[dict], bool, str]:
    """高管增减持(东财 stock_hold_management_em,按日期全市场)。
    actor=高管名, action=增持/减持, amount=变动金额, raw 存明细。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    try:
        df = ak.stock_hold_management_em()
    except Exception as e:
        _set_status("高管增减持", False, "", _friendly_err("高管增减持", e))
        return [], False, _friendly_err("高管增减持", e)
    if df is None or df.empty:
        _set_status("高管增减持", True, "", "当日无增减持")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["变动人", "高管名称", "姓名"])
    col_action = _first_col(df, ["变动方向", "增减"])
    col_amt = _first_col(df, ["变动金额", "成交金额", "变动数额"])
    recs = []
    for _, r in df.iterrows():
        act = str(r.get(col_action) or "")
        action = "增持" if "增持" in act else "减持" if "减持" in act else act
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "高管增减持", r.get(col_actor), action,
                        r.get(col_amt),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("高管增减持", True, "东财", "")
    return recs, True, ""


# ------------------------------------------------------------------
# 限售解禁(按月份)
# ------------------------------------------------------------------
def collect_share_unlock(date: str) -> tuple[list[dict], bool, str]:
    """限售解禁(东财 stock_share_change_em,按月份)。date 取所在月,
    拉当月解禁清单;actor=股东, action=解禁, amount=解禁数量, as_of=解禁日期。"""
    if not _AK_OK:
        return [], False, _AK_ERR
    month = date[:7]
    try:
        df = ak.stock_share_change_em(symbol=month)
    except Exception as e:
        _set_status("限售解禁", False, "", _friendly_err("限售解禁", e))
        return [], False, _friendly_err("限售解禁", e)
    if df is None or df.empty:
        _set_status("限售解禁", True, "", f"{month} 无解禁")
        return [], True, ""
    col_code = _first_col(df, ["代码", "股票代码", "code"])
    col_name = _first_col(df, ["名称", "股票简称", "name"])
    col_actor = _first_col(df, ["解禁股东", "股东名称"])
    col_amt = _first_col(df, ["解禁数量", "解禁股数", "实际解禁数量"])
    col_date = _first_col(df, ["解禁日期", "解禁时间", "公告日期"])
    recs = []
    for _, r in df.iterrows():
        recs.append(_rec(date, r.get(col_code), r.get(col_name), "股票",
                        "限售解禁", r.get(col_actor), "解禁",
                        r.get(col_amt), as_of=str(r.get(col_date) or ""),
                        raw={k: _clean(v) for k, v in r.items()}))
    _set_status("限售解禁", True, "东财", "")
    return recs, True, ""
```

`refresh_today` 的 `plan` 改(在"十大股东"项后加两通道):
```python
    plan = [("资金流", collect_fund_flow), ("北向", collect_northbound),
            ("龙虎榜", collect_dragon_tiger), ("十大股东", collect_holders),
            ("高管增减持", collect_management_hold),
            ("限售解禁", collect_share_unlock)]
```

注意:`collect_management_hold` 内 `_set_status` 第一参数必须与 CHANNEL_STATUS key 一致,用 `"高管增减持"`(测试断言 `CHANNEL_STATUS["高管增减持"]["ok"] is True`)。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_management_unlock.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add data/smart_money.py tests/test_management_unlock.py
git commit -m "feat(smart_money): 高管增减持/限售解禁两通道 + refresh_today 集成"
```

---

### Task 6: screener/smart_money.py 加 unlock_by_month 查询

**Files:**
- Modify: `screener/smart_money.py`
- Test: `tests/test_unlock_query.py`

**Interfaces:**
- Consumes: `data.db.query_rows`
- Produces: `unlock_by_month(month, code) -> dict`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_unlock_query.py
# -*- coding: utf-8 -*-
"""限售解禁按 as_of 月份查询测试。mock db.query_rows。"""
from screener import smart_money as sm_query


def test_unlock_by_month_filters_channel_and_as_of(monkeypatch):
    captured = {}

    def _q(table, where="", params=(), order_by="", limit=0):
        captured["where"] = where
        captured["params"] = params
        captured["order"] = order_by
        return [
            {"code": "600519", "name": "贵州茅台", "channel": "限售解禁",
             "as_of": "2026-07-20", "amount": 1000000.0},
            {"code": "000001", "name": "平安银行", "channel": "限售解禁",
             "as_of": "2026-07-25", "amount": 500000.0},
        ]

    from data import db
    monkeypatch.setattr(db, "query_rows", _q)
    res = sm_query.unlock_by_month(month="2026-07")
    assert "channel = ?" in captured["where"]
    assert "as_of LIKE ?" in captured["where"]
    assert "限售解禁" in captured["params"]
    assert "2026-07%" in captured["params"]
    assert captured["order"] == "as_of ASC"
    assert res["total"] == 2
    assert res["total_amount"] == 1500000.0
    assert res["month"] == "2026-07"


def test_unlock_by_month_with_code(monkeypatch):
    from data import db

    def _q(table, where="", params=(), order_by="", limit=0):
        assert "code = ?" in where
        assert "600519" in params
        return []

    monkeypatch.setattr(db, "query_rows", _q)
    res = sm_query.unlock_by_month(month="2026-07", code="600519")
    assert res["total"] == 0
    assert res["total_amount"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_unlock_query.py -v`
Expected: FAIL(`unlock_by_month` 未定义)

- [ ] **Step 3: 实现**

在 `screener/smart_money.py` 末尾(`top_by_amount` 之后)加:
```python
def unlock_by_month(month: str | None = None, code: str | None = None) -> dict:
    """限售解禁按 as_of 月份查(channel=限售解禁)。
    month 形如 2026-07;不传取当月。code 非空则再按 code 过滤。as_of 升序。

    合规:主力动向观察清单,机械归类,非荐股非买卖信号。"""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    where, params = ["channel = ?", "as_of LIKE ?"], ["限售解禁", f"{month}%"]
    if code:
        where.append("code = ?")
        params.append(code)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="as_of ASC", limit=0)
    total_amt = 0.0
    for r in rows:
        a = r.get("amount")
        if a is not None:
            total_amt += a
    return {"rows": rows, "total": len(rows),
            "month": month, "total_amount": total_amt}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_unlock_query.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add screener/smart_money.py tests/test_unlock_query.py
git commit -m "feat(screener): 限售解禁按 as_of 月份查询 unlock_by_month"
```

---

### Task 7: api/server.py 加 5 条新路由

**Files:**
- Modify: `api/server.py`
- Test: `tests/test_server_new_routes.py`

**Interfaces:**
- Consumes: `data.db.query_rows`,`screener.smart_money.top_by_amount`/`unlock_by_month`,`data.research.query_reports`/`fetch_comments`,`SM_CAND_DISCLAIMER`,`_wrap`
- Produces: `GET /api/st-list` `/api/management` `/api/share-unlock` `/api/research` `/api/comments`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_server_new_routes.py
# -*- coding: utf-8 -*-
"""新路由 smoke 测试:验证返回结构 + disclaimer。用 FastAPI TestClient。"""
from fastapi.testclient import TestClient

from api.server import app

client = TestClient(app)


def test_st_list_route(monkeypatch):
    from data import db

    def _q(table, where="", params=(), order_by="", limit=0):
        assert table == "st_list"
        return [{"code": "600250", "name": "*ST兴源", "st_type": "*ST"}]
    monkeypatch.setattr(db, "query_rows", _q)
    r = client.get("/api/st-list")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert "disclaimer" in body


def test_management_route(monkeypatch):
    from screener import smart_money as sm

    monkeypatch.setattr(sm, "top_by_amount",
                        lambda **kw: {"rows": [], "total": 0})
    r = client.get("/api/management?days=30")
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()


def test_share_unlock_route(monkeypatch):
    from screener import smart_money as sm

    monkeypatch.setattr(sm, "unlock_by_month",
                        lambda **kw: {"rows": [], "total": 0,
                                      "month": "2026-07", "total_amount": 0})
    r = client.get("/api/share-unlock?month=2026-07")
    assert r.status_code == 200
    body = r.json()
    assert "cand_disclaimer" in body
    assert body["month"] == "2026-07"


def test_research_route(monkeypatch):
    from data import research

    monkeypatch.setattr(research, "query_reports",
                        lambda **kw: {"rows": [], "total": 0})
    r = client.get("/api/research?days=30")
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()


def test_comments_route(monkeypatch):
    from data import research

    monkeypatch.setattr(research, "fetch_comments",
                        lambda code: ({"rows": []}, False))
    r = client.get("/api/comments?code=600519")
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_server_new_routes.py -v`
Expected: FAIL(404 路由不存在)

- [ ] **Step 3: 实现**

在 `api/server.py` 顶部 import 区确认/补:
```python
from data import db, research as research_data
from screener import smart_money
```
(`research_data` alias 避免与路由函数名 `research` 冲突;`smart_money` 若已 import 跳过)

在 `/api/smart-money/channels` 路由之后加:
```python
@app.get("/api/st-list")
def st_list():
    rows = db.query_rows("st_list", order_by="st_type, change_pct DESC")
    return _wrap({"rows": rows, "total": len(rows)})


@app.get("/api/management")
def management(days: int = 30):
    """高管增减持(主力动向观察清单口径)。"""
    res = smart_money.top_by_amount(days=days, channel="高管增减持", limit=100)
    return _wrap(res, {"cand_disclaimer": SM_CAND_DISCLAIMER})


@app.get("/api/share-unlock")
def share_unlock(month: str | None = None, code: str | None = None):
    """限售解禁按 as_of 月份查(主力动向观察清单口径)。"""
    res = smart_money.unlock_by_month(month=month, code=code)
    return _wrap(res, {"cand_disclaimer": SM_CAND_DISCLAIMER})


@app.get("/api/research")
def research(code: str | None = None, days: int = 30, limit: int = 200):
    """研报评级(机构视角机械汇总,非荐股)。"""
    res = research_data.query_reports(code=code, days=days, limit=limit)
    return _wrap(res, {"cand_disclaimer":
        "机构研报评级机械汇总，非荐股非买卖信号，盈亏自负。"})


@app.get("/api/comments")
def comments(code: str):
    """千股千评(机构视角机械汇总,非荐股)。"""
    res = research_data.fetch_comments(code)
    return _wrap(res, {"cand_disclaimer":
        "千股千评机构视角机械汇总，非荐股非买卖信号，盈亏自负。"})
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_server_new_routes.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add api/server.py tests/test_server_new_routes.py
git commit -m "feat(api): ST名单/高管增减持/限售解禁/研报/千股千评 5 路由"
```

---

### Task 8: backtest/buffett.py FCF 升级

**Files:**
- Modify: `backtest/buffett.py`
- Test: `tests/test_buffett_fcf.py`

**Interfaces:**
- Consumes: `data.fundamentals.fetch(code, "cashflow")`,现有 `fetch_abstract`/`_annual`/`_latest`/`_spot`
- Produces: `analyze()` 输出含 `ratios.fcf_source`;新增 `_pick_col_sum(df, candidates)`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_buffett_fcf.py
# -*- coding: utf-8 -*-
"""buffett FCF 升级测试:完整现金流表可得时用真实 FCF,失败降级摘要代理。"""
import pandas as pd

from backtest import buffett


def _cashflow_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "经营活动产生的现金流量净额": 1000.0,
        "购建固定资产、无形资产及其他长期资产支付的现金": 300.0,
    }])


def test_pick_col_sum_finds_annual():
    v = buffett._pick_col_sum(_cashflow_df(),
                              ["经营活动产生的现金流量净额"])
    assert v == 1000.0
    v2 = buffett._pick_col_sum(_cashflow_df(),
                               ["购建固定资产、无形资产及其他长期资产支付的现金"])
    assert v2 == 300.0


def test_pick_col_sum_none_on_empty():
    assert buffett._pick_col_sum(pd.DataFrame(), ["x"]) is None


def test_analyze_real_fcf(monkeypatch, tmp_path):
    """完整现金流表可得 → fcf_source=完整现金流表, fcf_proxy=经营-资本开支。"""
    from data import db
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()
    monkeypatch.setattr(buffett, "fetch_abstract",
                        lambda c: (pd.DataFrame(), False))
    monkeypatch.setattr(buffett, "_spot",
                        lambda c: {"code": c, "name": "X", "latest_price": 100.0})
    monkeypatch.setattr(buffett, "fundamentals",
                        type("M", (), {"fetch": staticmethod(
                            lambda code, src: (_cashflow_df(), False))}))
    monkeypatch.setattr(buffett, "_row_pairs",
                        lambda df, kw: [("2024-12-31", 800.0)] if "净利润" == kw
                        else [])
    res = buffett.analyze("600519")
    assert res["ratios"]["fcf_source"] == "完整现金流表(经营-资本开支)"
    assert res["ratios"]["fcf_proxy"] == 700.0  # 1000-300
    assert res["ratios"]["fcf_to_netincome"] == round(700.0 / 800.0, 2)


def test_analyze_fallback_to_abstract(monkeypatch, tmp_path):
    """完整现金流表失败 → 降级摘要代理。"""
    from data import db
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()

    def _abstract_pairs(df, kw):
        if kw == "经营现金流量净额":
            return [("2024-12-31", 900.0)]
        if kw == "净利润":
            return [("2024-12-31", 800.0)]
        return []
    monkeypatch.setattr(buffett, "fetch_abstract",
                        lambda c: (pd.DataFrame({"指标": ["x"]}), False))
    monkeypatch.setattr(buffett, "_spot",
                        lambda c: {"code": c, "name": "X", "latest_price": 100.0})
    monkeypatch.setattr(buffett, "fundamentals",
                        type("M", (), {"fetch": staticmethod(
                            lambda code, src: (None, False))}))
    monkeypatch.setattr(buffett, "_row_pairs", _abstract_pairs)
    res = buffett.analyze("600519")
    assert "摘要代理" in res["ratios"]["fcf_source"]
    assert res["ratios"]["fcf_proxy"] == 900.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_buffett_fcf.py -v`
Expected: FAIL(`_pick_col_sum` 未定义 / `ratios.fcf_source` 不存在)

- [ ] **Step 3: 实现**

在 `backtest/buffett.py` 顶部 import 区加:
```python
from data import fundamentals
```

在 `_spot` 函数之前加辅助:
```python
def _pick_col_sum(df: pd.DataFrame, candidates: list[str]) -> float | None:
    """从完整财报表(行=报告期,列=科目)取最近年报(报告期 endswith 1231)行的命中科目值。
    列名模糊匹配(contains),NaN→None。"""
    if df is None or df.empty:
        return None
    date_col = None
    for c in list(df.columns):
        if str(c) in ("报告期", "报告日期", "REPORT_DATE", "统计截止日期"):
            date_col = c
            break
    row = None
    if date_col is not None:
        for _, r in df.iterrows():
            if str(r.get(date_col) or "").endswith("1231"):
                row = r
                break
    if row is None:
        row = df.iloc[0]
    for col in df.columns:
        if col == date_col:
            continue
        cs = str(col)
        if any(k in cs for k in candidates):
            v = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
            if pd.notna(v):
                return float(v)
            return None
    return None
```

在 `analyze(code)` 函数里,`df, stale = fetch_abstract(code)` 与 `spot = _spot(code)` 之后、`res = {...}` 之后,加 best-effort 完整现金流表块:
```python
    # best-effort 完整现金流表,取真实 FCF(经营-资本开支)
    cf_df, cf_stale = fundamentals.fetch(code, "cashflow")
    real_fcf = None
    fcf_source = "摘要代理(经营现金流量净额)"
    if cf_df is not None and not cf_df.empty:
        ocf = _pick_col_sum(cf_df, ["经营活动产生的现金流量净额",
                                    "经营活动现金流量净额"])
        capex = _pick_col_sum(cf_df,
                              ["购建固定资产、无形资产及其他长期资产支付的现金",
                               "购建固定资产无形资产和其他长期资产支付的现金"])
        if ocf is not None:
            real_fcf = (ocf - capex) if capex is not None else ocf
            fcf_source = "完整现金流表(经营-资本开支)"
            if cf_stale:
                res["stale_data"] = True
```

`ratios` 块的 FCF 部分(替换原 `if ocf_ann and ni_ann and ni_ann:` 块)改为:
```python
    ocf_ann = _annual(ocf_p, latest_only=True)
    ni_ann = _annual(ni_p, latest_only=True)
    if real_fcf is not None:
        ratios["fcf_proxy"] = round(float(real_fcf), 2)
        ratios["fcf_source"] = fcf_source
        if ni_ann:
            ratios["fcf_to_netincome"] = round(float(real_fcf / ni_ann), 2)
    elif ocf_ann and ni_ann:
        ratios["fcf_proxy"] = round(float(ocf_ann), 2)
        ratios["fcf_source"] = fcf_source
        ratios["fcf_to_netincome"] = round(float(ocf_ann / ni_ann), 2)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_buffett_fcf.py -v`
Expected: 4 passed

- [ ] **Step 5: 回归现有 buffett 测试**

Run: `python -m pytest tests/test_buffett_timeout.py -q`
Expected: PASS(不破坏现有)

- [ ] **Step 6: 提交**

```bash
git add backtest/buffett.py tests/test_buffett_fcf.py
git commit -m "feat(buffett): 完整现金流表升级真实 FCF(摘要代理降级)"
```

---

### Task 9: 前端 buffett FCF 来源标签 + 主力动向通道循环

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `/api/buffett` 响应含 `ratios.fcf_source`;`/api/smart-money/channels` 响应(6 通道)

**说明**:前端无单测;手动验证 + 代码检查。

- [ ] **Step 1: 定位 buffett 卡片渲染区**

用 Grep 工具搜 `fcf_proxy\|ratios\.` 于 `web/index.html`,确认 buffett 卡片渲染 `ratios.*` 字段位置,以及通道面板是否 `Object.keys(channels)` 循环还是硬编码 4 通道。

- [ ] **Step 2: 实现 fcf_source 标签**

在 buffett 卡片渲染 `ratios.fcf_proxy` 处旁加来源标签。例如找到 `fcf_proxy` 显示行,改为:
```html
<div>FCF: {{r.ratios.fcf_proxy}} <span class="tag">{{r.ratios.fcf_source || ''}}</span></div>
```
LABEL 字典(若前端有 LABEL 映射)补:`fcf_source: "FCF来源"`。

- [ ] **Step 3: 通道面板循环确认/修正**

若主力动向 tab 通道面板是硬编码 4 通道,改为从 `channels` 响应 keys 动态循环,使"高管增减持"/"限售解禁"灰点自动显示:
```javascript
Object.keys(channels).forEach(ch => { /* 渲染灰点 */ });
```

- [ ] **Step 4: 手动验证**

Run: `uvicorn api.server:app --port 8000`(或 docker compose up)
浏览器开 `http://localhost:8000/web/index.html`:
1. "优质筛选"tab → buffett 卡片显示"FCF来源"标签
2. "主力动向"tab → 通道面板显示 6 通道(含新增 2 个灰点)

- [ ] **Step 5: 提交**

```bash
git add web/index.html
git commit -m "feat(web): buffett FCF 来源标签 + 主力动向 6 通道循环"
```

---

### Task 10: CLAUDE.md 更新 + 全量集成验证

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 全部前述任务

- [ ] **Step 1: 更新 CLAUDE.md 路由速查**

在 `### 路由速查（server.py 全量）` 的 `/api/smart-money/channels` 之后补 5 条新路由说明:
```
→ `/api/st-list`（ST 全名单快照，默认 disclaimer）→ `/api/management`（高管增减持，channel=高管增减持，附 `cand_disclaimer`）→ `/api/share-unlock`（限售解禁按 as_of 月份查，附 `cand_disclaimer`）→ `/api/research`（研报评级，近 N 日，附 `cand_disclaimer`："机构研报评级机械汇总，非荐股非买卖信号"）→ `/api/comments`（千股千评 per-code 按需+缓存 source=comments，附 `cand_disclaimer`）
```

在 `data/` 模块描述补:`fundamentals.py`(完整三大财报按需+缓存)、`research.py`(研报+千股千评);`smart_money.py` 通道数 4→6。

在 `backtest/` 描述补:buffett FCF 优先用 `fundamentals_cache` source=cashflow 真实 FCF(经营-资本开支),失败降级摘要代理;`fcf_source` 字段标注来源。

- [ ] **Step 2: 更新改动检查清单**

在 `## 改动检查清单` 补一条:
```
- 新增 AKShare 采集源 → 列表型(stock_zh_a_st_em/stock_hold_management_em/stock_share_change_em/stock_research_report_em)进 refresh_all/refresh_today;per-code(stock_*_sheet_by_report_em/stock_comment_detail)走 fundamentals.fetch + fundamentals_cache 缓存(7天 TTL,超时降级 stale);复用 `_first_col` 候选名容错,单测 mock 验证字段映射。
```

- [ ] **Step 3: 全量测试回归**

Run: `python -m pytest tests/ -q`
Expected: 全部 PASS(含现有 + 新增)

- [ ] **Step 4: 集成验证(本地或 Docker)**

Run: `curl -X POST http://localhost:8000/api/refresh` → 检查 `counts` 含 `st_list` + `research_report`
Run: `curl -X POST http://localhost:8000/api/smart-money/refresh` → 检查 `channels` 含 6 通道
Run: `curl "http://localhost:8000/api/management?days=30"` / `/api/share-unlock?month=2026-07` / `/api/research` / `/api/comments?code=600519` / `/api/st-list` → 200 + 对应 disclaimer

(东财被封时这些路由返回空 rows + 灰点状态,不崩;`SCREENER_HTTPS_PROXY` 代理后填充)

- [ ] **Step 5: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 更新路由速查/检查清单覆盖 AKShare 扩展源"
```

---

## Self-Review

**Spec coverage:**
- §3 编排方案A → Task 4/5 进 refresh,Task 2/3 per-code 缓存 ✓
- §4 文件清单 → Task 1-10 逐一覆盖 ✓
- §5 schema → Task 1 ✓
- §6 采集层 → Task 2/3/4/5 ✓
- §7 API → Task 6/7 ✓
- §8 buffett FCF → Task 8 ✓
- §9 前端 → Task 9 ✓
- §10 测试 → Task 2-8 内嵌 + Task 10 Step 3 回归 ✓
- §11 风险降级 → 各采集函数 err 路径覆盖 ✓
- §12 范围排除 → 无 `/api/fundamentals` 路由(Task 2 只内部用)✓

**Placeholder scan:** 无 TBD/TODO;`...` 已全部展开为完整代码。Task 9 前端为手动验证(无单测,已说明)。

**Type consistency:**
- `fetch(code, source)` 签名 Task 2 定义,Task 3/8 消费一致 ✓
- `unlock_by_month(month, code)` Task 6 定义,Task 7 消费一致 ✓
- `query_reports`/`fetch_comments` Task 3 定义,Task 7 消费一致 ✓
- `_pick_col_sum(df, candidates)` Task 8 定义并在同任务测试 ✓
- `CHANNEL_STATUS["高管增减持"]` Task 5 实现 + 测试统一(Step 3 已注明 `_set_status` 用 `"高管增减持"`)✓

无未对齐项。

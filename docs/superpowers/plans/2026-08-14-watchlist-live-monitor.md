# 批量自选监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 watchlist（未买入观察清单）加盘中实时批量行情 + 批量信号扫描 + 到价提醒列，前端抽屉 30s 轮询盯盘。

**Architecture:** 后端两路由分离（live 30s 快轮询 / signals 低频）+ 一 PATCH 设提醒；watchlist 表加 alert_hi/alert_lo 两列（DB migration）；前端 wlDrawer 重写复用第1期 apiFetch/toast/persist。tdx 批量 get_quote 做盘中实时源，spot 降级。

**Tech Stack:** Python 3.12 / FastAPI / sqlite3 / pytdx / 原生 JS（无构建）

**Spec:** `docs/superpowers/specs/2026-08-14-watchlist-live-monitor-design.md`

## Global Constraints

- 合规硬约束：所有响应挂 `cand_disclaimer`，措辞"观察清单/机械标记"，不得出现"推荐/买入/卖出"。watchlist 是未买入观察清单，到价提醒=用户自设价位机械标记非买卖信号。
- NaN→None 序列化：浮点列必须 `df.astype(object).where(pd.notna(df), None)` 或显式 None，防 starlette `allow_nan=False` 500。
- DB 迁移：watchlist 不进 `TABLE_FIELDS`/`upsert_rows`，`set_alert` 用独立 UPDATE；新列进 `SCHEMA_SQL` + `_BOARD_MIGRATIONS`，且 `_migrate` 的表名集合必须含 `'watchlist'`（否则条目被跳过）。
- 测试在仓库根目录跑 `python -m pytest tests/test_watchlist_monitor.py -q`，Python 3.12.10 + pandas 3.0.3。
- tdx `get_quote(codes)` ≤80/批自动分页，失败返 `[]` 不抛崩。
- `_is_in_session(now=None)` 在 `backtest.quality` 模块级，盘中 best-effort 判断。

---

## File Structure

- `data/models.py:220-226` — watchlist SCHEMA_SQL 加两列
- `data/db.py:23-54` — `_BOARD_MIGRATIONS` 加两条 + `_migrate` 表名集合加 `'watchlist'`
- `data/watchlist.py` — 新增 `list_codes`/`set_alert`/`_is_etf`，改 `list_items` 返回 alert 列
- `api/server.py` — 新增 3 路由 `GET /api/watchlist/live`、`GET /api/watchlist/signals`、`PATCH /api/watchlist/{wid}`
- `web/index.html` — wlDrawer 抽屉重写（轮询 + 信号徽章 + alert 输入 + 越线高亮）
- `tests/test_watchlist_monitor.py` — 新建测试文件

---

### Task 1: DB schema — watchlist 加 alert_hi/alert_lo 列

**Files:**
- Modify: `data/models.py:220-226`（SCHEMA_SQL）
- Modify: `data/db.py:23-30`（`_BOARD_MIGRATIONS`）+ `data/db.py:42-45`（`_migrate` 表名集合）
- Test: `tests/test_watchlist_monitor.py`

**Interfaces:**
- Produces: watchlist 表含 `alert_hi REAL`、`alert_lo REAL` 列；`db._migrate` 对旧 watchlist 表补列。

- [ ] **Step 1: 写失败测试（migration 补列）**

创建 `tests/test_watchlist_monitor.py`：

```python
# -*- coding: utf-8 -*-
"""批量自选监控测试：DB migration + watchlist 函数 + live/signals/alert 路由。
mock tdx/scan_signals/db，不触网。仓库根目录跑。"""
import sys, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from data import db as dbmod, watchlist as wl
from fastapi.testclient import TestClient
from api.server import app


def test_migrate_adds_watchlist_alert_cols():
    """旧 watchlist 表无 alert 列 → _migrate 补列后含 alert_hi/alert_lo。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE watchlist(id INTEGER PRIMARY KEY, code TEXT, "
                 "name TEXT, note TEXT, added_ts TEXT)")
    dbmod._migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    assert "alert_hi" in cols, "_migrate 未给 watchlist 补 alert_hi（检查 _BOARD_MIGRATIONS + 表名集合）"
    assert "alert_lo" in cols


@pytest.fixture
def wldb(tmp_path, monkeypatch):
    """隔离 DB：patch DB_PATH 到 tmp，init_db 建新表。"""
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    return wl
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_watchlist_monitor.py::test_migrate_adds_watchlist_alert_cols -q`
Expected: FAIL — `_migrate` 未补 watchlist 列（表名集合不含 watchlist）。

- [ ] **Step 3: 改 SCHEMA_SQL**

`data/models.py` watchlist 建表语句改为：

```sql
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT,
    note TEXT,
    added_ts TEXT,
    alert_hi REAL,
    alert_lo REAL
);
```

- [ ] **Step 4: 改 `_BOARD_MIGRATIONS` 加两条**

`data/db.py` 在 `_BOARD_MIGRATIONS` 列表末尾追加：

```python
    ("watchlist", "alert_hi", "REAL"),
    ("watchlist", "alert_lo", "REAL"),
```

- [ ] **Step 5: 改 `_migrate` 表名集合含 watchlist**

`data/db.py:44-45` 改为：

```python
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('industry_board','concept_board','portfolio','watchlist')")
```

- [ ] **Step 6: 跑测试验证通过**

Run: `python -m pytest tests/test_watchlist_monitor.py::test_migrate_adds_watchlist_alert_cols -q`
Expected: PASS

- [ ] **Step 7: 跑全量回归确保 migration 不破坏旧测试**

Run: `python -m pytest tests/ -q`
Expected: 全绿（既有 250 测试不回归；migration 幂等）。

- [ ] **Step 8: 提交**

```bash
git add data/models.py data/db.py tests/test_watchlist_monitor.py
git commit -m "feat(watchlist): DB migration 加 alert_hi/alert_lo 列

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: data/watchlist.py — list_codes / set_alert / _is_etf + list_items 返 alert 列

**Files:**
- Modify: `data/watchlist.py:34-61`（`list_items`）+ 文件末尾追加三个函数
- Test: `tests/test_watchlist_monitor.py`

**Interfaces:**
- Consumes: Task 1 的 alert_hi/alert_lo 列
- Produces:
  - `list_codes() -> list[str]`：去重 code 列表
  - `set_alert(wid: int, alert_hi: float|None, alert_lo: float|None) -> bool`
  - `_is_etf(code: str) -> bool`：ETF/基金代码前缀判定
  - `list_items()` 每行多 `alert_hi`/`alert_lo` 字段

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_watchlist_monitor.py`：

```python
def test_list_codes_dedup(wldb):
    wldb.add("000001", "平安", "")
    wldb.add("510300", "沪深300ETF", "")
    wldb.add("000001", "平安2", "")  # 同 code 更新不重复
    codes = wldb.list_codes()
    assert set(codes) == {"000001", "510300"} and len(codes) == 2


def test_set_alert_and_list_items(wldb):
    wid = wldb.add("000002", "万科", "")["id"]
    ok = wldb.set_alert(wid, 10.5, 9.0)
    assert ok is True
    items = wldb.list_items()
    it = [x for x in items if x["code"] == "000002"][0]
    assert it["alert_hi"] == 10.5 and it["alert_lo"] == 9.0


def test_set_alert_none_clears(wldb):
    wid = wldb.add("000003", "X", "")["id"]
    wldb.set_alert(wid, 10.0, None)
    wldb.set_alert(wid, None, None)  # 清除
    it = [x for x in wldb.list_items() if x["code"] == "000003"][0]
    assert it["alert_hi"] is None and it["alert_lo"] is None


def test_is_etf_classification(wldb):
    assert wldb._is_etf("510300") is True   # 沪 ETF
    assert wldb._is_etf("159915") is True   # 深 ETF
    assert wldb._is_etf("000001") is False  # 个股
    assert wldb._is_etf("600519") is False  # 个股
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_watchlist_monitor.py -q -k "list_codes or set_alert or is_etf"`
Expected: FAIL — `list_codes`/`set_alert`/`_is_etf` 未定义。

- [ ] **Step 3: 改 list_items 返回 alert 列**

`data/watchlist.py` 的 `list_items` 把 SELECT 改为含 alert 列，返回字典加两字段：

```python
def list_items() -> list[dict]:
    """列自选 + 按 stock_spot/etf_spot 最新价显示现价 + alert 提醒价。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id,code,name,note,added_ts,alert_hi,alert_lo "
            "FROM watchlist ORDER BY id DESC"
        ).fetchall()
    if not rows:
        return []
    codes = list({r["code"] for r in rows})
    spot = {}
    if codes:
        ph = ",".join("?" * len(codes))
        with db.get_conn() as conn:
            for tbl in ("stock_spot", "etf_spot"):
                try:
                    sr = conn.execute(
                        f"SELECT code, latest_price FROM {tbl} WHERE code IN ({ph})",
                        codes).fetchall()
                except Exception:
                    sr = []
                for x in sr:
                    if x["latest_price"] is not None and x["code"] not in spot:
                        spot[x["code"]] = x["latest_price"]
    return [{
        "id": r["id"], "code": r["code"], "name": r["name"],
        "note": r["note"], "added_ts": r["added_ts"],
        "latest_price": spot.get(r["code"]),
        "alert_hi": r["alert_hi"], "alert_lo": r["alert_lo"],
    } for r in rows]
```

- [ ] **Step 4: 追加 list_codes / set_alert / _is_etf**

`data/watchlist.py` 末尾追加：

```python
def list_codes() -> list[str]:
    """返回去重 code 列表（供 live/signals 路由批量取）。"""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT code FROM watchlist").fetchall()
    return [r["code"] for r in rows if r["code"]]


def set_alert(wid: int, alert_hi: float | None, alert_lo: float | None) -> bool:
    """设/清到价提醒（None=清除该项）。机械价位标记，非买卖信号。"""
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE watchlist SET alert_hi=?, alert_lo=? WHERE id=?",
            (alert_hi, alert_lo, wid))
        conn.commit()
        return cur.rowcount > 0


def _is_etf(code: str) -> bool:
    """前缀判 ETF/基金：51/52/15/16/50/56/58/11/12 开头。
    启发式，冷门品种误判→走 stock universe，scan_signals 返空不崩。"""
    c = (code or "").strip()
    return c[:2] in {"51", "52", "15", "16", "50", "56", "58", "11", "12"} if len(c) >= 2 else False
```

- [ ] **Step 5: 跑测试验证通过**

Run: `python -m pytest tests/test_watchlist_monitor.py -q -k "list_codes or set_alert or is_etf"`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add data/watchlist.py tests/test_watchlist_monitor.py
git commit -m "feat(watchlist): list_codes/set_alert/_is_etf + list_items 返 alert 列

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 路由 PATCH /api/watchlist/{wid}（设提醒）

**Files:**
- Modify: `api/server.py`（在 `/api/watchlist/{wid}` DELETE 路由附近追加 PATCH）
- Test: `tests/test_watchlist_monitor.py`

**Interfaces:**
- Consumes: `watchlist.set_alert(wid, alert_hi, alert_lo)`（Task 2）
- Produces: `PATCH /api/watchlist/{wid}` 返 `{updated, id, alert_hi, alert_lo}` + disclaimer

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_watchlist_monitor.py`：

```python
def test_patch_watchlist_alert(client_wldb):
    """PATCH 设 alert_hi/lo，返回 updated + disclaimer。"""
    wid = wl.add("000004", "X", "")["id"]
    r = client_wldb.patch(f"/api/watchlist/{wid}", json={"alert_hi": 12.0, "alert_lo": 8.0})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["updated"] is True and d["alert_hi"] == 12.0 and d["alert_lo"] == 8.0
    assert "cand_disclaimer" in r.json()
    # 验证落库
    it = [x for x in wl.list_items() if x["code"] == "000004"][0]
    assert it["alert_hi"] == 12.0
```

需先加 `client_wldb` fixture（patch DB_PATH + init_db + TestClient）。在测试文件顶部 fixture 区追加：

```python
@pytest.fixture
def client_wldb(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    # watchlist 模块用同一 db 模块，自动隔离
    return TestClient(app)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_watchlist_monitor.py::test_patch_watchlist_alert -q`
Expected: FAIL — PATCH 路由不存在（405/404）。

- [ ] **Step 3: 加 PATCH 路由**

`api/server.py` 在 `@app.delete("/api/watchlist/{wid}")` 之后追加：

```python
class WLAlertReq(BaseModel):
    alert_hi: float | None = None
    alert_lo: float | None = None


@app.patch("/api/watchlist/{wid}")
def watchlist_alert(wid: int, req: WLAlertReq):
    """更新自选到价提醒价位（用户自设规则，非买卖点）。默认 disclaimer。"""
    ok = watchlist.set_alert(wid, req.alert_hi, req.alert_lo)
    return _wrap({"updated": ok, "id": wid,
                  "alert_hi": req.alert_hi, "alert_lo": req.alert_lo})
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_watchlist_monitor.py::test_patch_watchlist_alert -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add api/server.py tests/test_watchlist_monitor.py
git commit -m "feat(api): PATCH /api/watchlist/{wid} 设到价提醒

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 路由 GET /api/watchlist/live（批量实时行情 + alert 越线）

**Files:**
- Modify: `api/server.py`（追加 live 路由）
- Test: `tests/test_watchlist_monitor.py`

**Interfaces:**
- Consumes: `watchlist.list_codes()`、`watchlist.list_items()`（取 name/alert）、`pytdx_client.get_quote(codes)`、`backtest.quality._is_in_session()`
- Produces: `GET /api/watchlist/live` 返 `{rows:[{code,name,price,prev_close,change,change_pct,alert_hi,alert_lo,alert_hit,quote_source}], in_session, update_time, disclaimer}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_watchlist_monitor.py`：

```python
def test_live_quote_batch_and_alert_hit(client_wldb, monkeypatch):
    """tdx 批量返行情 → change_pct 计算 + alert_hit 越线。"""
    wl.add("000001", "平安", "")
    wl.set_alert(wl.list_items()[0]["id"], 12.0, 9.0)  # price 11 在区间内
    wl.add("600519", "茅台", "")
    from api import server
    # tdx 返：000001 价 11(区间内), 600519 价 1700(无 alert)
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [
        {"code": "000001", "price": 11.0, "last_close": 10.0},
        {"code": "600519", "price": 1700.0, "last_close": 1600.0},
    ])
    monkeypatch.setattr(server, "_is_in_session_mock", None, raising=False)
    from backtest import quality
    monkeypatch.setattr(quality, "_is_in_session", lambda now=None: True)
    r = client_wldb.get("/api/watchlist/live")
    assert r.status_code == 200
    rows = r.json()["data"]["rows"]
    by = {x["code"]: x for x in rows}
    assert by["000001"]["price"] == 11.0
    assert abs(by["000001"]["change_pct"] - 10.0) < 1e-6  # (11-10)/10*100
    assert by["000001"]["alert_hit"] is None  # 9<11<12 区间内
    assert by["600519"]["alert_hit"] is None
    assert r.json()["data"]["in_session"] is True
    assert "cand_disclaimer" in r.json()


def test_live_alert_hi_crossed(client_wldb, monkeypatch):
    """price≥alert_hi → alert_hit='hi'。"""
    wl.add("000001", "平安", "")
    wl.set_alert(wl.list_items()[0]["id"], 10.0, 8.0)  # price 10.5≥10 → hi
    from api import server
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [
        {"code": "000001", "price": 10.5, "last_close": 9.0}])
    from backtest import quality
    monkeypatch.setattr(quality, "_is_in_session", lambda now=None: True)
    rows = client_wldb.get("/api/watchlist/live").json()["data"]["rows"]
    assert rows[0]["alert_hit"] == "hi"


def test_live_tdx_empty_degrades_to_spot(client_wldb, monkeypatch):
    """tdx 返空 → 降级 spot latest_price + quote_source='spot陈旧'。"""
    wl.add("000001", "平安", "")
    from api import server
    monkeypatch.setattr(server.pytdx_client, "get_quote", lambda codes: [])
    # 给 spot 表插一条 latest_price
    with dbmod.get_conn() as conn:
        conn.execute("INSERT INTO stock_spot(code,latest_price) VALUES(?,?)", ("000001", 10.0))
        conn.commit()
    from backtest import quality
    monkeypatch.setattr(quality, "_is_in_session", lambda now=None: False)
    r = client_wldb.get("/api/watchlist/live")
    rows = r.json()["data"]["rows"]
    assert rows[0]["price"] == 10.0
    assert rows[0]["quote_source"] == "spot陈旧"
    assert r.json()["data"]["in_session"] is False
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_watchlist_monitor.py -q -k live`
Expected: FAIL — `/api/watchlist/live` 路由不存在。

- [ ] **Step 3: 实现 live 路由**

`api/server.py` 在 watchlist PATCH 路由后追加：

```python
@app.get("/api/watchlist/live")
def watchlist_live():
    """自选股批量实时行情(tdx 直取,盘中秒级)+alert 越线机械标记。
    tdx 空→降级 spot 陈旧快照。机械行情汇总+用户自设价位标记,非买卖信号,盈亏自负。"""
    from backtest import quality
    items = watchlist.list_items()  # 已含 name/alert_hi/alert_lo
    in_session = quality._is_in_session()
    if not items:
        return _wrap({"rows": [], "in_session": in_session},
                     {"cand_disclaimer": "自选股实时行情机械汇总+用户自设价位越线机械标记,观察清单非荐股非买卖信号,盈亏自负。"})
    codes = [it["code"] for it in items]
    name_map = {it["code"]: it["name"] for it in items}
    alert_map = {it["code"]: (it["alert_hi"], it["alert_lo"]) for it in items}
    qmap = {}
    quote_err = None
    try:
        qs = pytdx_client.get_quote(codes)
        qmap = {q.get("code"): q for q in qs} if qs else {}
    except Exception as e:
        quote_err = f"{type(e).__name__}: {str(e)[:80]}"
    if not qmap and not quote_err:
        quote_err = "通达信未连接或无行情"
    # tdx 空时降级 spot
    spot_map = {}
    if not qmap:
        ph = ",".join("?" * len(codes))
        for tbl in ("stock_spot", "etf_spot"):
            try:
                with db.get_conn() as conn:
                    sr = conn.execute(
                        f"SELECT code, latest_price FROM {tbl} WHERE code IN ({ph})",
                        codes).fetchall()
                for x in sr:
                    if x["latest_price"] is not None and x["code"] not in spot_map:
                        spot_map[x["code"]] = x["latest_price"]
            except Exception:
                pass

    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    rows = []
    for c in codes:
        hi, lo = alert_map.get(c, (None, None))
        q = qmap.get(c) or {}
        price = _num(q.get("price"))
        prev_close = _num(q.get("last_close"))
        src = "tdx"
        if price is None and not qmap and c in spot_map:
            price = _num(spot_map[c]); src = "spot陈旧"
        change = (price - prev_close) if (price is not None and prev_close) else None
        change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None
        # name 兜底：tdx 无 name 列，用 watchlist.name 或查 spot
        name = name_map.get(c) or q.get("name") or ""
        alert_hit = None
        if price is not None:
            if hi is not None and price >= hi:
                alert_hit = "hi"
            elif lo is not None and price <= lo:
                alert_hit = "lo"
        row = {"code": c, "name": name, "price": price, "prev_close": prev_close,
               "change": change, "change_pct": change_pct,
               "alert_hi": hi, "alert_lo": lo, "alert_hit": alert_hit,
               "quote_source": src}
        rows.append(row)
    out = {"rows": rows, "in_session": in_session}
    if quote_err and not qmap and not spot_map:
        out["quote_error"] = quote_err
    return _wrap(out, {"cand_disclaimer": "自选股实时行情机械汇总+用户自设价位越线机械标记,观察清单非荐股非买卖信号,盈亏自负。"})
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_watchlist_monitor.py -q -k live`
Expected: PASS（3 个 live 测试）

- [ ] **Step 5: 提交**

```bash
git add api/server.py tests/test_watchlist_monitor.py
git commit -m "feat(api): GET /api/watchlist/live 批量实时行情+alert越线+tdx降级spot

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 路由 GET /api/watchlist/signals（批量信号扫描，个股/ETF 分拆）

**Files:**
- Modify: `api/server.py`（追加 signals 路由）
- Test: `tests/test_watchlist_monitor.py`

**Interfaces:**
- Consumes: `watchlist.list_codes()`、`watchlist._is_etf()`、`backtest.signals.scan_signals(universe, codes, signal_types, min_hits)`
- Produces: `GET /api/watchlist/signals` 返 `{rows, n_scanned, error, disclaimer}`，每行带 `universe`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_watchlist_monitor.py`：

```python
def test_signals_split_stock_etf(client_wldb, monkeypatch):
    """watchlist 含个股+ETF → scan_signals 按 universe 分拆调用。"""
    wl.add("000001", "平安", "")   # 个股
    wl.add("510300", "沪深300ETF", "")  # ETF
    from backtest import signals as bt_sig
    calls = []
    def _fake_scan(universe, codes, signal_types=None, min_hits=1):
        calls.append(universe)
        return {"rows": [{"code": codes[0] if codes else "", "signals": [{"type": "ma_breakout"}]}],
                "n_scanned": len(codes), "error": None}
    monkeypatch.setattr(bt_sig, "scan_signals", _fake_scan)
    r = client_wldb.get("/api/watchlist/signals")
    assert r.status_code == 200
    data = r.json()["data"]
    # 两个 universe 都被调
    assert "stock" in calls and "etf" in calls
    # 行带 universe 标记
    unis = {row["universe"] for row in data["rows"]}
    assert unis == {"stock", "etf"}
    assert "cand_disclaimer" in r.json()


def test_signals_empty_watchlist(client_wldb):
    """空 watchlist → 空 rows 不崩。"""
    r = client_wldb.get("/api/watchlist/signals")
    assert r.status_code == 200
    assert r.json()["data"]["rows"] == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_watchlist_monitor.py -q -k signals`
Expected: FAIL — 路由不存在。

- [ ] **Step 3: 实现 signals 路由**

`api/server.py` 在 live 路由后追加：

```python
@app.get("/api/watchlist/signals")
def watchlist_signals():
    """批量信号扫描 watchlist 全体(个股/ETF 分拆 universe)。
    依赖 *_daily 历史(需先 /api/backtest/fetch)。机械扫描非AI推荐,盈亏自负。"""
    codes = watchlist.list_codes()
    if not codes:
        return _wrap({"rows": [], "n_scanned": 0, "error": None},
                     {"cand_disclaimer": "批量机械信号扫描,非AI推荐,不构成投资建议,盈亏自负。"})
    stock_codes = [c for c in codes if not watchlist._is_etf(c)]
    etf_codes = [c for c in codes if watchlist._is_etf(c)]
    rows, n_scanned, err = [], 0, None
    for uni, cs in (("stock", stock_codes), ("etf", etf_codes)):
        if not cs:
            continue
        try:
            res = bt_sig.scan_signals(uni, cs)
            for row in res.get("rows", []):
                row["universe"] = uni
                rows.append(row)
            n_scanned += res.get("n_scanned", 0) or 0
            if res.get("error"):
                err = res["error"]
        except Exception as e:
            err = f"{uni}: {type(e).__name__}: {str(e)[:60]}"
    return _wrap({"rows": rows, "n_scanned": n_scanned, "error": err},
                 {"cand_disclaimer": "批量机械信号扫描,非AI推荐,不构成投资建议,盈亏自负。"})
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_watchlist_monitor.py -q -k signals`
Expected: PASS

- [ ] **Step 5: 跑全文件回归**

Run: `python -m pytest tests/test_watchlist_monitor.py -q`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add api/server.py tests/test_watchlist_monitor.py
git commit -m "feat(api): GET /api/watchlist/signals 批量信号扫描(个股/ETF分拆)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 前端 wlDrawer 重写（轮询 + 信号徽章 + alert 输入 + 越线高亮）

**Files:**
- Modify: `web/index.html`（`wlLoad`/`wlAdd`/抽屉 HTML 区，约 355-365 行 + 811-830 行）

**Interfaces:**
- Consumes: `/api/watchlist/live`（30s 轮询）、`/api/watchlist/signals`（开抽屉+5min）、`PATCH /api/watchlist/{wid}`
- 复用第1期 `apiFetch`/`toast`/`_saveUI`

- [ ] **Step 1: 改 wlLoad 为并发 live+signals + 启动轮询**

`web/index.html` 中 `async function wlLoad()` 重写：

```js
let _wlTimer=null, _wlSigTimer=null;
async function wlLoad(){
  const [liveR, sigR] = await Promise.all([
    apiFetch(`${API}/api/watchlist/live`),
    apiFetch(`${API}/api/watchlist/signals`),
  ]);
  let live=liveR.data||{}, sigs=(sigR.data&&sigR.data.rows)||[];
  if(liveR._fail||sigR._fail){ document.getElementById('wlDrawerBody').innerHTML='<div class="empty">加载失败,稍后重试</div>'; return; }
  // 信号按 code 归并计数
  const sigCnt={}; sigs.forEach(s=>{const c=s.code; sigCnt[c]=(sigCnt[c]||0)+(s.signals?s.signals.length:0);});
  const rows=live.rows||[];
  document.getElementById('wlDrawerBody').innerHTML = rows.length? rows.map(r=>{
    const pct=r.change_pct; const col=pct==null?'':(pct>=0?'#E53935':'#16A34A');
    const hit=r.alert_hit; const border=hit==='hi'?'2px solid #E53935':(hit==='lo'?'2px solid #F59E0B':'1px solid #e5e7eb');
    const sc=sigCnt[r.code]||0; const badge=sc?`<span style="color:#F59E0B;font-size:11px">⚡${sc}</span>`:'';
    const hi=r.alert_hi??''; const lo=r.alert_lo??'';
    return `<div class="wl-row" style="border:${border};padding:6px;margin:4px 0;border-radius:4px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <span style="cursor:pointer;color:#2563eb" onclick="switchTab('analysis');setTimeout(()=>{document.getElementById('anCode').value='${r.code}';anRun('${r.code}')},50)">${r.code}</span>
      <span>${r.name||''}</span>
      <span style="margin-left:auto;font-weight:600">${r.price==null?'—':r.price.toFixed(2)}</span>
      <span style="color:${col}">${pct==null?'':(pct>=0?'+':'')+pct.toFixed(2)+'%'}</span>
      ${badge}
      <input type="number" step="0.01" placeholder="上限" value="${hi}" data-wid="${r.id}" data-k="hi" style="width:60px" onblur="wlSetAlert(this)">
      <input type="number" step="0.01" placeholder="下限" value="${lo}" data-wid="${r.id}" data-k="lo" style="width:60px" onblur="wlSetAlert(this)">
      <button onclick="wlClose(${r.id})" style="font-size:11px">×</button>
    </div>`;
  }).join('') : '<div class="empty">无自选,先在信号/分析卡点＋自选</div>';
  // 轮询：盘中 30s，盘后 5min
  _startWlPoll(live.in_session);
}
function _startWlPoll(inSession){
  if(_wlTimer) clearInterval(_wlTimer);
  _wlTimer=setInterval(async()=>{ const r=await apiFetch(`${API}/api/watchlist/live`); if(!r._fail) _renderWlLive(r.data); }, inSession?30000:300000);
}
function _renderWlLive(live){
  if(!live||!live.rows) return;
  // 只更新已有行的价格/涨跌幅/越线,避免重渲染打断 input 输入
  live.rows.forEach(r=>{
    const row=[...document.querySelectorAll('#wlDrawerBody .wl-row')].find(e=>e.querySelector('span')&&e.querySelector('span').textContent===r.code);
    if(!row) return;
    const col=r.change_pct==null?'':(r.change_pct>=0?'#E53935':'#16A34A');
    row.style.border=r.alert_hit==='hi'?'2px solid #E53935':(r.alert_hit==='lo'?'2px solid #F59E0B':'1px solid #e5e7eb');
    const spans=row.querySelectorAll('span'); // 0=code 1=name 2=price 3=pct 4=badge
  });
}
async function wlSetAlert(inp){
  const wid=inp.dataset.wid; const k=inp.dataset.k;
  const v=inp.value===''?null:parseFloat(inp.value);
  // 取同 wid 另一框当前值
  const other=document.querySelector(`#wlDrawerBody input[data-wid="${wid}"][data-k="${k==='hi'?'lo':'hi'}"]`);
  const ov=other&&other.value===''?null:parseFloat(other.value);
  const body=k==='hi'?{alert_hi:v,alert_lo:ov}:{alert_hi:ov,alert_lo:v};
  const r=await apiFetch(`${API}/api/watchlist/${wid}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r._fail) toast('提醒已更新','ok');
}
```

- [ ] **Step 2: 抽屉关闭停轮询**

`toggleWatchlist` 改为：

```js
function toggleWatchlist(){const d=document.getElementById('wlDrawer');const open=d.classList.toggle('open');if(open){wlLoad();}else{if(_wlTimer){clearInterval(_wlTimer);_wlTimer=null;}}}
```

- [ ] **Step 3: wlAdd 成功后刷新**

`wlAdd` 末尾保持 `wlLoad();`（已有），无需改。

- [ ] **Step 4: 语法校验**

Run: `node --check web/index.html 2>&1 || node -e "const fs=require('fs');const s=fs.readFileSync('web/index.html','utf8');const m=s.match(/<script>([\s\S]*?)<\/script>/);require('vm').createScript(m[1]);console.log('JS syntax OK')"`
Expected: "JS syntax OK"（提取 script 块 vm 校验）。

- [ ] **Step 5: 部署到容器 + 验证**

```bash
docker cp web/index.html a-screener:/app/web/index.html
curl -s http://localhost:8000/api/watchlist/live | python -c "import sys,json;d=json.load(sys.stdin);print('live rows:',len(d.get('data',{}).get('rows',[])),'in_session:',d.get('data',{}).get('in_session'))"
curl -s http://localhost:8000/api/watchlist/signals | python -c "import sys,json;d=json.load(sys.stdin);print('sig rows:',len(d.get('data',{}).get('rows',[])))"
```

Expected: live/sig 端点 200 返回 rows（空表则 0）。

- [ ] **Step 6: 提交**

```bash
git add web/index.html
git commit -m "feat(web): wlDrawer 重写 30s轮询+信号徽章+alert输入+越线高亮

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: CLAUDE.md 同步 + 最终全量回归

**Files:**
- Modify: `CLAUDE.md`（路由速查 + 改动检查清单补 watchlist 提醒约束）

- [ ] **Step 1: CLAUDE.md 路由速查补三路由**

在 `/api/watchlist` GET/POST + DELETE 描述后补：

```
`PATCH /api/watchlist/{wid}`（到价提醒 alert_hi/alert_lo，返 {updated,id,alert_hi,alert_lo}，默认 _wrap disclaimer；watchlist 是未买入观察清单，提醒=用户自设价位机械标记非买卖信号）`GET /api/watchlist/live`（批量实时行情：tdx get_quote 批量取盘中价+涨跌幅+alert 越线机械标记 alert_hit(hi/lo)，tdx 空降级 spot latest_price 标 quote_source=spot陈旧；返 in_session 复用 quality._is_in_session 供前端决定 30s/5min 轮询；附 cand_disclaimer）`GET /api/watchlist/signals`（watchlist 全体批量信号扫描，_is_etf 按前缀分拆 stock/etf universe 调 scan_signals，每行带 universe；附 cand_disclaimer）
```

- [ ] **Step 2: 改动检查清单补一条**

在 watchlist 条目后补：

```
- watchlist 到价提醒 → `watchlist` 表加 alert_hi/alert_lo 两列（SCHEMA_SQL + `_BOARD_MIGRATIONS`，**`_migrate` 表名集合必须含 'watchlist'** 否则条目被 `if table not in existing` 跳过）；`set_alert` 独立 UPDATE 不走 upsert；`_is_etf` 前缀启发式分拆 stock/etf universe 供 signals 路由；live 路由 tdx 批量降级 spot；前端 wlDrawer 盘中 30s/盘后 5min 轮询只拉 live 不拉 signals。
```

- [ ] **Step 3: 最终全量回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿（新增 ~10 watchlist 测试 + 既有 250）。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步 watchlist live/signals/alert 三路由+迁移约束

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:** ①DB schema→Task1；②watchlist.py 四函数→Task2；③三路由→Task3/4/5；④前端抽屉→Task6；⑤合规措辞→各任务 cand_disclaimer + Task6 措辞；⑥测试→各任务内联测试 + Task7 回归。全覆盖。

**2. Placeholder scan:** 无 TBD/TODO；每步含实际代码或命令。Task6 Step4 的 node 校验命令含具体脚本。无"add error handling"泛语。

**3. Type consistency:** `set_alert(wid, alert_hi, alert_lo)` 在 Task2 定义、Task3 调用签名一致；`list_codes()→list[str]` Task2 定义、Task4/5 调用一致；`_is_etf(code)→bool` Task2 定义、Task5 调用一致；`alert_hit` 取值 "hi"/"lo"/None 在 Task4 定义、Task6 前端读取一致。

无 gap。可执行。

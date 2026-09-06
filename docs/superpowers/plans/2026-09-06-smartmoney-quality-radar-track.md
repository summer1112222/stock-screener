# 主力动向 × 优质筛选 实战化优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地五包优化——A 主力雷达（多通道共振）、D quality 主清单附主力行为列+主力阶段、E 清单历史验证（list_track 落库+回填+汇总）、B 游资追踪日视图（当日龙虎榜×席位历史胜率）、C 北向备源探源 spike。

**Architecture:** 全部复用现有只读查询层与按需采集层：A/B/D 在 `screener/smart_money.py` 与 `backtest/quality.py` 内加纯函数/富化段（不新增采集）；E 新增 `backtest/tracker.py` + `list_track` 表（watchlist 先例：独立 INSERT，不走 upsert_rows）；路由挂 `api/server.py` 现有 `_wrap`+disclaimer 模式；前端 `web/index.html` 原生 JS 无构建。

**Tech Stack:** Python 3.12 / FastAPI / SQLite / pandas 3.0 / pytest（宿主仓库根目录跑）/ 原生 JS 前端。

**Spec:** `docs/superpowers/specs/2026-09-06-smartmoney-quality-radar-track-design.md`

## Global Constraints

- 测试从**仓库根目录**跑：`python -m pytest tests/ -q`（无 conftest/pytest.ini，子目录跑找不到 data 包）；测试全 mock，不触网。
- NaN→None：所有响应字段经 `_nan()` 或 `df.astype(object).where(pd.notna(df), None)`，防 starlette `allow_nan=False` 500。
- 日期窗口必须**封顶 today**（`date <= today_str`）——限售解禁通道把未来日期写进 `date` 列。
- 新路由一律 `_wrap()` + `cand_disclaimer`（E 的 summary 挂 `bt_disclaimer`）；措辞"机械统计/观察清单/非买卖信号"。
- `list_track` 走 watchlist 先例：`SCHEMA_SQL` 加 `CREATE TABLE IF NOT EXISTS` 即可，**不进 `TABLE_FIELDS`/`_BOARD_MIGRATIONS`**（spec 写"进 TABLE_FIELDS"，但其 idempotent 意图由 UNIQUE+INSERT OR IGNORE 达成；TABLE_FIELDS 仅服务 `upsert_rows`，本表不用它——与 `data/watchlist.py` 一致）。
- 股数通道（`_SHARES_CHANNELS` = 十大股东/高管增减持/限售解禁）amount 单位是股，**绝不与元量纲混算**。
- 每任务独立 commit；每阶段结束跑全量 `python -m pytest tests/ -q` + `python -m compileall api data screener backtest scripts tests`。
- 部署：阶段合并后 `bash deploy.sh`（测试→重建→健康检查），不手动 docker cp。

---

## Phase 1 · 包 A 主力雷达

### Task 1: `radar()` 多通道共振聚合（后端）

**Files:**
- Modify: `screener/smart_money.py`（在 `summarize_by_code` 之后、`unlock_by_month` 之前插入）
- Test: `tests/test_sm_radar.py`（新建）

**Interfaces:**
- Consumes: `db.query_rows(table, where, params, order_by, limit)`、`_nan(v)`、`_attach_intensity(rows)`、`_SHARES_CHANNELS`（均已存在于同文件/`data.db`）
- Produces: `radar(days=5, market=None, limit=50) -> {"rows": [...], "total": int, "date": str|None, "note": str|None}`；row 结构 `{"code","name","channel_hits","channels":{ch:{"net","latest_date","positive"}},"net_intensity","unlock_flag","unlock_next","amount_net"}`——Task 2 路由与前端直接消费该结构。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""主力雷达 radar() 多通道共振测试（spec 2026-09-06 包A）。
mock db.query_rows，不触网。覆盖：共振计数/解禁负向/高管股数量纲/排序/封顶today/空表降级。"""
from datetime import datetime, timedelta

import pytest

from data import db
import screener.smart_money as smq

_TODAY = datetime.now().strftime("%Y-%m-%d")
_YEST = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
_FUTURE = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")

ROWS = [
    # 000001: 资金流+龙虎榜+北向 三通道净流入 → hits=3
    {"code": "000001", "name": "甲", "channel": "资金流", "date": _TODAY, "amount": 5e7, "market": "股票"},
    {"code": "000001", "name": "甲", "channel": "龙虎榜", "date": _TODAY, "amount": 3e7, "market": "股票"},
    {"code": "000001", "name": "甲", "channel": "北向", "date": _YEST, "amount": 1e7, "market": "股票"},
    # 000002: 资金流净流入 + 高管增持(股数通道正向) → hits=2；另有未来解禁(负向,不计hits)
    {"code": "000002", "name": "乙", "channel": "资金流", "date": _TODAY, "amount": 2e7, "market": "股票"},
    {"code": "000002", "name": "乙", "channel": "高管增减持", "date": _TODAY, "amount": 5e5, "market": "股票"},
    {"code": "000002", "name": "乙", "channel": "限售解禁", "date": _FUTURE, "amount": 1e8,
     "as_of": _FUTURE, "market": "股票"},
    # 000003: 资金流净流出 → hits=0
    {"code": "000003", "name": "丙", "channel": "资金流", "date": _TODAY, "amount": -4e7, "market": "股票"},
]
SPOT = [
    {"code": "000001", "turnover_amount": 1e9},
    {"code": "000002", "turnover_amount": 4e8},
    {"code": "000003", "turnover_amount": 2e8},
]


def _mock(monkeypatch, rows=None):
    rows = ROWS if rows is None else rows

    def fake(table, where="", params=(), order_by="", limit=0, **kw):
        if table == "stock_spot":
            return SPOT
        if table != "smart_money_action":
            return []
        # 最新日期探测（封顶 today, date DESC limit 1）
        if order_by == "date DESC" and limit == 1:
            dates = sorted({r["date"] for r in rows if r["date"] <= params[0]}, reverse=True)
            return [{"date": dates[0]}] if dates else []
        # 窗口查询：params=(start, end[, market])
        start, end = params[0], params[1]
        out = [r for r in rows if start <= r["date"] <= end]
        if "market = ?" in where:
            out = [r for r in out if r.get("market") == params[2]]
        return out

    monkeypatch.setattr(db, "query_rows", fake)


@pytest.fixture(autouse=True)
def _clear_cache():
    smq._RADAR_CACHE.clear()
    yield
    smq._RADAR_CACHE.clear()


def test_radar_hits_and_sort(monkeypatch):
    _mock(monkeypatch)
    res = smq.radar(days=5)
    codes = [r["code"] for r in res["rows"]]
    assert codes[0] == "000001"          # hits=3 排最前
    assert res["rows"][0]["channel_hits"] == 3
    r2 = next(r for r in res["rows"] if r["code"] == "000002")
    assert r2["channel_hits"] == 2        # 资金流 + 高管增持
    r3 = next(r for r in res["rows"] if r["code"] == "000003")
    assert r3["channel_hits"] == 0        # 净流出


def test_radar_unlock_negative_flag_not_counted(monkeypatch):
    _mock(monkeypatch)
    r2 = next(r for r in smq.radar(days=5)["rows"] if r["code"] == "000002")
    assert r2["unlock_flag"] is True
    assert r2["unlock_next"] == _FUTURE
    assert "限售解禁" not in r2["channels"]   # 负向事件不进通道聚合


def test_radar_window_caps_today(monkeypatch):
    # 未来解禁行不能劫持窗口端点：date 应为 _TODAY 而非 _FUTURE
    _mock(monkeypatch)
    res = smq.radar(days=5)
    assert res["date"] == _TODAY


def test_radar_shares_channel_not_in_amount_net(monkeypatch):
    # 高管增减持 5e5 股不得混入元量纲 amount_net
    _mock(monkeypatch)
    r2 = next(r for r in smq.radar(days=5)["rows"] if r["code"] == "000002")
    assert r2["amount_net"] == pytest.approx(2e7)


def test_radar_intensity_attached(monkeypatch):
    _mock(monkeypatch)
    r1 = next(r for r in smq.radar(days=5)["rows"] if r["code"] == "000001")
    # amount_net=5e7+3e7+1e7=9e7, turnover=1e9 → 0.09
    assert r1["net_intensity"] == pytest.approx(0.09, abs=1e-4)


def test_radar_empty_table_degrades(monkeypatch):
    _mock(monkeypatch, rows=[])
    res = smq.radar(days=5)
    assert res["rows"] == [] and res["date"] is None and res["note"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_sm_radar.py -v`
Expected: FAIL / ERROR，`AttributeError: module 'screener.smart_money' has no attribute '_RADAR_CACHE'`（及 radar 未定义）

- [ ] **Step 3: 最小实现**

在 `screener/smart_money.py` 的 `summarize_by_code` 之后插入：

```python
# ---------------------------------------------------------------------------
# 主力雷达 radar (包A, spec 2026-09-06): 多通道正向共振计数
# 正向通道: 资金流/龙虎榜/北向(金额,净额>0) + 高管增减持(股数,和>0=增持)。
# 限售解禁=负向事件只打 unlock_flag 不计 hits; 十大股东季度快照(P2 diff 未做)不计入。
# 只读 DB 不触网; 30s 进程缓存。机械统计,非买卖信号。
# ---------------------------------------------------------------------------
_RADAR_CACHE: dict = {}
_RADAR_TTL = 30.0
_RADAR_AMOUNT_CHANNELS = ("资金流", "龙虎榜", "北向")


def radar(days: int = 5, market: str | None = None, limit: int = 50) -> dict:
    import time as _t
    key = (days, market, limit)
    now = _t.time()
    hit = _RADAR_CACHE.get(key)
    if hit and now - hit[0] < _RADAR_TTL:
        return hit[1]
    today_str = datetime.now().strftime("%Y-%m-%d")
    latest = db.query_rows("smart_money_action", where="date <= ?",
                           params=(today_str,), order_by="date DESC", limit=1)
    if not latest:
        out = {"rows": [], "total": 0, "date": None,
               "note": "无主力动向数据(先 /api/smart-money/refresh)",
               "cand_note": "多通道资金共振机械统计，非买卖信号"}
        _RADAR_CACHE[key] = (now, out)
        return out
    latest_date = latest[0].get("date")
    end = datetime.strptime(latest_date, "%Y-%m-%d")
    start = (end - timedelta(days=max(days - 1, 0))).strftime("%Y-%m-%d")
    where, params = ["date >= ?", "date <= ?"], [start, latest_date]
    if market:
        where.append("market = ?"); params.append(market)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="", limit=0)
    agg: dict[str, dict] = {}
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        code = str(code)
        s = agg.setdefault(code, {
            "code": code, "name": r.get("name"), "_ch": {},
            "unlock_flag": False, "unlock_next": None})
        if not s.get("name") and r.get("name"):
            s["name"] = r["name"]
        ch = r.get("channel") or ""
        dt = str(r.get("date") or "")
        if ch == "限售解禁":                       # 负向事件: 打标不进通道聚合
            s["unlock_flag"] = True
            ao = str(r.get("as_of") or "")
            if ao and (s["unlock_next"] is None or ao < s["unlock_next"]):
                s["unlock_next"] = ao
            continue
        d = s["_ch"].setdefault(ch, {"net": 0.0, "latest_date": "", "has": False})
        a = _nan(r.get("amount"))
        if a is not None:
            d["net"] += a
            d["has"] = True
        if dt > d["latest_date"]:
            d["latest_date"] = dt
    pool = []
    for s in agg.values():
        chs = s.pop("_ch")
        channels = {}
        hits = 0
        amount_net = 0.0
        for ch, d in chs.items():
            positive = (d["net"] > 0) if d["has"] else None
            channels[ch] = {"net": _nan(round(d["net"], 2)) if d["has"] else None,
                            "latest_date": d["latest_date"] or None,
                            "positive": positive}
            if positive:
                hits += 1
            if ch in _RADAR_AMOUNT_CHANNELS and d["has"]:
                amount_net += d["net"]
        s["channels"] = channels
        s["channel_hits"] = hits
        s["amount_net"] = _nan(round(amount_net, 2))
        pool.append(s)
    # net_intensity: 金额通道合计净额 / 当日成交额（复用 _attach_intensity）
    _attach_intensity([{**p, "amount": p["amount_net"]} for p in pool]
                      if False else pool)  # 见下：先临时置换 amount
    # 上一行不成立——_attach_intensity 读 r["amount"]；改为显式:
    for p in pool:
        p["_amt_bak"] = p.get("amount")
        p["amount"] = p["amount_net"]
    _attach_intensity(pool)
    for p in pool:
        p["amount"] = p.pop("_amt_bak")
    pool.sort(key=lambda x: (x["channel_hits"],
                             x["net_intensity"] if x.get("net_intensity") is not None else -1),
              reverse=True)
    rows_out = pool[:limit] if limit else pool
    out = {"rows": rows_out, "total": len(rows_out), "date": latest_date,
           "note": None}
    _RADAR_CACHE[key] = (now, out)
    return out
```

> **实现注意**（执行者按此清理，勿保留上面注释掉的错误行）：`_attach_intensity(rows)` 以 `r["amount"]` 为分子、`stock_spot.turnover_amount` 为分母写入 `r["net_intensity"]`。正确做法是把 pool 每行临时 `amount=amount_net` 调一次 `_attach_intensity(pool)` 再还原，即只保留"显式置换"那 6 行，删掉带 `if False else` 的死代码行。还原后 `amount` 键若原不存在则 pop 返回 None，直接 `p.pop("amount", None)` 删除临时键，最终 row 不含 `amount` 字段（前端用 `amount_net`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_sm_radar.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add screener/smart_money.py tests/test_sm_radar.py
git commit -m "feat(smart-money): radar 多通道正向共振聚合(包A后端)"
```

### Task 2: `/api/smart-money/radar` 路由 + 前端"主力雷达"视图

**Files:**
- Modify: `api/server.py`（`sm_board_link` 路由之后，约 868 行附近）
- Modify: `web/index.html`（`#smView` 下拉约 285-289 行；`smRender` 分发约 1244 行起；`smView.onchange` 约 1364 行）
- Test: `tests/test_sm_radar.py`（追加路由测试）

**Interfaces:**
- Consumes: Task 1 `smq.radar(days, market, limit)`
- Produces: `GET /api/smart-money/radar?days=&market=&limit=` 响应 `_wrap(rows, {total,date,note,cand_disclaimer})`；前端 `smRenderRadar()`。

- [ ] **Step 1: 写失败路由测试**（追加到 `tests/test_sm_radar.py`）

```python
def test_radar_route(monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as srv
    monkeypatch.setattr(srv.sm_query, "radar",
                        lambda days=5, market=None, limit=50: {
                            "rows": [{"code": "000001", "channel_hits": 3}],
                            "total": 1, "date": "2026-09-04", "note": None},
                        raising=False)
    c = TestClient(srv.app)
    r = c.get("/api/smart-money/radar?days=5")
    assert r.status_code == 200
    j = r.json()
    assert j["data"]["rows"][0]["channel_hits"] == 3
    assert "非买卖信号" in j["meta"]["cand_disclaimer"]
```

> 注意：`api/server.py` 顶部已有 `from screener import smart_money as sm_query` 类似导入（执行者确认实际别名，路由实现用同一别名调用 `radar`）。`_wrap` 的 meta 键名以现有实现为准（看 `sm_today` 路由的断言方式，若 `cand_disclaimer` 在 `j["meta"]` 之外则调整断言路径）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_sm_radar.py::test_radar_route -v`
Expected: FAIL，404（路由不存在）

- [ ] **Step 3: 加路由**（`api/server.py`，`smart_money_board_link` 之后）

```python
@app.get("/api/smart-money/radar")
def smart_money_radar(days: int = Query(5, ge=1, le=30),
                      market: str | None = Query(None),
                      limit: int = Query(50, ge=1, le=300)):
    """主力雷达: 多通道正向共振(资金流/龙虎榜/北向/高管增持)计数排序。
    限售解禁=负向标记不计共振。机械统计,非买卖信号。"""
    res = sm_query.radar(days=days, market=market, limit=limit)
    return _wrap(res["rows"], {
        "total": res["total"], "date": res.get("date"), "note": res.get("note"),
        "cand_disclaimer": "多通道资金共振机械统计观察清单，非荐股非买卖信号，盈亏自负。"})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_sm_radar.py -v`
Expected: 7 passed

- [ ] **Step 5: 前端"主力雷达"视图**

`web/index.html` 三处改动：

① `#smView` 下拉（约 285-289 行）加 option：

```html
          <option value="radar">主力雷达</option>
```

② 新增渲染函数（放在 `smRender` 函数定义之后、`smView.onchange`（约 1364 行）之前；先 `grep -n "viewMode" web/index.html` 找到 `smRender` 内按 `smState.viewMode` 分发的 if/else 链，给 `radar` 加分支 `if(smState.viewMode==='radar'){smRenderRadar();return;}`）：

```javascript
async function smRenderRadar(){
  const out=document.getElementById('smOut');
  const days=document.getElementById('smDays').value||7;
  out.innerHTML='<div class="empty">主力雷达加载中(多通道共振,30s缓存)...</div>';
  try{
    const r=await fetch(`${API}/api/smart-money/radar?days=${days}&limit=50`,{signal:AbortSignal.timeout(30000)}).then(r=>r.json());
    const rows=(r.data&&r.data.rows)||[];
    if(!rows.length){out.innerHTML=`<div class="empty">${(r.data&&r.data.note)||'无数据(先刷新主力动向)'}</div>`;return;}
    const chOrder=['资金流','龙虎榜','北向','高管增减持'];
    const trs=rows.map(x=>{
      const chs=x.channels||{};
      const chips=chOrder.map(ch=>{
        const d=chs[ch]; if(!d) return '';
        const col=d.positive?'#DC2626':'#16A34A'; // A股红涨绿跌: 净流入红/净流出绿
        const v=d.net!=null?(Math.abs(d.net)>=1e8?(d.net/1e8).toFixed(2)+'亿':(d.net/1e4).toFixed(0)+'万'):'—';
        return `<span style="font-size:11px;border:1px solid ${col};color:${col};border-radius:4px;padding:0 4px;margin-right:4px" title="${ch} 净额 ${v}">${ch.slice(0,2)} ${v}</span>`;
      }).join('');
      const unlock=x.unlock_flag?`<span style="font-size:11px;color:#B45309" title="近期解禁 ${x.unlock_next||''}">⚠解禁${x.unlock_next?(' '+x.unlock_next):''}</span>`:'';
      const ni=x.net_intensity!=null?(x.net_intensity*100).toFixed(2)+'%':'—';
      return `<tr class="clk" data-code="${x.code||''}"><td>${x.code||''}</td><td>${x.name||''}</td><td class="num"><b style="color:#DC2626">${x.channel_hits||0}</b></td><td>${chips}</td><td class="num">${ni}</td><td>${unlock}</td></tr>`;
    }).join('');
    out.innerHTML=`<div style="font-size:12px;margin:4px 0;color:var(--muted)">主力雷达 (${rows.length}) · 数据日 ${r.data.date||'-'} · 共振=资金流/龙虎榜/北向/高管增持同向为正 · 机械统计非买卖信号</div><table><thead><tr><th>代码</th><th>名称</th><th class="num">共振</th><th>通道明细</th><th class="num">强度</th><th>事件</th></tr></thead><tbody>${trs}</tbody></table>`;
    out.querySelectorAll('tr.clk').forEach(tr=>tr.onclick=()=>{const c=tr.dataset.code;if(c){switchTab('analysis');const i=document.getElementById('saCode');if(i){i.value=c;}const b=document.getElementById('saRun');if(b)b.onclick&&b.onclick();}});
  }catch(e){out.innerHTML=`<div class="empty">加载失败: ${e.message||e}</div>`;}
}
```

> 行点击跳个股分析的写法以现有 handler 为准：`grep -n "switchTab('analysis')" web/index.html` 找到既有模式（含填码与触发分析的准确 id），照抄该模式替换上面 onclick 内的三行。

③ `#smDays` 的 onchange 若只触发 `smLoad`，radar 视图下切天数应重新拉取：在现有 `smDays` 变更处理里加 `if(smState.viewMode==='radar'){smRenderRadar();return;}`（`grep -n "smDays" web/index.html` 定位）。

- [ ] **Step 6: 手工验证前端**

浏览器开 `http://localhost:8000/web/index.html`（或 `docker compose up -d` 后访问），主力动向 tab → 视图切"主力雷达"：表格出现共振列与通道 chips；无数据时显示 note 不白屏。

- [ ] **Step 7: Commit**

```bash
git add api/server.py web/index.html tests/test_sm_radar.py
git commit -m "feat(smart-money): /api/smart-money/radar + 前端主力雷达视图(包A)"
```

---

## Phase 2 · 包 D quality 行为联动

### Task 3: `_behavior_batch` 扩展 `cum_net` + quality 主清单富化（后端）

**Files:**
- Modify: `screener/smart_money.py:578-631`（`_behavior_batch` 加 `cum_net` 键）
- Modify: `backtest/quality.py:1159-1169`（`_apply_combo` 调用之后、`_clean_item` 之前插富化段）
- Test: `tests/test_quality_behavior_cols.py`（新建）

**Interfaces:**
- Consumes: `_behavior_batch(codes, days) -> {code: {streak_inflow, streak_outflow, margin_accel, north_cum, cum_net}}`（新增 `cum_net`：窗口内金额通道[资金流/龙虎榜/北向]净额合计，股数通道不混入）；`main_force_phase(code) -> {"phase","confidence",...}`
- Produces: quality `main` item 新字段 `streak_inflow/streak_outflow/cum_net/net_intensity/mf_phase/mf_confidence`；出货预警追加进 `warnings`。Task 4 前端与 Task 6 tracker meta 消费这些字段。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""quality 主清单主力行为列 + 主力阶段（spec 2026-09-06 包D）。
mock _behavior_batch/main_force_phase/db，不触网。"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from data import db
import screener.smart_money as smq
from backtest import quality

_TODAY = datetime.now().strftime("%Y-%m-%d")
_YEST = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

SPOT = [
    {"code": "000001", "name": "甲", "latest_price": 10.0, "change_pct": 2.0,
     "turnover_amount": 1e9, "turnover_rate": 3.0, "main_net_inflow": 5e7,
     "board": "银行"},
]
SMA = [
    {"code": "000001", "channel": "资金流", "date": _TODAY, "amount": 3e7},
    {"code": "000001", "channel": "资金流", "date": _YEST, "amount": 2e7},
    {"code": "000001", "channel": "龙虎榜", "date": _TODAY, "amount": 1e7},
    {"code": "000001", "channel": "高管增减持", "date": _TODAY, "amount": 5e5},  # 股数,不混入
]


# ---------- _behavior_batch cum_net ----------

def test_behavior_batch_cum_net_amount_channels_only(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SMA if table == "smart_money_action" else [])
    out = smq._behavior_batch(["000001"], days=30)
    assert out["000001"]["cum_net"] == pytest.approx(6e7)   # 3e7+2e7+1e7, 不含高管股数


# ---------- quality_rank 富化 ----------

def _mock_quality(monkeypatch, phase="吸筹", conf=0.8):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT if table == "stock_spot" else
                        (SMA if table == "smart_money_action" else []))
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    monkeypatch.setattr(smq, "top_by_amount",
                        lambda **kw: {"rows": [{"code": "000001", "amount": 1e8}], "total": 1})
    monkeypatch.setattr(smq, "main_force_phase",
                        lambda code, days=30: {"phase": phase, "confidence": conf})


def test_quality_main_has_behavior_cols(monkeypatch):
    _mock_quality(monkeypatch)
    res = quality.quality_rank("stock", min_dims=1, strict_quality=False, refine=False)
    assert res["main"], "main 不应为空"
    it = res["main"][0]
    assert it["streak_inflow"] == 2
    assert it["cum_net"] == pytest.approx(6e7)
    assert it["net_intensity"] == pytest.approx(0.06, abs=1e-4)   # 6e7/1e9
    assert it["mf_phase"] == "吸筹" and it["mf_confidence"] == pytest.approx(0.8)


def test_quality_distribution_phase_warning(monkeypatch):
    _mock_quality(monkeypatch, phase="出货", conf=0.7)
    it = quality.quality_rank("stock", min_dims=1, strict_quality=False, refine=False)["main"][0]
    assert any("出货" in w for w in it["warnings"])


def test_quality_low_conf_phase_no_warning(monkeypatch):
    _mock_quality(monkeypatch, phase="出货", conf=0.3)   # <0.6 不预警
    it = quality.quality_rank("stock", min_dims=1, strict_quality=False, refine=False)["main"][0]
    assert not any("出货" in w for w in it["warnings"])


def test_quality_phase_failure_degrades_none(monkeypatch):
    _mock_quality(monkeypatch)
    def _boom(code, days=30):
        raise RuntimeError("no history")
    monkeypatch.setattr(smq, "main_force_phase", _boom)
    it = quality.quality_rank("stock", min_dims=1, strict_quality=False, refine=False)["main"][0]
    assert it["mf_phase"] is None and it["mf_confidence"] is None   # 不崩
```

> 执行者注意：`quality.py` 的 `sm_query` 导入别名以文件实际为准（`grep -n "smart_money" backtest/quality.py`），富化段与测试 monkeypatch 都要打在 quality 实际引用的模块对象上。若 quality 是 `from screener.smart_money import _behavior_batch` 直接引名，测试改为 monkeypatch `quality._behavior_batch`/`quality.main_force_phase`（并让实现用同名模块级导入，保持可 patch）。缓存干扰：`quality._RESULT_CACHE` 每个测试前 `monkeypatch.setattr(quality, "_RESULT_CACHE", {})` 清空（沿用 test_quality_confidence.py 既有做法，若无则在 `_mock_quality` 里加）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_quality_behavior_cols.py -v`
Expected: FAIL——`cum_net` KeyError / main item 无 `streak_inflow` 字段

- [ ] **Step 3: 实现 `_behavior_batch` cum_net**

`screener/smart_money.py` `_behavior_batch`：out 初始化字典加 `"cum_net": None`；聚合循环里对金额通道累计：

```python
    # 在 for c in codes: 循环内, chans 取到后:
        amt_sum, has_amt = 0.0, False
        for ch_name in ("资金流", "龙虎榜", "北向"):
            dd = chans.get(ch_name)
            if dd:
                amt_sum += sum(dd.values())
                has_amt = True
        if has_amt:
            out[c]["cum_net"] = round(amt_sum, 2)
```

- [ ] **Step 4: 实现 quality 富化段**

`backtest/quality.py` `quality_rank` 中，`main = _apply_combo(...)` 之后、`def _clean_item(it):` 之前插入：

```python
    # 包D(spec 2026-09-06): 主清单附主力行为列+主力阶段(仅个股)。
    # _behavior_batch 一次 DB 查询零触网; main_force_phase 逐只(main≤limit,自带30s缓存),
    # 无历史/异常→None 降级不崩。出货预警只标注进 warnings,不扣分不剔除。
    if universe == "stock" and main:
        from screener import smart_money as _smq
        _codes = [str(it["code"]) for it in main]
        try:
            _beh = _smq._behavior_batch(_codes, days)
        except Exception:
            _beh = {}
        _tn = {}
        if "turnover_amount" in df.columns:
            for _, _rr in df[["code", "turnover_amount"]].iterrows():
                _v = _to_float(_rr.get("turnover_amount"))
                if _v:
                    _tn[str(_rr["code"])] = _v
        for it in main:
            c = str(it["code"])
            b = _beh.get(c) or {}
            it["streak_inflow"] = b.get("streak_inflow")
            it["streak_outflow"] = b.get("streak_outflow")
            it["cum_net"] = _to_float(b.get("cum_net"))
            _t = _tn.get(c)
            it["net_intensity"] = (round(it["cum_net"] / _t, 4)
                                   if (_t and it["cum_net"] is not None) else None)
            try:
                ph = _smq.main_force_phase(c)
                it["mf_phase"] = ph.get("phase")
                it["mf_confidence"] = _to_float(ph.get("confidence"))
            except Exception:
                it["mf_phase"], it["mf_confidence"] = None, None
            if it["mf_phase"] == "出货" and (it["mf_confidence"] or 0) >= 0.6:
                it["warnings"].append(
                    f"主力阶段=出货(置信 {round(it['mf_confidence'], 2)})")
```

> 测试可 patch 性：上面用 `from screener import smart_money as _smq` 函数内导入并 `_smq.main_force_phase(...)` 属性访问，monkeypatch `smq.main_force_phase` 即生效（模块属性查找在调用时发生）。

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_quality_behavior_cols.py tests/test_quality_confidence.py tests/test_quality.py tests/test_quality_factors.py -v`
Expected: 全 PASS（旧 quality 测试不因新字段/新查询破——若旧测试 mock 的 `db.query_rows` 对 `smart_money_action` 返回 [] 导致 `_behavior_batch` 空，字段为 None 不影响断言）

Run: `python -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add screener/smart_money.py backtest/quality.py tests/test_quality_behavior_cols.py
git commit -m "feat(quality): 主清单附主力行为列+主力阶段+出货预警(包D后端)"
```

### Task 4: 前端 qsRender 行为列

**Files:**
- Modify: `web/index.html`（`qsRender` 约 1532-1577 行）

**Interfaces:**
- Consumes: Task 3 的 item 字段 `streak_inflow/cum_net/net_intensity/mf_phase/mf_confidence`
- Produces: 主清单新列"主力行为"；阶段列优先用服务端 `mf_phase`（`qsState.phaseMap` 懒加载降级保留，ETF/异常时仍走旧路径）。

- [ ] **Step 1: 改列定义与行模板**

`qsRender` 内（约 1541 行）`cols` 数组在 `['phase','阶段']` 后加一列：

```javascript
  const cols=[['code','代码'],['name','名称'],['phase','阶段'],['mf','主力行为'],['resonance','共振'],['hits','命中'],['dim_scores','分位(1-5)']];
```

`ths` 生成行（约 1544 行）把 `mf` 加入不可排序集合：

```javascript
  const ths=cols.map(([k,h])=>(k==='dim_scores'||k==='phase'||k==='mf'||k==='reasons')?`<th class="num">${h}</th>`:`<th class="num" data-qk="${k}" style="cursor:pointer;user-select:none">${h}${_arr(k)}</th>`).join('');
```

行模板（约 1548-1551 行）：阶段单元格优先服务端值，行为单元格新增：

```javascript
    const phSrv=(x.mf_phase!=null)?{phase:x.mf_phase,confidence:x.mf_confidence}:null;
    const ph=phSrv||qsState.phaseMap[x.code];
    const phaseTd=`<td class="qsPhase num" data-code="${x.code||''}">${ph?_phaseBadge(ph):'<span style="color:var(--muted);font-size:11px">…</span>'}</td>`;
    const _fmtAmt=v=>v==null?'—':(Math.abs(v)>=1e8?(v/1e8).toFixed(2)+'亿':(v/1e4).toFixed(0)+'万');
    const si=x.streak_inflow,so=x.streak_outflow;
    const streak=(si>0)?`<span style="color:#DC2626" title="连续净流入天数">+${si}天</span>`:((so>0)?`<span style="color:#16A34A" title="连续净流出天数">-${so}天</span>`:'—');
    const mfTd=`<td class="num" style="font-size:11px" title="连续净流入/出 · 窗口累计主力净额 · 净额/成交额">${streak} · ${_fmtAmt(x.cum_net)} · ${x.net_intensity!=null?(x.net_intensity*100).toFixed(1)+'%':'—'}</td>`;
```

并把 `${phaseTd}` 后插入 `${mfTd}`（return 的行模板字符串中）。

> `_phaseBadge(p)` 读 `p.phase/p.confidence`——先 `grep -n "_phaseBadge" web/index.html` 确认字段名，若不同按实际适配 `phSrv` 的键。懒加载 phase 的既有逻辑（约 1484-1494 行）对已有 `mf_phase` 的行会重复 fetch：在该懒加载函数开头对 `qsState.raw.main` 里 `mf_phase!=null` 的 code 跳过（`if(x.mf_phase!=null)` 不加入待 fetch 列表）。

- [ ] **Step 2: 手工验证**

浏览器 quality tab 跑一次筛选：主清单出现"主力行为"列；个股行阶段徽章立即显示（服务端值，不再等懒加载"…"）；ETF universe 该列显示 —。

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat(quality): 前端主清单主力行为列+服务端阶段优先(包D)"
```

---

## Phase 3 · 包 E 清单历史验证

### Task 5: `list_track` 表 + `backtest/tracker.py`（record/默认参数判定）

**Files:**
- Modify: `data/models.py`（`SCHEMA_SQL` 内 `watchlist` 表定义之后，约 228 行后）
- Create: `backtest/tracker.py`
- Test: `tests/test_tracker.py`（新建）

**Interfaces:**
- Consumes: `db.get_conn()`（watchlist 先例：`with db.get_conn() as conn` + 显式 commit）
- Produces:
  - `tracker.record_list(module: str, mode: str, date: str, items: list[dict], score_key: str) -> int`（返回插入行数；INSERT OR IGNORE 幂等）
  - `tracker.is_default_params(module: str, params: dict) -> bool`
  - `tracker.DEFAULTS = {"quality": {...}, "nextday": {...}}`
  - Task 6 路由钩子、Task 7 fill、Task 8 summary 均建在本模块上。

- [ ] **Step 1: 加表**（`data/models.py` SCHEMA_SQL，watchlist 之后）

```sql
-- 清单历史验证(spec 2026-09-06 包E): quality/nextday 清单每日快照+前视收益回填。
-- UNIQUE(module,mode,date,code)+INSERT OR IGNORE 幂等; 不走 upsert_rows/TABLE_FIELDS(watchlist 先例)。
CREATE TABLE IF NOT EXISTS list_track (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT NOT NULL,
    mode TEXT NOT NULL,
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    rank INTEGER,
    score REAL,
    meta_json TEXT,
    ret_k1 REAL, ret_k3 REAL, ret_k5 REAL,
    entry_basis TEXT,
    filled_ts TEXT,
    ts TEXT,
    UNIQUE(module, mode, date, code)
);
```

- [ ] **Step 2: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""清单历史验证 tracker（spec 2026-09-06 包E）。
内存 SQLite 真库测幂等（record 走 db.get_conn 裸 SQL，mock query_rows 不适用）。"""
import json
import sqlite3

import pytest

from data import db, models
import backtest.tracker as tracker


@pytest.fixture()
def memdb(monkeypatch, tmp_path):
    path = str(tmp_path / "t.db")

    def _conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    with _conn() as c:
        c.executescript(models.SCHEMA_SQL)
    monkeypatch.setattr(db, "get_conn", _conn)
    return path


ITEMS = [
    {"code": "000001", "name": "甲", "adjusted_resonance": 8.5, "hits": 3,
     "data_confidence": 0.8, "mf_phase": "吸筹"},
    {"code": "000002", "name": "乙", "adjusted_resonance": 7.2, "hits": 2,
     "data_confidence": 0.6, "mf_phase": None},
]


def test_record_list_inserts_with_rank_and_meta(memdb):
    n = tracker.record_list("quality", "strict", "2026-09-04", ITEMS,
                            score_key="adjusted_resonance")
    assert n == 2
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM list_track ORDER BY rank").fetchall()
    assert rows[0]["code"] == "000001" and rows[0]["rank"] == 1
    assert rows[0]["score"] == pytest.approx(8.5)
    meta = json.loads(rows[0]["meta_json"])
    assert meta["hits"] == 3 and meta["mf_phase"] == "吸筹"


def test_record_list_idempotent(memdb):
    tracker.record_list("quality", "strict", "2026-09-04", ITEMS, "adjusted_resonance")
    n2 = tracker.record_list("quality", "strict", "2026-09-04", ITEMS, "adjusted_resonance")
    assert n2 == 0                       # UNIQUE 冲突全忽略
    with db.get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) c FROM list_track").fetchone()["c"]
    assert cnt == 2


def test_is_default_params_quality():
    d = dict(tracker.DEFAULTS["quality"])
    assert tracker.is_default_params("quality", d) is True
    d["days"] = 30
    assert tracker.is_default_params("quality", d) is False
    # 缺省键按默认补齐后比较（路由不传 = 默认）
    partial = {k: v for k, v in tracker.DEFAULTS["quality"].items() if k != "days"}
    assert tracker.is_default_params("quality", partial) is True


def test_record_list_empty_items_noop(memdb):
    assert tracker.record_list("nextday", "strict", "2026-09-04", [], "score") == 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'backtest.tracker'`

- [ ] **Step 4: 实现 `backtest/tracker.py`**

```python
# -*- coding: utf-8 -*-
"""清单历史验证 tracker（spec 2026-09-06 包E）。

quality/nextday 清单每日快照落库 list_track + 前视收益回填 + 汇总。
watchlist 先例：独立 INSERT OR IGNORE（幂等），不走 upsert_rows/TABLE_FIELDS。
落库仅在请求参数=前端默认时记录（is_default_params），防任意参数污染样本。
历史清单机械追踪统计，非预测，不构成买卖信号。
"""
from __future__ import annotations

import json
from datetime import datetime

from data import db

# 前端默认参数（与 api/server.py 路由 Query 默认值一致；改动路由默认值时同步此处）
DEFAULTS = {
    "quality": {
        "universe": "stock", "days": 20, "min_dims": 2, "min_turnover": 5e7,
        "max_per_board": 3, "max_corr": 0.85, "limit": 10,
        "combo_method": "greedy", "resonance_mode": "greedy", "dim_thresh": 0.7,
        "refine": True, "refine_pool": 50, "strict_quality": True,
        "min_confidence": 0.50, "risk_penalty": True,
    },
    "nextday": {
        "universe": "stock", "limit": 50, "days": 30, "min_change_pct": 5.0,
        "min_turnover": 3.0, "max_price": 50.0, "min_mv": 10.0,
        "max_mv": 200.0, "max_pe": 150.0, "exclude_st": True, "codes": "",
    },
}

_META_KEYS = ("hits", "data_confidence", "confidence_level", "mf_phase",
              "factor_scores", "step_status", "score_coverage")


def is_default_params(module: str, params: dict) -> bool:
    d = DEFAULTS.get(module)
    if not d:
        return False
    for k, v in d.items():
        got = params.get(k, v)          # 路由未传 = 默认
        if isinstance(v, float) or isinstance(got, float):
            try:
                if abs(float(got) - float(v)) > 1e-9:
                    return False
                continue
            except (TypeError, ValueError):
                pass
        if isinstance(v, bool) or isinstance(got, bool):
            if bool(got) != bool(v):
                return False
            continue
        if got != v:
            return False
    return True


def record_list(module: str, mode: str, date: str, items: list[dict],
                score_key: str) -> int:
    """清单落库。INSERT OR IGNORE 幂等（UNIQUE(module,mode,date,code)）。
    score_key: quality=adjusted_resonance / nextday=score。返回实际插入行数。"""
    if not items:
        return 0
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    with db.get_conn() as conn:
        for i, it in enumerate(items):
            code = str(it.get("code") or "")
            if not code:
                continue
            meta = {k: it.get(k) for k in _META_KEYS if it.get(k) is not None}
            cur = conn.execute(
                "INSERT OR IGNORE INTO list_track"
                "(module,mode,date,code,name,rank,score,meta_json,ts) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (module, mode, date, code, it.get("name"), i + 1,
                 it.get(score_key), json.dumps(meta, ensure_ascii=False, default=str),
                 ts))
            n += cur.rowcount
        conn.commit()
    return n
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add data/models.py backtest/tracker.py tests/test_tracker.py
git commit -m "feat(tracker): list_track 表+清单幂等落库+默认参数判定(包E)"
```

### Task 6: 路由钩子（/api/quality 与 /api/nextday-strong 默认参数时记录）

**Files:**
- Modify: `api/server.py:918-968`（两个路由函数尾部）
- Test: `tests/test_tracker.py`（追加）

**Interfaces:**
- Consumes: Task 5 `tracker.record_list/is_default_params`
- Produces: 默认参数请求后 `list_track` 有当日快照；quality mode=响应 `selection_mode`，nextday 记 strict（`passed_items`）与 score（`all_items` 截 top limit）两 mode。

- [ ] **Step 1: 写失败测试**（追加 `tests/test_tracker.py`）

```python
def _client():
    from fastapi.testclient import TestClient
    import api.server as srv
    return TestClient(srv.app), srv


def test_quality_route_records_default_params(memdb, monkeypatch):
    c, srv = _client()
    import backtest.quality as q
    canned = {"main": [{"code": "000001", "name": "甲", "adjusted_resonance": 8.0,
                        "hits": 3}],
              "selection_mode": "strict", "cand_disclaimer": "x"}
    monkeypatch.setattr(q, "quality_rank", lambda **kw: canned)
    calls = []
    monkeypatch.setattr(tracker, "record_list",
                        lambda *a, **k: calls.append((a, k)) or 0)
    r = c.get("/api/quality")
    assert r.status_code == 200
    assert calls and calls[0][0][0] == "quality" and calls[0][0][1] == "strict"


def test_quality_route_skips_nondefault_params(memdb, monkeypatch):
    c, srv = _client()
    import backtest.quality as q
    monkeypatch.setattr(q, "quality_rank",
                        lambda **kw: {"main": [], "selection_mode": "strict",
                                      "cand_disclaimer": "x"})
    calls = []
    monkeypatch.setattr(tracker, "record_list",
                        lambda *a, **k: calls.append(a) or 0)
    c.get("/api/quality?days=30")        # 非默认
    assert not calls


def test_nextday_route_records_both_modes(memdb, monkeypatch):
    c, srv = _client()
    import screener.nextday as nd
    canned = {"passed_items": [{"code": "000001", "name": "甲", "score": 0.9}],
              "all_items": [{"code": "000001", "name": "甲", "score": 0.9},
                            {"code": "000002", "name": "乙", "score": 0.5}],
              "selection_mode": "strict", "items": []}
    monkeypatch.setattr(nd, "nextday_strong_rank", lambda **kw: canned)
    calls = []
    monkeypatch.setattr(tracker, "record_list",
                        lambda *a, **k: calls.append(a) or 0)
    r = c.get("/api/nextday-strong")
    assert r.status_code == 200
    modes = {a[1] for a in calls}
    assert modes == {"strict", "score"}
```

> monkeypatch 目标以 server.py 实际导入方式为准：路由是函数内 `from backtest import quality` / `from screener import nextday` 延迟导入，patch 源模块属性即可生效。tracker 钩子实现必须 `from backtest import tracker` 后**属性访问** `tracker.record_list(...)`（保证测试可 patch）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tracker.py -k route -v`
Expected: FAIL（calls 为空——钩子未实现）

- [ ] **Step 3: 实现钩子**

`api/server.py` `quality_screen` 路由 `return` 前插入：

```python
    # 包E(spec 2026-09-06): 默认参数时清单落库追踪(幂等,失败不影响响应)
    try:
        from backtest import tracker
        _p = dict(universe=universe, days=days, min_dims=min_dims,
                  min_turnover=min_turnover, max_per_board=max_per_board,
                  max_corr=max_corr, limit=limit, combo_method=combo_method,
                  resonance_mode=resonance_mode, dim_thresh=dim_thresh,
                  refine=refine, refine_pool=refine_pool,
                  strict_quality=strict_quality, min_confidence=min_confidence,
                  risk_penalty=risk_penalty)
        if tracker.is_default_params("quality", _p):
            from datetime import datetime as _dt
            tracker.record_list("quality", res.get("selection_mode") or "strict",
                                _dt.now().strftime("%Y-%m-%d"),
                                res.get("main") or [], "adjusted_resonance")
    except Exception:
        pass
```

`nextday_strong` 路由 `return` 前插入：

```python
    # 包E: 默认参数时 strict/score 双 mode 落库(幂等,失败不影响响应)
    try:
        from backtest import tracker
        _p = dict(universe=universe, limit=limit, days=days,
                  min_change_pct=min_change_pct, min_turnover=min_turnover,
                  max_price=max_price, min_mv=min_mv, max_mv=max_mv,
                  max_pe=max_pe, exclude_st=exclude_st, codes=codes)
        if tracker.is_default_params("nextday", _p):
            from datetime import datetime as _dt
            _d = _dt.now().strftime("%Y-%m-%d")
            tracker.record_list("nextday", "strict", _d,
                                res.get("passed_items") or [], "score")
            tracker.record_list("nextday", "score", _d,
                                (res.get("all_items") or [])[:limit], "score")
    except Exception:
        pass
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add api/server.py tests/test_tracker.py
git commit -m "feat(tracker): quality/nextday 默认参数清单落库钩子(包E)"
```

### Task 7: 收益回填 `fill_returns` + 脚本 + `/api/track/fill`

**Files:**
- Modify: `backtest/tracker.py`（追加 `fill_returns`）
- Create: `scripts/fill_track_returns.py`
- Modify: `api/server.py`（tracker 路由区，Task 8 的 summary 一起放）
- Test: `tests/test_tracker.py`（追加）

**Interfaces:**
- Consumes: `list_track` 表、`stock_daily` 表（code,date,open,close）
- Produces: `tracker.fill_returns(k_list=(1,3,5)) -> {"filled": int, "pending": int, "skipped": int}`；口径：entry=记录日后**首个交易日开盘价**（缺则降级记录日收盘价，`entry_basis="t_close"`），`ret_k = close(entry_idx+k)/entry - 1`；仅当 ret_k5 可算时写 `filled_ts`（k1/k3 可先独立回填，重跑幂等 `WHERE ret_kX IS NULL`）。

- [ ] **Step 1: 写失败测试**（追加 `tests/test_tracker.py`；需要 pandas）

```python
def _mk_daily(conn, code, dates, opens, closes):
    conn.executemany(
        "INSERT OR REPLACE INTO stock_daily(code,date,open,close) VALUES(?,?,?,?)",
        [(code, d, o, c) for d, o, c in zip(dates, opens, closes)])
    conn.commit()


def test_fill_returns_next_open_basis(memdb):
    # 记录日 T=09-01; T+1=09-02 open=10 买入; k1 exit=09-03 close=11 → 0.1
    # k3 exit=09-05 close=12 → 0.2; k5 exit=09-08(跳过周末无所谓,按交易日序列) close=9 → -0.1
    dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
             "2026-09-05", "2026-09-07", "2026-09-08"]
    opens = [9.5, 10.0, 10.5, 10.8, 11.0, 11.2, 11.5]
    closes = [9.8, 10.2, 11.0, 10.9, 12.0, 11.8, 9.0]
    with db.get_conn() as conn:
        _mk_daily(conn, "000001", dates, opens, closes)
    tracker.record_list("quality", "strict", "2026-09-01",
                        [{"code": "000001", "name": "甲",
                          "adjusted_resonance": 8.0}], "adjusted_resonance")
    rep = tracker.fill_returns()
    assert rep["filled"] == 1
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM list_track WHERE code='000001'").fetchone()
    assert r["entry_basis"] == "next_open"
    assert r["ret_k1"] == pytest.approx(0.1, abs=1e-4)   # 11/10-1
    assert r["ret_k3"] == pytest.approx(0.2, abs=1e-4)   # 12/10-1
    assert r["ret_k5"] == pytest.approx(-0.1, abs=1e-4)  # 9/10-1
    assert r["filled_ts"]


def test_fill_returns_degrades_to_t_close(memdb):
    # 无 T+1 行(记录日=序列最后一天之前只有 T 自己) → entry=T close, 标 t_close
    dates = ["2026-09-01", "2026-09-03", "2026-09-04", "2026-09-07",
             "2026-09-08", "2026-09-09"]
    opens = [9.5, 10.5, 10.8, 11.0, 11.2, 11.5]
    closes = [9.8, 11.0, 10.9, 12.0, 11.8, 9.0]
    with db.get_conn() as conn:
        _mk_daily(conn, "000002", dates, opens, closes)
        # 删掉 T+1(09-02 不存在于序列, 09-03 即首个后续交易日 → 仍是 next_open!)
    # 构造真正降级: 记录日不在序列中且序列首日晚于记录日+? —— 用"记录日=序列首日、
    # 但 open 缺失(None)"模拟:
    with db.get_conn() as conn:
        conn.execute("UPDATE stock_daily SET open=NULL WHERE code='000002' AND date='2026-09-03'")
        conn.commit()
    tracker.record_list("quality", "strict", "2026-09-02",
                        [{"code": "000002", "name": "乙",
                          "adjusted_resonance": 7.0}], "adjusted_resonance")
    rep = tracker.fill_returns()
    assert rep["filled"] == 1
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM list_track WHERE code='000002'").fetchone()
    assert r["entry_basis"] == "t_close"   # T+1 open 缺 → 降级 T 收盘(09-02 不在序列→取<=T 最近收盘 9.8)
    assert r["ret_k1"] == pytest.approx(10.9 / 9.8 - 1, abs=1e-4)


def test_fill_returns_pending_when_horizon_not_reached(memdb):
    dates = ["2026-09-01", "2026-09-02", "2026-09-03"]   # 只够 k1
    with db.get_conn() as conn:
        _mk_daily(conn, "000003", dates, [9.5, 10.0, 10.5], [9.8, 10.2, 11.0])
    tracker.record_list("nextday", "strict", "2026-09-01",
                        [{"code": "000003", "name": "丙", "score": 0.9}], "score")
    rep = tracker.fill_returns()
    assert rep["filled"] == 0 and rep["pending"] >= 1
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM list_track WHERE code='000003'").fetchone()
    assert r["ret_k1"] == pytest.approx(0.1, abs=1e-4)   # k1 先回填
    assert r["ret_k5"] is None and not r["filled_ts"]     # k5 未到不标完成
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tracker.py -k fill -v`
Expected: FAIL，`fill_returns` 未定义

- [ ] **Step 3: 实现 `fill_returns`**（`backtest/tracker.py` 追加）

```python
def _daily_series(conn, codes: list[str]) -> dict[str, list[dict]]:
    """一次拉全部涉及 code 的 stock_daily（date 升序），{code: [{date,open,close}]}。"""
    out: dict[str, list[dict]] = {c: [] for c in codes}
    if not codes:
        return out
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT code,date,open,close FROM stock_daily WHERE code IN ({ph}) "
        "ORDER BY code, date", codes).fetchall()
    for r in rows:
        out[r["code"]].append({"date": r["date"], "open": r["open"],
                               "close": r["close"]})
    return out


def fill_returns(k_list: tuple = (1, 3, 5)) -> dict:
    """回填前视收益。entry=T+1 开盘(缺→T 收盘,entry_basis 标注),
    ret_k=close(entry_idx+k)/entry-1。各 k 独立幂等回填(WHERE ret_kX IS NULL);
    最大 k 可算时才写 filled_ts。不触网,仅读 stock_daily(不足先 /api/backtest/fetch)。"""
    filled = pending = skipped = 0
    kmax = max(k_list)
    with db.get_conn() as conn:
        todo = conn.execute(
            "SELECT * FROM list_track WHERE filled_ts IS NULL").fetchall()
        if not todo:
            return {"filled": 0, "pending": 0, "skipped": 0}
        series = _daily_series(conn, sorted({r["code"] for r in todo}))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in todo:
            bars = series.get(r["code"]) or []
            dates = [b["date"] for b in bars]
            t = r["date"]
            later = [i for i, d in enumerate(dates) if d > t]
            if not later:
                skipped += 1
                continue
            e_idx = later[0]
            entry = bars[e_idx]["open"]
            basis = "next_open"
            if entry is None:      # T+1 open 缺 → 降级 T 收盘(<=t 最近)
                prior = [i for i, d in enumerate(dates) if d <= t]
                entry = bars[prior[-1]]["close"] if prior else None
                basis = "t_close"
            if not entry:
                skipped += 1
                continue
            rets, complete = {}, True
            for k in k_list:
                x = e_idx + k
                c1 = bars[x]["close"] if x < len(bars) else None
                if c1 is None:
                    complete = False
                    rets[k] = None
                else:
                    rets[k] = round(c1 / entry - 1, 4)
            sets, vals = [], []
            for k in k_list:
                col = f"ret_k{k}"
                if rets[k] is not None and r[col] is None:
                    sets.append(f"{col}=?"); vals.append(rets[k])
            if r["entry_basis"] is None:
                sets.append("entry_basis=?"); vals.append(basis)
            if complete and rets[kmax] is not None:
                sets.append("filled_ts=?"); vals.append(now)
                filled += 1
            else:
                pending += 1
            if sets:
                vals.append(r["id"])
                conn.execute(
                    f"UPDATE list_track SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
    return {"filled": filled, "pending": pending, "skipped": skipped}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: 10 passed

- [ ] **Step 5: 脚本 + 路由**

`scripts/fill_track_returns.py`（薄壳，参照 `scripts/backfill_lhb_history.py` 风格）：

```python
# -*- coding: utf-8 -*-
"""清单验证收益回填(幂等可重跑): python -m scripts.fill_track_returns
读 list_track 未填行 + stock_daily(不足先 /api/backtest/fetch),不触网。"""
from backtest import tracker


def main():
    rep = tracker.fill_returns()
    print(f"filled={rep['filled']} pending={rep['pending']} skipped={rep['skipped']}")


if __name__ == "__main__":
    main()
```

`api/server.py`（tracker 路由区新增，放 quality 路由区之后）：

```python
# ------------------------------------------------------------------
# 清单历史验证（包E）：回填 + 汇总（机械追踪统计，非预测）
# ------------------------------------------------------------------
@app.api_route("/api/track/fill", methods=["GET", "POST"])
def track_fill():
    from backtest import tracker
    rep = tracker.fill_returns()
    return _wrap(rep, {"bt_disclaimer":
                       "历史清单机械追踪统计，非预测，不构成买卖信号，盈亏自负。"})
```

- [ ] **Step 6: Commit**

```bash
git add backtest/tracker.py scripts/fill_track_returns.py api/server.py tests/test_tracker.py
git commit -m "feat(tracker): 前视收益回填(T+1开盘口径)+脚本+/api/track/fill(包E)"
```

### Task 8: `/api/track/summary` + 前端"清单验证"卡

**Files:**
- Modify: `backtest/tracker.py`（追加 `summary`）
- Modify: `api/server.py`（track_fill 之后）
- Modify: `web/index.html`（quality tab `#qsOut` 之后 + nextday 输出容器之后各插一个卡容器；`qsLoad`/`ndLoad` 成功后触发渲染）
- Test: `tests/test_tracker.py`（追加）

**Interfaces:**
- Consumes: `list_track` 表
- Produces: `tracker.summary(module=None, mode=None) -> {"groups": [{"module","mode","n_days","n_rows","n_filled","by_k": {"1": {"avg","median","win_rate","n"}, ...}}]}`；`GET /api/track/summary?module=&mode=`。

- [ ] **Step 1: 写失败测试**（追加 `tests/test_tracker.py`）

```python
def test_summary_aggregates_by_module_mode(memdb):
    tracker.record_list("quality", "strict", "2026-09-01",
                        [{"code": "000001", "adjusted_resonance": 8.0},
                         {"code": "000002", "adjusted_resonance": 7.0}],
                        "adjusted_resonance")
    with db.get_conn() as conn:
        conn.execute("UPDATE list_track SET ret_k1=0.05, ret_k3=0.1, ret_k5=-0.02, "
                     "filled_ts='x' WHERE code='000001'")
        conn.execute("UPDATE list_track SET ret_k1=-0.01, filled_ts='x' WHERE code='000002'")
        conn.commit()
    s = tracker.summary(module="quality")
    g = s["groups"][0]
    assert g["module"] == "quality" and g["mode"] == "strict"
    assert g["n_days"] == 1 and g["n_rows"] == 2 and g["n_filled"] == 2
    assert g["by_k"]["1"]["n"] == 2
    assert g["by_k"]["1"]["avg"] == pytest.approx(0.02, abs=1e-4)
    assert g["by_k"]["1"]["win_rate"] == pytest.approx(0.5)
    assert g["by_k"]["5"]["n"] == 1      # 只有 000001 有 k5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tracker.py::test_summary_aggregates_by_module_mode -v`
Expected: FAIL，`summary` 未定义

- [ ] **Step 3: 实现 summary + 路由**

`backtest/tracker.py` 追加：

```python
def summary(module: str | None = None, mode: str | None = None) -> dict:
    """按 module×mode 聚合追踪统计: 天数/样本/回填数/各k 平均·中位·胜率。"""
    where, params = [], []
    if module:
        where.append("module=?"); params.append(module)
    if mode:
        where.append("mode=?"); params.append(mode)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    groups = []
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM list_track{w}").fetchall()
    agg: dict[tuple, list] = {}
    for r in rows:
        agg.setdefault((r["module"], r["mode"]), []).append(r)
    for (m, md), rs in sorted(agg.items()):
        by_k = {}
        for k in (1, 3, 5):
            vals = [x[f"ret_k{k}"] for x in rs if x[f"ret_k{k}"] is not None]
            if vals:
                svals = sorted(vals)
                mid = len(svals) // 2
                median = (svals[mid] if len(svals) % 2
                          else (svals[mid - 1] + svals[mid]) / 2)
                by_k[str(k)] = {
                    "n": len(vals),
                    "avg": round(sum(vals) / len(vals), 4),
                    "median": round(float(median), 4),
                    "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4)}
            else:
                by_k[str(k)] = {"n": 0, "avg": None, "median": None,
                                "win_rate": None}
        groups.append({
            "module": m, "mode": md,
            "n_days": len({x["date"] for x in rs}),
            "n_rows": len(rs),
            "n_filled": sum(1 for x in rs if x["filled_ts"]),
            "by_k": by_k})
    return {"groups": groups}
```

`api/server.py` track_fill 之后：

```python
@app.get("/api/track/summary")
def track_summary(module: str | None = Query(None),
                  mode: str | None = Query(None)):
    from backtest import tracker
    return _wrap(tracker.summary(module=module, mode=mode), {"bt_disclaimer":
        "历史清单机械追踪统计，非预测，不构成买卖信号，盈亏自负。"})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: 11 passed

- [ ] **Step 5: 前端"清单验证"卡**

① `web/index.html` quality tab：`grep -n 'id="qsOut"' web/index.html`，其后插入：

```html
      <details id="qsTrackBox" style="margin-top:8px"><summary style="cursor:pointer;color:var(--muted);font-size:12px">清单验证（每日快照 × 前视收益机械追踪）</summary><div id="qsTrack"></div></details>
```

nextday 区：`grep -n 'id="ndOut"' web/index.html`（若无 ndOut，找 ndLoad 渲染目标容器 id），其后同样插入：

```html
      <details id="ndTrackBox" style="margin-top:8px"><summary style="cursor:pointer;color:var(--muted);font-size:12px">清单验证（strict vs score 模拟追踪）</summary><div id="ndTrack"></div></details>
```

② JS（放在 qsRender 之后的脚本区）：

```javascript
async function trackRender(module,targetId){
  const el=document.getElementById(targetId); if(!el) return;
  try{
    const r=await fetch(`${API}/api/track/summary?module=${module}`,{signal:AbortSignal.timeout(15000)}).then(r=>r.json());
    const gs=(r.data&&r.data.groups)||[];
    if(!gs.length){el.innerHTML='<div class="empty">暂无追踪样本（默认参数跑一次筛选后自动落库；收益回填 curl -X POST /api/track/fill）</div>';return;}
    const pct=v=>v==null?'—':(v*100).toFixed(1)+'%';
    const rows=gs.map(g=>{
      const k1=g.by_k['1']||{},k3=g.by_k['3']||{},k5=g.by_k['5']||{};
      return `<tr><td>${g.mode}</td><td class="num">${g.n_days}</td><td class="num">${g.n_rows}</td><td class="num">${g.n_filled}</td><td class="num">${pct(k1.win_rate)}</td><td class="num">${pct(k1.median)}</td><td class="num">${pct(k3.win_rate)}</td><td class="num">${pct(k3.median)}</td><td class="num">${pct(k5.win_rate)}</td><td class="num">${pct(k5.median)}</td></tr>`;
    }).join('');
    el.innerHTML=`<table><thead><tr><th>mode</th><th class="num">天数</th><th class="num">样本</th><th class="num">已回填</th><th class="num">k1胜率</th><th class="num">k1中位</th><th class="num">k3胜率</th><th class="num">k3中位</th><th class="num">k5胜率</th><th class="num">k5中位</th></tr></thead><tbody>${rows}</tbody></table><div style="font-size:11px;color:var(--muted)">口径:T+1开盘买入,T+1+k收盘卖出,不含费用 · 机械追踪非预测</div>`;
  }catch(e){el.innerHTML=`<div class="empty">加载失败: ${e.message||e}</div>`;}
}
```

③ 触发：`qsLoad` 成功回调末尾加 `trackRender('quality','qsTrack');`；`ndLoad` 成功回调末尾加 `trackRender('nextday','ndTrack');`（`grep -n "async function qsLoad\|async function ndLoad" web/index.html` 定位，加在各自 `finally`/渲染完成之后）。details 展开时也可刷新：`document.getElementById('qsTrackBox').ontoggle=e=>{if(e.target.open)trackRender('quality','qsTrack');};`（ndTrackBox 同理）。

- [ ] **Step 6: 手工验证**

`curl -X POST http://localhost:8000/api/track/fill`、`curl http://localhost:8000/api/track/summary?module=quality`；浏览器两 tab 展开"清单验证"卡渲染正常。

- [ ] **Step 7: Commit**

```bash
git add backtest/tracker.py api/server.py web/index.html tests/test_tracker.py
git commit -m "feat(tracker): /api/track/summary+前端清单验证卡(包E)"
```

---

## Phase 4 · 包 B 游资追踪

### Task 9: `seat_radar()` 当日龙虎榜 × 席位历史胜率（后端）

**Files:**
- Modify: `screener/smart_money.py`（`seat_winrate` 之后追加）
- Test: `tests/test_seat_radar.py`（新建）

**Interfaces:**
- Consumes: `data.smart_money.collect_seats_stocks(date) -> {"rows": [{"date","code","name","seat","buy","sell","net"}], "date", "error"}`（触网按需，已有）；`db.query_rows`（近 180 日 `channel=龙虎榜` 行，actor=席位名）；`backtest.signals._uni_panels("stock", codes)`；`_fwd_ret(close_s, date_str, k)`
- Produces: `seat_radar(date=None, top=30, win_rate_min=0.55, min_listings=10) -> {"date","rows":[{"code","name","buy_total","hot_count","seats":[{"actor","buy","listings","median_ret_k5","win_rate_k5","hot"}]}],"total","note"}`；排序 `(hot_count DESC, buy_total DESC)`；5min 进程缓存。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""游资追踪 seat_radar（spec 2026-09-06 包B）。全 mock 不触网。"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from data import db
import screener.smart_money as smq

_TODAY = datetime.now().strftime("%Y-%m-%d")

# 当日席位明细(collect_seats_stocks 出口)
SEAT_STOCKS = {"date": _TODAY, "error": None, "rows": [
    {"date": _TODAY, "code": "000001", "name": "甲", "seat": "知名游资A", "buy": 8e7, "sell": 1e7, "net": 7e7},
    {"date": _TODAY, "code": "000001", "name": "甲", "seat": "普通席位B", "buy": 2e7, "sell": 0, "net": 2e7},
    {"date": _TODAY, "code": "000002", "name": "乙", "seat": "普通席位B", "buy": 5e7, "sell": 0, "net": 5e7},
]}

# 历史龙虎榜行(actor=席位): 知名游资A 12 次上榜且后市好; 普通席位B 3 次
def _hist_rows():
    rows = []
    base = datetime.now() - timedelta(days=100)
    for i in range(12):
        d = (base + timedelta(days=i * 5)).strftime("%Y-%m-%d")
        rows.append({"code": "600000", "date": d, "actor": "知名游资A",
                     "channel": "龙虎榜", "amount": 1e7})
    for i in range(3):
        d = (base + timedelta(days=i * 5)).strftime("%Y-%m-%d")
        rows.append({"code": "600000", "date": d, "actor": "普通席位B",
                     "channel": "龙虎榜", "amount": 5e6})
    return rows

HIST = _hist_rows()


def _close_panel():
    # 600000: 每个上榜日 5 日后 +6% → 游资A 胜率 1.0; B 同数据也 1.0 但样本 3<10 非 hot
    dates = pd.date_range(end=datetime.now(), periods=200, freq="D")
    s = pd.Series([10.0] * len(dates), index=dates)
    return pd.DataFrame({"600000": s})


def _mock(monkeypatch, seat_stocks=None, hist=None):
    import data.smart_money as dsm
    monkeypatch.setattr(dsm, "collect_seats_stocks",
                        lambda date=None: seat_stocks if seat_stocks is not None else SEAT_STOCKS)
    def fake(table, where="", params=(), order_by="", limit=0, **kw):
        if table == "smart_money_action":
            if "channel = ?" in where and params and params[0] == "龙虎榜" and "date <= ?" not in where:
                return hist if hist is not None else HIST   # 180日历史查询
            if order_by == "date DESC" and limit == 1:
                return [{"date": _TODAY}]
            return [{"code": "000001", "date": _TODAY, "channel": "龙虎榜", "amount": 9e7},
                    {"code": "000002", "date": _TODAY, "channel": "龙虎榜", "amount": 5e7}]
        return []
    monkeypatch.setattr(db, "query_rows", fake)
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda universe, codes, with_ohlc=False: (_close_panel(), None))


@pytest.fixture(autouse=True)
def _clear():
    smq._SEAT_RADAR_CACHE.clear()
    yield
    smq._SEAT_RADAR_CACHE.clear()


def test_seat_radar_hot_flag_and_sort(monkeypatch):
    _mock(monkeypatch)
    res = smq.seat_radar()
    assert res["date"] == _TODAY
    first = res["rows"][0]
    assert first["code"] == "000001"          # hot_count=1 > 000002 的 0
    assert first["hot_count"] == 1
    sa = next(s for s in first["seats"] if s["actor"] == "知名游资A")
    assert sa["hot"] is True and sa["listings"] >= 10 and sa["win_rate_k5"] == pytest.approx(1.0)
    sb = next(s for s in first["seats"] if s["actor"] == "普通席位B")
    assert sb["hot"] is False                 # 胜率1.0 但样本 3 < 10


def test_seat_radar_no_history_degrades(monkeypatch):
    _mock(monkeypatch, hist=[])
    res = smq.seat_radar()
    assert res["rows"] and res["rows"][0]["seats"][0]["win_rate_k5"] is None
    assert res["note"]                        # 诚实标注缺历史


def test_seat_radar_collect_error_propagates(monkeypatch):
    _mock(monkeypatch, seat_stocks={"rows": [], "date": _TODAY, "error": "akshare 未安装"})
    res = smq.seat_radar()
    assert res["rows"] == [] and res["error"] == "akshare 未安装"
```

> mock 的 `fake` 分支按最终实现的查询形状对齐（实现先行确定查询语义后允许微调 mock 判别条件，但**测试断言不得改**）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_seat_radar.py -v`
Expected: FAIL，`_SEAT_RADAR_CACHE`/`seat_radar` 未定义

- [ ] **Step 3: 实现 `seat_radar`**（`screener/smart_money.py` `seat_winrate` 之后）

```python
_SEAT_RADAR_CACHE: dict = {}
_SEAT_RADAR_TTL = 300.0   # 盘后数据日内不变, 5min


def _seat_hist_stats(days: int = 180, k: int = 5) -> dict[str, dict]:
    """批量席位历史胜率: 一次查近 days 日全部龙虎榜行按 actor 分组,
    _uni_panels 一次加载全部 code, 内存算每席 listings/median_ret_k/win_rate_k。
    替代逐席调 seat_winrate 的 N 次查询+N 次 panel 加载。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.query_rows("smart_money_action",
                         where="channel = ? AND date >= ?",
                         params=("龙虎榜", since), order_by="", limit=0)
    by_actor: dict[str, set] = {}
    codes = set()
    for r in rows:
        a, c, d = r.get("actor"), r.get("code"), r.get("date")
        if not a or not c or not d:
            continue
        by_actor.setdefault(str(a), set()).add((str(c), str(d)))
        codes.add(str(c))
    if not by_actor:
        return {}
    from backtest.signals import _uni_panels
    close, _ = _uni_panels("stock", sorted(codes))
    out: dict[str, dict] = {}
    for a, pairs in by_actor.items():
        rets = []
        for c, d in sorted(pairs):
            if close is None or c not in close.columns:
                continue
            r = _fwd_ret(close[c], d, k)
            if r is not None:
                rets.append(r)
        out[a] = {"listings": len(pairs),
                  "median_ret_k5": round(float(np.median(rets)), 4) if rets else None,
                  "win_rate_k5": (round(sum(1 for x in rets if x > 0) / len(rets), 4)
                                  if rets else None),
                  "n_ret": len(rets)}
    return out


def seat_radar(date: str | None = None, top: int = 30,
               win_rate_min: float = 0.55, min_listings: int = 10) -> dict:
    """游资追踪日视图: 当日龙虎榜个股 × 买入席位历史胜率联动。
    hot=胜率>=win_rate_min 且样本>=min_listings(机械阈值)。
    排序 (hot_count DESC, buy_total DESC)。触网: collect_seats_stocks 按需(慢 N×1)。
    历史统计事实非预测,机械联动非买卖信号。"""
    import time as _t
    key = (date, top, win_rate_min, min_listings)
    now = _t.time()
    hit = _SEAT_RADAR_CACHE.get(key)
    if hit and now - hit[0] < _SEAT_RADAR_TTL:
        return hit[1]
    import data.smart_money as dsm
    res = dsm.collect_seats_stocks(date)
    d0 = res.get("date")
    if res.get("error") or not res.get("rows"):
        out = {"date": d0, "rows": [], "total": 0,
               "error": res.get("error"), "note": None}
        _SEAT_RADAR_CACHE[key] = (now, out)
        return out
    stats = _seat_hist_stats()
    note = None if stats else "无龙虎榜席位历史(先跑 scripts/backfill_lhb_history.py),胜率列空"
    by_stock: dict[str, dict] = {}
    for r in res["rows"]:
        code = str(r.get("code") or "")
        if not code:
            continue
        s = by_stock.setdefault(code, {"code": code, "name": r.get("name"),
                                       "buy_total": 0.0, "seats": [], "hot_count": 0})
        seat = str(r.get("seat") or "")
        buy = _nan(r.get("buy")) or 0.0
        s["buy_total"] += buy
        st = stats.get(seat) or {}
        wr, ls = st.get("win_rate_k5"), st.get("listings")
        hot = bool(wr is not None and ls is not None
                   and wr >= win_rate_min and ls >= min_listings)
        s["seats"].append({"actor": seat, "buy": _nan(r.get("buy")),
                           "net": _nan(r.get("net")),
                           "listings": ls, "median_ret_k5": st.get("median_ret_k5"),
                           "win_rate_k5": wr, "hot": hot})
        if hot:
            s["hot_count"] += 1
    rows_out = sorted(by_stock.values(),
                      key=lambda x: (x["hot_count"], x["buy_total"]), reverse=True)[:top]
    for s in rows_out:
        s["buy_total"] = _nan(round(s["buy_total"], 2))
        s["seats"].sort(key=lambda x: (x["hot"], x["buy"] or 0), reverse=True)
    out = {"date": d0, "rows": rows_out, "total": len(rows_out),
           "error": None, "note": note}
    _SEAT_RADAR_CACHE[key] = (now, out)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_seat_radar.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add screener/smart_money.py tests/test_seat_radar.py
git commit -m "feat(smart-money): seat_radar 当日龙虎榜×席位历史胜率批量联动(包B后端)"
```

### Task 10: `/api/smart-money/seat-radar` 路由 + 前端"游资追踪"

**Files:**
- Modify: `api/server.py`（radar 路由之后）
- Modify: `web/index.html`（`#smSeatsStocks` 按钮旁约 276 行加按钮；渲染函数放 `smRenderRadar` 之后）
- Test: `tests/test_seat_radar.py`（追加路由测试）

**Interfaces:**
- Consumes: Task 9 `smq.seat_radar(date, top)`
- Produces: `GET /api/smart-money/seat-radar?date=&top=`；前端 `smSeatRadar()`。

- [ ] **Step 1: 路由测试**（追加 `tests/test_seat_radar.py`）

```python
def test_seat_radar_route(monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as srv
    monkeypatch.setattr(smq, "seat_radar",
                        lambda date=None, top=30: {"date": "2026-09-04", "rows": [],
                                                   "total": 0, "error": None, "note": None})
    c = TestClient(srv.app)
    r = c.get("/api/smart-money/seat-radar")
    assert r.status_code == 200
    assert "非预测" in r.json()["meta"]["cand_disclaimer"]
```

> server.py 路由需经 `sm_query.seat_radar(...)` 属性访问（与 Task 2 同一导入别名），保证可 patch。`meta` 键路径以 `_wrap` 实际结构为准（对照 Task 2 测试的处理方式）。

- [ ] **Step 2: 跑测试确认失败 → 加路由**

Run: `python -m pytest tests/test_seat_radar.py::test_seat_radar_route -v` → FAIL 404

`api/server.py` radar 路由后：

```python
@app.get("/api/smart-money/seat-radar")
def smart_money_seat_radar(date: str | None = Query(None),
                           top: int = Query(30, ge=1, le=100)):
    """游资追踪日视图: 当日龙虎榜个股×买入席位历史胜率(批量,5min缓存)。
    慢路径(席位明细按需 N×1 调用)。历史统计非预测,机械联动非买卖信号。"""
    res = sm_query.seat_radar(date=date, top=top)
    return _wrap(res["rows"], {
        "total": res["total"], "date": res.get("date"),
        "note": res.get("note"), "error": res.get("error"),
        "cand_disclaimer": "席位历史胜率×当日上榜机械联动，历史统计非预测，非买卖信号，盈亏自负。"})
```

Run: `python -m pytest tests/test_seat_radar.py -v` → 4 passed

- [ ] **Step 3: 前端按钮 + 渲染**

`#smSeatsStocks` 按钮（约 276 行）之后加：

```html
        <button class="btn ghost" id="smSeatRadar" type="button" title="当日龙虎榜个股×买入席位历史胜率(高胜率游资标hot,慢:席位明细按需取,5min缓存)">游资追踪</button>
```

JS（`smRenderRadar` 函数之后）：

```javascript
async function smSeatRadarLoad(){
  const out=document.getElementById('smOut');
  out.innerHTML='<div class="empty">游资追踪加载中(当日龙虎榜×席位历史胜率,席位明细按需取较慢,5min缓存)...</div>';
  try{
    const r=await fetch(`${API}/api/smart-money/seat-radar?top=30`,{signal:AbortSignal.timeout(120000)}).then(r=>r.json());
    const rows=(r.data&&r.data.rows)||[];
    const note=(r.meta&&r.meta.note)||(r.data&&r.data.note)||'';
    if(!rows.length){out.innerHTML=`<div class="empty">${(r.meta&&r.meta.error)||note||'当日无龙虎榜数据'}</div>`;return;}
    const trs=rows.map(x=>{
      const seats=(x.seats||[]).map(s=>{
        const wr=s.win_rate_k5!=null?(s.win_rate_k5*100).toFixed(0)+'%':'—';
        const md=s.median_ret_k5!=null?(s.median_ret_k5*100).toFixed(1)+'%':'—';
        const hot=s.hot?'<b style="color:#DC2626">🔥</b>':'';
        return `<span style="font-size:11px;margin-right:6px" title="${s.actor||''} 买入${s.buy!=null?(s.buy/1e4).toFixed(0)+'万':'—'} · 上榜${s.listings??'—'}次 · k5中位${md} · 胜率${wr}">${hot}${(s.actor||'').slice(0,8)} ${wr}</span>`;
      }).join('');
      return `<tr class="clk" data-code="${x.code||''}"><td>${x.code||''}</td><td>${x.name||''}</td><td class="num"><b style="color:#DC2626">${x.hot_count||0}</b></td><td class="num">${x.buy_total!=null?(x.buy_total/1e4).toFixed(0)+'万':'—'}</td><td>${seats}</td></tr>`;
    }).join('');
    out.innerHTML=`<div style="font-size:12px;margin:4px 0;color:var(--muted)">游资追踪 (${rows.length}) · ${r.data.date||r.meta?.date||'-'} · 🔥=胜率≥55%且上榜≥10次(机械阈值) · ${note} · 历史统计非预测</div><table><thead><tr><th>代码</th><th>名称</th><th class="num">🔥数</th><th class="num">买入合计</th><th>买入席位(胜率)</th></tr></thead><tbody>${trs}</tbody></table>`;
    out.querySelectorAll('tr.clk').forEach(tr=>tr.onclick=()=>{const c=tr.dataset.code;if(c){switchTab('analysis');/* 填码触发与 Task 2 同款 handler */}});
  }catch(e){out.innerHTML=`<div class="empty">加载失败(接口较慢可能超时): ${e.message||e}</div>`;}
}
document.getElementById('smSeatRadar').onclick=smSeatRadarLoad;
```

> 行点击跳分析复用 Task 2 已确认的现有 handler 写法（同一处 grep 结果），替换注释行。

- [ ] **Step 4: 手工验证 + Commit**

浏览器点"游资追踪"按钮：有 backfill 历史时席位带胜率与 🔥；无历史时 note 提示回填脚本。

```bash
git add api/server.py web/index.html tests/test_seat_radar.py
git commit -m "feat(smart-money): /api/smart-money/seat-radar + 前端游资追踪(包B)"
```

---

## Phase 5 · 包 C 北向备源（spike）

### Task 11: 北向个股级数据探源 spike

**Files:**
- Create: `docs/superpowers/specs/2026-09-06-northbound-source-probe.md`（结论文档，唯一保留产物）
- 探查脚本一律临时文件（`_probe_*.py`），**不进 git**，结束删除

**Interfaces:**
- Produces: 探源结论 + 决策门（探到→另立接线任务；探不到→关闭本包）。

- [ ] **Step 1: 探源三连**（每源 10 分钟内出结论，出口 IP 直测）

```bash
# ① HKEX CCASS 持股披露(官方, T+1): akshare 是否有可用包装
python -c "import akshare as ak; print([f for f in dir(ak) if 'hsgt' in f or 'hkex' in f or 'ccass' in f.lower()])"
# 逐个试可个股级持股的函数(如 stock_hsgt_stock_hold_pos_em / stock_hkex_...), 记录: 可达? 字段? 滞后?

# ② a-stock-data(CLAUDE.md 记录的官方交易所备胎方向)
python -m pip install a-stock-data -q 2>/dev/null; python -c "import a_stock_data as a; print(dir(a))" 2>/dev/null || echo "无此包/装不上"

# ③ 交易所官网直取: 沪股通/深股通日度持股汇总页 requests 探测
python - <<'EOF'
import requests
for url in ("http://query.sse.com.cn/", "http://www.szse.cn/api/report/ShowReport?CATALOGID=SGT_LSCC"):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        print(url, r.status_code, len(r.content))
    except Exception as e:
        print(url, "FAIL", e)
EOF
```

- [ ] **Step 2: 写结论文档**

`docs/superpowers/specs/2026-09-06-northbound-source-probe.md`：每源记录可达性/字段覆盖（持股量/市值/占比）/滞后/反爬风险/结论（可用|不可用+原因）。

- [ ] **Step 3: 决策门**

- 探到可用个股级源 → 向用户汇报并**另立接线任务**（改 `data/smart_money.py::collect_northbound` 在十大成交股前插备援 + `(df, ok, err)` 约定 + 幂等回填近 30 日脚本 + 测试），经批准后实施；quality 口径 3 无需改动（读库自动复活）；
- 全部不可用 → 结论文档记录后关闭本包，现状降级不变。

- [ ] **Step 4: Commit（仅结论文档）**

```bash
git add docs/superpowers/specs/2026-09-06-northbound-source-probe.md
git commit -m "docs(specs): 北向个股级数据探源结论(包C spike)"
```

---

## Phase 6 · 收尾

### Task 12: 全量回归 + CLAUDE.md 同步 + 部署

**Files:**
- Modify: `CLAUDE.md`（路由速查/架构/改动检查清单）

- [ ] **Step 1: 全量测试 + 静态检查**

```bash
python -m pytest tests/ -q
python -m compileall api data screener backtest scripts tests
```

Expected: 全绿、无编译错误。

- [ ] **Step 2: CLAUDE.md 同步**（对照"改动检查清单"）

- 路由速查：加 `/api/smart-money/radar`、`/api/smart-money/seat-radar`、`/api/track/fill`、`/api/track/summary`（各附 disclaimer 文案与参数）；
- 架构段：`screener/smart_money.py` 加 radar/seat_radar/_seat_hist_stats 描述；`backtest/quality.py` 加包 D 富化段描述；新增 `backtest/tracker.py`（list_track 表、默认参数判定、回填口径 T+1 open、watchlist 先例不走 upsert_rows）；`scripts/fill_track_returns.py`；
- 关键设计决策：list_track 收益口径（entry=T+1 open 降级 t_close，exit=entry_idx+k，不含费用）；radar 股数通道量纲隔离；quality 改路由默认参数时**同步 `tracker.DEFAULTS`**；
- 数据依赖链：seat_radar 依赖 backfill 历史 + collect_seats_stocks 触网慢路径（5min 缓存）。

- [ ] **Step 3: 部署验证**

```bash
bash deploy.sh
curl -s http://localhost:8000/api/smart-money/radar?days=5 | head -c 400
curl -s http://localhost:8000/api/track/summary | head -c 400
curl -s -X POST http://localhost:8000/api/track/fill | head -c 200
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): 同步主力雷达/游资追踪/行为联动/清单验证四包落地"
```

---

## Self-Review 记录

- **Spec 覆盖**：包 A→Task 1/2；包 B→Task 9/10；包 C→Task 11；包 D→Task 3/4；包 E→Task 5/6/7/8；实施顺序 A+D→E→B→C 与 spec 一致（Phase 1 A、Phase 2 D、Phase 3 E、Phase 4 B、Phase 5 C）；spec"风险与边界"中 D 的 phase 降级阈值（实测拖慢>5s 改 top 10）在 Task 12 部署验证时观察 /api/quality 冷启动耗时决定，若触发则改富化段 `main` 为 `main[:10]` 并同步测试。
- **类型一致性**：`radar()` 返回键（rows/total/date/note）与 Task 2 路由消费一致；`seat_radar()` 返回键与 Task 10 一致；`record_list(module, mode, date, items, score_key)` 五参在 Task 6 钩子/Task 7 测试中一致；`fill_returns()` 返回 `{filled,pending,skipped}` 与脚本/路由一致；`_behavior_batch` 新增键 `cum_net` 在 Task 3 测试与 quality 富化段一致。
- **占位符**：前端两处"复用现有 handler"均给出 grep 锚点与替换位置，属"照抄既有模式"指令而非 TBD；其余步骤均含完整代码。

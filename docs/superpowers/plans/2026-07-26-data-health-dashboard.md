# 数据健康仪表盘 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 跨 tab 顶部 banner + 抽屉展示全局数据健康（7 域三态 + overall），`/api/health` 只读聚合，60s 轮询，顺手重写 README。

**Architecture:** 不新增表/列。`/api/health` 用 `db.get_conn()` SQL COUNT/MIN/MAX 聚合 7 域 + 复用 `smart_money.channel_status()`；前端顶部 banner + `#healthDrawer`（复用 `#pfDrawer` 模式）+ 60s 轮询。

**Tech Stack:** Python 3.12 + FastAPI + SQLite + 原生 JS。pytest 合成数据 mock。

## Global Constraints

- 合规：措辞"数据健康/新鲜度/观察清单"，禁"推荐/买入信号"；默认 disclaimer 经 `_wrap` 附。
- 只读聚合：不触发采集、不写表。手动刷新调既有 `/api/refresh`+`/api/smart-money/refresh`。
- 单域查询 try/except 不崩，该域标 red+err。
- NaN→None 经 `_wrap` 兜底（聚合标量无 DataFrame 风险）。
- 仓库根目录跑 `python -m pytest tests/ -q`。

## File Structure

- `api/server.py`（修改）：+`/api/health` 路由 + `_collect_health()` 聚合函数。
- `web/index.html`（修改）：+`#healthBar` banner + `#healthDrawer` 抽屉 + `healthLoad()`/`toggleHealth()` JS + 60s 轮询。
- `tests/test_health.py`（新）：5 测试。
- `README.md`（重写）：门面更新。
- `data/` 层：**不改**。

---

## Task 1 (P1): `/api/health` 聚合路由

**Files:**
- Modify: `api/server.py`（+路由 +`_collect_health`，放在 `/api/meta` 之后）
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `db.get_conn()`/`db.last_update_time()`/`db.get_meta()`/`smart_money.channel_status()`；表 `stock_spot`/`etf_spot`/`stock_daily`/`etf_daily`/`board_daily`/`smart_money_action`/`financial_abstract_cache`/`fundamentals_cache`/`research_report`/`st_list`/`portfolio`。
- Produces: `GET /api/health` → `_wrap({domains, update_time, last_refresh_time, overall})`。

- [ ] **Step 1: 写失败测试** `tests/test_health.py`

```python
# -*- coding: utf-8 -*-
"""数据健康仪表盘单测：mock db.get_conn/last_update_time + smart_money.channel_status，不触网。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from api import server


class _FakeCur:
    def __init__(self, val): self._val = val
    def fetchone(self): return self._val[0] if isinstance(self._val, list) else self._val
    def fetchall(self): return self._val if isinstance(self._val, list) else [self._val]


class _FakeConn:
    """按 sql 子串匹配预置结果。"""
    def __init__(self, mapping): self._m = mapping  # {sql_substring: row(list) or scalar}
    def execute(self, sql, *params):
        for key, val in self._m.items():
            if key in sql:
                return _FakeCur(val)
        return _FakeCur(None)
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _patch_db(monkeypatch, mapping, update_time="2026-07-31 14:20:00",
              last_refresh="2026-07-31 14:20:00", channels=None):
    monkeypatch.setattr(server.db, "get_conn", lambda: _FakeConn(mapping))
    monkeypatch.setattr(server.db, "last_update_time", lambda: update_time)
    monkeypatch.setattr(server.db, "get_meta", lambda k, default="": last_refresh)
    from data import smart_money as sm
    monkeypatch.setattr(sm, "channel_status", lambda: channels or {
        "资金流": {"ok": True, "rows": 5200, "date": "2026-07-31",
                  "stale": False, "last_ok_date": "2026-07-31"}})


def _client():
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def test_health_returns_all_domains(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": (100,), "MAX(date": ("2026-07-31",), "MIN(date": ("2023-01-01",)})
    r = _client().get("/api/health").json()["data"]
    dom = r["domains"]
    for k in ("spot", "history", "smart_money", "fundamentals",
              "research", "st_list", "portfolio"):
        assert k in dom and "status" in dom[k]
    assert r["overall"] in ("green", "yellow", "red")
    assert r["update_time"] == "2026-07-31 14:20:00"


def test_health_status_derivation(monkeypatch):
    # smart_money 任一通道 stale → yellow
    _patch_db(monkeypatch, {"COUNT": (100,), "MAX(date": ("2026-07-31",)},
               channels={"资金流": {"ok": False, "stale": True, "stale_date": "2026-07-29",
                                   "last_ok_date": "2026-07-29", "rows": 100}})
    sm = _client().get("/api/health").json()["data"]["domains"]["smart_money"]
    assert sm["status"] == "yellow"


def test_health_overall_worst_domain(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": (0,)},
               channels={"资金流": {"ok": False, "stale": True, "last_ok_date": "2026-07-29", "rows": 1}})
    r = _client().get("/api/health").json()["data"]
    assert r["overall"] == "red"


def test_health_partial_failure(monkeypatch):
    class _C:
        def execute(self, sql, *p):
            if "stock_daily" in sql:
                raise RuntimeError("history boom")
            return _FakeCur((100,))
        def __enter__(self): return self
        def __exit__(self,*a): pass
    monkeypatch.setattr(server.db, "get_conn", lambda: _C())
    monkeypatch.setattr(server.db, "last_update_time", lambda: "2026-07-31 14:20:00")
    monkeypatch.setattr(server.db, "get_meta", lambda k, default="": "")
    from data import smart_money as sm
    monkeypatch.setattr(sm, "channel_status", lambda: {
        "资金流": {"ok": True, "rows": 1, "date": "2026-07-31", "stale": False, "last_ok_date": "2026-07-31"}})
    dom = _client().get("/api/health").json()["data"]["domains"]
    assert dom["history"]["status"] == "red"
    assert "boom" in dom["history"].get("err", "")
    assert dom["spot"]["status"] != "red"


def test_health_smart_money_reuses_channel_status(monkeypatch):
    _patch_db(monkeypatch, {"COUNT": (10,), "MAX(date": ("2026-07-31",)},
               channels={"北向": {"ok": True, "stale": True, "stale_date": "2026-07-29",
                                  "last_ok_date": "2026-07-29", "rows": 20, "date": "2026-07-29"}})
    sm = _client().get("/api/health").json()["data"]["domains"]["smart_money"]
    assert sm["channels"]["北向"]["stale"] is True
    assert sm["channels"]["北向"]["last_ok_date"] == "2026-07-29"
```

- [ ] **Step 2: 跑测试验证失败**

`python -m pytest tests/test_health.py -q` → FAIL（`/api/health` 不存在）。

- [ ] **Step 3: 实现 `_collect_health` + 路由**（`api/server.py`，`/api/meta` 之后）

```python
import datetime as _dt


def _domain_status(rows, latest, stale=False, has_old=True):
    """三态：red(空/失败) > yellow(stale 或非当日有旧) > green(当日非空)。"""
    if rows is None:
        return "red"
    if rows == 0:
        return "red" if not has_old else "yellow"
    if stale:
        return "yellow"
    if latest and latest.startswith(_dt.date.today().strftime("%Y-%m-%d")):
        return "green"
    if latest:
        return "yellow"
    return "green"


def _collect_health():
    """聚合 7 数据域健康（只读，单域失败不崩）。"""
    from data import smart_money as sm
    domains = {}
    try:
        with db.get_conn() as conn:
            def _q(sql):
                return conn.execute(sql).fetchone()
            def _n(t):
                try:
                    r = _q(f"SELECT COUNT(*) AS n FROM {t}")
                    return r["n"] if r else 0
                except Exception:
                    return None
            ss, es = _n("stock_spot"), _n("etf_spot")
            ut = db.last_update_time()
            domains["spot"] = {"stock": {"rows": ss}, "etf": {"rows": es},
                               "status": _domain_status((ss or 0)+(es or 0), ut)}
            try:
                h = _q("SELECT COUNT(*) AS n, MIN(date) AS mn, MAX(date) AS mx, "
                       "COUNT(DISTINCT code) AS cc FROM stock_daily")
                etd, bdd = _n("etf_daily"), _n("board_daily")
                domains["history"] = {"rows": h["n"] if h else 0, "codes": h["cc"] if h else 0,
                                      "date_range": [h["mn"], h["mx"]] if h else [None, None],
                                      "etf_rows": etd, "board_rows": bdd,
                                      "status": "green" if h and h["n"] else "red"}
            except Exception as e:
                domains["history"] = {"status": "red", "err": str(e)}
            try:
                smr = _q("SELECT COUNT(*) AS n, MAX(date) AS d FROM smart_money_action")
                ch = sm.channel_status()
                any_stale = any(v.get("stale") for v in ch.values())
                any_red = any(not v.get("ok") and not v.get("last_ok_date") for v in ch.values())
                domains["smart_money"] = {"channels": ch, "rows": smr["n"] if smr else 0,
                                          "latest_date": smr["d"] if smr else "",
                                          "status": "red" if any_red else ("yellow" if any_stale else "green")}
            except Exception as e:
                domains["smart_money"] = {"status": "red", "err": str(e)}
            try:
                fa, fc = _n("financial_abstract_cache"), _n("fundamentals_cache")
                domains["fundamentals"] = {"abstract": {"hit": fa or 0}, "full": {"hit": fc or 0},
                                            "status": "green" if (fa or fc) else "red"}
            except Exception as e:
                domains["fundamentals"] = {"status": "red", "err": str(e)}
            try:
                rr = _q("SELECT COUNT(*) AS n, MAX(date) AS d FROM research_report")
                domains["research"] = {"rows": rr["n"] if rr else 0, "latest": rr["d"] if rr else "",
                                       "status": _domain_status(rr["n"] if rr else 0, rr["d"] if rr else "")}
            except Exception as e:
                domains["research"] = {"status": "red", "err": str(e)}
            try:
                sl = _q("SELECT COUNT(*) AS n FROM st_list")
                domains["st_list"] = {"rows": sl["n"] if sl else 0,
                                      "status": _domain_status(sl["n"] if sl else 0, ut)}
            except Exception as e:
                domains["st_list"] = {"status": "red", "err": str(e)}
            try:
                pf = _q("SELECT COUNT(*) AS n FROM portfolio")
                domains["portfolio"] = {"positions": pf["n"] if pf else 0, "status": "green"}
            except Exception as e:
                domains["portfolio"] = {"status": "red", "err": str(e)}
    except Exception as e:
        for k in ("spot", "history", "smart_money", "fundamentals",
                  "research", "st_list", "portfolio"):
            domains.setdefault(k, {"status": "red", "err": str(e)})
    order = {"red": 0, "yellow": 1, "green": 2}
    overall = "green"
    for d in domains.values():
        s = d.get("status", "red")
        if order.get(s, 0) < order.get(overall, 2):
            overall = s
    return {"domains": domains, "update_time": db.last_update_time(),
            "last_refresh_time": db.last_update_time(), "overall": overall}


@app.get("/api/health")
def health():
    """数据健康聚合（只读）：各域新鲜度三态+overall。非荐股，默认 disclaimer。"""
    return _wrap(_collect_health())
```

> `last_refresh_time` 复用 `last_update_time`（YAGNI，不改 collector）。

- [ ] **Step 4: 跑测试验证通过**

`python -m pytest tests/test_health.py -q` → PASS。

- [ ] **Step 5: 回归 + 提交**

`python -m pytest tests/ -q` → 全 PASS。
```bash
git add api/server.py tests/test_health.py
git commit -m "feat(api): /api/health 数据健康聚合路由(7域三态+overall)"
```

---

## Task 2 (P2): 前端 banner + 抽屉 + 60s 轮询

**Files:**
- Modify: `web/index.html`：+`#healthBar`(顶部，tab 按钮行之上) + `#healthDrawer`(仿 `#pfDrawer`) + JS。

**Interfaces:**
- Consumes: `GET /api/health` → `{domains, update_time, last_refresh_time, overall}`。

- [ ] **Step 1: 加 banner HTML**（`.tabs` 之前）

```html
<div id="healthBar" style="display:flex;align-items:center;gap:10px;padding:6px 12px;border-bottom:1px solid var(--border);font-size:12px">
  <span id="hbOverall" style="font-size:14px">●</span>
  <span id="hbTime">数据 …</span>
  <span id="hbDots" style="color:var(--muted)"></span>
  <button id="hbDetail" class="btn ghost" type="button" style="font-size:12px;padding:2px 10px">详情▾</button>
</div>
```

- [ ] **Step 2: 加 `#healthDrawer`**（`#pfDrawer` 旁）

```html
<aside id="healthDrawer" hidden style="position:fixed;right:0;top:0;bottom:0;width:360px;background:var(--bg);border-left:1px solid var(--border);padding:16px;overflow-y:auto;z-index:50">
  <div class="card-t" style="display:flex;justify-content:space-between">数据健康 <button onclick="toggleHealth()" type="button" class="btn ghost">×</button></div>
  <div id="healthBody" style="margin-top:12px"></div>
  <div style="margin-top:16px">
    <div id="hbRefreshTime" style="color:var(--muted);font-size:12px"></div>
    <button id="hbRefresh" class="btn" type="button" style="margin-top:6px">手动刷新采集</button>
  </div>
  <div class="disc" style="margin-top:16px"><b>数据健康</b>为新鲜度观察，非投资建议。</div>
</aside>
```

- [ ] **Step 3: 加 JS**（`<script>` 内）

```javascript
const _HCOLOR={green:'#16A34A',yellow:'#F59E0B',red:'#5C6884'};
async function healthLoad(){
  try{
    const r=await fetch(`${API}/api/health`).then(r=>r.json());
    const d=r.data||{};
    document.getElementById('hbTime').textContent='数据 '+(r.update_time||'…');
    const ov=d.overall||'red';
    const oE=document.getElementById('hbOverall');
    oE.style.color=_HCOLOR[ov]||_HCOLOR.red;
    oE.textContent=ov==='yellow'?'◐':'●';
    const dom=d.domains||{};
    document.getElementById('hbDots').innerHTML=Object.entries(dom).map(([k,v])=>{
      const s=(v&&v.status)||'red';
      return `<span style="color:${_HCOLOR[s]||_HCOLOR.red};margin-right:8px" title="${k}:${s}">${s==='yellow'?'◐':'●'}${k}</span>`;
    }).join('');
    document.getElementById('hbRefreshTime').textContent='上次采集: '+(d.last_refresh_time||r.update_time||'—');
    if(!document.getElementById('healthDrawer').hidden) healthRender(d);
  }catch(e){
    const oE=document.getElementById('hbOverall'); oE.textContent='●'; oE.style.color=_HCOLOR.red;
    document.getElementById('hbTime').textContent='数据健康检查失败';
  }
}
function healthRender(d){
  const dom=d.domains||{};
  const names={spot:'板块/ETF快照',history:'历史日线',smart_money:'主力动向',fundamentals:'财报缓存',research:'研报/千评',st_list:'ST名单',portfolio:'持仓'};
  const rows=Object.entries(dom).map(([k,v])=>{
    const s=(v&&v.status)||'red';
    const det=v?Object.entries(v).filter(([kk])=>kk!=='status'&&kk!=='err').map(([kk,vv])=>`${kk}=${vv instanceof Object?JSON.stringify(vv):vv}`).join(' '):'';
    const err=v&&v.err?` <span style="color:var(--warn)">${v.err.slice(0,40)}</span>`:'';
    return `<tr><td>${names[k]||k}</td><td style="color:${_HCOLOR[s]||_HCOLOR.red}">${s==='yellow'?'◐':'●'}${s}</td><td style="font-size:11px;color:var(--muted)">${det}${err}</td></tr>`;
  }).join('');
  document.getElementById('healthBody').innerHTML=`<table><thead><tr><th>域</th><th>状态</th><th>详情</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function toggleHealth(){
  const dr=document.getElementById('healthDrawer');
  dr.hidden=!dr.hidden;
  if(!dr.hidden) healthLoad();
}
document.getElementById('hbDetail').onclick=toggleHealth;
document.getElementById('hbRefresh').onclick=async function(){
  const btn=this; btn.disabled=true; btn.textContent='采集中…';
  try{ await fetch(`${API}/api/smart-money/refresh`,{method:'POST'}); await fetch(`${API}/api/refresh`,{method:'POST'}); }
  finally{ btn.disabled=false; btn.textContent='手动刷新采集'; await healthLoad(); }
};
setInterval(healthLoad,60000);
window.addEventListener('load',healthLoad);
```

- [ ] **Step 4: 手测**

启动服务 → `/web/index.html`：顶部 banner 显整体灯+update_time+域小灯串；"详情▾"展开抽屉；等 60s 自动刷新；"手动刷新采集"触发采集后更新。

- [ ] **Step 5: 提交**

```bash
git add web/index.html
git commit -m "feat(web): 数据健康顶部banner+抽屉+60s轮询"
```

---

## Task 3 (P3): README 重写

**Files:**
- Modify: `README.md`（整体重写）

- [ ] **Step 1: 重写 README** 结构（面向用户，不复制 CLAUDE.md 的"关键设计决策"）：

1. **产品定位**（一段）：本地优先 A 股板块/ETF 筛选+回测+主力动向观察工具。
2. **合规边界**（一段）：非投资咨询，不荐股不下单不承诺收益；输出为机械排序观察清单。
3. **功能**：4 tab（实时筛选/历史回测/主力动向/优质筛选）+ 持仓浮窗 + 数据健康 banner。
4. **快速开始**：`docker compose up --build -d` → `http://localhost:8000/web/index.html`；Swagger `/docs`；`POST /api/refresh` 触发采集。
5. **路由速查**（精简，按域分组）：筛选/回测/候选池/信号/主力动向/优质/基本面/研报/持仓/数据健康。
6. **架构**（文字图）：四层 data/screener/backtest/api + 领域模块。
7. **数据源与已知限制**：AKShare；东财 IP 封禁+备援链；北向盘后十大成交股口径（2024-08 起实时停披露）；概念板块无涨跌幅。
8. **测试/开发**：`python -m pytest tests/ -q`（根目录）。
9. 链接 CLAUDE.md（开发指引）+ docs/superpowers/specs|plans（设计文档）。

- [ ] **Step 2: 校验**

`grep -c "阶段1\|stage 1" README.md` → 0。路由表与 `api/server.py` 路由数核对（~30）。

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: README 重写门面(路由/架构/合规/数据源限制)"
```

---

## Self-Review

**Spec 覆盖**：§4 `/api/health` → Task 1 ✓；§5 banner+抽屉+轮询 → Task 2 ✓；§6 README → Task 3 ✓；§8 测试 5 条 → Task 1 含全 5 ✓。

**Placeholder 扫描**：无 TBD；Task 1/2 含完整代码。Task 3 README 为内容型文档给结构+要点（非代码，实施时填充）。

**类型一致**：`_collect_health` 返回结构与测试读取、前端 `healthLoad` 读取一致；`_domain_status(rows, latest, stale, has_old)` 签名与调用一致；前端 `_HCOLOR` 三态色与后端 `green/yellow/red` 一致。

**风险标注**：
- Task 1 `_n(t)` 表不存在（旧库未迁移）时返回 None → 该域 red，不崩；`portfolio` 表同。
- Task 1 `last_refresh_time` 复用 `last_update_time`（YAGNI，不改 collector）。
- Task 2 前端无单测，靠手测。
- Task 3 README 路由数需与 server.py 实测对齐。

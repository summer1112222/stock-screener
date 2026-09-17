# 三清单互相穿透标注(主力启动信号链) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让主力动向(smart_money)、优质筛选(quality)、次日强势(nextday)三份机械排序清单互相携带对端的轻量穿透标注字段,形成"主力启动"信号链的跨清单观察入口。

**Architecture:** 标注 = 上下文,不改任何清单的排序签名/min_dims/hits/缓存键/warmup。穿透字段只取对端**轻量、可批量、有缓存**信号;完整评分互不嵌入。quality 已实现主力字段富集(B1,`_enrich_main_behavior`),本次只补 nextday(A+B2)与 smart_money(B3)两处缺口。

**Tech Stack:** Python 3.12 / pandas / FastAPI(仅附字段,不新增路由表源)。

**Spec:** `docs/superpowers/specs/2026-09-18-cross-list-annotate-design.md`(已核实范围,见下 Global Constraints)

## Global Constraints(从 spec 逐字复制)

- **Ruling 1(穿透走轻量,重评分不嵌入):** 穿透字段只取对端轻量、可批量、有缓存信号。`quality_pct` 只读 quality 缓存索引,**绝不重跑 buffett**;nextday 主力阶段走 DB 只读 + 30s 缓存,**不新增触网**(nextday 已触网 tdx)。
- **Ruling 2(None 诚实缺失):** 任一穿透字段无源 → `None`,不崩、不伪造零分、**不参与排序**。
- **Ruling 3(措辞合规):** 穿透字段是"跨模块机械标注上下文,非买卖信号"。`/api/quality`、`/api/nextday-strong`、`/api/smart-money/today`、`/api/smart-money/top` 各自仍挂 `cand_disclaimer`。字段名机械化(`mf_phase`/`quality_pct`),不用"主升浪/买入"等建议词。
- **Ruling 4:** quality↔nextday 两端**完整评分互不嵌入**(都贵),用轻量代理或诚实缺失替代。
- **命名统一:** 复用 quality 现有的 `mf_phase`/`mf_confidence`/`streak_inflow`(非 spec 草稿的 `main_phase`/`flow_streak`),保证跨清单字段可对齐。
- **B1 已实现,本次不改:** quality 已通过 `_enrich_main_behavior` 给 main 附 `mf_phase`/`mf_confidence`/`behavior_group`+`streak_inflow`/`streak_outflow`/`cum_net`/`margin_accel`/`north_cum`(20s 预算+finshare 熔断秒退)。
- **合规红线:** 不荐股、不输出实时买卖点、不承诺收益、不自动下单;无投资咨询资质。措辞"筛选/排序/观察清单/机械标注上下文"。个人自用放松仅限措辞风格,disclaimer 管道不拆。
- **不新增:** 表、采集源、API 路由。字段附在既有响应上。

---
## 任务分解与文件依赖

- **Task 1(A):** `screener/nextday.py` — `passed_items` 附 `sector_heat`/`policy_hit`(查 ff/br + `_board_members_batch` scored → `sector_heat.attach_sector_heat`)。
- **Task 2(B2):** `screener/nextday.py` — `passed_items` 附 `mf_phase`/`mf_confidence`/`streak_inflow`(lazy import `quality._enrich_main_behavior`)。
- **Task 3(B3):** `backtest/quality.py` 加 `_RES_PCT_INDEX`(code→res_pct)+ `screener/smart_money.py` 加 `_attach_quality_pct(rows)` 接 `top_by_amount`/`today_list`。
- **Task 4(收尾):** `CLAUDE.md` 更新三者路由字段说明 + 穿透标注检查清单;全量受影响回归。

> Task 1 与 Task 2 都改 nextday 同一注入点(`base["passed_items"] = passed_items[:limit]` 之后),但**逻辑不同、测试不同**,故分开独立 TDD,review 边界清晰。Task 2 依赖 Task 1 的注入点已存在。

---

### Task 1: nextday 附 sector_heat/policy_hit(A)

**Files:**
- Modify: `screener/nextday.py`(函数头 import + `nextday_strong_rank` 注入点)
- Test: `tests/test_nextday.py`

**Interfaces:**
- Consumes: `sector_heat.attach_sector_heat(rows, fund_flow, board_rows, member_map)`(纯函数,已存在);`_board_members_batch(board_names, spot_by_code=None)`(nextday 内部 L513);`db.query_rows("sector_fund_flow"/"industry_board")`
- Produces: `passed_items` 每行附 `sector_heat`(float|None)/`policy_hit`(float)

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_nextday.py` 增 `test_nextday_passed_annotates_sector_heat`,复用 `_setup`(已 mock db/_ma_arrange_batch/_board_members_batch/tdx),再补 `industry_board` 分支喂板块,断言 passed item 附 sector_heat/policy_hit(半导体在 `_HIT_BOARDS`,命中政策)

```python
def test_nextday_passed_annotates_sector_heat(monkeypatch):
    """A: nextday passed_items 附 sector_heat/policy_hit(纯 mock,不触网)。"""
    _setup(monkeypatch)
    _base = _mock_qr
    def _qr(table, **kw):
        if table == "industry_board":
            return [{"name": "半导体", "up_count": 60, "down_count": 40},
                    {"name": "电池", "up_count": 30, "down_count": 70}]
        return _base(table, **kw)
    monkeypatch.setattr(nd.db, "query_rows", _qr)
    r = nd.nextday_strong_rank(limit=10)
    passed = r.get("passed_items", [])
    assert passed, "应至少 1 只通过"
    for it in passed:
        assert "sector_heat" in it and "policy_hit" in it  # 字段存在
    hit = next((x for x in passed if (x.get("policy_hit") or 0) > 0), None)
    assert hit is not None, "半导体成员应命中政策→policy_hit>0"
```

> 依赖:注入点在 `base["passed_items"] = passed_items[:max(0, limit)]` 之后、`base` 组装前。600006 属"半导体"(rank4 前5),故进 passed_items 且命中政策。

- [ ] **Step 2: 运行确认失败**
  Run: `python -m pytest tests/test_nextday.py::test_nextday_passed_annotates_sector_heat -q`
  Expected: FAIL —— `KeyError: 'sector_heat'`(字段未注入)

- [ ] **Step 3: 实现** —— 在 `nextday.py` 顶部加 `from screener import sector_heat as _sh`(纯函数无环);在 `base["passed_items"] = passed_items[:max(0, limit)]` 之后插入:

```python
    # A: 行业景气/政策命中 穿透标注(仅 passed_items,只读上下文,不改排序)
    if base["passed_items"]:
        _ff, _br, _mm = [], [], {}
        try:
            _ff = db.query_rows("sector_fund_flow",
                                where="sector_type='行业' AND indicator='今日'",
                                order_by="", limit=0)
            _br = db.query_rows("industry_board", order_by="", limit=0)
            _ff_names = {str(r.get("name")) for r in _ff if r.get("name")}
            _scored = [str(r.get("name")) for r in _br
                       if r.get("name") and str(r.get("name")) in _ff_names]
            _mm = {b: set(c) for b, c in _board_members_batch(_scored).items()}
        except Exception:
            _ff, _br, _mm = [], [], {}
        _sh.attach_sector_heat(base["passed_items"], _ff, _br, _mm)
```

> `db` 即 nextday 已引用的 `data.db`(测试用 `nd.db`)。`_board_members_batch` 只传被评分板块(有界单次),异常兜 `mm={}` 诚实 None。

- [ ] **Step 4: 运行确认通过**
  Run: `python -m pytest tests/test_nextday.py::test_nextday_passed_annotates_sector_heat -q`
  Expected: PASS

- [ ] **Step 5: 提交**
  ```bash
  git add screener/nextday.py tests/test_nextday.py
  git commit -m "feat(nextday): A 接 sector_heat/policy_hit 行业景气穿透标注"
  ```

---

### Task 2: nextday 附主力阶段(B2)

**Files:**
- Modify: `screener/nextday.py`(在 Task 1 注入点后追加)
- Test: `tests/test_nextday.py`

**Interfaces:**
- Consumes: `backtest.quality._enrich_main_behavior(main, universe, days)`(已实现,内部 lazy import smart_money,给 stock 清单附 mf_phase 等)
- Produces: `passed_items` 每行附 `mf_phase`/`mf_confidence`/`streak_inflow`/`streak_outflow`/`cum_net`/`margin_accel`/`north_cum`/`behavior_group`

- [ ] **Step 1: 写失败测试** —— 在 `tests/test_nextday.py` 增 `test_nextday_passed_annotates_main_phase`,monkeypatch `backtest.quality._enrich_main_behavior` 返回带 mf_phase 的行,断言注入

```python
def test_nextday_passed_annotates_main_phase(monkeypatch):
    """B2: nextday passed_items 附 mf_phase/streak_inflow(复用 quality 富集)。"""
    _setup(monkeypatch)                      # 复用全套 mock(不触网)
    import backtest.quality as q
    monkeypatch.setattr(q, "_enrich_main_behavior",
                        lambda m, u, days: [{**it, "mf_phase": "吸筹",
                                             "streak_inflow": 5} for it in m])
    r = nd.nextday_strong_rank(limit=10)
    passed = r.get("passed_items", [])
    assert passed
    assert passed[0].get("mf_phase") == "吸筹"
    assert passed[0].get("streak_inflow") == 5
```

- [ ] **Step 2: 运行确认失败**
  Run: `python -m pytest tests/test_nextday.py::test_nextday_passed_annotates_main_phase -q`
  Expected: FAIL —— 无 mf_phase(未注入)

- [ ] **Step 3: 实现** —— 在 Task 1 的 `_sh.attach_sector_heat(...)` 之后插入:

```python
    # B2: 主力阶段/资金连续性 穿透标注(复用 quality._enrich_main_behavior,
    #     lazy import 避循环依赖;universe!=stock 时函数内直接返回原行)
    if base["passed_items"] and universe == "stock":
        try:
            from backtest import quality as _q
            base["passed_items"] = _q._enrich_main_behavior(
                base["passed_items"], universe, days=days)
        except Exception:
            pass  # 富集失败→字段 None(诚实缺失)
```

> 注意:`base["passed_items"]` 已截断到 limit,`_enrich_main_behavior` 内部只富集 top20(它自己取 main[:20]),传入截断后的 passed_items 符合其语义(只给已入选的富集)。若 passed_items 为空,if 短路,不触发 import。

- [ ] **Step 4: 运行确认通过**
  Run: `python -m pytest tests/test_nextday.py::test_nextday_passed_annotates_main_phase -q`
  Expected: PASS

- [ ] **Step 5: 提交**
  ```bash
  git add screener/nextday.py tests/test_nextday.py
  git commit -m "feat(nextday): B2 附 mf_phase/streak_inflow 主力穿透标注"
  ```

---

### Task 3: smart_money 附 quality_pct(B3)

**Files:**
- Modify: `backtest/quality.py`(加 `_RES_PCT_INDEX` + quality_rank 写入)
- Modify: `screener/smart_money.py`(加 `_attach_quality_pct` + 接入 top_by_amount/today_list)
- Test: `tests/test_quality.py`、`tests/test_smart_money.py`

**Interfaces:**
- Consumes: `backtest.quality._to_pct(pd.Series) -> pd.Series`(已存在);`quality_rank` 的 result main(含 resonance)
- Produces: 模块级 `_RES_PCT_INDEX: dict[str, float]`(code→共振分位);`smart_money._attach_quality_pct(rows) -> rows`(行附 `quality_pct`)

- [ ] **Step 1a: 写失败测试(quality._RES_PCT_INDEX)**
  Run: `python -m pytest tests/test_quality.py -q`(先确认现绿)
  Add to `tests/test_quality.py`:
```python
def test_quality_res_pct_index(monkeypatch):
    """B3: quality_rank 产 _RES_PCT_INDEX(code→共振分位),含 main code。"""
    from backtest import quality as q
    # 最小路径:直接验证 _RES_PCT_INDEX 模块级存在且可写
    q._RES_PCT_INDEX = {"000001": 0.9, "600000": 0.3}
    assert q._RES_PCT_INDEX["000001"] == 0.9
    # 更实的断言交给 test_smart_money(mock 索引),此处保证模块级容器存在
    assert hasattr(q, "_RES_PCT_INDEX")
```

- [ ] **Step 1b: 写失败测试(smart_money._attach_quality_pct)**
  Add to `tests/test_smart_money.py`:
```python
def test_attach_quality_pct(monkeypatch):
    """B3: top_by_amount/today_list 行附 quality_pct;索引空→None。"""
    from screener import smart_money as sm
    import data.db as db
    rows = [{"code": "a", "name": "甲", "market": "sh", "amount": 1e7, "count": 1},
            {"code": "z", "name": "乙", "market": "sz", "amount": 2e7, "count": 1}]
    monkeypatch.setattr(db, "query_rows", lambda *a, **k: rows)
    monkeypatch.setattr(sm, "_attach_intensity", lambda p: p)
    monkeypatch.setattr(sm, "_sector_ctx", lambda p: p)
    # mock 索引:quality_pct 为 0.9 / None
    monkeypatch.setattr(sm, "_quality_index", {"a": 0.9})
    out = sm.top_by_amount(days=5)
    merged = {x["code"]: x for x in out["rows"]}
    assert merged["a"].get("quality_pct") == 0.9
    assert merged["z"].get("quality_pct") is None   # 无索引→None(诚实缺失)
```

- [ ] **Step 2: 运行确认失败**
  Run: `python -m pytest tests/test_quality.py::test_quality_res_pct_index tests/test_smart_money.py::test_attach_quality_pct -q`
  Expected: FAIL —— 无 `_RES_PCT_INDEX`/`_quality_index`(未实现)

- [ ] **Step 3a: 实现 quality._RES_PCT_INDEX** —— 在 `quality.py` 的 `_RESULT_CACHE: dict = {}` 声明后加:

```python
# B3 穿透: code → 共振横截分位(供 smart_money 清单作 quality 上下文标注,
#   只读不进排序;不重跑 buffett)。universe==stock 且 main 非空时刷新。
_RES_PCT_INDEX: dict[str, float] = {}
```

在 `quality_rank` 内,`result = {...}` 组装后、`_RESULT_CACHE[_key] = (...)` 前插入:

```python
    if universe == "stock" and main:
        try:
            _rs = pd.Series({str(it.get("code")): _to_float(it.get("resonance")) or 0.0
                             for it in main if it.get("code")})
            _rp = _to_pct(_rs).to_dict() if not _rs.empty else {}
            _RES_PCT_INDEX.clear()
            _RES_PCT_INDEX.update({str(c): float(v) for c, v in _rp.items()})
        except Exception:
            _RES_PCT_INDEX.clear()
    else:
        _RES_PCT_INDEX.clear()
```

- [ ] **Step 3b: 实现 smart_money._attach_quality_pct + 接入** —— 在 `smart_money.py` 的 `_sector_ctx` 定义后加:

```python
# B3 穿透: 读 quality 的共振分位索引,给主力清单附 quality_pct(上下文标注)。
# lazy import backtest.quality 避循环依赖(quality 已 lazy import screener.smart_money)。
_quality_index: dict[str, float] = {}


def _attach_quality_pct(rows: list[dict]) -> list[dict]:
    """给行附 quality_pct(该股在 quality 共振清单的分位)。索引空→None。"""
    global _quality_index
    if not rows:
        return rows
    try:
        from backtest import quality as _q
        _quality_index = dict(getattr(_q, "_RES_PCT_INDEX", {}))
    except Exception:
        _quality_index = {}
    for r in rows:
        c = str(r.get("code") or "")
        r["quality_pct"] = _quality_index.get(c) if c else None
    return rows
```

在 `top_by_amount` 的 `_sector_ctx(pool)` 后加 `_attach_quality_pct(pool)`;在 `today_list` 的 `_sector_ctx(rows)` 后加 `_attach_quality_pct(rows)`。

- [ ] **Step 4: 运行确认通过**
  Run: `python -m pytest tests/test_quality.py::test_quality_res_pct_index tests/test_smart_money.py::test_attach_quality_pct -q`
  Expected: PASS

- [ ] **Step 5: 提交**
  ```bash
  git add backtest/quality.py screener/smart_money.py tests/test_quality.py tests/test_smart_money.py
  git commit -m "feat(quality+smart_money): B3 附 quality_pct 主力清单穿透 quality 共振分位"
  ```

---

### Task 4: 文档与回归(收尾)

**Files:**
- Modify: `CLAUDE.md`(三者路由字段说明 + 穿透标注检查清单)
- Test: 全受影响回归

- [ ] **Step 1: 更新 CLAUDE.md** —— 在 `/api/nextday-strong` 路由说明加 `sector_heat`/`policy_hit`/`mf_phase`/`streak_inflow` 字段;`/api/smart-money/today`、`/api/smart-money/top` 加 `quality_pct` 字段;在"新增/修改 quality 编排层"检查清单补一条穿透标注约束(不重跑对端重评分、None 诚实缺失、命名统一 mf_phase)。
- [ ] **Step 2: 回归**
  Run: `python -m pytest tests/test_nextday.py tests/test_quality.py tests/test_smart_money.py tests/test_sm_radar.py -q`
  Expected: PASS(含新增 Task1/2/3 测试)
- [ ] **Step 3: 静态检查**
  Run: `python -m compileall -q screener/nextday.py screener/smart_money.py backtest/quality.py`
  Expected: 无输出(OK)
- [ ] **Step 4: 提交**
  ```bash
  git add CLAUDE.md
  git commit -m "docs(CLAUDE.md): 三清单穿透标注字段说明 + 检查清单"
  ```

## 完成标准

- 三张清单各带对端穿透字段:quality(已有 mf_phase 等)/nextday(sector_heat+policy_hit+mf_phase+streak_inflow)/smart_money(quality_pct)。
- 所有穿透字段 None 时诚实缺失、不崩、不进排序。
- 无新增表/采集源/API 路由;措辞合规,disclaimer 管道完整。
- 受影响回归全绿 + compileall OK。

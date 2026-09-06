# 主力动向强度排序优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (inline execution). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让主力动向页面默认以净强度排序，保留绝对净额选项，并在按个股视图隔离金额与股数单位、展示通道一致性。

**Architecture:** 复用 `/api/smart-money/today` 已返回的 `net_intensity`，主要在前端视图层实现排序选择和按个股聚合指标，避免新增查询和网络请求。必要的纯聚合计算放在 `smRender` 内并通过单测覆盖可抽取的 Python 查询逻辑，不改变采集表结构。

**Tech Stack:** Python 3.12, FastAPI, 原生 JavaScript, pytest。

**Spec:** `docs/superpowers/specs/2026-09-06-quality-smart-money-filter-design.md`

## Global Constraints

- 默认强度优先，绝对净额排序必须保留。
- 金额通道与股数通道不混入同一数值排序。
- 不新增数据库表，不增加网络调用。
- 继续使用“机械排序/观察清单/非荐股非买卖信号”措辞。
- API 现有字段保持兼容。

### Task 1: Add smart-money sort controls

**Files:**
- Modify: `web/index.html`（主力动向控件、`smRender`）
- Test: `tests/test_smart_money.py`（如抽取后端纯函数则同步测试）

**Interfaces:**
- New UI state `smState.sortMetric`, values `intensity`, `amount`, `channels`, `positive_ratio`。
- Default `sortMetric = "intensity"` for flow and code views.

- [ ] **Step 1: Add sorting behavior tests or pure helper first**

If the existing frontend-only implementation remains in JavaScript, add a small deterministic helper in the query/aggregation layer for per-code summary and test it. The helper must return amount-channel intensity, absolute amount, channel count, positive channel count, and positive ratio without including share-based channels in amount comparisons.

- [ ] **Step 2: Implement controls**

Add a “排序” select near `smView`. In flow view, intensity sorts by `net_intensity` descending and amount sorts by absolute `amount`. In code view, intensity sorts by the strongest available amount-channel intensity, amount sorts by absolute amount, channels sorts by channel count, and positive ratio sorts by positive amount-channel count divided by available amount channels.

- [ ] **Step 3: Show unit-safe diagnostics**

Keep share channels (`十大股东`, `高管增减持`, `限售解禁`) in separate display columns; show amount-channel coverage and direction counts. If intensity is unavailable, place the row after available-intensity rows and display `—`, never treat missing as zero.

- [ ] **Step 4: Run focused validation**

Run: `python -m pytest tests/test_smart_money.py -q`; open the page and verify switching flow/code views and all sort options produces no JavaScript error.

- [ ] **Step 5: Commit**

```bash
git add web/index.html tests/test_smart_money.py
 git commit -m "feat(smart-money): prioritize intensity sorting"
```

### Task 2: Full regression and deployment readiness

**Files:**
- Test: `tests/test_smart_money.py`, `tests/test_quality*.py`

- [ ] **Step 1: Run focused suites**

Run: `python -m pytest tests/test_smart_money.py tests/test_quality*.py -q`.
Expected: all pass.

- [ ] **Step 2: Run full suite and compile check**

Run: `python -m pytest tests/ -q` and `python -m compileall -q api backtest data screener tests`.
Expected: no failures or compile errors.

- [ ] **Step 3: Browser smoke test**

Verify `/api/smart-money/today` remains HTTP 200, the main-force page defaults to intensity sorting, and unit labels remain distinct for amount and share channels.

- [ ] **Step 4: Commit any required compatibility fixes**

Only include fixes required by this plan; preserve unrelated working-tree files.

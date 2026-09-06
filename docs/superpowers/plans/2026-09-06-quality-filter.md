# 优质筛选严格结果优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让优质筛选严格主清单只包含满足硬门槛、可信度和口径条件的标的，并明确报告结果不足与排除原因。

**Architecture:** 在现有 `backtest.quality.quality_rank` 的候选汇总阶段集中计算 eligibility 和 exclusion reasons，不改变既有因子来源或网络调用。API 继续透传结果，前端在现有优质筛选卡内增加摘要和排除说明。

**Tech Stack:** Python 3.12, pandas, FastAPI, 原生 JavaScript, pytest。

**Spec:** `docs/superpowers/specs/2026-09-06-quality-smart-money-filter-design.md`

## Global Constraints

- 不新增数据库表，不增加网络调用。
- 严格模式不使用低可信度、硬门槛失败或风险项补足 `limit`。
- `by_dim` 保留诊断项，不能与严格 `main` 混淆。
- 继续使用“机械排序/观察清单/非荐股非买卖信号”措辞。
- 新字段只扩展响应，不删除现有字段。

### Task 1: Add strict eligibility metadata

**Files:**
- Modify: `backtest/quality.py`（候选汇总、主清单选择处）
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces per-item `eligibility: bool`, `exclusion_reasons: list[str]`。
- Produces top-level `selection_status`, `eligible_count`, `requested_limit`, `selection_note`, `excluded_summary`。

- [ ] **Step 1: Write failing tests**

Add tests covering: strict mode does not pad `main`; an item failing `hard_gate_pass` receives `hard_gate` in reasons; an item below `min_confidence` receives `confidence`; a strict result smaller than requested limit returns `selection_status == "insufficient"` and accurate counts; non-strict mode keeps existing behavior.

- [ ] **Step 2: Run focused tests**

Run: `python -m pytest tests/test_quality.py -q`
Expected: new assertions fail before implementation.

- [ ] **Step 3: Implement minimal metadata and strict selection**

Build reasons in deterministic order: `hard_gate`, `confidence`, `risk_flags`, `min_dims`, `no_resonance`. Set `eligibility` only when all strict conditions pass. In strict mode select only eligible items; set `selection_status` to `ok` when `eligible_count >= requested_limit`, otherwise `insufficient`. Keep non-strict selection unchanged.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_quality.py -q`
Expected: all quality tests pass.

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): make strict eligibility explicit"
```

### Task 2: Update quality page summary and diagnostics

**Files:**
- Modify: `web/index.html`（`qsLoad`/quality result renderer）

**Interfaces:**
- Consumes top-level `selection_status`, `eligible_count`, `requested_limit`, `excluded_summary` and item `exclusion_reasons`.

- [ ] **Step 1: Add renderer behavior**

Add a summary row showing requested, eligible, hard-gate exclusions, low-confidence exclusions. Label main rows “严格合格”; show `selection_note` when status is `insufficient`; show exclusion reasons in diagnostic rows.

- [ ] **Step 2: Verify static rendering**

Run the existing frontend through the local app and call `/api/quality` with a small limit. Confirm no JavaScript exception and that empty/insufficient responses render a message instead of silently padding.

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat(quality): explain strict selection capacity"
```

### Task 3: Regression validation

**Files:**
- Test: `tests/test_quality_confidence.py`, `tests/test_quality_refine.py`, `tests/test_quality_factors.py`, `tests/test_quality_warmup.py`

- [ ] **Step 1: Run quality suite**

Run: `python -m pytest tests/test_quality*.py -q`
Expected: all pass.

- [ ] **Step 2: Run full suite and compile check**

Run: `python -m pytest tests/ -q` and `python -m compileall -q api backtest data screener tests`.
Expected: no failures or compile errors.

- [ ] **Step 3: Commit any only-if-needed compatibility fixes**

Only commit fixes required by the new strict metadata; do not alter unrelated modules.

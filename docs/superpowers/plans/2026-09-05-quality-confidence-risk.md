# 优质筛选可信度与风险门槛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有五口径共振基础上加入数据可信度、硬质量门槛和风险惩罚，减少数据不完整、财务红旗和短期资金脉冲造成的虚高排序。

**Architecture:** 保留 `backtest/quality.py` 现有因子与共振实现，在其上增加三个纯函数边界：逐标的数据可信度评估、质量硬门槛、风险惩罚。`quality_rank` 先预筛和计算口径，再为每个候选附加质量元数据，严格模式过滤硬拒绝/低可信度标的，最后用风险调整后的共振分进入既有组合约束；API 仅透传新参数并保持 `_wrap`/免责声明契约。

**Tech Stack:** Python 3.12, pandas 3.x, NumPy, FastAPI, pytest, SQLite 只读查询。

**Spec:** `docs/superpowers/specs/2026-09-05-quality-confidence-risk-design.md`

## Global Constraints

- 不新增采集源、数据库表或列；只读现有 `stock_spot`、`etf_spot`、`*_daily`、`smart_money_action`、`fundamentals_cache` 与既有因子模块结果。
- 仍是公开数据的机械排序观察清单，不输出买卖建议，不承诺收益。
- 缺失数据与负面数据严格区分：缺失降低可信度，负面风险触发门槛或惩罚。
- 盘口微结构只作盘中流动性观察，不进入长期质量分。
- 所有数值出口继续 NaN→None，日期比较使用 `pd.to_datetime`。
- 默认 `strict_quality=True`、`min_confidence=0.50`、`risk_penalty=True`；新参数必须纳入结果缓存键。

---

### Task 1: 添加数据可信度计算

**Files:**
- Modify: `backtest/quality.py`（在 `_dim_scores` 前后增加纯函数）
- Create: `tests/test_quality_confidence.py`

**Interfaces:**
- Produces `_data_confidence(code, df, close, dim_scores, behavior=None, fundamental=None) -> dict`，返回 `score`、`level`、`components`、`warnings`。
- `components` 使用 `spot/history/fundamental/flow/research` 键；不适用组件标记为 `None` 并从分母排除。

- [ ] **Step 1: Write failing tests**

```python
def test_complete_stock_has_high_confidence():
    result = quality._data_confidence(
        "a", _spot_row(), _history(60), {1: .8, 2: .8, 3: .8, 4: .8, 5: .8},
        behavior_days=5, fundamental_ok=True, research_count=3,
    )
    assert result["level"] == "high"
    assert result["score"] >= .75


def test_missing_history_and_fundamental_lower_confidence():
    result = quality._data_confidence(
        "a", _spot_row(), _history(10), {3: .8},
        behavior_days=1, fundamental_ok=False, research_count=0,
    )
    assert result["level"] == "low"
    assert result["components"]["history"] == 0.0
    assert result["components"]["fundamental"] == .5
    assert result["warnings"]


def test_etf_excludes_inapplicable_components():
    result = quality._data_confidence(
        "a", _spot_row(), _history(60), {1: .8, 3: .8},
        universe="etf", behavior_days=5, fundamental_ok=None, research_count=None,
    )
    assert result["components"]["fundamental"] is None
    assert result["components"]["research"] is None
    assert result["level"] == "high"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_quality_confidence.py -q`
Expected: FAIL because `_data_confidence` does not exist.

- [ ] **Step 3: Implement minimal confidence calculation**

Implement explicit component scoring: spot 0/1 based on code, price and turnover; history 0/0.5/1 based on valid row count `<20/20-59/>=60`; fundamental 1 for valid individual financial result, 0.5 for spot proxy, `None` for ETF; flow 1 for at least 3 behavior days, 0.5 for spot-only, 0 for absent; research 1 for at least 2 reports, 0.5 for one, 0 for absent, `None` for ETF. Calculate weighted mean over applicable components with weights history `.25`, fundamental `.30`, flow `.20`, spot `.15`, research `.10`; map high `>=.75`, medium `>=.50`, low otherwise.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality_confidence.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_confidence.py
git commit -m "feat: add quality data confidence scoring"
```

### Task 2: 添加硬质量门槛和风险惩罚

**Files:**
- Modify: `backtest/quality.py`
- Modify: `tests/test_quality_confidence.py`

**Interfaces:**
- Produces `_quality_gate(item, confidence, fundamental=None, behavior_days=0) -> dict`，返回 `hard_pass`、`risk_flags`、`warnings`。
- Produces `_risk_penalty(item, gate, dim_scores) -> float`，惩罚上限 5.0。
- Produces `_confidence_multiplier(level) -> float`：high=1.0、medium=.85、low=.65。

- [ ] **Step 1: Write failing tests**

```python
def test_financial_red_flags_fail_hard_gate():
    gate = quality._quality_gate(
        {}, {"score": .9, "level": "high"},
        {"goodwill_to_equity_pct": 40, "debt_ratio_latest": 60},
    )
    assert gate["hard_pass"] is False
    assert "商誉" in " ".join(gate["risk_flags"])


def test_single_day_flow_is_flagged_not_quality():
    gate = quality._quality_gate({}, {"score": .8, "level": "high"}, behavior_days=1)
    assert any("脉冲" in x for x in gate["risk_flags"])


def test_risk_penalty_reduces_adjusted_score():
    gate = {"hard_pass": True, "risk_flags": ["高杠杆ROE", "高波动"]}
    assert quality._risk_penalty({}, gate, {1: .05}) > 0
    assert quality._confidence_multiplier("medium") == .85
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_quality_confidence.py -q`
Expected: FAIL because the gate and penalty functions do not exist.

- [ ] **Step 3: Implement gate and penalty**

Use existing Buffett ratio keys for goodwill, debt, FCF/net income, owner earnings, leverage-adjusted ROE and equity multiplier. Mark hard reject for financial red flags, missing spot price/turnover, history below 20 valid rows, or confidence below configured minimum. Mark warnings for high leverage, weak FCF, high downside risk and single-day flow pulse. Map flags to penalties: leverage 1.5, weak FCF 1.5, high volatility/downside 2.0, pulse 1.0, capped at 5.0.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality_confidence.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_confidence.py
git commit -m "feat: add quality hard gates and risk penalties"
```

### Task 3: 接入 quality_rank 和 API 参数

**Files:**
- Modify: `backtest/quality.py:815-924`
- Modify: `api/server.py` 的 `/api/quality` 路由
- Modify: `tests/test_quality.py`
- Modify: `tests/test_quality_refine.py`（如 API 参数测试所在文件）

**Interfaces:**
- Extend `quality_rank(..., strict_quality=True, min_confidence=0.50, risk_penalty=True) -> dict`。
- Each item gets `raw_resonance`, `adjusted_resonance`, `data_confidence`, `confidence_level`, `risk_flags`, `warnings`, `hard_gate_pass`.
- Result gets `confidence_summary` and `selection_mode` (`strict`/`degraded`).

- [ ] **Step 1: Write failing integration tests**

```python
def test_low_confidence_not_in_strict_main(monkeypatch):
    res = _run_quality_with_confidence(monkeypatch, confidence=.3)
    assert res["main"] == []
    assert res["confidence_summary"]["low_excluded"] == 1


def test_raw_and_adjusted_resonance_are_preserved(monkeypatch):
    res = _run_quality_with_confidence(monkeypatch, confidence=.6, risk_flags=["高波动"])
    item = res["main"][0]
    assert item["raw_resonance"] > item["adjusted_resonance"]
    assert item["resonance"] == item["adjusted_resonance"]


def test_new_parameters_are_in_cache_key(monkeypatch):
    quality.quality_rank(strict_quality=True, risk_penalty=True)
    quality.quality_rank(strict_quality=False, risk_penalty=False)
    assert len(quality._RESULT_CACHE) >= 2
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_quality.py tests/test_quality_confidence.py -q`
Expected: FAIL because `quality_rank` lacks the new parameters and output fields.

- [ ] **Step 3: Integrate pipeline**

After `_dim_scores`, build per-code confidence using available DataFrame/history/口径状态. Pass Buffett result and flow/research availability where available; do not add network calls. Apply `_quality_gate`; retain rejected items in `by_dim` only when appropriate. For accepted items compute `raw_resonance`, then `adjusted_resonance = raw_resonance * multiplier - penalty`; use adjusted value for `resonance` and sorting. In strict mode require `hard_gate_pass` and `confidence_score >= min_confidence`; in non-strict mode preserve old inclusion semantics. Add `confidence_summary` counts and degraded note when all candidates fail confidence. Add parameters to `_key`.

Update `/api/quality` query signature and call to pass all three parameters, preserving `_wrap` and `cand_disclaimer`.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality.py tests/test_quality_factors.py tests/test_quality_refine.py tests/test_quality_confidence.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py api/server.py tests/test_quality.py tests/test_quality_refine.py tests/test_quality_confidence.py
git commit -m "feat: integrate quality confidence and risk-adjusted ranking"
```

### Task 4: 文档、全量验证和部署

**Files:**
- Modify: `docs/superpowers/specs/2026-09-05-quality-confidence-risk-design.md` only if implementation details require clarification.
- Modify: `CLAUDE.md` only if route parameter documentation differs from the implemented contract.

- [ ] **Step 1: Run focused and full tests**

```bash
python -m pytest tests/test_quality.py tests/test_quality_factors.py tests/test_quality_refine.py tests/test_quality_warmup.py tests/test_quality_confidence.py -q
python -m pytest tests/ -q
python -m compileall api data screener backtest scripts tests
```

Expected: all tests pass; only pre-existing dependency deprecation warnings may remain.

- [ ] **Step 2: Verify API response contract**

Start the existing service or use the project deployment flow, then request:

```bash
curl -sS 'http://localhost:8000/api/quality?universe=stock&strict_quality=true&min_confidence=0.5&risk_penalty=true'
```

Check HTTP 200, `data.confidence_summary`, item fields, `cand_disclaimer`, and no NaN values.

- [ ] **Step 3: Deploy through repository workflow**

```bash
bash deploy.sh
```

Expected: host tests pass, Docker image builds, existing container is recreated without deleting SQLite volumes, `/api/health` returns 200, and fast route smoke checks pass.

- [ ] **Step 4: Commit documentation if changed**

```bash
git add docs/superpowers/specs/2026-09-05-quality-confidence-risk-design.md CLAUDE.md
git commit -m "docs: document quality confidence contract"
```

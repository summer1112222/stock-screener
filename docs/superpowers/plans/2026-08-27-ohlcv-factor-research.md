# OHLCV 因子研究接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将一组可审计的 OHLCV 因子接入历史因子研究接口，仅用于 IC、Rank IC、分层收益和样本外研究，不改变实时筛选、次日强势或质量排序。

**Architecture:** 新增独立的 `backtest/ohlcv_features.py`，接收规范化的日线 DataFrame，返回以日期为索引、代码为列的因子矩阵。`backtest/research.py` 保留现有因子兼容，并在因子名称解析处调用该模块；API 只扩展可选因子名称和来源说明，不新增数据源或数据库表。

**Tech Stack:** Python 3.12, pandas 3.x, NumPy, pytest, FastAPI。

**Spec:** 本次聊天已确认的 OHLCV 研究因子接入范围（无独立 spec 文件）。

## Global Constraints

- 因子严格只使用 t 日及以前的 OHLCV 数据，不允许未来函数。
- 新因子只进入 `backtest/research.py` 历史研究，不改变线上筛选排序。
- 不新增 SQLite 表，不触网，不新增运行时依赖。
- 日期比较使用 `pd.to_datetime`，序列化前将 NaN 转为 None。
- 保持项目合规措辞：历史统计/研究优先级，不输出买卖建议。
- 每个新增行为先写失败测试，再写最小实现。

### Task 1: 新增 OHLCV 因子计算模块

**Files:**
- Create: `backtest/ohlcv_features.py`
- Test: `tests/test_ohlcv_features.py`

**Interfaces:**
- Produces `available_factors() -> tuple[str, ...]`。
- Produces `compute_factor(panel: pd.DataFrame, factor: str) -> pd.DataFrame`，输入 long-form 日线数据，至少支持 `date`, `code`, `open`, `high`, `low`, `close`, `volume`, `amount`，输出 date×code 矩阵。
- Produces `compute_factors(panel: pd.DataFrame, factors: Iterable[str]) -> dict[str, pd.DataFrame]`。

- [ ] **Step 1: Write the failing tests**

```python
def test_return_factor_uses_only_prior_rows():
    panel = make_daily_panel()
    out = compute_factor(panel, "return_5")
    assert out.loc[pd.Timestamp("2024-01-06"), "600001"] == pytest.approx(0.05)
    assert pd.isna(out.loc[pd.Timestamp("2024-01-04"), "600001"])


def test_supported_factors_have_expected_shape_and_no_future_leak():
    panel = make_daily_panel()
    for name in available_factors():
        out = compute_factor(panel, name)
        assert list(out.index) == sorted(out.index)
        assert set(out.columns) == {"600001", "600002"}
        out2 = compute_factor(panel.assign(close=panel["close"] * 100), name)
        assert out2.index.equals(out.index)


def test_missing_columns_return_nan_matrix():
    panel = make_daily_panel().drop(columns=["volume", "amount"])
    out = compute_factor(panel, "volume_ratio_20")
    assert out.isna().all().all()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_ohlcv_features.py -q`
Expected: FAIL because `backtest.ohlcv_features` does not exist.

- [ ] **Step 3: Implement minimal calculations**

Implement grouped, date-sorted rolling calculations:

```python
FACTOR_DEFINITIONS = {
    "return_5": "close / close.shift(5) - 1",
    "return_10": "close / close.shift(10) - 1",
    "return_20": "close / close.shift(20) - 1",
    "volatility_20": "close.pct_change().rolling(20, min_periods=5).std()",
    "volume_ratio_20": "volume / volume.rolling(20, min_periods=5).mean()",
    "price_position_20": "(close - rolling_low_20) / (rolling_high_20 - rolling_low_20)",
    "amplitude_20": "((high - low) / close).rolling(20, min_periods=5).mean()",
    "turnover_proxy": "amount / amount.rolling(20, min_periods=5).mean()",
}
```

Normalize dates with `pd.to_datetime`, codes to strings, pivot to date×code, and return an all-NaN matrix when required input columns are absent. Reject unknown factor names with `ValueError`.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_ohlcv_features.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest/ohlcv_features.py tests/test_ohlcv_features.py
git commit -m "feat: add OHLCV research factors"
```

### Task 2: 接入历史研究因子矩阵

**Files:**
- Modify: `backtest/research.py`
- Test: `tests/test_factor_research.py`

**Interfaces:**
- Existing `run_factor_research(...)` and旧因子行为保持兼容。
- New factor names resolve through `ohlcv_features.compute_factor` when研究输入提供 OHLCV panel。
- Result includes `factor_source` and `factor_definition` for new OHLCV factors。

- [ ] **Step 1: Write the failing tests**

```python
def test_run_factor_research_accepts_ohlcv_factor(monkeypatch):
    result = run_factor_research(panel=make_daily_panel(), factor="return_5", horizons=[1])
    assert result["factor"] == "return_5"
    assert result["factor_source"] == "ohlcv"
    assert result["factor_definition"]


def test_existing_factor_path_is_unchanged():
    result = run_factor_research(panel=make_existing_factor_panel(), factor="momentum_n", horizons=[1])
    assert result["factor_source"] != "ohlcv"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_factor_research.py -q`
Expected: FAIL because the research function cannot resolve the new factor or expose metadata.

- [ ] **Step 3: Implement the adapter**

Import the new module locally, detect OHLCV factor names before the legacy factor resolver, compute the factor matrix, and pass it through the existing forward-return, IC, split, and cost-after-research pipeline. Add metadata from `FACTOR_DEFINITIONS`; do not alter legacy factor formulas or sorting paths.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_factor_research.py -q`
Expected: PASS, including all pre-existing tests in that file.

- [ ] **Step 5: Commit**

```bash
git add backtest/research.py tests/test_factor_research.py
git commit -m "feat: research OHLCV factors"
```

### Task 3: 扩展 API 参数与说明

**Files:**
- Modify: `api/server.py` (现有 `/api/research/factor` 路由)
- Modify: `web/index.html` (仅在研究台已有因子下拉时补充选项)
- Test: `tests/test_factor_research.py` 或现有 API 测试文件

**Interfaces:**
- `/api/research/factor` 接受 `return_5`, `return_10`, `return_20`, `volatility_20`, `volume_ratio_20`, `price_position_20`, `amplitude_20`, `turnover_proxy`。
- 响应继续包含 `bt_disclaimer`，并增加 `factor_source`/`factor_definition`。
- API 默认行为和线上筛选路由不变。

- [ ] **Step 1: Write the failing route test**

```python
def test_factor_research_route_accepts_ohlcv_factor(client, monkeypatch):
    response = client.post("/api/research/factor", json={"factor": "return_5", "horizons": [1]})
    assert response.status_code == 200
    assert response.json()["factor_source"] == "ohlcv"
    assert "bt_disclaimer" in response.json()
```

- [ ] **Step 2: Run the route test and verify failure**

Run: `python -m pytest tests/test_factor_research.py::test_factor_research_route_accepts_ohlcv_factor -q`
Expected: FAIL with unsupported factor or missing metadata.

- [ ] **Step 3: Implement route plumbing**

Add the names to the existing research parameter validation/options, pass the OHLCV panel already loaded by the route into `run_factor_research`, and preserve `_wrap()`/`bt_disclaimer`. Do not add any network call or modify `/api/quality`, `/api/nextday-strong`, or `/api/screen`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_factor_research.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/server.py web/index.html tests/test_factor_research.py
git commit -m "feat: expose OHLCV factors in research API"
```

### Task 4: 全量验证与代码审查

**Files:**
- Review: `backtest/ohlcv_features.py`, `backtest/research.py`, `api/server.py`, `web/index.html`
- Tests: all `tests/`

- [ ] **Step 1: Run focused tests**

```bash
python -m pytest tests/test_ohlcv_features.py tests/test_factor_research.py -q
```

- [ ] **Step 2: Run compile and complete test suite**

```bash
python -m compileall -q api data screener backtest scripts tests
python -m pytest tests/ -q
```

- [ ] **Step 3: Review changed code**

Use `ecc:code-review`/`ecc:python-review`; verify no future-data leakage, no NaN JSON failures, no online-ranking behavior changes, and no new dependencies.

- [ ] **Step 4: Commit final verified changes**

```bash
git status --short
git diff --check
git add backtest tests api web
 git commit -m "feat: complete OHLCV factor research integration"
```

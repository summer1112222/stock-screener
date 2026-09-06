# 优质筛选量化权重实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为优质筛选增加基于历史 Rank IC、ICIR、方向稳定性和相关性惩罚的量化权重模式，同时保持经验权重回退与现有筛选接口兼容。

**Architecture:** 在 `backtest/quality.py` 中新增纯计算的历史权重校准层，输入按日期组织的因子与未来收益，输出归一化维度权重及透明统计；训练窗口只计算权重，测试窗口只做验证，禁止使用未来数据。`quality_rank` 通过 `weight_mode` 选择经验权重或 ICIR 权重，并把校准元数据返回给 API/前端；样本不足、历史为空或计算失败时稳定回退经验权重。

**Tech Stack:** Python 3.12、pandas、NumPy、FastAPI、SQLite、pytest。

**Spec:** 用户已确认的量化设计：优先提升横截面排序能力；计算 Rank IC、ICIR、IC 正向比例、分层收益；去极值、rank 标准化、相关性去冗余；训练/测试或 walk-forward 防未来数据泄漏；在线展示权重、来源、样本期和数据健康状态；数据不足回退经验权重。

## Global Constraints

- 仅做历史统计和机械排序，不输出投资建议、实时买卖点或收益承诺。
- 保留现有五个质量口径、经验权重和 API 默认行为；`weight_mode` 默认 `experience`。
- 所有历史因子必须只使用 t 日及以前数据预测 t+1 至 t+k 的横截面收益。
- 权重和必须为 1；不能因单个因子缺失导致接口崩溃。
- 不能新增实时采集源或数据库表；复用现有 `*_daily`、`stock_spot` 和研究数据。
- 每个任务完成后运行对应测试，避免引入网络依赖。

### Task 1: 新增横截面 Rank IC 统计纯函数

**Files:**
- Modify: `backtest/quality.py`
- Test: `tests/test_quality_quant.py`

**Interfaces:**
- Produces `rank_ic_series(factor_panel: pd.DataFrame, forward_returns: pd.DataFrame) -> pd.DataFrame`，按日期计算每个因子的 Spearman 横截面相关系数。
- Produces `factor_stats(ic: pd.Series) -> dict`，返回 `sample_count`、`mean_ic`、`icir`、`positive_ic_ratio`。

- [ ] **Step 1: Write failing tests**

```python
def test_rank_ic_uses_same_date_cross_section():
    factors = pd.DataFrame({"a": [1, 2, 3], "b": [3, 2, 1]}, index=["x", "y", "z"])
    returns = pd.DataFrame({"a": [0.01, 0.02, 0.03], "b": [0.03, 0.02, 0.01]}, index=["x", "y", "z"])
    got = quality._cross_section_rank_ic(factors, returns)
    assert got == pytest.approx(1.0)


def test_factor_stats_reports_stability():
    stats = quality._factor_stats(pd.Series([0.1, 0.2, -0.1]))
    assert stats["sample_count"] == 3
    assert stats["positive_ic_ratio"] == pytest.approx(2 / 3)
    assert stats["icir"] == pytest.approx(pd.Series([0.1, 0.2, -0.1]).mean() / pd.Series([0.1, 0.2, -0.1]).std(ddof=0))
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_quality_quant.py -q`
Expected: FAIL because the new pure functions are not defined.

- [ ] **Step 3: Implement minimal functions**

实现 `_cross_section_rank_ic`：对齐相同日期/股票，删除缺失行；两列均先 `rank(method="average")`，再计算 Pearson 相关，样本少于 3 或常数列返回 `None`。实现 `_factor_stats`：忽略 NaN，样本数不足时将 ICIR 置 `None`，标准差为 0 时 ICIR 使用 0，并统一返回 JSON 可序列化的 float/None。

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality_quant.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_quant.py
git commit -m "feat: add cross-sectional rank IC statistics"
```

### Task 2: 实现去极值、相关性去冗余和 ICIR 权重校准

**Files:**
- Modify: `backtest/quality.py`
- Test: `tests/test_quality_quant.py`

**Interfaces:**
- Produces `_calibrate_dim_weights(history: dict, train_end: str, min_samples: int = 20) -> dict`。
- 返回 `{"weights": dict[int, float], "source": str, "factor_stats": dict, "train_period": dict, "sample_count": int}`。

- [ ] **Step 1: Write failing tests**

```python
def test_calibrated_weights_sum_to_one_and_prefer_stable_factor():
    history = make_history_with_stable_and_noisy_factors()
    result = quality._calibrate_dim_weights(history, train_end="2025-12-31", min_samples=3)
    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert result["weights"][1] > result["weights"][2]
    assert result["source"] == "icir"


def test_insufficient_history_falls_back_to_experience_weights():
    result = quality._calibrate_dim_weights({}, train_end="2025-12-31", min_samples=20)
    assert result["source"] == "experience_fallback"
    assert result["weights"] == quality._DEFAULT_DIM_WEIGHTS


def test_training_cutoff_excludes_future_rows():
    history = make_history_with_future_rows()
    result = quality._calibrate_dim_weights(history, train_end="2025-06-30", min_samples=3)
    assert result["train_period"]["end"] == "2025-06-30"
    assert result["sample_count"] == 3
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_quality_quant.py -q`
Expected: FAIL because calibration is not implemented.

- [ ] **Step 3: Implement minimal calibration**

对每个维度先在每个日期做 MAD/winsorize 去极值，再转横截 rank；计算训练截止日前的 forward-return Rank IC。使用 `abs(mean_ic) * max(icir, 0) * positive_ic_ratio` 作为基础权重；若平均 IC 为负，反转该因子方向后再计权。对因子相关系数绝对值大于 0.85 的组保留 ICIR 更高者，其他因子乘以相关性惩罚。将维度内因子聚合为五个维度权重，缺失维度使用经验先验；最后归一化。样本不足、全零或异常时返回 `_DEFAULT_DIM_WEIGHTS` 的拷贝，不能返回共享可变对象。

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality_quant.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_quant.py
git commit -m "feat: calibrate quality weights from ICIR"
```

### Task 3: 接入 quality_rank、缓存键和 API 参数

**Files:**
- Modify: `backtest/quality.py`
- Modify: `api/server.py`
- Test: `tests/test_quality_quant.py`, `tests/test_quality.py`

**Interfaces:**
- `quality_rank(..., weight_mode: str = "experience") -> dict`。
- API `/api/quality` 新增 `weight_mode: str = Query("experience")`。
- 返回字段：`weights`、`weight_source`、`factor_stats`、`train_period`、`sample_count`。

- [ ] **Step 1: Write failing tests**

```python
def test_quality_rank_icir_mode_exposes_metadata(monkeypatch):
    result = run_quality_with_quant_calibration(monkeypatch)
    assert result["weight_source"] in {"icir", "experience_fallback"}
    assert "factor_stats" in result
    assert "train_period" in result
    assert "sample_count" in result


def test_api_cache_key_separates_weight_modes(monkeypatch):
    calls = []
    monkeypatch.setattr(server.quality, "quality_rank", lambda **kw: calls.append(kw) or minimal_quality_result())
    client = TestClient(server.app)
    client.get("/api/quality?weight_mode=experience")
    client.get("/api/quality?weight_mode=icir")
    assert calls[-1]["weight_mode"] == "icir"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_quality_quant.py tests/test_quality.py -q`
Expected: FAIL because the parameter and metadata are not connected.

- [ ] **Step 3: Implement minimal integration**

在 `quality_rank` 中把 `weight_mode` 加入结果缓存键。`experience` 使用现有默认维度权重；`icir` 调用校准函数，失败时使用经验权重但保留 `weight_source="experience_fallback"`。将校准元数据写入结果。API Query 校验值仅允许 `experience`/`icir`，非法值返回 HTTP 422；将参数完整传给 `quality_rank`，并确保 API 层缓存/结果缓存均区分模式。

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality_quant.py tests/test_quality.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py api/server.py tests/test_quality_quant.py tests/test_quality.py
git commit -m "feat: expose quantized quality weight mode"
```

### Task 4: 前端透明展示量化权重与样本健康度

**Files:**
- Modify: `web/index.html`
- Test: `tests/test_quality_quant.py`（HTML contract test）

**Interfaces:**
- 前端请求 `/api/quality?...&weight_mode=experience|icir`。
- 前端显示 `weight_source`、五维权重、`sample_count`、训练截止日期和因子统计摘要。

- [ ] **Step 1: Write failing test**

```python
def test_quality_html_contains_weight_mode_and_metadata_fields():
    html = Path("web/index.html").read_text(encoding="utf-8")
    assert "weight_mode" in html
    assert "weight_source" in html
    assert "sample_count" in html
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_quality_quant.py::test_quality_html_contains_weight_mode_and_metadata_fields -q`
Expected: FAIL until the UI request and fields are added.

- [ ] **Step 3: Implement minimal UI changes**

在 quality 控件区增加「权重模式」下拉框（经验权重/ICIR量化权重），onchange 触发已有 `qsLoad`。请求 URL 追加 `weight_mode`；结果卡片显示权重来源、训练样本数、训练期和各维度统计，缺失时显示“经验权重回退/数据不足”，不使用推荐性措辞。

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_quality_quant.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add web/index.html tests/test_quality_quant.py
git commit -m "feat: show quantized quality weight metadata"
```

### Task 5: 全量验证与合规检查

**Files:**
- Modify: `CLAUDE.md`（仅在路由速查或设计决策缺少新参数时更新）
- Test: 全部 `tests/`

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_quality_quant.py tests/test_quality.py tests/test_quality_factors.py tests/test_quality_refine.py -q`
Expected: PASS。

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: 全部通过，不能忽略失败。

- [ ] **Step 3: Run static compilation**

Run: `python -m compileall api backtest data screener tests`
Expected: 无语法错误。

- [ ] **Step 4: Check API behavior and compliance text**

调用 `/api/quality?weight_mode=experience` 与 `/api/quality?weight_mode=icir`，确认两种模式都返回 200、结果结构一致、缺数据时是经验回退；确认响应仍含 `cand_disclaimer`，没有“推荐/买入/卖出/收益承诺”等措辞。

- [ ] **Step 5: Commit documentation if needed**

```bash
git add CLAUDE.md
git commit -m "docs: document quantized quality weight mode"
```

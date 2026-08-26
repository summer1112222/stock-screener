# 优质筛选因子审计与研究报告实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为优质筛选增加严格的因子数据审计、时点覆盖信息和历史 IC/Rank IC/分层收益研究能力，同时保持当前 `/api/quality` 排序权重与门槛不变。

**Architecture:** 采用“TDX 数据准备层 + 只读统计层”。数据准备层以 `data/pytdx_client.py` 为主源，通过 `get_daily_bars`、`get_quote`、`get_finance_info` 和 `get_company_info` 预热/缓存研究所需数据；统计层复用现有 `backtest/research.py` 与 `backtest/eval.py`，只消费已准备的 DataFrame/SQLite 数据，计算 IC、Rank IC、分层收益、覆盖与时点审计。API 仅返回研究报告，不把研究结果自动写入 `_DEFAULT_DIM_WEIGHTS` 或线上缓存；TDX 不提供可靠历史公告日/研报发布日期/完整历史资金流时，必须报告 `unknown_timing`，不得伪造无泄漏结论。

**Tech Stack:** Python 3.12, pandas 3.x, numpy, FastAPI, pytest, SQLite, pytdx TCP 7709。

**Spec:** 已批准的第一阶段设计：数据时点审计 + 因子 IC/分层收益报告，不改变线上优质筛选排序。

## Global Constraints

- 所有研究必须严格使用 t 日及以前的数据，前瞻收益从 t+1 开始计算。
- 研究报告是历史统计事实，不是预测，不改变 `/api/quality` 的筛选排序。
- 不新增 SQLite 表；数据准备层以 TDX 为主源允许按需 TCP 7709 拉取，统计函数本身不直接触网。
- 历史日 K 优先使用 `pytdx_client.get_daily_bars`，必要时经过现有本地 `adjust.qfq` 后写入/读取 `stock_daily`。
- 当前截面行情优先使用 `pytdx_client.get_quote`，财务字段优先使用 `get_finance_info`/`get_company_info("财务分析")`；必须保留 source/as_of 标记。
- 缺失因子保持 None/NaN 并报告 coverage，不以 0 分填充。
- 盘中盘口字段不进入跨日因子研究；若出现只能标记 `excluded_realtime_factor`。
- API 返回遵循现有 `_wrap()`，研究路由附 `bt_disclaimer`。
- 财报/研报/资金流没有可靠公告/生效日时，只输出 `timing_status`/coverage，不声称完成无泄漏历史回测。
- 不修改 `backtest.quality._DEFAULT_DIM_WEIGHTS`、`quality_rank` 的默认 `min_dims`、`dim_thresh` 或线上共振公式。
- 测试必须使用合成 DataFrame/`db.query_rows` mock，不依赖网络或真实行情。

## 文件结构与职责

- `data/pytdx_client.py`：作为 TDX 主源适配边界；复用现有日 K/行情/财务接口，不在统计函数中直接管理连接。
- `data/history.py` / `data/adjust.py`：负责 TDX 日 K 的拉取、前复权和 `stock_daily` 缓存一致性；研究前先完成数据准备。
- `backtest/research.py`：承载 TDX 缓存数据的通用截面因子研究、前瞻收益、分层收益和数据审计纯函数；不耦合 FastAPI、不直接触网。
- `api/server.py`：扩展已有 `/api/research/factor` 参数/响应，把质量因子研究结果作为可选研究模式输出；不改变 `/api/quality`。
- `tests/test_factor_research.py`：新增时点不泄漏、IC、分层、coverage、缺失和成本处理测试。
- `tests/test_server_new_routes.py` 或新增 `tests/test_quality_audit_api.py`：验证 API 参数透传、`bt_disclaimer` 和失败降级。
- `CLAUDE.md`：补充研究报告与线上排序解耦、时点审计约束。

### Task 1: 建立 TDX 数据准备与来源审计契约

**Files:**
- Modify: `data/history.py`（确认/扩展 TDX 主源历史准备接口，保持现有 fetch API 兼容）
- Modify: `data/pytdx_client.py`（仅在现有接口缺少研究所需 source/as_of 元数据时补充）
- Create: `backtest/research.py` 中的 TDX 研究准备函数（若现有文件已有实现则扩展，不重复建模块）
- Test: `tests/test_factor_research.py`
- Test: `tests/test_pytdx_client.py`

**Interfaces:**
- Produces: `prepare_tdx_panel(universe: str, codes: list[str], start: str, end: str, fields: list[str]) -> dict`，返回 `{panel, source, as_of, missing_codes, warnings}`；统计阶段只接收该结果中的缓存面板。
- Produces: `audit_data_source(source: str, requested_start: str, requested_end: str, actual_dates, fields: list[str]) -> dict`，返回 `status`, `source`, `coverage`, `actual_start`, `actual_end`, `missing_codes`, `warnings`。
- Produces: 统一研究输入契约：日期索引的 `factor_df`（index=date，columns=code）、日期索引的 `close_df`（index=date，columns=code）、前瞻周期 `horizons: list[int]`、分层数 `quantiles: int`。

- [ ] **Step 1: 读取现有 TDX 历史链路与研究接口，确认 source/as_of 字段**
- [ ] **Step 2: 写失败测试：TDX 返回空/部分代码时必须报告 missing_codes，不得静默降级成完整覆盖**

```python
def test_prepare_tdx_panel_reports_partial_source(monkeypatch):
    monkeypatch.setattr(pytdx_client, "get_daily_bars", lambda code, count: pd.DataFrame())
    out = research.prepare_tdx_panel("stock", ["600519"], "20240101", "20241231", ["close"])
    assert out["source"] == "tdx"
    assert out["missing_codes"] == ["600519"]
    assert out["warnings"]
```

- [ ] **Step 3: 写失败测试：研究统计函数不允许通过参数触发网络调用**
- [ ] **Step 4: 运行测试确认当前实现失败**

Run: `python -m pytest tests/test_factor_research.py tests/test_pytdx_client.py -q`
Expected: FAIL because the TDX preparation/audit contract is not implemented yet.

### Task 2: 明确研究数据契约并补前瞻测试

**Files:**
- Modify: `backtest/research.py`（保持现有函数兼容）
- Test: `tests/test_factor_research.py`

**Interfaces:**
- Produces: 测试所需的统一研究输入契约：日期索引的 `factor_df`（index=date，columns=code）、日期索引的 `close_df`（index=date，columns=code）、前瞻周期 `horizons: list[int]`、分层数 `quantiles: int`。
- Produces: 研究输出必须包含 `as_of_policy`, `coverage`, `ic`, `rank_ic`, `quantile_returns`, `cost_after_returns`, `warnings`。

- [ ] **Step 1: 读取现有研究函数与 API 测试，列出兼容签名**
  - 确认已有 `factor_research`/`run_factor_research` 的参数名、返回字段和错误格式。
  - 不删除或重命名旧字段；新字段只能追加。

- [ ] **Step 2: 写失败测试：前瞻收益禁止使用 t 日收盘作为收益终点**

```python
def test_forward_return_starts_after_signal_date():
    factor = pd.DataFrame({"A": [1.0, 2.0, 3.0]}, index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=factor.index)
    out = research.compute_forward_returns(close, horizons=[1])
    assert out.loc[pd.Timestamp("2024-01-01"), "A_1d"] == pytest.approx(0.10)
```

- [ ] **Step 3: 写失败测试：缺失因子不填零且 coverage 单独报告**

```python
def test_factor_report_preserves_missing_values_and_coverage():
    factor = pd.DataFrame({"A": [1.0, None], "B": [None, 2.0]}, index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    close = pd.DataFrame({"A": [100.0, 101.0], "B": [100.0, 99.0]}, index=factor.index)
    report = research.factor_report(factor, close, horizons=[1], quantiles=2)
    assert report["coverage"]["2024-01-01"]["available"] == 1
    assert report["coverage"]["2024-01-01"]["total"] == 2
    assert report["warnings"]
```

- [ ] **Step 4: 运行测试确认当前实现失败**

Run: `python -m pytest tests/test_factor_research.py -q`
Expected: FAIL because the new pure functions/fields are not implemented yet.

### Task 2: 实现无泄漏前瞻收益、IC 与分层统计

**Files:**
- Modify: `backtest/research.py`
- Test: `tests/test_factor_research.py`

**Interfaces:**
- Produces: `compute_forward_returns(close_df: pd.DataFrame, horizons: list[int], cost_bps: float = 0.0) -> pd.DataFrame`，结果列命名 `<code>_<horizon>d`，收益为 `close[t+h]/close[t+1]-1`，若中间/终点缺失则为 NaN。
- Produces: `factor_report(factor_df: pd.DataFrame, close_df: pd.DataFrame, horizons: list[int], quantiles: int = 5, cost_bps: float = 0.0, as_of_policy: str = "signal_close") -> dict`。
- `factor_report` 返回：`coverage`, `ic`, `rank_ic`, `quantile_returns`, `cost_after_returns`, `sample_counts`, `warnings`, `as_of_policy`。

- [ ] **Step 1: 实现 `compute_forward_returns`，统一日期排序、代码交集和 t+1 起算**
- [ ] **Step 2: 实现按日期横截面 Pearson IC 与 Spearman Rank IC**
  - 每个日期只使用因子和对应前瞻收益均非空的代码。
  - 有效样本少于 3 时返回 None，并增加 warning/sample count。
- [ ] **Step 3: 实现分位数组收益**
  - 每个日期按因子从低到高分 `quantiles` 组；每组使用等权前瞻收益均值。
  - 分组样本不足时跳过该日期，不补样本。
- [ ] **Step 4: 实现交易成本后收益**
  - `cost_bps` 按单次调仓成本从每个前瞻收益中扣除；不引入成交模拟器，避免把研究统计误称为真实成交。
- [ ] **Step 5: 运行针对性测试**

Run: `python -m pytest tests/test_factor_research.py -q`
Expected: 新增统计测试全部 PASS，旧 factor research 测试保持 PASS。

### Task 3: 实现数据时点审计与质量因子适配器

**Files:**
- Modify: `backtest/research.py`
- Modify: `backtest/quality.py`（仅新增只读因子导出/审计适配，不改排序）
- Test: `tests/test_factor_research.py`
- Test: `tests/test_quality_factors.py`

**Interfaces:**
- Produces: `audit_factor_timing(records: list[dict], factor_name: str, as_of_field: str | None, max_age_days: int | None = None) -> dict`，返回 `status`, `coverage`, `latest_as_of`, `future_rows`, `stale_rows`, `warnings`。
- Produces: `quality_research_snapshot(universe: str, days: int, codes: list[str] | None = None) -> dict`，只读取 quality 可复用的历史 OHLCV 因子与现有快照状态，不调用实时盘口，不改变 `_dim_scores`。

- [ ] **Step 1: 写财报/研报缺失生效日测试**
  - 没有公告日字段时必须返回 `status="unknown_timing"`，不能返回 `ok(no_leakage)`。
  - `future_rows` 只按明确的 `as_of`/公告日与研究截止日比较。
- [ ] **Step 2: 写盘口排除测试**
  - 输入含 `inner_outer_ratio`/`liquidity_depth` 时，研究快照将其列入 `excluded_realtime_factor`，不进入 IC 因子集合。
- [ ] **Step 3: 实现审计纯函数与 warning 汇总**
- [ ] **Step 4: 实现 quality 只读适配器**
  - 只返回因子名称、数据日期范围、覆盖率和 timing status；不得改变线上 `quality_rank` 输出。
- [ ] **Step 5: 运行质量相关测试**

Run: `python -m pytest tests/test_factor_research.py tests/test_quality_factors.py -q`
Expected: PASS。

### Task 4: 扩展研究 API，保持线上筛选不变

**Files:**
- Modify: `api/server.py`（已有 `/api/research/factor` 路由）
- Test: `tests/test_server_new_routes.py` 或 Create: `tests/test_quality_audit_api.py`

**Interfaces:**
- API 继续使用已有 `POST /api/research/factor`，追加可选参数：`audit_timing: bool = True`、`cost_bps: float = 0.0`、`quantiles: int = 5`、`horizons: list[int]`（沿用旧默认）。
- 响应追加 `data_quality`, `factor_audit`, `research_report` 字段；保留旧 `result`/统计字段。
- 失败时返回 HTTP 200 的研究错误结构（沿用现有路由约定），并附 `bt_disclaimer`，不吞掉明确的参数校验错误。

- [ ] **Step 1: 写 API 测试：参数透传与免责声明**

```python
def test_factor_research_returns_audit_and_disclaimer(client, monkeypatch):
    monkeypatch.setattr(server, "run_factor_research", fake_report)
    r = client.post("/api/research/factor", json={"horizons": [1, 5], "cost_bps": 10})
    assert r.status_code == 200
    assert "bt_disclaimer" in r.json()
    assert "factor_audit" in r.json()["data"]
```

- [ ] **Step 2: 实现参数解析、默认值与旧响应兼容**
- [ ] **Step 3: 确保 API 不调用 `/api/quality` 或修改 quality 缓存**
- [ ] **Step 4: 运行 API 测试**

Run: `python -m pytest tests/test_quality_audit_api.py tests/test_server_new_routes.py -q`
Expected: PASS。

### Task 5: 补充文档与全量验证

**Files:**
- Modify: `CLAUDE.md`（研究层说明与数据时点约束）
- Modify: `README.md`（仅补充用户可见的研究 API 说明，如现有内容缺失）
- Test: no new file

- [ ] **Step 1: 更新 `CLAUDE.md`**
  - 写明研究报告只读、t+1 起算、公告日未知时标记 unknown_timing、盘口不进入跨日质量研究。
- [ ] **Step 2: 检查 API/代码字段命名与现有文档一致**
- [ ] **Step 3: 运行专项测试**

Run: `python -m pytest tests/test_factor_research.py tests/test_quality_factors.py tests/test_quality_refine.py tests/test_quality_warmup.py -q`
Expected: PASS。

- [ ] **Step 4: 运行全量测试与编译检查**

Run: `python -m pytest tests/ -q`
Expected: 全部通过。

Run: `python -m compileall api data screener backtest scripts tests`
Expected: 无编译错误。

## Verification Checklist

- [ ] 研究收益从 t+1 开始，不使用 t 收盘作为买入成交价。
- [ ] 缺失值不填零，coverage/sample_counts/warnings 可解释。
- [ ] IC 与 Rank IC 使用横截面有效交集，样本不足不输出伪造数值。
- [ ] 分层收益按日期分组，不把未来日期混入训练截面。
- [ ] 财报/研报缺少公告日时标记 `unknown_timing`。
- [ ] 实时盘口字段不进入跨日质量研究。
- [ ] `/api/quality` 输出排序、权重、阈值和缓存行为未改变。
- [ ] `/api/research/factor` 保留现有响应兼容性与免责声明。
- [ ] 409+ 全量测试通过。

# 次日强势 V1 因子扩展实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有次日强势五步流程中加入市场环境、板块扩散、个股结构和风险惩罚，并以可解释字段输出。

**Architecture:** 在 `screener/nextday.py` 内增加纯计算辅助函数与一个增强聚合阶段，继续复用已有 DB 查询和历史面板，不引入新表或新增全市场网络请求。API 仅透传 `enhanced` 参数并保持既有响应结构兼容。

**Tech Stack:** Python 3.12, pandas/numpy, FastAPI, pytest, SQLite 只读查询。

**Spec:** `docs/superpowers/specs/2026-08-23-nextday-factor-v1-design.md`

## Global Constraints

- 不新增 SQLite 表、列或采集通道。
- 不在 `nextday` 计算中逐股触发 finshare、TDX 或其他网络请求。
- 缺数据必须返回 `None` 或明确降级状态，不得把缺失数据伪造成强势分。
- 保留 `hard_pass`、`step1_pass` 至 `step5_pass`、`step4_score` 等兼容字段。
- 所有 API 输出保持“机械排序观察清单，非荐股非买卖信号”措辞。
- 输出 JSON 前防止 NaN/Infinity。

### Task 1: 为 V1 因子定义测试夹具与纯函数契约

**Files:**
- Modify: `tests/test_nextday.py`
- Test: `tests/test_nextday.py`

**Interfaces:**
- Consumes: 现有 `_SPOT`、`_SFF`、`_BOARD_MEMBERS` 测试夹具。
- Produces: 后续实现必须满足的 `_market_environment(spots)`, `_sector_diffusion(board, spots)`, `_stock_structure(spot, history=None, sector_change=None, market_change=None)`, `_risk_penalty(spot, action_rows=None)` 纯函数契约。

- [ ] **Step 1: 写失败测试**

增加以下行为测试：

```python
def test_market_environment_returns_regime_and_scores():
    env = nd._market_environment(_SPOT)
    assert env["market_regime"] in {"strong", "neutral", "weak"}
    assert 0 <= env["score"] <= 100


def test_market_environment_empty_is_explicitly_missing():
    env = nd._market_environment([])
    assert env["score"] is None
    assert env["market_regime"] == "unknown"


def test_sector_diffusion_counts_up_and_limit_members():
    row = nd._sector_diffusion("电池", _SPOT, _BOARD_MEMBERS["电池"])
    assert row["up_ratio"] > 0
    assert row["limit_up_count"] >= 2
    assert 0 <= row["score"] <= 100


def test_stock_structure_uses_close_location():
    row = nd._stock_structure({"latest_price": 10, "day_low": 9,
                               "day_high": 10.5, "change_pct": 5,
                               "turnover_rate": 6})
    assert row["close_location"] == round((10 - 9) / 1.5, 4)
    assert row["score"] is not None


def test_risk_penalty_is_zero_without_risk_fields():
    row = nd._risk_penalty({"change_pct": 2, "turnover_rate": 4})
    assert row["penalty"] == 0
```

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_nextday.py -q`
预期：FAIL，提示新增纯函数尚未定义。

- [ ] **Step 3: 保持测试夹具不触网**

所有测试继续通过 `monkeypatch` 替换 `db.query_rows`、`_ma_arrange_batch`、`_board_members_batch` 和 TDX 客户端；不得在测试中访问真实网络。

- [ ] **Step 4: 暂不提交，进入 Task 2**

### Task 2: 实现市场、板块、个股和风险纯函数

**Files:**
- Modify: `screener/nextday.py`

**Interfaces:**
- Consumes: `list[dict]` spot 行、板块成员代码、单个 spot 行和可选历史/行为数据。
- Produces: 只包含 Python 标量和 `None` 的可序列化字典；分数字段统一为 0-100，缺数据为 `None`。

- [ ] **Step 1: 实现 `_market_environment(spots)`**

统计有效 `change_pct`、涨停（普通股票约 9.8% 以上）、跌停（约 -9.8% 以下）、成交额。成交额历史不可用时只使用横截面指标；返回：

```python
{"score": float | None, "market_regime": str,
 "up_ratio": float | None, "limit_up_count": int,
 "limit_down_count": int, "amount_ratio": float | None,
 "median_change_pct": float | None}
```

`score >= 65` 为 `strong`，`score <= 35` 为 `weak`，其余为 `neutral`；无有效行情返回 `unknown`。

- [ ] **Step 2: 实现 `_sector_diffusion(board, spots, members)`**

按纯代码归一化成员，计算上涨占比、涨幅超过 3% 占比、涨停数量和成员平均涨幅。至少有一个有效成员即可输出横截面结果；无有效成员时所有分数为 `None`。

- [ ] **Step 3: 实现 `_stock_structure(spot, history=None, sector_change=None, market_change=None)`**

优先使用 `day_high/day_low/latest_price` 计算收盘位置；计算上影线风险时没有开盘价则返回 `None`。若有历史收益和基准收益，计算相对强度；换手率直接保留为结构输入。返回 `score`、`close_location`、`upper_shadow_ratio`、`relative_strength`。

- [ ] **Step 4: 实现 `_risk_penalty(spot, action_rows=None)`**

基于可用字段计算 0-30 的惩罚：高涨幅/高换手轻度拥挤，以及行为行中的解禁、减持记录。无风险数据返回 0；不因缺失数据惩罚。

- [ ] **Step 5: 运行纯函数测试**

运行：`python -m pytest tests/test_nextday.py -q`
预期：Task 1 新增测试与既有测试均通过。

### Task 3: 接入 nextday 编排与可用因子归一化

**Files:**
- Modify: `screener/nextday.py`
- Modify: `tests/test_nextday.py`

**Interfaces:**
- Consumes: Task 2 四类纯函数、现有五步结果、`stock_spot`/`sector_fund_flow`/板块成员数据。
- Produces: 每个 item 增加 `gross_score`、`risk_penalty`、`market_regime`、扩展 `factor_scores`；顶层增加 `factor_version` 和 `enhanced`。

- [ ] **Step 1: 写排序与缺失数据测试**

```python
def test_enhanced_result_contains_factor_breakdown(monkeypatch):
    _setup(monkeypatch)
    result = nd.nextday_strong_rank(limit=10, enhanced=True)
    item = result["items"][0]
    assert result["factor_version"] == "v1"
    assert item["gross_score"] is not None
    assert "market_environment" in item["factor_scores"]
    assert "sector_diffusion" in item["factor_scores"]
    assert "stock_structure" in item["factor_scores"]
    assert item["score"] <= item["gross_score"]


def test_enhanced_false_preserves_legacy_score(monkeypatch):
    _setup(monkeypatch)
    enhanced = nd.nextday_strong_rank(limit=10, enhanced=True)
    legacy = nd.nextday_strong_rank(limit=10, enhanced=False)
    assert legacy["enhanced"] is False
    assert legacy["factor_version"] == "legacy"
    assert [x["code"] for x in enhanced["items"]]
```

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_nextday.py -q`
预期：FAIL，现有函数不接受 `enhanced` 或不返回新字段。

- [ ] **Step 3: 增加参数和缓存键**

给 `nextday_strong_rank` 增加 `enhanced: bool = True`，并把 `enhanced` 纳入 30 秒缓存 key，避免增强和旧模式互相污染。

- [ ] **Step 4: 编排全市场环境和板块扩散**

在候选循环前计算一次市场环境；复用既有板块排名/成员映射，为每只候选生成板块扩散分。不得在循环中重复查询数据库。

- [ ] **Step 5: 编排个股结构与风险惩罚**

使用现有 TDX/spot 补全字段与已有历史面板结果；行为风险只做单次批量 DB 查询或使用已经取得的数据，不调用 `behavior_series` 逐股回退网络。

- [ ] **Step 6: 实现可用因子归一化**

实现内部 `_weighted_available(scores, weights)`：忽略 `None`，将剩余权重归一化；全部缺失时返回 `None`。分数统一用 `_nan` 清理。

- [ ] **Step 7: 运行 nextday 测试**

运行：`python -m pytest tests/test_nextday.py -q`
预期：全部 PASS。

### Task 4: 扩展 API 参数和合规字段

**Files:**
- Modify: `api/server.py:937-961`
- Modify: `tests/test_server_new_routes.py` 或新增 `tests/test_nextday_api.py`

**Interfaces:**
- Consumes: `nextday.nextday_strong_rank(..., enhanced=...)`。
- Produces: `/api/nextday-strong?enhanced=true|false`，统一 disclaimer 文本。

- [ ] **Step 1: 写 API 测试**

验证 TestClient 请求能透传 `enhanced=false`，响应包含 `factor_version`、`enhanced`，并包含 `cand_disclaimer`。

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_nextday_api.py -q`
预期：FAIL，路由尚未声明或透传参数。

- [ ] **Step 3: 修改 Query 参数和函数调用**

增加：`enhanced: bool = Query(True)`，透传给 `nextday_strong_rank`；将 disclaimer 从“5因子”改成“多因子机械排序观察清单”。

- [ ] **Step 4: 运行 API 测试**

运行：`python -m pytest tests/test_nextday_api.py -q`
预期：PASS。

### Task 5: 全量验证与文档同步

**Files:**
- Modify: `CLAUDE.md`（补充 V1 字段和测试命令，若当前未提交内容已包含等价说明则只保留必要增量）
- Modify: `README.md`（同步次日接口已支持增强因子与 legacy 开关）

**Interfaces:**
- Consumes: Task 1-4 的实现与测试。
- Produces: 可由后续 Claude 实例直接使用的开发说明。

- [ ] **Step 1: 运行聚焦测试**

```bash
python -m pytest tests/test_nextday.py -q
python -m pytest tests/test_nextday_api.py -q
python -m pytest tests/test_factor_research.py -q
```

预期：全部 PASS。

- [ ] **Step 2: 运行静态检查**

运行：`python -m compileall api data screener backtest scripts tests`
预期：无语法错误。

- [ ] **Step 3: 运行全量测试**

运行：`python -m pytest tests/ -q`
预期：全部 PASS；若出现既有环境/网络依赖失败，记录具体失败测试，不伪称通过。

- [ ] **Step 4: 同步文档**

只补充实际实现的 `enhanced`、`factor_version`、风险惩罚和新增测试命令，不复制完整路由清单。

- [ ] **Step 5: 检查 diff**

运行：`git diff --check` 与 `git status --short`，确认没有调试文件、数据库或缓存被加入。

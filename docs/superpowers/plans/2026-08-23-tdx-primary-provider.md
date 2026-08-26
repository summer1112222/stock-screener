# TDX 主源统一数据层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可追溯的 TDX 主源访问层，并将核心行情、历史、回测、分析模块迁移到该协议，同时对未覆盖能力保留诚实标注的备援。

**Architecture:** 在 `data/tdx_provider.py` 增加轻量 Provider 结果协议，底层复用现有 `pytdx_client.py`/`adjust.py`；领域模块通过 Provider 获取 TDX 数据并保留现有算法。API 层统一输出来源、新鲜度、状态和备援字段，健康检查按模块聚合状态。

**Tech Stack:** Python 3.12, pandas 3.x, FastAPI, SQLite, pytest, pytdx。

**Spec:** `docs/superpowers/specs/2026-08-23-tdx-primary-provider-design.md`

## Global Constraints

- TDX 能覆盖的数据必须优先使用 TDX；不能把备援数据伪装成 TDX。
- TDX 与备援均失败时返回结构化 `unavailable`，禁止用默认值、随机值或静默空数组填充。
- 所有 API 响应继续经 `_wrap()`，保留 `disclaimer`、`update_time`、`bt_disclaimer`、`cand_disclaimer`。
- JSON 序列化必须将 NaN 转为 None；测试不得访问公网。
- 回测严格使用 t 日及以前数据，不得引入未来数据。
- 不新增投资建议、实时买卖点、自动下单或收益承诺。

---

### Task 1: 建立 Provider 结果协议

**Files:**
- Create: `data/tdx_provider.py`
- Test: `tests/test_tdx_provider.py`

**Interfaces:**
- Produces `ProviderResult` dataclass，以及 `quote(codes)`, `daily_bars(code, count)`, `company_info(code, category)` 和 `xdxr(code)` 包装函数。
- `ProviderResult` 字段：`data`, `source`, `status`, `fetched_at`, `data_date`, `rows`, `error`, `original_source`。
- Provider 只调用现有 `data.pytdx_client` 与 `data.adjust`，不新增网络库。

- [ ] **Step 1: 写失败测试**：覆盖 TDX 成功、空结果、异常、缓存来源保留和 NaN 清理；通过 monkeypatch 替换 `pytdx_client.get_quote/get_daily_bars/get_company_info/get_xdxr`。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest tests/test_tdx_provider.py -q`，预期因模块/接口不存在失败。
- [ ] **Step 3: 实现最小协议**：用 `@dataclass(frozen=True)` 定义结果对象；包装器把异常转为 `status="error"`，空返回转为 `status="empty"`；`rows` 从 DataFrame/list/dict 安全计算；日期统一为字符串。
- [ ] **Step 4: 运行测试确认通过**：`python -m pytest tests/test_tdx_provider.py -q`。
- [ ] **Step 5: 提交**：`git add data/tdx_provider.py tests/test_tdx_provider.py && git commit -m "feat: add tdx provider result protocol"`。

### Task 2: 接入历史 K 线与实时行情主链路

**Files:**
- Modify: `data/history.py`
- Modify: `data/portfolio.py`
- Modify: `data/watchlist.py`
- Modify: `screener/engine.py`
- Test: `tests/test_data_source_metadata.py`
- Test: `tests/test_pytdx_client.py`

**Interfaces:**
- 领域函数保留现有返回字段，并新增来源元数据；TDX 读取统一调用 `tdx_provider.daily_bars()` 或 `tdx_provider.quote()`。

- [ ] **Step 1: 写失败测试**：mock Provider，验证历史获取、持仓行情、自选行情和实时筛选成功时来源为 `tdx`；TDX 异常时验证已有缓存/备援带 `fallback` 或 `stale`。
- [ ] **Step 2: 运行指定测试确认失败**：`python -m pytest tests/test_data_source_metadata.py tests/test_pytdx_client.py -q`。
- [ ] **Step 3: 迁移调用**：将直接处理 TDX 连接的路径改为 Provider；历史路径保留本地 `adjust.qfq`；非交易时段明确标记收盘/缓存；批量报价一次调用，不逐股联网。
- [ ] **Step 4: 运行回归测试**：`python -m pytest tests/test_data_source_metadata.py tests/test_portfolio.py tests/test_watchlist.py tests/test_engine.py -q`。
- [ ] **Step 5: 提交**：`git add data/history.py data/portfolio.py data/watchlist.py screener/engine.py tests && git commit -m "feat: route market data through tdx provider"`。

### Task 3: 迁移历史研究与信号模块

**Files:**
- Modify: `backtest/engine.py`
- Modify: `backtest/research.py`
- Modify: `backtest/signals.py`
- Modify: `screener/nextday.py`
- Modify: `screener/daily_strong.py`
- Test: `tests/test_source_backtest_metadata.py`
- Test: `tests/test_nextday.py`
- Test: `tests/test_factor_research.py`

**Interfaces:**
- 回测和研究继续读取本地 `*_daily`，其采集入口保证数据源为 TDX 或明确备援；结果增加 `data_source`/`data_date`，不改变评分公式。

- [ ] **Step 1: 写失败测试**：mock 历史 Provider，验证 TDX 日 K 被保存并标记来源；验证回测截止日过滤未来行；验证无历史时返回结构化缺口。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest tests/test_source_backtest_metadata.py tests/test_nextday.py tests/test_factor_research.py -q`。
- [ ] **Step 3: 实现迁移**：次日/每日强势只通过统一历史数据接口取数；保留批量 `_behavior_batch`，不得逐股触发 finshare；所有结果 NaN→None。
- [ ] **Step 4: 运行测试确认通过**：执行上述测试及 `python -m compileall backtest screener data`。
- [ ] **Step 5: 提交**：`git add backtest screener tests && git commit -m "feat: mark tdx source across historical research"`。

### Task 4: 迁移财务、盘口与个股分析

**Files:**
- Modify: `data/fundamentals.py`
- Modify: `backtest/buffett.py`
- Modify: `api/server.py`
- Test: `tests/test_tdx_financial.py`
- Test: `tests/test_stock_analysis_source.py`
- Test: `tests/test_server_new_routes.py`

**Interfaces:**
- 财务路径保留 `parse_tdx_financial()` 主源和 AKShare 降备援；个股分析通过 Provider 聚合 quote/daily/company info，并在响应中提供 `sources`。

- [ ] **Step 1: 写失败测试**：验证 TDX 财务成功标记 tdx、解析失败才触发 AKShare fallback；股票分析同时有 TDX 行情和 fallback 研报时返回字段级来源。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest tests/test_tdx_financial.py tests/test_stock_analysis_source.py -q`。
- [ ] **Step 3: 实现最小改动**：复用现有缓存键规范和缺源哨兵；Server `_wrap()` 增加来源元数据兼容字段，不破坏既有响应结构与 disclaimer。
- [ ] **Step 4: 运行测试确认通过**：`python -m pytest tests/test_tdx_financial.py tests/test_server_new_routes.py -q`。
- [ ] **Step 5: 提交**：`git add data/fundamentals.py backtest/buffett.py api/server.py tests && git commit -m "feat: expose tdx source in analysis responses"`。

### Task 5: 建立模块级健康检查

**Files:**
- Modify: `api/server.py`
- Modify: `data/market.py`
- Create: `data/source_health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- `source_health.record(module, result)` 记录最近状态；`source_health.snapshot()` 返回模块级状态；`/api/health` 合并 TDX、fallback、stale、unavailable、最近时间、日期、行数和错误。

- [ ] **Step 1: 写失败测试**：验证同一模块的 TDX 成功、备援、不可用状态聚合；验证 `/api/health` 不泄露异常堆栈且保持 disclaimer。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest tests/test_health.py -q`。
- [ ] **Step 3: 实现状态注册表**：使用进程内线程安全字典；错误只保留类型和简短消息；无请求记录的模块返回 `unavailable`/`not_checked`，不虚构成功。
- [ ] **Step 4: 运行测试确认通过**：`python -m pytest tests/test_health.py tests/test_server_new_routes.py -q`。
- [ ] **Step 5: 提交**：`git add api/server.py data/source_health.py data/market.py tests && git commit -m "feat: add per-module source health"`。

### Task 6: 前端来源状态展示

**Files:**
- Modify: `web/index.html`
- Test: `tests/test_web_source_badges.py`

**Interfaces:**
- 前端提供 `sourceBadge(meta)`，根据 `tdx/cache/fallback/unavailable/stale` 输出无投资含义的来源标签；不改变数据排序和按钮行为。

- [ ] **Step 1: 写失败测试**：静态检查页面包含来源标签映射，且不会把 fallback 文案渲染为 TDX。
- [ ] **Step 2: 运行测试确认失败**：`python -m pytest tests/test_web_source_badges.py -q`。
- [ ] **Step 3: 实现展示**：在核心模块卡片头部显示“TDX / TDX 缓存 / 备援 / 暂无数据 / 数据源异常”，缺少元数据时显示“来源未声明”，不阻塞旧接口。
- [ ] **Step 4: 运行测试确认通过**：`python -m pytest tests/test_web_source_badges.py -q`。
- [ ] **Step 5: 提交**：`git add web/index.html tests/test_web_source_badges.py && git commit -m "feat: show source status in web ui"`。

### Task 7: 全量验证与文档同步

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-23-tdx-primary-provider-design.md`
- Test: existing `tests/`

- [ ] **Step 1: 运行编译检查**：`python -m compileall api data screener backtest scripts tests`。
- [ ] **Step 2: 运行全量测试**：`python -m pytest tests/ -q`。
- [ ] **Step 3: 运行来源静态检查**：确认新字段均经过 `_wrap()`，确认 fallback 不被标记为 tdx，确认 `q.json`/`q20.json` 等噪声不纳入提交。
- [ ] **Step 4: 更新 CLAUDE.md**：补充 Provider 协议、来源元数据、健康检查和 TDX 主源迁移约束。
- [ ] **Step 5: 提交文档与验证结果**：`git add CLAUDE.md docs && git commit -m "docs: document tdx primary data contract"`。

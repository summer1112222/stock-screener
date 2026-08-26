# 企业所有者研究卡实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有企业基本面分析链路中增量加入所有者收益质量、资本配置、三情景估值、反向估值和 A 股会计/治理风险雷达，并在个股分析页展示，不改变现有路由、数据库和回测口径。

**Architecture:** 继续以 `backtest/buffett.py` 为唯一研究计算层，在 `analyze()` 内复用已有摘要、三大表和 spot 数据，新增小型纯函数负责质量、资本配置、估值与风险字段组装。`/api/buffett` 已直接返回 `analyze()` 结果；`/api/stock-analysis` 的 Buffett worker 已将完整结果放入 `fundamentals`，因此 API 只需保持透传并增加 smoke 测试。前端在现有个股深度分析卡中加入四个独立研究区块，企业质量不进入 `score` 或 `outlook`。

**Tech Stack:** Python 3.12, pandas, SQLite 现有查询层, FastAPI/TestClient, 原生 JavaScript/HTML/CSS, pytest。

**Spec:** `docs/superpowers/specs/2026-08-21-owner-research-card-design.md`

## Global Constraints

- 不新增 SQLite 表、列、路由或数据源。
- 不修改回测可得日期、公告披露日逻辑或 `backtest/eval.py`。
- 缺失字段必须返回 `None` 并进入 `missing_fields`/`data_gaps`，不得以零冒充事实。
- 维持性资本开支无法从现有表区分时，明确标注“完整现金流表代理/不可区分”，不得宣称精确维护性资本开支。
- `owner_earnings_quality`、`capital_allocation`、`valuation_scenarios`、`reverse_valuation`、`risk_radar` 都是附加字段，保持现有字段兼容。
- 保留 `_wrap()`、`bt_disclaimer`、统一 disclaimer；前端措辞使用“机械估值/研究优先级/证据不足”，不输出买入、卖出或收益承诺。
- 企业质量研究不得与实时盘口、次日强势、技术信号合成总分。
- 风险雷达只陈述已有财报字段能支持的风险；关联交易、股权质押、审计意见、问询函等未接入内容必须列为数据缺口。

---

### Task 1: 新增纯函数测试与所有者收益质量字段

**Files:**
- Modify: `backtest/buffett.py:41-65,315-480`（新增纯函数并在 `analyze()` 组装）
- Modify: `tests/test_buffett_value.py:88-250`（完整、缺失和边界测试）
- Modify: `tests/test_buffett_fcf.py:30-76`（现金流比例回归测试）

**Interfaces:**
- Produces `buffett._safe_ratio(numerator: float|None, denominator: float|None) -> float|None`，分母缺失或为零返回 `None`。
- Produces `buffett._owner_earnings_quality(ratios: dict, history_years: int|None, missing_fields: list[str]) -> dict`，返回 `status`、质量指标和缺失字段。
- `analyze()` 新增 `res["owner_earnings_quality"]`，不删除既有 `ratios.owner_earnings`、`ratios.fcf_to_netincome` 等字段。

- [ ] **Step 1: 写失败测试。** 在 `tests/test_buffett_value.py` 添加：
  - 完整现金流数据时 `ocf_to_netincome=1000/800`、`capex_to_ocf=300/1000`、`working_capital_drag=50`、`history_years` 为可用年报数量，且 `status` 属于 `strong|medium|weak|uncertain`。
  - 缺失现金流表或净利润时对应值为 `None`，`missing_fields` 包含稳定字段名如 `cashflow`/`net_income`，不把缺失转成 0。
  - owner earnings/净利润为负或分母为 0 时比例为 `None`，不抛异常。
- [ ] **Step 2: 运行失败测试。**
  ```bash
  python -m pytest tests/test_buffett_value.py -q
  ```
  预期新增测试因函数/字段不存在失败，现有测试保持可定位。
- [ ] **Step 3: 实现最小逻辑。** 在 `buffett.py` 增加 `_safe_ratio` 与 `_owner_earnings_quality`；从现有 `cf_fields`、`ni_ann`、`real_fcf`、`ocf_ann`、`roe_ann` 组装：`ocf_to_netincome`、`capex_to_ocf`、`working_capital_drag`、`share_dilution`（现有摘要无股本历史时为 `None` 并记录缺失）、`history_years`。状态规则固定为：证据不足→`uncertain`；现金转换和 owner earnings 比例均≥0.8 且无重大缺失→`strong`；比例≥0.5 或存在轻微缺失→`medium`；比例<0.5 或 owner earnings/净利<0.5→`weak`。
- [ ] **Step 4: 运行通过测试。**
  ```bash
  python -m pytest tests/test_buffett_value.py tests/test_buffett_fcf.py -q
  ```
  预期 PASS，且旧 owner earnings/FCF 断言不变。
- [ ] **Step 5: 提交。**
  ```bash
  git add backtest/buffett.py tests/test_buffett_value.py tests/test_buffett_fcf.py
  git commit -m "feat: add owner earnings quality research"
  ```

---

### Task 2: 资本配置与风险雷达

**Files:**
- Modify: `backtest/buffett.py:466-532`（新增资本配置、风险项纯函数和 `analyze()` 组装）
- Modify: `tests/test_buffett_value.py`（资本配置与风险雷达测试）

**Interfaces:**
- Produces `buffett._capital_allocation(ratios: dict, abstract_metrics: dict, missing_fields: list[str]) -> dict`，返回 `status`、`score`、`dividend_signal`、`dilution_signal`、`leverage_signal`、`acquisition_signal`、`retained_earnings_return`、`evidence`、`missing_fields`。
- Produces `buffett._risk_radar(ratios: dict, capital_allocation: dict, owner_quality: dict, data_gaps: list[str]) -> dict`，返回 `overall`、`accounting`、`governance`、`data_gaps`；每项风险对象含稳定 `code`、`severity`、`message`、`value`。
- `analyze()` 新增 `res["capital_allocation"]` 与 `res["risk_radar"]`，继续输出原 `red_flags`。

- [ ] **Step 1: 写失败测试。** 增加测试覆盖：高商誉/高杠杆/低 FCF 转换生成 accounting 风险；缺少分红、股本和并购字段时资本配置为 `uncertain` 并记录缺失；已有杠杆、资本开支、留存收益线索生成对应 evidence；未接入的关联交易/质押/审计/问询函始终出现在 `data_gaps`，不能生成“无风险”结论；风险项包含 `code/severity/message/value`。
- [ ] **Step 2: 运行失败测试。**
  ```bash
  python -m pytest tests/test_buffett_value.py -q
  ```
  预期新增测试失败。
- [ ] **Step 3: 实现最小逻辑。** 只使用现有 `ratios` 和摘要/三大表已解析字段；可可靠识别的线索包括 FCF/净利润、资本开支占 OCF、债务率/权益乘数、商誉/权益、owner earnings/净利润和 BPS/净资产变化。无法从现有数据可靠识别的股息、回购、并购、股本稀释返回 `None` 并记录缺失。风险 severity 使用 `info|watch|high`，整体按最高 severity 聚合；任何关键数据缺失但没有可判定风险时为 `uncertain`。
- [ ] **Step 4: 运行通过测试。**
  ```bash
  python -m pytest tests/test_buffett_value.py tests/test_buffett_fcf.py -q
  ```
- [ ] **Step 5: 提交。**
  ```bash
  git add backtest/buffett.py tests/test_buffett_value.py
  git commit -m "feat: add capital allocation and risk radar"
  ```

---

### Task 3: 三情景估值与反向估值

**Files:**
- Modify: `backtest/buffett.py:570-624`（抽取/扩展估值计算）
- Modify: `tests/test_buffett_value.py:180-235`（情景和反向估值测试）

**Interfaces:**
- Produces `buffett._scenario_valuation(bps_latest: float|None, price: float|None, roe_avg: float|None, bps_cagr: float|None, cost_of_equity: float = _COST_OF_EQUITY) -> dict`，返回 `bear/base/bull` 三情景，每个情景含 `growth_g`、`cost_of_equity_r`、`sustainable_roe`、`intrinsic_value`、`margin_of_safety`、`status`/`note`。
- Produces `buffett._reverse_valuation(price: float|None, bps_latest: float|None, roe_avg: float|None, cost_of_equity: float = _COST_OF_EQUITY) -> dict`，返回 `implied_growth_g`、`implied_roe`、`implied_pb`、`status`、`note`。
- `analyze()` 保留原 `intrinsic_value`、`margin_of_safety`、`dcf_assumptions`，并附 `valuation_scenarios` 与 `reverse_valuation`。

- [ ] **Step 1: 写失败测试。** 增加：完整数据生成三种可排序情景且 bear/base/bull 的增长或 ROE假设单调；每个情景字段齐全；缺价格/BPS 时返回 `insufficient_data`；`g>=r` 的情景返回 `invalid` 而非除零；反向估值对现有 price/pb 可计算 implied PB/增长，缺数据显式 `insufficient_data`。
- [ ] **Step 2: 运行失败测试。**
  ```bash
  python -m pytest tests/test_buffett_value.py -q
  ```
- [ ] **Step 3: 实现最小逻辑。** 以现有 Gordon-on-Book 公式为唯一估值底层，bear/base/bull 使用透明的保守/基准/乐观参数扰动（不得新增网络数据）；统一对 `r-g<=0`、非正 BPS、非正价格做显式状态处理。反向估值仅根据当前价格/BPS 得到市场隐含 PB，并在可行时反解 `g` 或 `roe`，否则返回 `None` 和说明。
- [ ] **Step 4: 运行回归。**
  ```bash
  python -m pytest tests/test_buffett_value.py tests/test_buffett_fcf.py -q
  ```
- [ ] **Step 5: 提交。**
  ```bash
  git add backtest/buffett.py tests/test_buffett_value.py
  git commit -m "feat: add valuation scenarios and reverse valuation"
  ```

---

### Task 4: API 透传与 disclaimer 回归

**Files:**
- Modify: `api/server.py:978-983,1177-1390`（仅必要时补充明确的研究字段透传/错误隔离，不改变 outlook 计算）
- Modify: `tests/test_server_new_routes.py`（新增 `/api/buffett` 与 `/api/stock-analysis` smoke 测试）
- Modify: `tests/test_outlook_continuous.py`（如 mock fragment 需兼容新增字段，仅做最小调整）

**Interfaces:**
- `/api/buffett?code=` 的 `body.data` 包含四个新增顶层字段并保留 `body.bt_disclaimer`、`body.disclaimer`、`body.update_time`。
- `/api/stock-analysis?code=` 的 `body.data.fundamentals` 包含新增字段；这些字段不出现在 `score`、`outlook.contribs` 的计算输入中。

- [ ] **Step 1: 写失败 smoke 测试。** monkeypatch `server.bt_buf.analyze` 返回包含四个研究块的 fixture，mock TDX/主力/研报/千评/信号子域，断言两个 API 路由的字段位置和 disclaimer；另测 `fundamentals` 失败时路由仍返回 200 和错误片段。
- [ ] **Step 2: 运行失败测试。**
  ```bash
  python -m pytest tests/test_server_new_routes.py tests/test_outlook_continuous.py -q
  ```
- [ ] **Step 3: 实现。** 保持 `buffett_route()` 直接返回 `bt_buf.analyze()`；保持 `_buffett()` 将完整 `ba` 放在 `fundamentals`。只有在现有结构无法满足测试时，增加明确的 `owner_research` 别名或透传，不复制计算逻辑，不改统一包装。
- [ ] **Step 4: 运行通过测试。**
  ```bash
  python -m pytest tests/test_server_new_routes.py tests/test_outlook_continuous.py -q
  ```
- [ ] **Step 5: 提交。**
  ```bash
  git add api/server.py tests/test_server_new_routes.py tests/test_outlook_continuous.py
  git commit -m "test: verify owner research API passthrough"
  ```

---

### Task 5: 个股分析页四个研究区块

**Files:**
- Modify: `web/index.html:1575-1770`（增加渲染 helper 与 `anRun()` 卡片区块）

**Interfaces:**
- 新增前端 helper：`_renderOwnerQuality(q)`, `_renderCapitalAllocation(c)`, `_renderValuationScenarios(v, reverse)`, `_renderRiskRadar(r)`，均接收 API JSON 对象并返回 HTML 字符串；缺字段时显示“证据不足/暂无数据”，不得把 `null` 显示成 0。
- `anRun()` 从 `d.fundamentals.owner_earnings_quality`、`capital_allocation`、`valuation_scenarios`、`reverse_valuation`、`risk_radar` 读取并插入四个区块。

- [ ] **Step 1: 写可执行的渲染检查。** 在现有前端静态检查命令基础上，确认 helper 名称、四个区块标题和字段键均存在；若项目已有浏览器 smoke 测试，增加 fixture 渲染断言，否则用 `node --check`（若环境可用）或脚本括号/模板字符串检查。
- [ ] **Step 2: 实现渲染。** 在现有 `_kv`、颜色变量和表格风格上实现：所有者收益显示 status/转换比例/资本开支代理/缺失字段；资本配置显示 status/score/evidence；估值显示 bear/base/bull 及当前价格安全边际、反向估值 note；风险雷达按 severity 着色并展示 `code/message/value` 与 data gaps。HTML 文本统一使用研究措辞，不与“多因子机械预判”合并。
- [ ] **Step 3: 本地验证。**
  ```bash
  python -m compileall api data screener backtest tests
  ```
  并在浏览器打开个股分析页，使用已有 mock/本地服务验证缺失字段与完整字段两种状态不抛 JS 异常。
- [ ] **Step 4: 提交。**
  ```bash
  git add web/index.html
  git commit -m "feat: show owner research card"
  ```

---

### Task 6: 全量验证与文档同步

**Files:**
- Modify: `CLAUDE.md`（仅在实现字段命名或测试命令与现有说明不一致时补充）
- Modify: `docs/superpowers/specs/2026-08-21-owner-research-card-design.md`（仅记录已确认的实现细节，不改目标边界）

- [ ] **Step 1: 运行专项测试。**
  ```bash
  python -m pytest tests/test_buffett_value.py tests/test_buffett_fcf.py tests/test_server_new_routes.py tests/test_outlook_continuous.py -q
  ```
- [ ] **Step 2: 运行全量测试与编译。**
  ```bash
  python -m pytest tests/ -q
  python -m compileall api data screener backtest scripts tests
  ```
- [ ] **Step 3: 检查合规与兼容性。** 确认没有新路由/表/依赖，没有买卖指令或收益承诺，没有改变现有 score/outlook；确认 JSON 中不存在 NaN。
- [ ] **Step 4: 检查 diff。**
  ```bash
  git diff --check
  git status --short
  ```
- [ ] **Step 5: 提交最终文档（如有改动）。**
  ```bash
  git add CLAUDE.md docs/superpowers/specs/2026-08-21-owner-research-card-design.md
  git commit -m "docs: record owner research card implementation"
  ```

## Self-review

- **Spec coverage:** 所有者收益质量由 Task 1 覆盖；资本配置和风险雷达由 Task 2 覆盖；三情景估值与反向估值由 Task 3 覆盖；API 透传与 disclaimer 由 Task 4 覆盖；前端四区块由 Task 5 覆盖；缺失字段、风险 code/severity/message/value、非目标和最终验证由 Global Constraints/Task 6 覆盖。
- **Placeholder scan:** 计划不使用 TBD、TODO、“适当处理”或未定义函数；每个新增接口均给出参数和返回类型。
- **Type consistency:** Task 1 输出的质量对象供 Task 2 风险组装；Task 3 的估值对象供 Task 5 渲染；Task 4 约定四个字段位于 `/api/buffett` 顶层和 `/api/stock-analysis` 的 `data.fundamentals`；Task 5 使用相同键名。
- **Scope check:** 所有改动围绕同一研究卡数据流，未拆出独立项目；没有把回测日期、数据库或短线模块混入实现。

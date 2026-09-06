# 主力雷达与优质筛选联动实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将主力动向升级为多通道共振雷达，将主力行为/阶段附加到 quality 主清单，并落库验证 quality/nextday 清单的 T+1/T+3/T+5 历史表现。

**Architecture:** A 包在 `screener/smart_money.py` 只读聚合 `smart_money_action`，以封顶 today 的窗口计算共振、日净额、累计净额、连续性、边际加速和各通道数据日期。D 包在 `backtest/quality.py` 的组合完成后富化 main，不改变质量排序主语义；E 包新增 `list_track` 与 `backtest/tracker.py`，路由只在默认参数下记录，独立脚本/路由回填前视收益并按市场温度、行业强弱分层汇总。

**Tech Stack:** Python 3.12, pandas, SQLite, FastAPI, 原生 JavaScript。

**Spec:** `docs/superpowers/specs/2026-09-06-smartmoney-quality-radar-track-design.md`

## Global Constraints

- 所有输出仍是机械统计/观察清单，不构成荐股、买卖信号或收益承诺。
- API 响应继续使用 `_wrap()`，主力/清单输出附 `cand_disclaimer`，历史追踪附 `bt_disclaimer`。
- 所有浮点 NaN 必须转换为 `None`；新增 SQLite 表同步 `SCHEMA_SQL`、`TABLE_FIELDS`。
- 查询窗口必须封顶当前实盘日，不能被未来解禁日期劫持。
- 不新增 refresh 全量网络调用；A/D 只读 DB，E 不自动拉历史数据。

### Task 1: 实现主力多通道雷达后端

**Files:**
- Modify: `screener/smart_money.py`（新增 `_radar_window`、`radar`）
- Test: `tests/test_sm_radar.py`

**Interfaces:**
- Produces `radar(days: int = 5, market: str | None = None, limit: int = 50) -> dict`，返回 `rows/total/date/days/market`。
- 每行至少包含 `code/name/channel_hits/channels/daily_net/cum_net/streak_inflow/streak_outflow/margin_accel/net_intensity/data_asof/unlock_flag/unlock_as_of/unlock_amount`。

- [ ] 写失败测试：mock `data.db.query_rows` 返回资金流、龙虎榜、北向、高管、解禁和 `stock_spot`；断言金额通道正向各计一次、高管只按 `action`/方向计数、解禁不计 hits；断言同一窗口含未来解禁日时 date 仍不超过今天；断言排序先 hits 再 intensity；断言空表返回空结构。
- [ ] 运行 `python -m pytest tests/test_sm_radar.py -q`，确认新增函数测试失败。
- [ ] 实现 `_radar_window`：先用 `date <= datetime.now().strftime('%Y-%m-%d')` 查询最新实盘日，再取自然日窗口；按 `market` 下推过滤；读取 limit=0 后按 code×channel×date 聚合。
- [ ] 实现金额通道 `资金流/龙虎榜/北向` 的 `daily_net`、`cum_net`、`streak`、`margin_accel`；高管增持按 action/方向判正向且不进入金额和 intensity；解禁仅写独立风险信息；每通道保存 `latest_date` 与 `data_asof`。
- [ ] 复用 `_attach_intensity` 的成交额口径，补充 code 级 `net_intensity`，使用 `_nan` 守卫并按 `(channel_hits, net_intensity, cum_net)` 降序截断。
- [ ] 运行测试并提交 `feat: add smart money radar`。

### Task 2: 暴露雷达 API 与前端子视图

**Files:**
- Modify: `api/server.py:799-868`
- Modify: `web/index.html:269-306,1170-1405`
- Test: `tests/test_sm_radar_api.py`（如现有 API 测试模式可复用）

**Interfaces:** `GET /api/smart-money/radar?days=5&market=&limit=50`，通过 `_wrap` 返回雷达数据与 `cand_disclaimer`。

- [ ] 先为路由写 TestClient 测试，断言参数透传、响应含 disclaimer 和 `data_asof`。
- [ ] 在 `server.py` 增加 Query 范围校验与路由，错误时返回空 rows/诚实 note，不让聚合异常变成 500。
- [ ] 在 smart tab 增加雷达切换按钮、容器和加载状态；新增 `smRadarLoad/smRadarRender` 调 API，表格展示 hits、通道日期/净额、日净额、累计净额、连续流入、边际加速、强度、解禁标记；行点击复用现有 analysis 跳转处理。
- [ ] 在 `smLoad` 与 tab 初始化中保持现有视图默认行为，雷达只按用户切换或显式刷新加载。
- [ ] 运行 API/前端相关测试并提交 `feat: expose smart money radar`。

### Task 3: 将主力行为附加到 quality 主清单

**Files:**
- Modify: `backtest/quality.py:1145-1170`
- Modify: `web/index.html:310-347,1484-1600`
- Test: `tests/test_quality_behavior_cols.py`

**Interfaces:** 新增内部 `_enrich_main_behavior(main, universe, days)`；stock main 每项附 `streak_inflow/streak_outflow/cum_net/margin_accel/north_cum/net_intensity/data_asof/mf_phase/mf_confidence/behavior_group`。

- [ ] 写失败测试：mock `_behavior_batch` 和 `main_force_phase`，断言主清单出现行为字段、`出货`且 confidence≥0.6 进入 warnings、ETF 不触发 phase/行为查询、缺数据返回 None 不崩；断言四象限分组按 quality resonance 与 phase 生成。
- [ ] 运行 `python -m pytest tests/test_quality_behavior_cols.py -q`，确认失败。
- [ ] 在 `_apply_combo` 后、`_clean_item` 前只对 `universe == 'stock' and main` 批量调用 `_behavior_batch`；逐项调用 `main_force_phase`，限制 main 前 20 项，异常转 None。
- [ ] 从 `smart_money_action` 聚合结果取 `daily_net/cum_net/streak/margin_accel/data_asof`，不要改变 `adjusted_resonance`、严格门槛或排序；阶段只作为标签，出货只追加 warning 不扣分。
- [ ] 前端 quality 主表增加连续流入、累计净额、边际加速、阶段/置信度/出货告警，并增加四象限筛选/统计小块。
- [ ] 运行 quality 全量测试与新增测试，提交 `feat: link quality with smart money behavior`。

### Task 4: 新增清单追踪表与纯 SQLite 追踪层

**Files:**
- Modify: `data/models.py:252-366`
- Create: `backtest/tracker.py`
- Test: `tests/test_tracker.py`

**Interfaces:**
- `record_list(module: str, mode: str, date: str, items: list[dict]) -> int`
- `is_default_params(module: str, params: dict) -> bool`
- `fill_returns(limit: int = 0) -> dict`
- `summary(module: str | None = None, mode: str | None = None) -> dict`

- [ ] 写失败测试覆盖 `list_track` schema、`record_list` 同日同 code 幂等、默认参数判定、T+1 open 买入/T+1+k close 卖出、缺 open 时 T 日 close 降级并写 `meta_json.entry`、summary 平均/中位/胜率。
- [ ] 在 `SCHEMA_SQL` 增加 `list_track`：`module/mode/date/code/name/rank/score/meta_json/ret_k1/ret_k3/ret_k5/filled_ts/ts`，唯一键 `(module,mode,date,code)`；增加 `LIST_TRACK_FIELDS` 与 `TABLE_FIELDS`。
- [ ] `record_list` 使用 `INSERT OR IGNORE`，只记录 quality main、nextday strict passed_items、nextday score all_items；序列化 meta 时仅保留 hits/dim_scores/confidence 或 factor_scores/step_status。
- [ ] `fill_returns` 只读取 `stock_daily`，按交易日排序计算 entry/exit；不自动触网，未到期或缺历史跳过并返回 counters。
- [ ] `summary` 聚合 k1/k3/k5 的 count/filled/mean/median/win_rate，并读取 `market_daily`、`stock_spot.board` 形成 `by_market_temperature` 与 `by_industry` 分层；市场温度使用已有 up/down、zt/dt、pe_pct，行业没有映射时标注 unknown。
- [ ] 运行新增测试与 schema 初始化测试，提交 `feat: add list tracking storage`。

### Task 5: 接入 quality/nextday 默认清单记录与回填 API/脚本

**Files:**
- Modify: `api/server.py:919-968`
- Create: `scripts/fill_track_returns.py`
- Modify: `web/index.html:310-347,943-1000`
- Test: `tests/test_tracker_api.py`

- [ ] 写失败测试：默认 quality 请求调用 `record_list`，非默认参数不调用；nextday strict 记录 passed、score 记录 all；`GET/POST /api/track/fill` 与 `GET /api/track/summary` 返回对应 disclaimer。
- [ ] 在 quality/nextday 路由中显式构造默认参数字典，调用 `tracker.is_default_params` 后用响应真实实盘 date 记录，失败只写日志不影响主响应。
- [ ] 增加 `/api/track/fill`（GET/POST）与 `/api/track/summary`，统一 `_wrap`；summary 使用 `bt_disclaimer`：“历史清单机械追踪统计，非预测，不构成投资建议”。
- [ ] 创建脚本入口 `python -m scripts.fill_track_returns [limit]`，调用 `tracker.fill_returns` 并打印 JSON，不自动拉数据。
- [ ] 在 quality/nextday 前端增加追踪卡，显示追踪天数、样本数、k1/k3/k5 中位收益/胜率，并显示市场温度、行业强弱分层；加载失败显示诚实错误。
- [ ] 运行 `python -m pytest tests/test_tracker_api.py tests/test_tracker.py -q`，再运行完整 `python -m pytest tests/ -q` 与 `python -m compileall api data screener backtest scripts tests`。
- [ ] 检查 `git diff`、确认无 db/log/cache 噪声，提交 `feat: validate quality and nextday lists`。

## 验收清单

- `python -m pytest tests/ -q` 全绿。
- `/api/smart-money/radar` 不触网，未来解禁日期不会劫持窗口。
- quality 主排序数值不因行为富化改变，ETF 不执行个股阶段计算。
- 默认清单可幂等落库，回填口径明确为 T+1 open 买入、T+1+k close 卖出。
- summary 能区分 quality/nextday、strict/score，并提供市场温度和行业弱强分层。

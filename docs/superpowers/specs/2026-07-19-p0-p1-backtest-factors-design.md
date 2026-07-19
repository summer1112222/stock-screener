# 设计：P0（防自欺）+ P1（因子/筛选增强）

- **日期**：2026-07-19
- **状态**：已确认，待写实现计划
- **范围**：在现有 stock-screener 上落地 6 项改动（P0 三项 + P1 三项），不改变项目"数据筛选/回测研究工具，非投资咨询"的定位。

## 1. 背景与目标

项目已有完整的四层架构（data / screener / backtest / api）与多口径共振编排层（quality）。作为研究工具，当前的薄弱点集中在：

1. 回测引擎不含交易成本——高频策略净值虚高，是"月 20% 幻觉"的主要来源。
2. `robust.walk_forward` 是单次 60/40 切分，对过拟合的检测力弱。
3. 幸存者偏差仅在 docstring/字符串告警，未结构化、未提供钩子。
4. 历史因子库薄（仅 5 个 OHLCV 派生），缺反转/流动性这类 A 股有效因子。
5. 个股没有 conditions AND 过滤路径（只有 ETF/板块有；个股只能走 candidates 的单因子 rank）。
6. buffett 财务摘要按需实时拉取、无缓存，`_AK_OK=False` 时整路空。

本设计落地 P0（1-3，防自欺）与 P1（4-6，因子/筛选增强），为后续 P2/P3（信号胜率、ETF 特化因子、组合优化、资金流时序化）打基础。

## 2. 合规边界（硬约束，贯穿全部子项）

- **不荐股、不输出实时买卖点、不承诺收益**（含"月 10-20%"这类表述）。所有新因子/钩子沿用"机械筛选/研究优先级/非买卖信号"措辞。
- 回测类路由（`/api/backtest/run`、`/api/backtest/eval`、`/api/backtest/walkforward`）复用现有 `BT_DISCLAIMER`，个股筛选走 `_wrap` 附 `DISCLAIMER`。**不新增 disclaimer 文本**。
- 因子值、共振分位、IC 等均为"事实陈述/统计量"，非收益预测/推荐强度。

## 3. P0 设计

### P0-1 交易成本模型

- **文件**：`backtest/engine.py`、`api/server.py`（`BTRunReq`）
- **改动**：
  - `run_backtest` 新增参数 `cost_bps: float = 30.0`（默认 0.3%，A 股印花税+佣金+滑水的合理量级，可参数化）。
  - 扣减点：每个调仓日 d 已算出换手 `turnover_d`（新旧权重 L1 差，现 `turnovers` 列表已有），当日组合收益扣 `turnover_d * cost_bps / 10000`。
  - 响应增字段：`cost_bps`、`total_cost_drag`（累计成本拖累 = 扣减项之和）；保留现有 `turnover`（平均换手）。
  - `BTRunReq` 加 `cost_bps: float = 30.0` 字段；前端可不改（默认值生效）。
- **数据流**：`server.bt_run` → `run_backtest(close, factor, ..., cost_bps=req.cost_bps)` → 返回净值已含成本扣减 → `risk_metrics` 基于扣减后净值计算（夏普等反映真实可达成性）。
- **错误处理**：`cost_bps` 为负 → 当 0 处理（不抛异常）；非数值 → 用默认。
- **测试**：合成 close+factor，断言 `cost_bps=30` 的 `equity_curve` 末值 < `cost_bps=0` 的末值；断言 `total_cost_drag > 0`。

### P0-2 滚动 walk-forward

- **文件**：`backtest/robust.py`、`api/server.py`（`/api/backtest/walkforward` 路由）
- **改动**：
  - 新增 `rolling_walk_forward(factor, close, n, train_months=12, test_months=2, step_months=2)`：参数为后端常量，不暴露 API（用户已选）。
  - 按日历月滚动切分：训练段 `[t-12mo, t)` → 测试段 `[t, t+2mo)`，步长 2 月前移，覆盖整个样本期。
  - 每段算 IC/IR（复用 `eval.ic_series`/`ic_summary`）与 `decay = (train_ic - test_ic)/|train_ic|`。
  - 返回 `{segments: [{train_range, test_range, train_ic, test_ic, decay}], oos_ic_mean, oos_ic_median, n_segments, overfit_frac}`，`overfit_frac` = decay>0.5 段占比。
  - `/api/backtest/walkforward` 路由切到 `rolling_walk_forward`；旧 `walk_forward` 保留不删（避免破坏潜在调用，作单次切分 fallback）。
- **数据流**：路由复用 `BTEvalReq`，调 `rolling_walk_forward(factor, close, n=req.n)` → `_wrap(..., {"bt_disclaimer": BT_DISCLAIMER})`。
- **错误处理**：样本不足（< 14 个月，无法形成至少一段）→ 返回 `{"error": "样本不足(需≥14个月)", "n_segments": 0}`，不抛异常。
- **测试**：合成 24 月因子+前视收益，断言 `segments` 非空、`oos_ic_median` 为数值、`overfit_frac ∈ [0,1]`。

### P0-3 幸存者偏差轻量落地

- **文件**：`backtest/robust.py`、`backtest/engine.py`、`api/server.py`
- **改动**：
  - `robust.py` 新增 `survivorship_status()` 返回结构化：`{note: str, universe_approximation: True, delisted_coverage: "akshare 时点成分不全，已退市/ST 标的多缺失"}`。保留 `survivorship_note()` 返回字符串（兼容）。
  - `run_backtest` 加 `delisted_codes: list[str] | None` 钩子：**仅用于在响应里显式记账** `delisted_declared`（用户声明哪些标的已退市）。不改变收益计算——现有 `engine.py:68` 的 `fillna(0.0)` 已把停牌/退市早停的 NaN 按停牌近似处理（持仓价值不变），退市标的只要在 close 面板内且被选入 topN 即自动按此处理。钩子价值是"显式承认覆盖盲区"而非改算法（轻量方案的诚实边界）。
  - `/api/backtest/run`、`/api/backtest/eval` 响应的 `survivorship` 字段从字符串升级为该结构化对象。
- **数据流**：`BTRunReq` 加 `delisted_codes: list[str] | None = None`；`bt_run` 调 `run_backtest(..., delisted_codes=req.delisted_codes)`（默认 None，行为不变）；响应附 `survivorship=survivorship_status()`、`delisted_declared`。
- **错误处理**：`delisted_codes` 中的 code 不在 close 列 → 忽略，不报错。
- **测试**：合成 close+factor，传 `delisted_codes=["000001"]`，断言响应含 `delisted_declared=1` 且收益计算行为与不传时一致（fillna(0) 不变）。

## 4. P1 设计

### P1-1 反转 + 流动性因子

- **文件**：`backtest/eval.py`、`backtest/candidates.py`、`api/server.py`（`BACKTEST_FACTORS`、`LABEL`）
- **改动**：
  - `eval.compute_factor` 加分支：
    - `reversal_n` = `-close.pct_change(n)`（n=5/20，短期反转因子；A 股短期反转效应显著）。
    - `amihud_n` = `(|close.pct_change()| / amount).rolling(n).mean()`（Amihud 非流动性；量级极小但分档用 rank/pct 不受影响）。
  - `BACKTEST_FACTORS`（server.py:136）扩为 `["momentum_n", "volatility_n", "turnover_n", "activity", "momentum", "reversal_5", "reversal_20", "amihud_20"]`。
  - `candidates.history_factors`（candidates.py:137）同步扩。
  - `LABEL` 字典补：`reversal_5`→"5日反转"、`reversal_20`→"20日反转"、`amihud_20`→"Amihud非流动性(20日)"。
- **数据流**：`compute_factor` 按 factor_key 前缀派发（`reversal`/`amihud`），与现有 `momentum`/`volatility` 同级；IC/分档/回测路径自动复用，无需额外接线。
- **错误处理**：`amount` 为 None 时 `amihud_n` 返回空 DataFrame（同 `turnover`/`activity` 现有行为）；`reversal` 不依赖 amount，正常返回。
- **测试**：合成 close+amount，断言 `reversal_5.iloc[-1] == -close.pct_change(5).iloc[-1]`；`amihud_20` 形状与 close 一致；分档/IC 路径不报错。

### P1-2 个股 conditions AND 过滤

- **文件**：`screener/conditions.py`、`screener/engine.py`、`api/server.py`（`/api/screen` 路由、`/api/fields`）
- **改动**：
  - `conditions.py` 新增 `STOCK_FIELDS_CAT`，字段取自 `STOCK_SPOT_FIELDS`：`change_pct`/`turnover_amount`/`turnover_rate`/`total_market_cap`/`circulating_market_cap`/`pe`/`pb`/`amplitude`/`volume_ratio`/`latest_price`，ops 覆盖 gt/gte/lt/lte/eq/ne/between/topn。
  - `engine.py` 新增 `filter_stocks(conditions, sort, asc, limit, min_turnover, limit_pct)`：走 `db.query_rows("stock_spot")` → `_add_derived`（如适用）→ 可交易预筛（ST/涨停/停牌/低成交额，engine 内置等价实现，避免跨模块依赖 `candidates._tradable_filter` 私有函数）→ `_apply_conditions` → `_sort_df` → 截断。返回结构与 `filter_etfs` 一致：`{rows, total, skipped, category}`。
  - `server.py /api/screen` 加分支：`if category in ("stock","个股")` → 调 `engine.filter_stocks`，附 `DISCLAIMER`（经 `_wrap`）。
  - `/api/fields` 返回加 stock 分组（前端下拉已动态渲染，通常无需改 JS；`LABEL` 字典补个股字段中文标签）。
- **数据流**：前端选 `category=个股` → POST conditions → server → `filter_stocks` → `_wrap` 附 `DISCLAIMER`。
- **错误处理**：stock_spot 为空 → 返回 `{rows:[], total:0, skipped:["个股数据为空，先 /api/refresh"]}`（与 filter_etfs 一致）。
- **测试**：合成 stock_spot 行（含 ST、涨停、停牌），断言 ST 被滤、`between` 区间过滤生效、`topn` 取前 N。

### P1-3 buffett 财务摘要缓存

- **文件**：`data/models.py`、`data/db.py`、`backtest/buffett.py`
- **改动**：
  - `models.py`：`SCHEMA_SQL` 加表 `financial_abstract_cache(code TEXT PRIMARY KEY, payload_json TEXT, ts TEXT)`（按 code 单行存整张最新摘要，见 §9.E）；`TABLE_FIELDS` 加 `FINANCIAL_CACHE_FIELDS = {"code","payload_json","ts"}`。
  - `db.py`：迁移列表加 `_migrate financial_abstract_cache`（CREATE TABLE IF NOT EXISTS，旧库直接补建，无 ALTER 列需求）。
  - `buffett.fetch_abstract(code)` 改为：先查缓存行，`ts` 在 7 天内→命中返回（解析 payload_json）；未命中走 akshare `stock_financial_abstract`，成功后写缓存（upsert，带 ts）。
  - `_AK_OK=False` 时：缓存有则降级返回（响应标注 `stale=True`），无则返回 None（不再"整路空"，避免 quality 口径2 因网络问题全空）。
- **数据流**：`buffett.analyze_many` → `fetch_abstract(code)` → 缓存命中秒回 / 未命中走网络并回填 → 解析评分。
- **错误处理**：akshare 拉取异常 → 缓存有则降级、无则该 code 返回 None（不抛崩，与现有 `(df,ok,err)` 约定一致）；payload_json 解析失败 → 视为缓存未命中，重新拉取。
- **测试**：mock `db.query_rows`/`upsert_rows`，断言：(a) 缓存命中不走 akshare；(b) 未命中走 akshare 并写缓存；(c) ts 超过 7 天视为过期；(d) `_AK_OK=False` 且缓存有 → 返回 `stale=True`。

## 5. 改动检查清单覆盖（CLAUDE.md）

- P1-3 新表 → 已同步 `SCHEMA_SQL` + `TABLE_FIELDS` + `db` 迁移。
- P0-3/P1-2 新增响应字段（结构化 survivorship、个股筛选）→ 前端 `LABEL` 补。
- P1-1 新因子 → `BACKTEST_FACTORS` + `candidates.history_factors` + `LABEL` 三处同步。
- NaN→None 序列化：新增 `filter_stocks` 必须用 `df.astype(object).where(pd.notna(df), None)`（与 `filter_etfs`/`filter_boards` 一致），避免 `allow_nan=False` 500。
- 日期范围过滤：新因子 `compute_factor` 用 pandas 滚动，不涉及字符串日期比较，无 `_filter_range` 类陷阱。

## 6. 非目标（YAGNI，明确不做）

- 不做退市标的时序表与时点成分回测（P0-3 选轻量方案，akshare 数据不全）。
- 不做特质波动率（idio_vol，需市场模型 beta，留待 P2）。
- 不做 signals 历史胜率回测（P2）。
- 不做 ETF 特化因子填口径2（P2）。
- 不做 quality 组合层最小方差优化（P2）。
- 不做 smart_money 时序化（P3，动 schema 较大）。
- 不暴露 walk-forward 窗口参数为 API（用户已选后端常量）。

## 7. 测试策略

- 全部新逻辑放 `tests/`，合成数据 mock `db.query_rows`，不依赖网络。
- P0-1/P0-2/P0-3 各 1 个测试；P1-1 至少 2 个（reversal + amihud）；P1-2 一个覆盖 ST/between/topn；P1-3 覆盖四路径。
- 宿主跑 `python -m pytest tests/ -q`（tests/ 被 .dockerignore 排除，镜像里没有）。

## 8. 实现顺序建议

P0-1 → P0-2 → P0-3 → P1-1 → P1-3 → P1-2（P1-3 涉及 schema，先于 P1-2 落地以减少并行冲突；P0 内部按引擎→robust→钩子顺序）。

## 9. 审阅补充备忘（边界决策，实现时遵守）

- **A. cost_bps 语义（P0-1）**：`cost_bps` 是**双边换手成本率**（基点），`turnover_d` = 新旧权重 L1 差已含买+卖两侧，故扣减 = `turnover_d * cost_bps / 10000`，不重复乘 2。默认 30bps（0.3%）含印花税 0.05% 卖单边 + 佣金 0.025%×2 + 过户费 0.001%×2 + 滑点，偏保守但合理。
- **B. walk-forward 边界（P0-2）**：`n_segments = max(0, floor((总月数 - train_months - test_months) / step_months) + 1)`，总月数 < 14 → 返回 error 不抛异常。`train_ic=0` 或 `None` 时 `decay=None`（复用 robust.py 现有 `if ic_tr_mean not in (None,0)` 守卫，防除零）。
- **C. amihud 方向（P1-1）**：`amihud_20` 越**小**=流动性越好=越优。candidates/回测排序默认 `sort=desc` 取大值，amihud 需用 `sort=asc`。LABEL 标签注明"越小越好"；分档多空里低档（g=0）是高流动性组，高档是多空里的"低流动性"，解读时注意方向。
- **D. 涨停阈值已知限制（P1-2）**：`limit_pct=9.9` 适用于主板（10%涨停）。科创板/创业板 20%、北交所 30% 会被误杀为"涨停"过滤掉。与 `candidates._tradable_filter` 现有限制一致，本设计不修（留待后续按板块区分）。
- **E. buffett 缓存键（P1-3）**：`stock_financial_abstract` 返回多期摘要、无单一 `report_date`。缓存表简化为 `financial_abstract_cache(code TEXT PRIMARY KEY, payload_json TEXT, ts TEXT)`——按 code 单行存整张最新摘要 JSON，7 天 TTL。`payload_json` 解析失败视为未命中重拉。
- **F. 前端显示适配（P0-3/P0-2）**：`survivorship` 字段从 str 升级为 dict、walkforward 响应结构变化（segments 数组）。前端 `web/index.html` 相关渲染处需小改（显示 dict.note / segments 表格）；属实现范围内，LABEL 字典同步补。
- **G. P0-1 测试数据**：合成因子需保证调仓日 topN 有变化（换手>0），否则 `cost_bps=30` 与 `cost_bps=0` 净值相等、断言失败。用两期因子排名差异大的合成数据。

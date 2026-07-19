# 优质选股筛选 · 设计文档

**日期**: 2026-07-15
**状态**: 设计已确认（七节逐节通过），待转实施计划
**范围**: `backtest/quality.py`（新增编排层）+ `api/server.py` 1 路由 + `web/index.html` 1 card + `tests/test_quality.py`
**选型**: 方案 B —— 分口径并列 + 共振层 + 漏斗组合（不强行合成一个总分）

---

## 1. 背景与合规边界

需求：选到更优质的个股或 ETF。用户确认四档口径全要 + 加组合层。

本项目是**数据筛选/观察工具，非投资咨询**。本功能定位为"**多口径共振机械排序观察清单**"：

- 只做因子横截分位 + 共振标记 + 组合约束，**不输出买卖点、不承诺收益、不荐股**。
- 措辞统一用"优质筛选/观察清单/共振/分位/命中口径/通过预筛"，**禁**"推荐/买入/卖出/买卖点/强势股/必涨/看好"。
- `resonance` 是"多口径同靠前"的**事实陈述**，非"收益预测"或"推荐强度"。
- 清单与排序类响应附 `cand_disclaimer`："多口径共振机械排序观察清单，非荐股非买卖信号，盈亏自负。"
- 权重默认等权，**不替用户预设风格**（避免暗示动量优先/价值优先更优）。

---

## 2. 架构与模块边界

复用现有四套因子源（**不重写因子计算**），只加一层"口径合成 + 共振 + 组合约束"。最小侵入。

```
backtest/quality.py          # 新增：四口径分位 + 共振层 + 组合层编排
  quality_rank(universe, days, weights, k, min_dims, constraints) -> dict
    ├─ tradable 预筛(容量/ST/停牌/涨停)
    ├─ 口径分位计算(复用 buffett.analyze / signals.scan_signals /
    │               smart_money.top_by_amount / candidates 多因子 z-score 结果)
    ├─ 共振层: hits×10 + 命中口径平均分位; min_dims 门槛
    └─ 组合层: max_per_board 贪心 + max_corr 贪心(仅候选集) + limit

api/server.py                # 新增 1 路由
  GET /api/quality?universe=&days=&weights=&min_dims=&constraints=
    → _wrap(quality_rank(...), {"cand_disclaimer": ...})

web/index.html               # 新增"优质筛选"card
  四口径可配权重/约束 → 输出共振主清单 + 各口径子表 + 不可用口径灰灯
```

**模块边界**：
- `quality.py` 是编排层，**不碰因子计算**——四口径分位原始值从 buffett/signals/smart_money/candidates 拿，quality 只做横截 z-score → 分位 → 口径分 → 共振 → 组合。
- 因子源文件（buffett.py/signals.py/smart_money.py/candidates.py）**不动**。
- 因子数据来源分三类：
  - **spot/快照因子**（资金流、换手、估值 PE/PB、市值、成交额）：`stock_spot`/`etf_spot`，实时有。
  - **历史因子**（波动率、动量、夏普、最大回撤、RSI、MA、5 信号）：`*_daily`，**必须先 `/api/backtest/fetch` 拉过历史**，否则口径 1/4 空。
  - **基本面因子**（buffett）：`ak.stock_financial_abstract` 按需实时拉，**不入库**，`_AK_OK=False` 时口径 2 空。
- 与现有 signals/buffett 数据依赖链一致（CLAUDE.md 已记），不引入新依赖。

---

## 3. 四口径因子库 + 数据来源

每口径算 0-1 横截分位（tradable 集内 z-score → `rank(pct=True)`），方向统一"大=优质"。个股/ETF 分两套。

### 口径 1：风险调整（稳）— 个股 & ETF 通用
| 因子 | 来源 | 方向 | 备注 |
|---|---|---|---|
| `volatility_n` | `*_daily`，n=20 | 越小越好→取负 | 复用 `eval.compute_factor` |
| `momentum_n` | `*_daily`，n=20 | 越大越好 | 中期动量 |
| `sharpe_proxy` | `*_daily` | 越大越好 | 均收益/波动，本地算 |
| `max_drawdown` | `*_daily` | 越小越好→取负 | 近 N 日 |

分位 = 四因子横截 z-score 等权合成后百分位。**依赖历史**，无 `*_daily` → 口径 1 空。

### 口径 2：价值+质量（便宜且好）— 仅个股
复用 `buffett.analyze(code)` 现有输出：
| 因子 | 来源 | 方向 |
|---|---|---|
| `moat_score` | buffett | 越大越好 |
| `leverage_adj_roe` | buffett | 越大越好 |
| `earnings_yield_pct` | buffett(100/PE) | 越大越好(越便宜) |
| `valuation_rel_pct` | buffett 横截分位 | 越大越好 |

负面红旗（商誉/净资产>30% 或 资产负债率>75% 或 FCF/净利润<0.3）作**硬预筛**（红旗标的直接踢出口径 2，不参与分位）。`_AK_OK=False` 或缺财务摘要 → 口径 2 空。**ETF 口径 2 恒空**。

### 口径 3：资金流向（主力在买）— 个股 & ETF
| 因子 | 来源 | 方向 |
|---|---|---|
| N 日累计主力净额 | `smart_money_action`（`top_by_amount` 已算） | 越大越好 |
| `main_net_inflow` | `stock_spot`/`etf_spot` 当日 | 越大越好 |
| `turnover_rate` | spot | 越大越好(活跃度，辅) |

**ETF 限制**：smart_money 4 通道 P1 全 `market=股票`（ETF 资金流 P2 待补），故 ETF 资金流口径仅 spot 的 main_net_inflow/turnover，无主力动向累计——信号弱，接受。

### 口径 4：多信号共振（技术面）— 个股 & ETF
复用 `signals.scan_signals` 5 信号，口径分 = 触发信号数 / 5（0-1）。**依赖 `*_daily`**，无历史 → 空。可配 `min_signals`（默认 ≥2 才算"共振触发"）。

### 分位合成规则
- 每口径内：横截 z-score 等权（或可配 `weights`）合成 → `rank(pct=True)` 得 0-1。
- 四口径分位各自独立，**不合成一个总分**（方案 B 核心：分口径并列，共振见 §4）。
- 方向处理：波动率/回撤/估值 PE 取负或用盈利收益率正向化，保证"大=好"。

### 个股 vs ETF 分层
| 口径 | 个股 | ETF |
|---|---|---|
| 1 风险调整 | ✓(历史) | ✓(历史) |
| 2 价值质量 | ✓(buffett) | ✗ 恒空 |
| 3 资金流向 | ✓(smart_money+spot) | △(仅 spot，主力动向待 P2) |
| 4 多信号 | ✓(历史) | ✓(历史) |

个股 4 口径全可用，ETF 最多 3（口径 2 恒空）。

---

## 4. 合成打分 + 共振层

方案 B 核心：分口径并列 + 共振标记。需主排序键让最终清单有序。

### 4.1 共振分（主排序键）
每标的对每口径有 0-1 分位 `dim_score`：
1. **命中口径数** `hits` = 分位 ≥ 阈值（默认 0.6）的口径数（个股 0-4，ETF 0-3）。
2. **共振分** `resonance` = `hits × 10 + avg(命中口径分位)` —— 命中数为主（每命中 +10），同命中数内按平均分位细排。

"4 口径全中"必排"3 中"之上，"3 中"排"2 中"之上；同命中数内按分位平均分高低。**共振实质**：多口径同靠前 > 单口径高分。

### 4.2 `min_dims` 共振门槛
可配 `min_dims`（默认 2）：`hits < min_dims` 不进共振池（只在各口径子表 `by_dim`，不在主清单 `main`）。宁缺毋滥——单口径高分票不进主清单，除非另一口径也认可。ETF 默认 `min_dims=2`（上限 3，2 算强共振）。

### 4.3 口径权重（可选，默认等权）
`weights={1:1,2:1,3:1,4:1}`，命中口径 `avg` 用加权平均。默认等权，**不设默认偏向**（避免替用户预设风格）。

### 4.4 降级行为
- 口径 2 依赖 buffett 按需拉，`_AK_OK=False` → 口径 2 全 0 分位 → 该口径"不参与"，`hits` 分母=可用口径数，个股 `hits` 上限降到 3。
- 口径 1/4 依赖 `*_daily`，无历史 → 该两口径 0 → 个股 `hits` 上限降到 2（仅价值+资金）。
- **降级不报错**：`quality_rank` 返回 `dims_available` 标可用口径，`min_dims` 自动 clamp 到 `min(min_dims, len(dims_available))`，前端灰掉不可用口径。

### 4.5 输出结构
```json
{
  "main": [{"code","name","resonance","hits","dim_scores":{1:..,2:..,3:..,4:..},"reasons":[...]}],
  "by_dim": {"1":[...],"2":[...],"3":[...],"4":[...]},
  "dims_available": [1,2,3,4],
  "dim_status": {"1":"ok","2":"ok","3":"ok(仅spot)","4":"ok"},
  "min_dims": 2,
  "cand_disclaimer": "..."
}
```
`reasons` 拼出"命中风险调整(分位0.7)+价值(0.8)+资金(0.6)，共振3档"——叙事化已有分位，不引入新判断，合规用"命中/分位"不写"推荐买入"。

---

## 5. 组合层约束

共振主清单按 `resonance` 排序后，组合层**贪心顺序应用**约束（按优先级依次裁剪，不重新优化）。

### 5.1 容量约束（硬）
- `min_turnover`（默认 5e7）：成交额不足剔除。复用 `candidates._tradable_filter`。
- 排除 ST / 停牌 / 涨停（复用 `_tradable_filter`）。
- **作用阶段**：最先应用，在分位计算之前剔（防低流动性票靠低波动拿高分）。

### 5.2 行业/板块分散（硬）
- `max_per_board`（默认 3）：同行业板块最多保留 N 只。
- **取数**：个股行业归属——`stock_spot` 无行业列则查 `industry_board` 成分或 `ak.stock_individual_info_em` 兜底（best-effort，缺失则该约束跳过、`constraints_applied` 标"行业信息缺失"）。
- ETF "板块"用 ETF 分类（宽基/行业/主题/跨境），从 `etf_spot` 类型字段取；无分类则跳过。
- **贪心**：按 `resonance` 降序遍历，每只查其板块已入选数，<`max_per_board` 入选否则跳过，直到凑够 `limit`。

### 5.3 相关性上限（软，可选）
- `max_corr`（默认 0.85）：标的间近 N 日收益率相关系数 ≤ 阈值。
- **取数**：`*_daily` 收盘 pct_change 相关矩阵。**依赖历史**，无历史 → 跳过。
- **贪心**：入选池每加一只，检查它与已入选池 max 相关系数，超阈跳过。
- **成本**：只在"共振主清单候选"（≤100 只）上算，不算全市场。
- `max_corr=0` 关闭。

### 5.4 最终数量
- `limit`（默认 20）：组合层裁剪后取前 `limit` 只。候选不足 → 返回实际数 + `note:"约束过严，仅 N 只入选"`。

### 5.5 约束应用顺序
```
tradable 预筛(容量/ST/停牌/涨停)
  → 口径分位计算(tradable 集内横截)
  → 共振主清单(hits×10+avg 分位排序, min_dims 门槛)
  → 行业分散(max_per_board 贪心)
  → 相关性(max_corr 贪心, 仅候选集, 依赖历史)
  → 取 limit 只
```

### 5.6 输出补充
主清单每只加 `constraints`：`{"board":"银行","board_count_in_pool":2,"max_corr_hit":0.42}`，前端显示"为何入选/相邻票为何被剔"。

**默认值汇总**（用户确认按默认）：`min_turnover=5e7` / `max_per_board=3` / `max_corr=0.85` / `limit=20` / `min_dims=2` / 阈值=0.6 / `min_signals=2`。

---

## 6. ETF 口径分层

### 6.1 ETF 四口径映射
| 口径 | 个股 | ETF 替代因子 | 来源 |
|---|---|---|---|
| 1 风险调整 | volatility/momentum/sharpe/maxDD | **同**（ETF 有 `*_daily`） | etf_daily |
| 2 价值质量 | buffett 全套 | ✗ 恒空 | — |
| 3 资金流向 | smart_money 累计+spot 主力净额+换手 | spot `main_net_inflow`+`turnover_rate`（无累计，P2 待补） | etf_spot |
| 4 多信号 | 5 信号 | **同**（ETF 有 `*_daily`） | etf_daily |

ETF 最多 3 口径（2 恒空），`hits` 上限 3。

### 6.2 ETF 价值口径（P1 不做，P2 记）
真正适合 ETF 的"价值"是折溢价率、规模/流动性、跟踪误差、成分股加权估值。akshare ETF 接口可达性未知，**P2 探源再补**。P1 ETF 价值口径恒空，`dims_available` 标明。

### 6.3 个股/ETF 统一入口
`quality_rank(universe="stock"|"etf")` 单参控制，内部按 universe 选因子源。**不混跑**——个股 ETF 横截分位不可比，强行合跑会让 ETF turnover 和个股 ROE 同框 z-score 失真。

### 6.4 ETF 降级
- ETF 历史未 fetch → 口径 1/4 空，仅口径 3（spot 资金流），`hits` 上限 1 → `min_dims=2` clamp 到 1 → 主清单退化为"单口径资金流排序"。前端标"ETF 历史未拉，仅资金流口径"。

---

## 7. 错误处理与降级

### 7.1 因子源失败映射
| 因子源 | 失败情形 | 影响 | 处理 |
|---|---|---|---|
| `*_daily` 历史 | 未 fetch / 空表 | 口径 1、4 空 | 分位全 0，`dims_available` 不含 1/4 |
| buffett | `_AK_OK=False` / 拉取失败 | 口径 2 空 | 个股 `hits` 上限降到 3 |
| smart_money | 4 通道东财被封（已知） | 口径 3"主力累计"分位弱化（仅 spot 当日） | 口径 3 仍可用（靠 spot） |
| spot 快照 | stock_spot/etf_spot 空 | 全口径无基准 → 整体空 | 返回 `error:"spot 为空，先 /api/refresh"` |

### 7.2 异常不抛崩
每因子源调用包 try/except，异常 → 该口径 `dim_status="err:..."`，分位 0，不抛到 `quality_rank` 外。复用现有 buffett/signals/smart_money 已有 try/except，quality 再包一层兜底。

### 7.3 NaN→None 序列化
口径分位、resonance、hits 等数值出口时**必须** `df.astype(object).where(pd.notna(df), None)` 或标量级 `_to_float`+NaN 检查（与 smart_money 一致）——防 starlette `allow_nan=False` 500。分位计算中某标的某口径缺失（如 buffett 拉不到）→ 该口径分位 None，`hits` 计算时 None 不算命中、不计分母。

### 7.4 dim_status 不静默
`dims_available` + 各口径 `dim_status`（ok/err/空）显式回传，前端灰掉不可用口径（与 smart_money channels 灯一致）。例：`{"dims_available":[1,3,4], "dim_status":{"1":"ok","2":"err:_AK_OK=False","3":"ok(仅spot)","4":"ok"}}`。

### 7.5 性能兜底
- buffett `analyze_many` 逐股拉财务摘要 = N 次网络请求。quality 默认只在**已过 tradable 预筛 + 共振候选集**（≤100 只）上调 buffett，不全市场拉——复用 `buffett.shortlist_by_turnover` 先缩范围。
- 历史相关矩阵只在候选集算（§5.3）。
- buffett 单股超时 → 该股口径 2 空，不阻塞其他股。

---

## 8. 测试 `tests/test_quality.py`

合成数据 mock `db.query_rows` / 因子源返回值，不触网（沿用现有 tests/ 模式）：

- `test_dim_scores_percentile` — 4 口径分位合成集内正确算 0-1、方向正确（波动率取负后高分位）。
- `test_resonance_hits_priority` — hits=3 的标的 resonance > hits=1（哪怕后者单分位 0.99）——验证命中数为主键。
- `test_min_dims_gate` — hits<min_dims 不进主清单、只在 by_dim 子表。
- `test_etf_dim2_empty` — ETF 口径 2 恒空、hits 上限 3、min_dims clamp。
- `test_degrade_no_history` — 无 `*_daily` → 口径 1/4 空、dim_status 标 err、min_dims clamp、不崩。
- `test_degrade_no_akshare` — buffett `_AK_OK=False` → 口径 2 空、个股 hits 上限降 3、不崩。
- `test_tradable_filter_applied` — ST/停牌/涨停/低成交额在分位计算前剔除。
- `test_max_per_board_greedy` — 同行业最多 max_per_board 只，贪心保留高 resonance。
- `test_max_corr_greedy` — 相关性超阈跳过（合成相关矩阵）。
- `test_nan_to_none` — 分位/resonance 缺失为 None、不抛 NaN（防 500 回归）。
- `test_disclaimer_attached` — quality_rank 返回含 cand_disclaimer 串。

---

## 9. 改动检查清单（对齐 CLAUDE.md）

- 新增 `quality.py` 编排层 → **不新增表**（复用 stock_spot/etf_spot/*_daily/smart_money_action）、不新增采集源、不新增 SQLite 列、无需 `_BOARD_MIGRATIONS`。
- 新增 1 路由 `/api/quality` → 用 `_wrap` + `cand_disclaimer`。
- 新增前端 card → 沿用 `.card/.btn/.disc/.empty` 样式，LABEL 字典若涉新字段补中文标签。
- 单测放 `tests/`，合成 mock，不触网。
- 不破坏现有 NaN→None、日期过滤（pd.to_datetime 比较）、pandas 3.0（ME/QE）等约束。
- 因子源文件 buffett.py/signals.py/smart_money.py/candidates.py **不动**，只读其结果。

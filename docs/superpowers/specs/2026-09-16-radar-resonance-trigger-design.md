# 雷达共振触发因子：接回 nextday + 升级 quality 口径3

日期：2026-09-16
状态：待用户审阅

## 目标

2026-09-06 nextday 重构时**刻意移除**了 smart_money / chip / main_force_phase 因子，理由是 IC 校准显示单通道资金连续性/主力阶段/筹码收集因子前瞻收益解释力不足（仅 `mom_5_1` 有强正 IC）。但雷达 `radar()` 的**多通道共振**本质是不同于单通道叠加的信号：单通道噪声大、IC 均值统计稀释条件信号；当 ≥2 个金额通道**同向净流入**时，这是个**条件性信号**——命中率低但命中时强。

本设计把雷达共振作为**触发型因子**（不是连续权重）重新接入 nextday 与 quality 口径3，且比重构前砍掉的单通道因子更克制：

1. **触发与连续分彻底分离**：不复用 radar 的连续 `resonance` 求和分（它把"通道覆盖度"与"方向一致性"混在一起），只取底层每通道 `net` 符号重新定义干净的计数触发原语；
2. **nextday 不进五因子权重表**：五因子 IC 校准核心（mom_5_1 0.30 等）完全不动，radar 只做**乘法条件 boost**，且有 base_score 地板门槛，底部股不被救；
3. **quality 口径3 同依赖升级**：口径3 本来就读 `smart_money_action`（`_behavior_batch`），换 radar 多通道视角是同数据源升级、非新增依赖；
4. **诚实降级**：smart_money 未 refresh → 全部 `has_data=False` → nextday 退回纯五因子、quality 口径3 None 排除（`min_dims` clamp 兜底），不伪造不崩。

合规边界不变：所有新字段均为**机械统计/条件标记**，挂 `cand_disclaimer`，不构成买卖信号。

## 触发原语：`radar_resonance_for`

`screener/smart_money.py` 新增 `radar_resonance_for(codes: list[str], days: int = 5) -> dict[str, dict]`：

- **复用 `radar()` 30s 缓存**：调 `radar(days=days, limit=0)` 取全量后按 code 字典匹配候选集，不重复查库、不触网、不新增表；
- 候选集外（radar 未返回的 code）→ `has_data=False`；
- 每只返回：
  - `in_count` (0-3)：金额通道[资金流 / 龙虎榜 / 北向]里**累计净额同向为正且强度过门槛**的通道数；
  - `out_count` (0-3)：同向为负且强度过门槛的通道数；
  - `mgmt_confirm` (bool)：高管增减持通道为增持方向（股数通道，**单独标不并入 0-3**，量纲不混算）；
  - `has_data` (bool)：是否在 `smart_money_action` 有记录；
  - `low_liq` (bool)：成交额 < `radar` 的 `min_turnover`（强度失真）；
  - `asof` (str|None)：该 code 最新主力数据日期；
- **强度地板**（关键，防量纲盲）：每通道计数门槛为 `intensity = net / turnover` 绝对值 > **0.001**（净额占成交额 > 0.1% 才算）——radar 已算 `_intensity`（line 270），量纲归一，大小盘公平，与 `low_liq` 的 turnover 门槛配合（先挡低成交额股，再挡低强度通道）；
- **不复用连续 `resonance` 分**：理由见目标 1，连续求和分把缺通道记 0 致覆盖度污染方向信号；
- **low_liq 股通道一律不计入 in/out_count**：`low_liq=True`（turnover < `min_turnover`）的 code，其 `_intensity = net/turnover` 被小分母**放大失真**（小成交额股 net 即使不大，除以小 turnover 也易过 0.001 地板），强度地板挡不住——故 low_liq 股 in_count/out_count 恒为 0、`has_data=True`、`low_liq=True` 诚实标注，不谎报"无数据"也不触发。nextday/quality 侧统一按"low_liq=True → 不 boost / 口径3 该子因子 None 排除"处理。

**为何不复用 radar 现有 `channel_hits`**：`channel_hits` 计数无强度地板（+100 元与 +1 亿同计 1），且把高管增持 +1 混入金额通道计数。新原语拆开量纲、加强度地板、in/out 分离。

## 模块 1：nextday 接入（乘法条件 boost）

`screener/nextday.py`：

### 接入点

- **不进 `_FACTOR_WEIGHTS` / `_rank_pct` / `_weighted_score`**：五因子（mom_5_1 0.30 / rel_strength 0.20 / sr_10 0.15 / close_vol_corr 0.10 / liq_turnover 0.15）保持 IC 校准权重不变，`_weighted_score` 产出 `base_score` 不动；
- 粗筛 top 200 之后、`_weighted_score` 算出 `base_score` 之后，调 `radar_resonance_for(codes, days)` 取 in_count；
- **乘法 boost**：`final_score = base_score × (1 + 0.05 × max(0, in_count - 1))`
  - in_count=0 或 1 → +0%（单通道不算共振，避免噪声）；
  - in_count=2 → +5%；
  - in_count=3 → +10%；
  - `has_data=False` 或 `low_liq=True` → 不 boost（视同 in_count=0）；
- **base_score 地板门槛**（克制关键）：仅 `base_score ≥ 50` 的股吃 boost；低于 50 触发只标诊断字段 `radar_resonance_in` 不加分——radar 是"条件放大器"不是独立因子，不该救底部股；地板 50 = nextday score 0-100 的中位，等于"至少不弱于全市场中位动量才配吃主力加成"。

### 诊断字段

每只 item 追加：
- `radar_resonance_in` (int|None)：in_count 原值（0-3 或 None 当 has_data=False）；
- `radar_resonance_out` (int|None)：out_count；
- `radar_mgmt_confirm` (bool|None)：高管增持确认；
- `radar_boost` (float)：实际 boost 倍率（1.0 / 1.05 / 1.10，或 1.0 当 base<50）；
- `radar_data_source` (str)：`"smart_money_action@{asof}"` 或 `"无主力数据"` 或 `"low_liq"`；
- `selection_mode` / `step_status` / `factor_scores` 不变（radar 不进 factor_scores，是叠加层非因子层）。

### 风险标记（不硬拒）

- `out_count ≥ 2` → item 加 `risk_flag: "多通道主力净流出"`（仅标记，不进 strict 过滤的 hard_reject，不阻断入选——与 quality 的 risk_flag 语义一致，方向=择时非质量）；
- 前端 nextday 表格新增列展示 `radar_resonance_in` / `radar_boost` / `risk_flag`。

### 降级

- smart_money 未 refresh → 全部 `has_data=False` → 无 boost 无 flag → nextday 退回纯五因子排序，`radar_data_source="无主力数据"`；
- 30s 缓存键含 `radar_resonance_for` 的 days 参数（已含于 nextday 主缓存键）。

## 模块 2：quality 口径3 升级（替换 4 子因子）

`backtest/quality.py`：

### 替换口径3 子因子

- **现状**：口径3 经 `_behavior_batch` 取 `streak_inflow` / `north_cum` / `margin_accel` + `inner_outer_ratio`（tdx 内外盘 b_vol/s_vol），4 子因子经 `_avg_rank_pct` 聚合；
- **改后**：
  - 主子因子 = `in_count` 在**候选集内**横截 rank-pct（复用 `_to_pct` / `_avg_rank_pct`，skipna）——**不用 radar 全市场预排序**（radar 的 `resonance` 排序是全市场的，quality 要在 shortlist 内重排）；
  - `inner_outer_ratio` **保留为正交第二子因子**（tdx 内外盘是独立数据源，smart_money 数据失效时口径3 仍有 tdx 信号兜底）；
  - 口径3 权重**维持 0.6**（`_DEFAULT_DIM_WEIGHTS[3]=0.6`）——多通道共振虽强于单通道，但未经 IC 校准验证前不提权，保持克制；
- **`has_data=False` 处理**（诚实缺失）：`in_count` 给 **None，排除出口径3 rank-pct 分母**（`_avg_rank_pct` 本就 skipna），不伪造 0 分、不给中性 0.5——与 codebase "缺失保持 None" 原则一致；
  - 代价：多数候选无 smart_money 数据时口径3 变稀甚至缺，但 `min_dims` 门槛自动 clamp + `dim_status=err` 不崩，稀疏比伪造强；
- 口径3 仍空（ETF 或全无数据）→ 沿用现有降级，口径3 pct=None，hits 不计。

### 风险标记

- `out_count ≥ 2` → 进 quality 现有 `risk_flags`（`_risk_penalty` 机制，杠杆1.5/FCF1.5/波动2.0/脉冲1.0 之外的"主力多通道净流出"标记，上限5.0）——不硬拒，走 `hard_gate_pass` 之外的 risk_penalty；
- `strict_quality` 模式下 low_confidence 过滤逻辑不变（radar 不影响 confidence 判定）。

### 缓存

- `quality_rank` 缓存键追加 `radar_days`（口径3 数据依赖窗口），其余键不变；
- 预热 `_warm_quality_cache_background` 默认参数对齐，无需改（days 默认值即对齐前端）。

## 数据依赖与合规

- **不新增表、不触网**：`radar_resonance_for` 只读 `smart_money_action` + `stock_spot`，复用 `radar()` 30s 进程缓存；
- **nextday 重新获取 smart_money_action 依赖**（重构砍掉的那个），但只读不触网，降级路径干净；
- **量纲分离**：金额通道（资金流/龙虎榜/北向）进 in/out_count，股数通道（高管增持/限售解禁/十大股东）不并入计数——`mgmt_confirm` 单独标，与 CLAUDE.md 记录的 radar 量纲分离约束一致；
- **措辞**：nextday 前端展示用"主力共振"机械标记，不出现"推荐买入"；quality 沿用"多口径共振机械排序观察清单"措辞；两处 disclaimer 不变（nextday `cand_disclaimer` / quality `cand_disclaimer`）；
- **smart_money_action.date 列混有未来日期**（限售解禁 as_of）：`radar()` 已用 `date <= today` 封顶，`radar_resonance_for` 继承此逻辑，不改。

## 测试

- `tests/test_smart_money.py`（或新文件）加 `radar_resonance_for` 单测：mock `radar()` 返回，验证 in/out_count 强度门槛（net>0 但 intensity<0.001 不计数）、mgmt_confirm 分离、has_data=False、low_liq 不触发；
- `tests/test_nextday.py`（50 测试现有）加：①base_score<50 不 boost；②in_count=2/3 boost 倍率；③has_data=False 降级；④out_count≥2 标 risk_flag；
- `tests/test_quality_factors.py` 加：口径3 用 in_count rank-pct、has_data=False→None 排除、inner_outer_ratio 保留、out_count≥2 进 risk_flags；
- 全部 mock `db.query_rows` / `radar()`，不触网。

## 不改动（out of scope）

- `radar()` 函数本体与现有 `/api/smart-money/radar` 路由语义不变（`radar_resonance_for` 是新增薄封装，不改 radar 排序/返回）；
- nextday 五因子权重与 IC 校准注释不动；
- quality 口径1/2/4/5 不动；
- 不新增采集源、不改 smart_money 采集流程；
- 不做 nextday radar 因子的 IC 回测验证（本次是设计接入，IC 验证属后续研究台工作，用 `backtest/research.py` 独立做）。

# quality 两阶段盘口精排设计

> 日期：2026-08-13
> 范围：`backtest/quality.py` 编排层新增「盘口精排」阶段，复用 `data/pytdx_client.get_quote`
> 约束：不新增表、不新增采集源、不进 refresh；守合规硬约束（不荐股/不输出买卖信号）

## 1. 背景与动机

quality 当前是**只读库的全市场横截排序层**：四口径分位（风险调整/价值质量/资金流向/多信号）→ 共振 → 组合层（行业分散+相关性+最小方差权重）。四个口径全部只读 `stock_spot`/`etf_spot`/`*_daily`/`smart_money_action`，**不触网**。

通达信（pytdx）能读到 quality 未用的两个新维度：
- 实时五档盘口微结构（`get_quote`：bid/ask 五档价量、b_vol/s_vol 内外盘）
- 公司信息文本块（`get_company_info`：16 类）

本 spec 仅处理**实时盘口微结构**纳入优质筛选。公司信息文本块需正则/NLP 抽取、成本高且"主力追踪"类文本量化易越界成买卖信号，**不在本 spec 范围**，留后续迭代。

### 核心矛盾（已确认的两阶段方案）

quality 是给 ~5000 只全市场候选横截排序，pytdx 是按需单股 TCP 直连（单批≤80、带锁串行）。全市场逐个 `get_quote` ≈ 5000 次 TCP、几分钟、破坏 quality 不触网原则——与 CLAUDE.md「pytdx 不进 refresh、按需调避免 5200 次调用」既定决策正面冲突。

**解法**：两阶段。大池子用现有 DB 四口径共振粗筛 → 缩到 top50 小名单 → 对小名单逐个 `get_quote` 取实时盘口做精排。50 只一批一次 TCP，毫秒级，不破架构，符合操盘手「粗筛→盯盘」工作流。

## 2. 决策记录（澄清已锁定）

| # | 决策点 | 选定 |
|---|---|---|
| 1 | 盘口纳入方式 | 两阶段精排（粗筛 top50 → 小名单 get_quote 精排） |
| 2 | 盘口因子集 | A 流动性深度 + B 挂单不对称 + C 内外盘比（排除 D 挂单集中度，合规高风险） |
| 3 | 运行时机 | 自动判盘中/盘后（盘中跑 ABC，盘后仅 C、A/B 标失效） |
| 4 | 结果呈现 | 重排+组合层（精排重排 top50 → 组合层在精排后顺序上贪心取 limit） |
| 5 | B/C 方向处理 | A 进综合分；B/C 仅作 raw 字段展示，不进排序（守合规：方向=择时信号，纳入"优质排序"即推荐看多股，踩红线） |

## 3. 数据流（精排阶段插入点）

```
quality_rank(universe, days, ..., refine=True):
  db.query_rows(spot) → _tradable 预筛 → codes
  close = bt_eval.load_panel(...)          # 预加载，口径1/相关性/最小方差共用
  board_map = 预加载 industry_board        # _board_of 共用
  scores, dims_avail, dim_status = _dim_scores(...)
  enriched: 每行 {code, resonance, hits, dim_scores}
  # 过 hits 门槛（现状不变）
  main = [it for it in enriched if it["hits"] >= eff_min_dims]
  main.sort(by resonance desc)
  pool = main[:refine_pool]             # ← 粗筛 top50（已过 hits 门槛）

  ┌─ 新增精排阶段（仅 universe=stock 且 refine=True）──────────┐
  │ in_session = _is_in_session()                             │
  │ pool = _refine_by_quote(pool, df, in_session)             │
  │   → get_quote([code for code in pool])  # 一次 TCP ≤80/批 │
  │   → 算 liquidity_depth(A) / bid_ask_ratio(B) /            │
  │     inner_outer_ratio(C)                                  │
  │   → 盘中: liquidity_pct = 横截 pct(ln liquidity_depth)   │
  │     refine_score = 0.6×resonance_norm + 0.4×liquidity_pct │
  │     pool.sort(by refine_score desc)                      │
  │   → 盘后: A/B=None + note; 不重排（维持 resonance 序）    │
  │   → get_quote 失败: refine_status=err，pool 不变          │
  └───────────────────────────────────────────────────────────┘

  main = _apply_combo(pool, ..., combo_method, close, board_map)
         # 行业≤max_per_board + 相关性≤max_corr + 最小方差权重
         # 在精排后顺序上贪心取 limit
  main 每行附 quote 字段
```

**插入点依据**：精排放在共振粗筛之后、组合层之前。这样盘口流动性影响「选谁」（组合层贪心拿的候选受精排顺序影响），而非仅影响「排谁」。组合层约束（行业分散/相关性）仍生效，不会因精排而集中同行业。

## 4. 因子定义（操盘手口径）

| 字段 | 算法 | 进排序 | 盘后语义 |
|---|---|---|---|
| `liquidity_depth` (A) | `ln(Σ(bid_vol1..5 + ask_vol1..5))` | ✅ 综合 | 失效（收盘挂单不代表真实供求） |
| `liquidity_pct` | 横截 `_to_pct(ln liquidity_depth)` over pool | ✅ 综合 | 失效 |
| `bid_ask_ratio` (B) | `Σbid_vol / (Σbid_vol + Σask_vol)` ∈[0,1]，0.5 中性 | ❌ raw 展示 | 失效 |
| `inner_outer_ratio` (C) | `b_vol / s_vol` | ❌ raw 展示 | 有效（全天累计主动买卖方向，事实陈述） |

### 综合分
```
resonance_pct = _to_pct(resonance)             # pool 内横截分位 [0,1]
liquidity_pct = _to_pct(ln Σ(bid+ask vol))     # pool 内横截分位 [0,1]
refine_score  = 0.6 × resonance_pct + 0.4 × liquidity_pct
```
- 两项均为 pool 内横截分位 [0,1]，加权后 `refine_score∈[0,1]`，pool 内降序重排。
- 共振为主（0.6）、流动性为辅（0.4）：粗筛质量权重 > 盘口流动性权重。
- **权重 0.6/0.4 为经验先验，未经回归校准**（与 outlook WEIGHTS 措辞一致）。可调，见 §9。

### 合规说明（为何 B/C 不进综合分）
B/C 的价值在**方向**——B 买盘厚=托盘偏多、C 主动买>主动卖=偏多。把方向纳入"优质综合分"重排 =「主动买盘多的股排前面」= 实质推荐看多股，直接踩合规硬约束「不荐股/不输出买卖信号」。A 流动性深度是**非方向的质量维度**（可交易性），进排序安全。B/C 仍作为 raw 字段返回，操盘手可自行观察，但排序行为不输出方向性结论。

## 5. 盘中/盘后判定

```python
def _is_in_session() -> bool:
    """best-effort 判 A 股交易时段：周一至周五 9:30-11:30 / 13:00-15:00。
    节假日无历：误判盘中时 get_quote 仍返回收盘盘口，上层降级为盘后语义，不崩。"""
```
- **盘中**：跑 A+B+C 全量实时盘口，精排重排 pool。
- **盘后**：A/B 置 None + `note="收盘挂单，A/B 失效"`；仅保留 C 全天内外盘作展示；精排**跳过重排**，pool 维持原 resonance 降序，组合层照常。

节假日误判的处理：无节假日历，周一至周五交易时段内即判 `True`。节假日盘中调用时 `get_quote` 返回的是上一交易日收盘盘口（静止），此时 C 的 b_vol/s_vol 仍是上一日全天累计（事实陈述仍有意义），A/B 为收盘挂单（已标失效）。不崩，语义诚实降级。

## 6. 性能与缓存

- **性能**：top50 一批 `get_quote`，单次 TCP ≤80/批，毫秒级。不破「不触网」原则（小名单按需，非全市场 5200 次）。
- **缓存**：
  - 盘后：走原 `_RESULT_CACHE` 5min TTL（盘口静态，与盘后语义一致）。
  - 盘中：精排结果缓存缩到 **30s**（盘口实时变，5min 过期数据误导）。
  - 缓存 key 不变（已含 universe/days/... 参数），仅 TTL 按时段分档。

## 7. 接口

`/api/quality` 路由签名不变，`quality_rank` 新增参数：
```
refine: bool = True      # 是否启用盘口精排（默认开）
refine_pool: int = 50    # 粗筛后取 top N 进精排
```

返回 `main` 每行新增：
```json
{
  "quote": {
    "liquidity_depth": 12.3,
    "bid_ask_ratio": 0.55,
    "inner_outer_ratio": 1.2,
    "liquidity_pct": 0.72,
    "in_session": true
  }
}
```
返回顶层新增：
```json
{
  "refine_status": "ok(盘中)" | "ok(盘后,仅C展示)" | "skip(ETF不精排)"
                  | "skip(refine=False)" | "err:通达信不可用,跳过精排"
}
```

## 8. ETF 处理

`universe=etf`：默认跳过精排（做市商机制下内外盘/挂单不对称语义弱；ETF 口径2 是跟踪误差，盘口不适用）。`refine_status="skip(ETF不精排)"`，main 维持原四口径共振+组合层结果。

## 9. 可调参数（默认值，未回归校准）

| 参数 | 默认 | 说明 |
|---|---|---|
| `refine_pool` | 50 | 粗筛后取 top N 进精排。过小丢候选，过大 TCP 慢。 |
| 综合分权重 | 0.6 / 0.4 | 共振 / 流动性。共振为主。经验先验。 |
| 盘中缓存 TTL | 30s | 盘口实时性。 |

## 10. 合规措辞（守硬约束）

- `cand_disclaimer` 追加：「盘口微结构为实时供求机械观察，非买卖信号；A/B 收盘后失效」。
- 字段命名中性：`bid_ask_ratio`（非"看多度"）、`liquidity_pct`（非"优质度"）、`inner_outer_ratio`（非"主力方向"）。
- B/C 不进排序 = 排序行为不输出方向性结论。
- 综合分字段 `refine_score` 不对外暴露原始值（仅用于内部排序），对外只暴露 `liquidity_pct`（中性可交易性分位）。

## 11. 落点

- 代码：`backtest/quality.py` 内新增 `_refine_by_quote(pool, df, in_session)` + `_is_in_session()`（不新增模块，编排层内闭环）。
- 依赖：调 `data/pytdx_client.get_quote`（已有，不改）。
- 不新增表、不新增采集源、不进 refresh。
- 同步 `CLAUDE.md` 架构段 quality.py 描述 + 改动检查清单（quality 编排层条目补「盘口精排」说明）。

## 12. 测试（新增 `tests/test_quality_refine.py`）

mock `pytdx_client.get_quote`（不真连网），合成 spot/daily/smart_money 数据走完整 `quality_rank`：

1. **盘中场景**：`_is_in_session`=True，A/B/C 齐出，pool 按综合分重排正确（流动性深度高的排前）。
2. **盘后场景**：`_is_in_session`=False，A/B=None、仅 C 有值、`note` 标失效，pool 维持原 resonance 序不动。
3. **get_quote 失败**：`get_quote` 返空，`refine_status="err:通达信不可用,跳过精排"`，pool 不变，不崩。
4. **ETF**：`universe=etf`，`refine_status="skip(ETF不精排)"`，main 走原四口径+组合层。
5. **`refine=False`**：`refine_status="skip(refine=False)"`，完全跳过精排，等同现状。
6. **`_is_in_session` 时间判定**：mock 不同时间（盘前/盘中/午休/盘后/周末），返回正确 bool。
7. **NaN→None**：盘口字段 NaN 经 `_to_float`→None，防 `allow_nan=False` 500。
8. **组合层交互**：精排重排后 `_apply_combo` 在精排后顺序上贪心，行业≤max_per_board 约束仍生效。

## 13. 不在本 spec 范围

- 公司信息文本块（`get_company_info` 16 类）的解析与纳入 quality（成本高、合规风险，后续 spec）。
- 盘口因子的回归校准权重（需历史回测，留 backtest-robust 后续）。
- 节假日历接入（best-effort 降级即可）。

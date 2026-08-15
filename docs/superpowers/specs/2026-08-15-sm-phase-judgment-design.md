# 主力动向判断力深化 · 设计 spec

> 日期：2026-08-15
> 范围：`screener/smart_money.py` 查询层扩展 + 1 个一次性回填脚本 + 路由 + 前端
> 路径：方案 A（查询层按需算 + 零新表 + 席位胜率历史回填）
> 定位：本项目个人自用，措辞用直接操盘语言（吸筹/洗盘/拉升/出货），现有 `_wrap` disclaimer 管道保留不动。

## 背景与动机

现有主力动向模块只有"原材料"：`behavior_series`（streak/cum/accel）、`chip_distribution`（avg_cost/profit_ratio/concentration）、`top_by_amount`（net_intensity）。缺少把它们组合成操盘手能直接用的**阶段判定**，也缺**筹码迁移趋势**、**板块-个股资金联动**、**席位胜率**。本 spec 把这 4 项补齐。

## 合规与措辞（本项目自用）

- 阶段标签直接用"吸筹/洗盘/拉升/出货/观望"，不加"疑似"前缀。
- `confidence` 作为实际信号强度（命中条件数/总条件数），非"机械归类占比"。
- `triggers` 列出命中的具体条件与数值，供操盘手核验。
- 现有 `_wrap()` 的 disclaimer 管道保留不动（已接好、无害）；新增路由照常经 `_wrap`/`cand_disclaimer`，不刻意改写措辞。
- 技术底线照旧：NaN→None（防 `allow_nan=False` 500）、不崩、降级、单测 mock 不触网。

## 子项 1：主力阶段判定 `main_force_phase(code, days=30)`

### 输入（全复用现有函数，零新数据源）

| 来源 | 字段 |
|---|---|
| `behavior_series(code, days)` | `streak_inflow`, `streak_outflow`, `cum_inflow`, `margin_accel` |
| `chip_distribution(code, window=60)` | `avg_cost`, `profit_ratio`, `chip_concentration`, `spot` |
| `stock_spot`（`db.query_rows`） | `change_pct`, `turnover_rate`, `turnover_amount`, `latest_price` |
| `stock_daily`（经 `chip_distribution` 内 `_uni_panels`，间接） | 近 60 日收盘高位分位 |

### 阶段规则（计分制，非严格 AND）

对每个阶段，计算其条件中**命中数** `hits` 与 `confidence = hits / 该阶段总条件数`。最终阶段 = `confidence` 最高的阶段；并列时按风险优先级 `出货 > 拉升 > 洗盘 > 吸筹` 取胜（保守优先）。若最高 `confidence < 0.5` → `观望`。

各阶段条件：

1. **出货**（4 条）：`streak_outflow ≥ 2` / `profit_ratio > 0.8`（高位获利盘多）/ `margin_accel < 0` / `latest_price` 近 60 日高位分位 > 0.8
2. **拉升**（4 条）：`streak_inflow ≥ 2` / `change_pct > 3` / `turnover_amount > 近5日均 × 1.5`（放量）/ `latest_price > avg_cost`（站上筹码密集成本）
3. **吸筹**（4 条）：`streak_inflow ≥ 3` / `cum_inflow > 0` / `change_pct < 3`（持续流入但股价压住）/ `profit_ratio < 0.85`（非全获利高位）
4. **洗盘**（3 条）：`cum_inflow > 0`（前期累计正）/ `margin_accel < 0`（流入放缓/转负）/ `-5 < change_pct < -1`（小阴回落）

`confidence = 命中条件数 / 该阶段总条件数`。例：出货命中 3/4=0.75。`triggers` 列出命中的条件与实际数值。

> 计分制而非严格 AND 的理由：操盘手要的是"部分命中也有参考价值"（如出货命中 2/4 已值得警惕），且 confidence 作为实际信号强度更诚实。风险优先级 tiebreak 保证同等强度下偏保守（优先提示出货/洗盘）。

### 输出

```python
{
  "code": "000001", "name": "...", "phase": "吸筹",
  "confidence": 0.75,
  "triggers": ["streak_inflow=4≥3", "cum_inflow=+1.2亿>0", "change_pct=1.2<3", "profit_ratio=0.6<0.85"],
  "indicators": {"streak_inflow":4, "streak_outflow":0, "cum_inflow":1.2e8,
                 "margin_accel":-1.3e6, "profit_ratio":0.6, "chip_concentration":0.18,
                 "avg_cost":10.2, "change_pct":1.2, "turnover_amount":3.4e8, "latest_price":10.5,
                 "high60_pct":0.4},
  "ts": "2026-08-15T18:00:00"
}
```

### 性能与缓存

- 阶段判定依赖 `chip_distribution`（扫 60 日 stock_daily，单股重算），加进程缓存 30s（盘中盘口变化不影响日级 behavior/chip，30s 够；避免高频重复算）。
- 缓存键 `(code, days)`，TTL 30s，复用 `_RESULT_CACHE` 风格（模块级 dict + time戳）。

### 降级

- `behavior_series` 无记录且 finshare 不可用 → streak/cum/accel 为 None → 阶段规则中含这些条件的分支跳过，confidence 基于可用条件重算分母。
- `chip_distribution` 无历史（need_history=True）→ profit_ratio/concentration 为 None → 同上跳过。
- 全空 → `phase="观望"`, `confidence=0`, `note="数据不足"`。

## 子项 2：筹码迁移 —— 扩展 `chip_distribution` 返回加 `trend`

### 逻辑

用 `stock_daily` 滚动算近 20 日**每日**的 `avg_cost/profit_ratio/chip_concentration`（每日用截至该日的 60 日窗口），返回趋势：

```python
"trend": {
  "profit_ratio_5d": [0.55, 0.58, 0.60, 0.62, 0.65],   # 近5日获利盘比例
  "profit_ratio_20d": [...],                             # 近20日
  "profit_ratio_delta": 0.05,                            # 近5日均 - 近20日均（>0 套牢盘消化）
  "chip_concentration_delta": -0.02,                    # <0 筹码聚拢
  "avg_cost_delta": 0.03                                # 成本上移
}
```

- `profit_ratio_delta > 0`：获利盘增多/套牢盘消化（偏多）。
- `chip_concentration_delta < 0`：筹码聚拢（主力收集）。

### 性能

单股 20×60 日 close/amount 重算，可接受（<500ms）。加进程缓存（与子项1共用 30s 缓存，chip 已算则 trend 复用）。

### 向后兼容

`trend` 为新字段，旧调用方（个股分析卡、quality 不读 trend）不受影响。

## 子项 3：板块-个股资金联动 `board_money_link(code)`

### 逻辑

1. `board = stock_spot.board`（该股所属行业）；缺失则复用 `quality._board_of` 逻辑反查 `industry_board` 成分。
2. `board_rank`：`sector_fund_flow` where `sector_type='行业' AND indicator='今日'`，按 `main_net_inflow` 降序排名，取该 board 的排名与百分位。
3. `intra_board`：板块内成分股（`stock_spot` 同 board 全股）按 `net_intensity`（复用 `_attach_intensity` = 主力净额/成交额）横截排名，该股在板块内位置。
4. `board_5d_trend`：该板块近 5 日 `main_net_inflow` 序列（sector_fund_flow 5日指标或历史）。

### 输出

```python
{
  "code":"000001","board":"银行",
  "board_main_net_inflow": 3.2e8, "board_rank":3, "board_pct":0.9,
  "intra_board_rank":2, "intra_board_pct":0.8, "board_5d_trend":[...]
}
```

### 降级

- `sector_fund_flow` 空（未 refresh）→ board_rank=None, note。
- 板块无成分股 → intra_board=None。

## 子项 4：席位胜率 `seat_winrate(actor, k=5, days=180)` + 一次性回填

### 回填脚本 `scripts/backfill_lhb_history.py`

- finshare `get_lhb(start_date, end_date)` 拉过去 180 日龙虎榜（按月分页避免单次过大）。
- 落 `smart_money_action`（channel='龙虎榜', source=finshare，复用 `sm_data._rec`/`db.upsert_rows`），幂等（UNIQUE upsert 覆盖）。
- 可重复跑（只补缺失日期区间），失败标 `source=finshare` 不崩。
- 不进 refresh（一次性手动跑；以后每日 refresh_today 自然累积）。

### `seat_winrate` 逻辑

1. 查 `smart_money_action` where `channel='龙虎榜' AND actor LIKE %actor% AND date >= 180d 前` → 每条 (code, date)。
2. 对每条，用 `stock_daily` 前视 k 日收益（`close[t+k]/close[t]-1`，前视不足跳过）。
3. 多 k（5/10/20）：中位数收益 + 胜率（正收益占比） + 样本数。
4. `actor` 支持保留词"国家队"展开（复用 `_expand_national_team`）。

### 输出

```python
{
  "actor":"某某营业部","samples":12,
  "by_k":{"5":{"median_ret":0.03,"win_rate":0.6},
          "10":{"median_ret":0.05,"win_rate":0.7},
          "20":{"median_ret":0.08,"win_rate":0.75}},
  "recent":[{date,code,name,ret_k5},...]  # 近5次上榜明细
}
```

### 降级

- 无龙虎榜历史（未回填）→ samples=0, note="先跑 scripts/backfill_lhb_history.py"。
- stock_daily 前视不足（新股/未 fetch）→ 该条跳过，samples 减少。

## 路由（`api/server.py`）

| 路由 | 函数 | 说明 |
|---|---|---|
| `GET /api/smart-money/phase?code=` | `main_force_phase` | 主力阶段判定 |
| `GET /api/smart-money/seat-winrate?actor=&k=&days=` | `seat_winrate` | 席位胜率 |
| `GET /api/smart-money/board-link?code=` | `board_money_link` | 板块-个股资金联动 |
| `GET /api/chip` | (扩展) | 响应加 `trend` 字段 |

均经 `_wrap`（disclaimer 管道保留）。phase/board-link/seat-winrate 挂 `cand_disclaimer`。

## 前端（`web/index.html`）

- 个股分析卡：新增"主力阶段"色块（吸筹=红/洗盘=黄/拉升=橙/出货=绿/观望=灰）+ confidence + triggers 列表。
- 主力动向 tab：个股行可展开看 board-link（板块排名+板块内位置）。
- 个股分析卡筹码分布区：加迁移趋势小标（`profit_ratio_delta` ↑/↓）。

## 测试（TDD，mock db 不触网）

| 文件 | 覆盖 |
|---|---|
| `tests/test_main_force_phase.py` | 5 阶段边界 + confidence 计算 + 数据不足降级观望 |
| `tests/test_chip_trend.py` | 近5/20日序列 + delta 符号 + 无历史降级 |
| `tests/test_board_money_link.py` | board_rank/intra_board + sector_fund_flow 空降级 |
| `tests/test_seat_winrate.py` | 多 k 中位数/胜率 + 前视不足跳过 + 国家队展开 |
| `tests/test_backfill_lhb.py` | finshare mock 幂等 upsert + 失败不崩 |

mock 风格沿用现有测试（`db.query_rows` mock，`_uni_panels` mock 返回合成 close/amount）。

## 文件改动清单

- `screener/smart_money.py`：+`main_force_phase` / +`board_money_link` / +`seat_winrate` / `chip_distribution` 加 `trend` / 复用 `_attach_intensity`/`_streak`
- 新 `scripts/backfill_lhb_history.py`：一次性龙虎榜历史回填
- `api/server.py`：+3 路由，`/api/chip` 响应透传 trend
- `web/index.html`：phase 色块 + board-link 展开 + 筹码迁移标
- `tests/`：+5 测试文件
- `CLAUDE.md`：更新"主力动向"段（加 4 项能力）+ 修正口径5已实现（Phase4 待办改为仅 IC 报告）
- `README.md`：路由表+功能段补 phase/seat-winrate/board-link

## 不做（YAGNI）

- 阶段判定**不落库**（方案 B 留待以后要回测/接 quality 口径3 时升级）。
- 不加分时级别主力资金（分时采集成本高，日级够自用）。
- 不做席位胜率的实时增量（回填后靠每日 refresh_today 累积即可）。
- 北向实时个股净买额（端点下线，已知限制，不补）。

## 交付顺序（建议）

1. 主力阶段判定（最高价值，零新源）+ 测试
2. 筹码迁移（chip 扩展，与 1 同函数族）+ 测试
3. 板块联动 + 测试
4. 席位胜率回填脚本 + seat_winrate + 测试
5. 路由 + 前端联动
6. CLAUDE.md/README 同步

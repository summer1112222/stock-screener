# 主力动向 × 优质筛选 实战化优化设计（雷达 / 游资追踪 / 北向备源 / 行为联动 / 清单验证）

日期：2026-09-06
状态：待用户审阅

## 目标

把"主力动向"和"优质筛选"从分散查询升级为贴近实盘决策的作战视图：

1. **A 主力雷达**：多通道资金共振计数，一眼看出哪些股被多路主力同时买入；
2. **B 游资追踪**：当日龙虎榜个股 × 买入席位历史胜率联动，看高胜率游资今天进了哪些票；
3. **C 北向备源**：探源修复个股级北向数据，救活 quality 口径 3 的北向累计因子；
4. **D 行为联动**：quality 主清单附主力行为列（连续净流入/累计净额/强度）与主力阶段（吸筹/洗盘/拉升/出货），"好公司"叠加"主力在收集"才是好买点；
5. **E 清单验证**：quality 与 nextday 清单每日落库 + 回填前视收益，用模拟盘数据检验筛选体系是否真的有效。

合规边界不变：所有新视图均为**机械统计/观察清单**，挂 `cand_disclaimer`，不构成买卖信号。

## 包 A：主力雷达（多通道共振）

### 后端

`screener/smart_money.py` 新增 `radar(days=5, market=None, limit=50) -> dict`：

- 查询窗口复用 `today_list` 的**封顶 today** 逻辑（`date <= today` 取表内最新实盘日往前 `days` 日窗口），防止限售解禁通道的未来日期劫持窗口；
- 按 code 聚合 `smart_money_action` 各通道：
  - **正向共振通道**（计 `channel_hits`，0-4）：资金流 / 龙虎榜 / 北向 / 高管增减持。金额通道以窗口内净额 > 0 为正向；高管增减持为股数通道（`_SHARES_CHANNELS`），以变动方向字段为增持时计正向，**金额不与元量纲混算**；
  - **负向事件**：限售解禁只打 `unlock_flag=True` 并附最近解禁日期/规模，不计 hits；
  - 十大股东为季度快照（季度 diff 属 P2 待办），本期不计入共振；
- 每 code 附 `channels` 明细：`{channel: {net, latest_date, positive}}`；
- `net_intensity` 复用 `_attach_intensity`（主力净额/当日成交额，只读 `stock_spot`）；
- 排序主键 `(channel_hits DESC, net_intensity DESC)`；
- 只读 `db.query_rows`，不触网、不新增表；30s 进程缓存（键含全部参数）；NaN→None 复用 `_nan`。

### 路由与前端

- `GET /api/smart-money/radar?days=&market=&limit=`，`_wrap()` + `cand_disclaimer`（"多通道资金共振机械统计，非买卖信号，盈亏自负"）；
- 前端主力动向 tab 新增"主力雷达"子视图：表格列 code/name/hits/各通道明细/强度/解禁标记；行点击调 `switchTab('analysis')` 跳个股分析（复用现有 handler 模式）。

### 不改动

`today_list` / `top_by_amount` / `summarize_by_code` 现有语义与排序保持不变。

## 包 B：游资追踪日视图

### 后端

`screener/smart_money.py` 新增 `seat_radar(date=None, top=30) -> dict`：

- `date` 缺省取表内最新龙虎榜日期（同样封顶 today）；
- 取当日龙虎榜个股 → 逐股买入席位明细，复用 `/api/smart-money/seats-stocks` 现有取数路径（finshare/东财按需）；
- **席位历史胜率批量算**，不逐席调 `seat_winrate`（避免 N 次 DB 查询 + N 次 panel 加载）：
  1. 一次查近 180 日全部 `channel=龙虎榜` 行，内存按 actor 分组得每席 `(code, date)` 上榜对；
  2. `backtest.signals._uni_panels` 一次加载全部涉及 code 的 close 面板；
  3. 每席算 `listings`（上榜次数）/ `median_ret_k5` / `win_rate_k5`（复用 `_fwd_ret` 口径，k=5）；
- `hot_seats` 判定：`win_rate_k5 >= 0.55 且 listings >= 10`（机械阈值，参数可调）；
- 输出每只上榜股：买入席位列表（附各席胜率）+ `hot_count`（命中高胜率席位数）+ 买入额合计；排序 `(hot_count DESC, 买入额 DESC)`，截 `top`；
- 依赖 `scripts/backfill_lhb_history.py` 回填的历史；无历史时席位置信字段为 None 并附 note 诚实降级，不崩；
- 5min 进程缓存（盘后数据日内不变；键含 date/top）。

### 路由与前端

- `GET /api/smart-money/seat-radar?date=&top=`，`cand_disclaimer`（"席位历史胜率 × 当日上榜机械联动，历史统计非预测，盈亏自负"）；
- 前端主力动向 tab 新增"游资追踪"子视图。

## 包 C：北向备源（spike 先行）

现状：`collect_northbound` 已有"主源探活(已下线)→十大成交股(盘后)→总额"三级降级，但**个股级**覆盖仅十大成交股，quality 口径 3 的北向累计因子对大多数股为空。

- **第一步为探源 spike**（产出可行性结论，不产出保留代码）：
  1. HKEX CCASS 持股披露（北向个股持股的官方日度源）；
  2. a-stock-data 库北向接口（CLAUDE.md 记录的官方交易所备胎方向）;
  3. 沪/深交易所官网日度持股汇总；
  - 验证维度：当前出口 IP 可达性、个股级字段覆盖（持股量/持股市值/占比）、数据滞后（T+1 可接受）。
- 探到可用源 → `data/smart_money.py::collect_northbound` 在十大成交股之前插入新备援（`(df, ok, err)` 约定 + source 标注），并回填近 30 日（幂等）；quality 口径 3 经 `_behavior_batch` 读 `smart_money_action`，**数据入库即自动复活，不改 quality 代码**；
- 探不到 → 维持现有降级灰显，spike 结论文档化后关闭本包。

排期最后，不阻塞 A/B/D/E。

## 包 D：quality 主清单附主力行为列 + 主力阶段

### 后端

`backtest/quality.py::quality_rank` 在 `_apply_combo` 之后、`_clean_item` 之前追加富化段（仅 `universe="stock"` 且 `main` 非空时执行）：

- `_behavior_batch([it["code"] for it in main], days)`（现成批量函数，一次 DB 查询零触网）附每 item：
  - `streak_inflow` / `streak_outflow`（连续净流入/流出天数）；
  - `cum_net`（窗口累计主力净额）；
- `net_intensity` 用已加载的 `df`（spot 快照 `turnover_amount`）就地计算，不重查库；
- `main_force_phase(code)` 逐只批量算 main 全部标的（limit ≤ 20，函数自带 30s 进程缓存），附 `mf_phase` / `mf_confidence`；无 `stock_daily` 历史或计算异常 → 两字段 None，不崩不警告刷屏；
- **出货预警**：`mf_phase == "出货" 且 mf_confidence >= 0.6` → `warnings` 追加一条机械提示（如 "主力阶段=出货(置信 0.7)"），**只标注，不扣分不剔除**（方向判断留给使用者）；
- ETF（`universe != "stock"`）跳过整段，字段不出现或为 None；
- import 方向不变（quality 已依赖 `screener.smart_money`）；结果缓存键不变（新列随结果整体缓存；盘中 30s TTL 下阶段数据最多滞后 30s，可接受）。

### 前端

qsRender 主清单表加三列：连续净流入（streak）/ 累计净额 / 主力阶段（含置信度与出货预警高亮）。

## 包 E：清单历史验证（quality + nextday 通用追踪）

### 数据表

新表 `list_track`（进 `models.SCHEMA_SQL` + `TABLE_FIELDS`；新表 `CREATE TABLE IF NOT EXISTS` 启动即建，**无需进 `_BOARD_MIGRATIONS`**）：

```
list_track(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module TEXT NOT NULL,        -- quality / nextday
  mode TEXT NOT NULL,          -- quality: selection_mode 值(strict/loose/degraded); nextday: strict/score
  date TEXT NOT NULL,          -- 清单生成日（实盘日，封顶 today）
  code TEXT NOT NULL,
  name TEXT,
  rank INTEGER,                -- 清单内排名（1 起）
  score REAL,                  -- quality: adjusted_resonance; nextday: 五因子 score
  meta_json TEXT,              -- hits/dim_scores/confidence 或 factor_scores/step_status 摘要
  ret_k1 REAL, ret_k3 REAL, ret_k5 REAL,   -- 前视收益（回填前为 NULL）
  filled_ts TEXT,              -- 回填时间（幂等标记）
  ts TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(module, mode, date, code)
)
```

### 落库

- 新模块 `backtest/tracker.py`：`record_list(module, mode, date, items)`，`INSERT OR IGNORE` 幂等（同日同参数重复请求不重复写）；
- 挂钩点：`/api/quality` 与 `/api/nextday-strong` 路由在**请求参数等于前端默认值**时调用（避免任意参数组合污染追踪样本；判定函数 `tracker.is_default_params(...)` 显式列出默认键值）；
- nextday 一次响应记两个 mode：`mode=strict` 记 `passed_items`（五步全通过、按五因子 score 排序的清单），`mode=score` 记 `all_items` 按因子分排序截 top limit；quality 记 `main`，`mode` 取响应 `selection_mode` 值。

### 回填

- `scripts/fill_track_returns.py`（幂等可重跑）：对 `filled_ts IS NULL` 且 T+1+k 交易日已过的行回填收益；
- **收益口径**：清单为 T 日盘后选出 → **T+1 开盘价买入，T+1+k 收盘价卖出**（`ret_k = close(t+1+k)/open(t+1) - 1`）；无 T+1 开盘价（未 fetch 历史）时降级 T 日收盘价买入并在 `meta_json` 标注 `entry=t_close`；不含费用（研究口径，`execution.py` 的费率模拟属回测域不混入）；
- 数据源 `stock_daily`（不足时提示先 `/api/backtest/fetch`，不自动触网拉全量）；
- 路由 `GET /api/track/fill`（POST 亦可）触发同一回填逻辑，便于容器内 curl。

### 汇总与前端

- `GET /api/track/summary?module=&mode=`：追踪天数、总样本、已回填数、各 k 的平均/中位收益、胜率（ret>0 占比）——strict vs score、quality vs nextday 横向可比；附 `bt_disclaimer`（"历史清单机械追踪统计，非预测"）；
- 前端优质筛选 tab 与次日强势区各加一张"清单验证"小卡（追踪天数 / k1/k3/k5 胜率与中位收益）。

## 实施顺序与验收

**A+D（联动，同批）→ E → B → C（spike）**；每包独立 commit，部署走 `bash deploy.sh`（测试→重建→健康检查）。

测试全部合成数据 mock `db.query_rows`，不触网：

| 包 | 测试文件 | 覆盖要点 |
|---|---|---|
| A | `tests/test_sm_radar.py` | 共振计数（多通道/单通道/全负）、解禁负向标记不计 hits、高管股数量纲不混算、排序主键、日期封顶 today、空表降级 |
| B | `tests/test_seat_radar.py` | 席位批量胜率计算、hot_seats 阈值、无历史降级 note、缓存键 |
| D | `tests/test_quality_behavior_cols.py` | 行为列附加、phase 批量与 None 降级、出货预警进 warnings、ETF 跳过 |
| E | `tests/test_tracker.py` | record 幂等（UNIQUE 冲突）、默认参数判定、回填口径（T+1 open 买入/降级 t_close）、summary 聚合 |

C 为 spike，验收物是探源结论文档，不产测试。

## 风险与边界

- B 的席位明细按需取数为 N 股 × 1 调用，慢路径：`seat_radar` 只处理当日上榜股（通常 < 60 只）且 5min 缓存，可接受；
- D 给 quality 增加 main ≤ 20 只的 phase 计算（进程内 DB 读 + 本地计算），预计秒级，被 5min 结果缓存吸收；若实测拖慢冷启动超 5s，降级为仅 top 10 算 phase；
- E 的落库依赖"用户当天打开过页面"（项目无调度器，与 refresh 手动触发模式一致）；漏记的日子样本缺失属可接受的研究退化，不做补采；
- 所有新增响应字段遵守 NaN→None 序列化约定（`allow_nan=False`）。

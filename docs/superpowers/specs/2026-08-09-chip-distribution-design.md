# 筹码分布 + 主力行为时间序列 · 设计文档

**日期**: 2026-08-09
**状态**: 设计稿，待评审
**范围**: `screener/smart_money.py`（查询/计算层扩展）+ `api/server.py` 路由 + `web/index.html` 个股分析卡/主力动向页
**关联 spec**: `2026-07-14-smart-money-tracker-design.md`（底表 `smart_money_action` 与 6 通道采集，本 spec 在其之上做"时间序列"与"筹码"）

---

## 1. 背景与合规边界

现状缺口：主力动向 6 通道（龙虎榜/十大股东/北向/资金流/高管增减持/限售解禁）每日入库的是**当日切片**，查询层 `today_list`/`by_actor`/`top_by_amount` 也是切片与累加。判断主力真实意图需要的**时间序列视角**与**筹码结构**缺失：

- 个股**平均持仓成本 / 获利盘比例 / 筹码集中度**——判断上方套牢盘压力与下方支撑。
- **连续净流入天数**、主力**边际增减持**——区分"持续吸筹"与"一日脉冲"。
- **游资席位胜率历史**——某席位历史上榜后 t→t+k 收益，给"席位画像"而非裸的"它买了"。

**合规边界（最高优先级）**：本功能是"**机械统计事实 + 观察清单**"，非投资咨询：

- 筹码分布、连续流入、席位胜率均为公开数据机械计算，**不输出买卖点、不承诺收益、不荐股**。
- 措辞统一用"成本分布/获利盘比例/连续净流入/历史跟买收益统计"，**严禁**"推荐跟买/强势股/主力建仓完成/必涨"。
- 尤其游资席位胜率：胜率高易被误读为"跟买信号"——必须挂 `cand_disclaimer`："席位历史跟买收益统计事实，非推荐跟买，历史不代表未来，盈亏自负"。前端胜率列旁固定小字提示。
- 清单/统计类响应走 `_wrap()` 附 `cand_disclaimer`；个股分析卡内嵌的筹码/行为段挂 `bt_disclaimer`。

---

## 2. 架构与模块边界

**核心决策：本 spec 不新增 SQLite 表。** 筹码分布与主力行为序列全部基于已有表（`stock_daily` + `smart_money_action`）的只读计算 + 查询，与 `screener/smart_money.py` 查询层定位一致（不触网、纯 `db.query_rows`）。好处：零迁移风险、合规干净、单测只需 mock `db.query_rows`。

```
screener/smart_money.py        # 查询/计算层扩展（不触网）
  chip_distribution(code, window=60, spot_price=None)  # 移动成本分布
  behavior_series(code, days=30)                        # 连续净流入+边际+成本区
  seat_winrate(actor, k_days=5, period="近三月",
               stop_loss=None, fee_bps=0)               # 游资席位胜率（复用前视收益引擎）

api/server.py                  # 路由（见 §5）
web/index.html                 # 个股分析卡新增"筹码/主力行为"段 + 主力动向页席位胜率
```

**两层复用**：
- 筹码计算复用 `signals._uni_panels(universe="个股", codes=[code])` 取 `close`+`amount`（不用关心 `stock_daily.symbol` 的 sh/sz 前缀细节）。
- 席位胜率的前视收益 `close[t+k]/close[t]-1`（含 stop_loss 前视 low 截断、fee_bps 双边扣）复用 `signals.backtest_signals` 的逻辑——抽公共函数 `_forward_returns(close, k, stop_loss, fee_bps)`，原 `backtest_signals` 改调它，游资胜率也调它（见 §4.3）。

---

## 3. 数据源与算法

### 3.1 筹码分布 `chip_distribution`

**依赖**：`stock_daily`（需先 `/api/backtest/fetch` 拉过该 code，与 `signals` 同依赖；无历史→返回空 + `need_history=True`）+ `stock_spot` 最新价（实时获利盘判断；无 spot→用 `stock_daily` 末值）。

**算法（移动成交量加权成本分布，收盘价代理）**：取近 `window` 日（默认 60，前端可切 30/60/120）`close` 与 `amount`（成交额，元）。每日"买入成本"以收盘价代理、成交额为权重：

```python
avg_cost = Σ(close_t × amount_t) / Σ(amount_t)          # 加权平均成本
total = Σ(amount_t)
profit_ratio = Σ(amount_t where close_t < spot) / total  # 获利盘比例(现价下方筹码占比)
loss_ratio   = 1 - profit_ratio                          # 套牢盘比例
# 筹码集中度:加权价格标准差 / avg_cost(越小越集中)
var = Σ(amount_t × (close_t - avg_cost)²) / total
chip_concentration = sqrt(var) / avg_cost
# 90% 筹码价格区间(分位 5%↔95% 的 close 加权分位)
p05, p95 = weighted_percentile(close, amount, [0.05, 0.95])
chip_range_90 = (p95 - p05) / avg_cost
```

**返回**：
```python
{"code": str, "spot": float|None, "window": int,
 "avg_cost": float, "profit_ratio": float, "loss_ratio": float,
 "chip_concentration": float, "chip_range_90": float,
 "distribution": [{"price": float, "amount": float, "pct": float}, ...],  # 分箱直方图(供前端渲染)
 "need_history": False, "n_days": int}
```

**分箱**：把 `window` 日 close 按 20 等宽分箱聚合 amount，出直方图 `distribution`。NaN→None（`df.astype(object).where(pd.notna(df), None)`，防 `allow_nan=False` 500——同 `collector._to_records`）。

**缓存决策**：进程内 LRU（`functools.lru_cache` 按 `code+window+date` key），当日有效，**不入 DB**。理由：筹码随每日 close 变动，DB 持久缓存（如 `fundamentals_cache` 7 天 TTL）会失真；per-code 计算量 <50ms，进程内缓存足够扛并发。容器重启缓存丢失无影响（重算即可）。

### 3.2 主力行为序列 `behavior_series`

**依赖**：`smart_money_action`（已有表，channel=资金流/北向/龙虎榜）只读查询。

```python
rows = db.query_rows("smart_money_action",
        where="code = ? AND channel = ? AND date >= ?",
        params=(code, "资金流", date_n_days_ago))
# 按 date 排序，取 amount 列
streak_inflow = 连续末尾 amount>0 的天数    # 连续净流入
streak_outflow = 连续末尾 amount<0 的天数   # 连续净流出
cum_inflow = Σ amount                          # 区间累计净额
# 边际:近5日均额 vs 近20日均额，判断加速/减速
margin_accel = mean(amount[-5:]) - mean(amount[-20:])
```

**返回**：
```python
{"code": str, "days": int,
 "streak_inflow": int, "streak_outflow": int,
 "cum_inflow": float, "margin_accel": float,
 "daily": [{"date": str, "amount": float}, ...],   # 供 sparkline
 "channels": {"资金流": {...}, "北向": {...}, "龙虎榜": {...}}}  # 多通道并行
```

**主力成本区估算**：不单独拉陆股通持股比例（akshare 端点反爬、无备援，CLAUDE.md 已记录），改用"资金流净额加权近似"或直接引用 §3.1 的 `avg_cost` 作为"主力成本区代理"，`raw` 标 `est_source="chip_avg_cost"`，诚实标注是估算。**不把估算当精确值**，前端列名"成本区(估)"。

### 3.3 游资席位胜率 `seat_winrate`

**依赖**：`smart_money_action`（channel=龙虎榜，actor=席位名）取触发点 + `stock_daily` 算前视收益。

**算法**：
1. 取该 actor 近 `period`（近一月/近三月/近六月/近一年，复用 `collect_seats` 的 period 映射）所有龙虎榜触发点 `(date_t, code)`。
2. 对每个触发点，取 `stock_daily` 该 code 的 `close_t` 与 `close_{t+k}`（k=`k_days`，默认 5，前端可多 k 并行如 signals 研究台）。
3. 前视收益 `r = close[t+k]/close[t] - 1`；`stop_loss` 用前视 k 日 low 判触及截断；`fee_bps` 双边扣——**复用抽出的 `_forward_returns`**。
4. 聚合：`win_rate = mean(r>0)`、`mean_ret`、`median_ret`、`n_samples`、`excess_win_rate`（对比同期全部触发点基准，可后置）。

**返回**：
```python
{"actor": str, "period": str, "k_days": int,
 "win_rate": float, "mean_ret": float, "median_ret": float,
 "n_samples": int, "stop_loss": float|None, "fee_bps": float,
 "per_code": [{"code": str, "date": str, "ret": float}, ...],  # 明细(可截断 top 50)
 "note": "历史跟买收益统计事实，非推荐跟买"}
```

**安全/性能**：席位触发点通常 ≤ 数百，逐点前视计算量可接受；某 code 无 `stock_daily` 历史→该点 `ret=None` 跳过、`n_samples` 不计（不静默吞，`note` 标"部分触发点无历史被剔除 N 个"）。多 k 并行如 signals 研究台。

---

## 4. 复用点：前视收益引擎抽公共函数

`signals.backtest_signals` 现有前视逻辑（`backtest/signals.py:155-169`）内联在函数体内。本 spec 抽出：

```python
# backtest/signals.py
def _forward_returns(close: pd.DataFrame, k: int,
                    stop_loss: float|None = None,
                    fee_bps: float = 0,
                    low: pd.DataFrame|None = None) -> pd.DataFrame:
    """前视 k 日收益。close[t+k]/close[t]-1；stop_loss 用前视 low 截断；fee_bps 双边扣。
    原 backtest_signals 改为调用本函数；游资席位胜率(screener/smart_money.seat_winrate)也调用。"""
```

- `backtest_signals` 行为不变（单测 `test_signals_backtest` 验证回归）。
- 抽函数时保持 `stop_loss` 需 `with_ohlc=True` 取 low 的现有契约（`_uni_panels` 默认 2-tuple、`with_ohlc=True` 返 4-tuple）。
- 游资胜率是"指定 t 集合"而非"扫所有 t"：调 `_forward_returns` 拿全前视矩阵后，按触发点索引取值（逻辑等价、复用计算）。

---

## 5. API 路由（分期 P1/P2/P3）

所有路由走 `_wrap()`；清单/统计类附 `cand_disclaimer`；个股分析卡内嵌段挂 `bt_disclaimer`。

**P1 — 筹码分布**
```
GET /api/chip?code=&window=60
```
返回 §3.1 结构。无 `stock_daily` 历史→`{"need_history": True, ...}` + `cand_disclaimer`。措辞"移动成本分布机械统计，非支撑位/压力位预测"。

**P2 — 主力行为序列**
```
GET /api/smart-money/behavior?code=&days=30
```
返回 §3.2 结构。附 `cand_disclaimer`："主力行为序列机械统计，非买卖信号"。`channels` 多通道并行，单通道空不崩。

**P3 — 游资席位胜率**
```
GET /api/smart-money/seat-winrate?actor=&k_days=5&period=近三月&stop_loss=&fee_bps=
```
返回 §3.3 结构。`k_days` 支持多值（逗号分隔，前端多 k 并行渲染矩阵，如 signals 研究台）。附 `cand_disclaimer`："席位历史跟买收益统计事实，非推荐跟买，历史不代表未来"。

**个股分析卡集成**：`/api/stock-analysis?code=` 内 inline 聚合新增一段 `main_force_behavior`（调 `behavior_series` + `chip_distribution` 摘要），挂 `bt_disclaimer`。改 `stock_analysis()` 内 `WEIGHTS` 时如纳入"主力行为"维度需同步调权重总和（目前基本面0.35/资金面0.20/技术面0.20/机构0.15/估值0.10=1.00，资金面已含 smart_money 近 30 日净额，筹码/连续流入可作为资金面子项加权细化——P3 评估，不强制改权重）。

---

## 6. 前端 `web/index.html`

原生 JS，无构建步骤（沿用现有模式）。

- **个股分析卡**：新增"筹码 / 主力行为"段。筹码段渲染分箱直方图（inline SVG 或 div 条，绝对无外部库——CSP 限制）；主力行为段渲染近 30 日净额 sparkline（复用现有 sparkline 画法）+ 连续流入天数徽章。段头固定小字 disclaimer。
- **主力动向页**：席位统计表（`/api/smart-money/seats` 现有）每行追加"胜率"列，点开调 `/api/smart-money/seat-winrate`，多 k 矩阵如 signals 研究台。胜率列旁固定小字"历史统计非跟买信号"。
- `LABEL` 字典补中文标签：`avg_cost`→"平均成本"、`profit_ratio`→"获利盘比例"、`chip_concentration`→"筹码集中度"、`streak_inflow`→"连续净流入"。
- 中性措辞审计：列名不得出现"主力建仓/强势/推荐"。

---

## 7. 错误处理与降级

- `chip_distribution`：`stock_daily` 无该 code（未 fetch）→返回 `need_history=True` 不抛；`_uni_panels` 返 None→降级空直方图 + `err` 串。spot 缺失→用 `stock_daily` 末值 close，标 `spot_source="daily_close"`。
- `behavior_series`：某通道 `smart_money_action` 无记录→该通道字段为空，不崩；多通道独立 try/except。
- `seat_winrate`：actor 无龙虎榜触发点→`n_samples=0` + `note="无历史触发记录"`；某 code 无 `stock_daily`→该点剔除并 `note` 标剔除数（不静默吞，对齐 CLAUDE.md"不静默"原则）。
- **NaN→None**：所有出口 `df.astype(object).where(pd.notna(df), None)`，不用 `df.where(pd.notna(df), None)`——float64 列 None→NaN 又变回 NaN，`allow_nan=False` 500。
- `_forward_returns` 抽出后，`backtest_signals` 既有 `stop_loss`/`fee_bps` 行为不变，`test_signals_backtest` 必须全绿（回归保护）。

---

## 8. 测试 `tests/test_chip_behavior.py`

合成数据 mock `db.query_rows` + `signals._uni_panels`，不触网（沿用 `tests/` 模式）：

- `test_chip_avg_cost_weighted` — 合成 close/amount，校验加权平均成本公式。
- `test_chip_profit_ratio` — 现价上方/下方筹码占比正确，spot 缺失降级 daily_close。
- `test_chip_nan_to_none` — 合成含 NaN，出口无 NaN（防 500 回归）。
- `test_chip_need_history` — 无 stock_daily→`need_history=True` 不抛。
- `test_behavior_streak` — 合成近 N 日 amount 序列，连续净流入/流出天数正确。
- `test_behavior_multi_channel_partial` — 某通道空不崩，其他通道正常。
- `test_seat_winrate_aggregate` — 合成席位触发点 + daily，胜率/mean_ret 聚合正确。
- `test_seat_winrate_missing_history` — 部分 code 无 daily→剔除并 note 标数，n_samples 不计。
- `test_forward_returns_refactor` — 抽函数后 `backtest_signals` 输出与重构前一致（回归快照）。

---

## 9. 分期交付

- **P1**：`chip_distribution` + `_forward_returns` 抽公共函数（含 `backtest_signals` 回归）+ `/api/chip` + 个股分析卡筹码段 + 4 条单测。
- **P2**：`behavior_series` + `/api/smart-money/behavior` + 个股分析卡行为段 + 主力动向页 sparkline + 2 条单测。
- **P3**：`seat_winrate` + `/api/smart-money/seat-winrate` + 主力动向页席位胜率列（多 k 矩阵）+ 2 条单测。

建议 P1 先落（筹码是最大空白、零迁移、合规最干净），验证数据质量与前端直方图渲染后再推 P2/P3。

---

## 10. 改动检查清单（对齐 CLAUDE.md）

- **不新增 SQLite 表/列** → 无需动 `models.SCHEMA_SQL` / `TABLE_FIELDS` / `_BOARD_MIGRATIONS`（本 spec 核心优势）。
- 新增查询/计算层函数 → `screener/smart_money.py` 扩展，**只读 `db.query_rows`**（与现有查询层一致，不触网、便于单测 mock）。
- 抽 `_forward_returns` 公共函数 → 改 `backtest/signals.py`，**保持 `_uni_panels` 默认 `with_ohlc=False` 返 2-tuple**（旧测试 mock 兼容），`with_ohlc=True` 返 4-tuple；`test_signals_backtest` 回归必过。
- 新增 API 路由 → 用 `_wrap()`，筹码/行为/席位胜率类附 `cand_disclaimer`；个股分析卡内嵌段挂 `bt_disclaimer`。
- 新增前端 → 个股分析卡筹码段（直方图，无外部库，CSP 自洽）+ 主力动向页席位胜率列；`LABEL` 补中文标签；措辞审计中性（禁"主力建仓/推荐跟买"）。
- 单测放 `tests/test_chip_behavior.py`，合成数据 mock，不依赖网络。
- 合规：游资席位胜率尤其挂 `cand_disclaimer`"历史统计非推荐跟买"，前端胜率列旁固定小字。

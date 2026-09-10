# ETF/QDII 长期+短期筛选模块设计

- **日期**: 2026-09-11
- **状态**: 待评审
- **关联**: 现有 `conditions.ETF_FIELDS_CAT`（spot 量价）、`data.history._UNIVERSE["ETF"]`（etf_daily）、`backtest.eval.compute_factor`（新 `rel_strength_n`）、`screener.nextday._weighted_score`（横截面加权范式）

## 背景与目标

现有系统对 ETF/QDII 支持很基础：`ETF_FIELDS_CAT` 仅 4 个 spot 量价字段 + 2 派生，历史有 `etf_daily` 可跑 OHLCV 因子，但**缺失长期配置所需维度**（指数估值分位、规模、费率、跟踪质量）与 **QDII 专属维度**（溢价率、限购、净值）。本功能补齐这两块，做成**长期配置 + 短期交易两套独立筛选**。

目标（用户已确认）：
- 长短期**都要**，且长期须**估值 + 质量两维综合**（非纯低估优先）。
- **必须含 QDII 溢价率**（场内盘口价 vs 净值，核心风险因子）。
- QDII 溢价可通过**基金/数据网站备援源**获取（不承诺单源，Phase 0 实测择优）。
- 个人自用合规放松，措辞可用操盘语言，保留 disclaimer 管道。

合规硬约束：仅为数据筛选/观察清单，非投资咨询。机械排序观察清单，不荐股、不承诺收益。所有响应经 `_wrap()` 附 `cand_disclaimer`。

## 架构决策（方案 A：独立编排层）

- 新模块 `screener/etf_screen.py`，单一入口 `etf_screen_rank(universe, mode, codes, ...)`，返回两个独立清单 `long_term` 与 `short_term`。
- 新路由 `GET /api/etf-screen`（`universe` 透传 ETF/QDII，`mode=long|short` 切清单，默认 `long`；`limit`/`days` 透传）。附 `cand_disclaimer`。
- **不新增 SQLite 表**：复用 `etf_daily` + 现有 spot，估值分位/溢价在进程内算 + 30s 缓存（键含 universe/mode 与全参数，复刻 `nextday._CACHE`）。估值分位若需长期沉淀，二期可演进到采集落库（见"演进"）。
- **不污染现有 `/api/screen` 引擎**与 `scopy ETF_FIELDS_CAT`（仅当需复用字段名时扩展，默认不改）。

## 数据源与 Phase 0 验证门禁（实施第一步）

宿主无 akshare，须在容器内实测。**Phase 0 设门禁**：用一个小脚本遍历下表接口，在出口 IP 实测可用性，结果写入 `data/cache/etf_source_probe.json` 供决策，决定 QDII 溢价因子落地方式。任一源失败不阻塞，多源备援 + 诚实降级。

| 数据 | 首选源 | 口径 | 备援源 |
|---|---|---|---|
| ETF/QDII 实时（价/净值/规模/份额） | akshare `fund_etf_spot_em`（东财）| 需实测 | tdx `get_quote`（已有）+ THS |
| **溢价率** | 东财 spot 参考溢价 / 盘口价 vs 净值 | 需实测 | **集思录 jisilu**（ETF 折溢价最全）→ 天天基金（东财域，⚠️封禁风险）|
| **指数 PE/PB 历史分位** | akshare `stock_zh_index_value_csindex`（中证官网，非东财）| 大概率可用 | 雪球/蛋卷/韭圈儿估值分位 |
| ETF 历史日线 | tdx 主源（已有 `etf_daily`）| ✅ 稳定 | 新浪 |
| 费率/跟踪误差/限购 | 基金资料 | 需实测 | 天天基金/集思录 |

Phase 0 门禁结果回调设计：若溢价/估值在首选+全部备援均不可得，对应因子**诚实 `None`/`premium=null`，不硬造**（用户已接受不硬造）。

## 模块逻辑

### 长清单 `long_term`（估值 × 质量 综合）

**质量维度**（硬过滤门槛 + 评分）
- 规模(≤N 亿易清盘→低分/剔除)、流动性(成交额/换手)、费率(低优)、跟踪质量(跟踪误差紧优)
- 硬门槛：规模 ≥ `min_scale` + 成交额 ≥ `min_amount`（避迷你/僵尸）；通过者进入排序

**估值维度**
- 指数 PE/PB 历史分位（近 N 年，默认 5 年）+ 股息率
- 低分位=便宜，为长期观察主导向

**综合分**：质量门槛过滤后，按 估值分位 与 质量评分 加权/共振排序（复刻 `quality._resonance` 或 `nextday._weighted_score` 范式；维度权重经验先验：估值 0.55 / 质量 0.45）。输出含 `valuation_percentile`/`quality_score`/`premium`(QDII) 逐项透明披露。

### 短清单 `short_term`（量价，主要复用现有）

90% 复用 `etf_daily` + backtest 面板因子，横截面 rank-pct + 可用因子重归一加权（复刻 `nextday._weighted_score`）：
- 动量(n日) `momentum_n`、量价共振 `vol_corr_n`、相对强度 `rel_strength_20`、波动率 `volatility_n`（均为本次/已有实现，对 ETF 通用）
- spot：换手/成交额/量比
- 趋势动量看多方向，波动惩罚

### QDII 专属（贯穿两清单）
- **溢价率**：场内盘口 vs 净值。QDII 清单顶部强制展示，高溢价红色标记，长期清单作为风险过滤/降权（负/低溢价安全，高溢价警示）。
- **限购/暂停申购状态**：禁购→溢价难收敛→提高风险权重。仅展示 `raw` 不机械排序（合规方向=择时非质量，同 `quality` 盘口阶段理念）。

## 缓存与降级
- 30s 进程缓存（键含 universe/mode/limit/days 全参），复刻 `nextday._CACHE`。
- 任一因子源失败→对应因子 None 诚实缺失（不伪造 0），不崩整体。source 字段标注 `tdx`/`em`/`ths`/`jisilu`/`csindex`。

## 前端接入
- `web/index.html` 新 tab「ETF/QDII 筛选」：mode 长/短切换下拉 + 参数，渲染两张清单表格；QDII 溢价列高亮；复用现有存量 tab 状态保持范式。
- `research_guide.html` 为独立 artifact 非运行时一部分，无需同步。

## 测试
- `tests/test_etf_screen.py`：合成 `etf_daily` + spot mock，测长/短清单因子方向、质量门槛剔除、估值分位正确性、溢价标记、缺失降级、缓存、mode 切换。mock 复用 `db.query_rows`，不触网。
- Phase 0 探测脚本不进测试（一次性）。
- `tests/test_server_new_routes.py` 补 `/api/etf-screen` 冒烟（200 + disclaimer 字段）。

## 复用（零新轮子）
- `data/history._UNIVERSE` → 取 `etf_daily` 面板
- `backtest.eval.compute_factor` → `momentum_n`/`vol_corr_n`/`volatility_n`/`rel_strength_20`
- `screener.nextday._weighted_score`/`_rank_pct` → 横截面加权范式（需导出或复制纯函数）
- `screener.quality._resonance` → 长清单估值×质量综合（若做共振）
- tdx `get_quote` → QDII 盘口价

## 演进（二期，本期不做）
- 估值分位/溢价/规模落库新表（`etf_valuation`/`etf_premium`），支持长期序列与历史分位回溯——从方案 A 演进为"C：采集层为主"。
- 汇率/时区因子、成分股加权基本面。

## 验收
1. `/api/etf-screen?universe=ETF&mode=long` 返回含 `long_term` 清单，逐项 `valuation_percentile`/`quality_score`/`premium`，无 crash，附 disclaimer。
2. `mode=short` 返回量价排序清单。
3. QDII 溢价源不可得时字段诚实 `null`，list 不崩。
4. `tests/test_etf_screen.py` + 现有链路全绿。
5. 部署经 `deploy.sh`（测试→重建→健康→自检→冒烟）通过。
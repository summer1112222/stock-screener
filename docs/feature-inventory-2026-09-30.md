# 全仓功能清单与完成度盘点

**日期**：2026-09-30
**性质**：只读审计报告（阶段0，零代码改动）。供"优化和完成所有功能"的后续决策锚点。
**依据**：`CLAUDE.md`（已核验与代码一致的权威索引）+ 针对打磨档证据点的抽查（git log / grep / web 前端 / 记忆待办）。

---

## 一、总览判断

这套代码库**按自身路线图基本功能完整**，记忆里记录的历史待办（smart-money P2、cross-list-annotate 三清单穿透 A/B2/B3、北向全市场源）**均已落地**。grep 到的"降级/回退/stale/占位"绝大多数是**刻意设计的韧性降级路径**（东财被封→THS、tdx 空→akshare、超时→stale、盘后 A/B 失效→仅 C），**不是未完成功能，不应当作缺口修复**。

真正值得动手的"优化/完成"空间是**有限且可列举**的，集中在第 3 节。

---

## 二、功能清单（模块 × 路由 × 前端 × 脚本 × 测试）

### 2.1 数据采集层 `data/`（14 模块，全部完成）

| 模块 | 能力 | 状态 |
|---|---|---|
| `models.py` | 规范字段集 / AKShare 别名 / SQLite schema | ✅ |
| `db.py` | 连接/迁移/upsert/query/meta；线程本地连接复用(0.13ms) | ✅ |
| `collector.py` | spot 快照（板块/ETF）；HTTP patch 防封 | ✅ |
| `history.py` | 历史日线（tdx 主源 + 本地 qfq） | ✅ |
| `portfolio.py` | 本地持仓跟踪 | ✅ |
| `watchlist.py` | 自选观察清单（同 code 去重） | ✅ |
| `fundamentals.py` | 三大财报（tdx 主源 + 7 天缓存 + 哨兵） | ✅ |
| `research.py` | 研报评级 + 千股千评 | ✅ |
| `smart_money.py` | 6 通道主力动向采集（龙虎榜/十大股东/北向 incl. hist_em 市场级行/资金流 THS/高管/限售） | ✅ |
| `market.py` | 市场温度（4 路降级） | ✅ |
| `board_stocks.py` | 板块成分股（东财→THS 降级） | ✅ |
| `pytdx_client.py` | 通达信直连（五档/日K/除权/公司信息/板块文件） | ✅ |
| `adjust.py` | 前复权本地计算 | ✅ |
| `calendar.py` | 交易日历 + 盘中时段（Asia/Shanghai） | ✅ |

### 2.2 实时筛选 `screener/`（13 模块）

| 模块 | 能力 | 状态 |
|---|---|---|
| `conditions.py` / `engine.py` / `indicators.py` | 实时筛选 + 排序 + 技术指标 | ✅ |
| `nextday.py` | 次日强势（五因子评分 + 五步诊断，有界板块反查） | ✅ |
| `daily_strong.py` | 每日强势 — **旧版，已退役，仅供 `/api/daily-strong` 兼容转调** | ⚠️ 见 3.1 |
| `smart_money.py` | 主力动向查询/聚合（today/by_actor/top+强度/chip/behavior/phase/radar/board-link/seat-winrate） | ✅ |
| `sector_heat.py` | 行业景气加成 + B2 宏风格调制 | ✅ |
| `policy_events.py` | 事件催化标注 | ✅ |
| `macro_style.py` | 宏观风格状态机（B1） | ✅ |
| `market_style.py` | 宏观静态标注层 | ✅ |
| `etf_screen.py` | ETF/QDII 长短清单 | ✅ |

### 2.3 回测/研究 `backtest/`（15 模块，全部完成）
`eval/engine/risk/robust/candidates/execution/research/buffett/tracker/signals/quality/` + `factor_registry/factor_compute/factor_snapshot`（因子注册/计算/快照三层）。含成交模拟、T+1 回测、可信度/风险门槛、网络尾段硬墙钟、主清单政加成等。

### 2.4 API `api/server.py`
**58 个路由装饰器**，路由速查已全部写入 CLAUDE.md（`/api/meta` … `/api/stock-analysis`）。全部挂合规 disclaimer。

### 2.5 前端 `web/index.html`（单文件，无构建）
- tab：实时筛选 / 历史回测 / 主力动向 / 优质筛选 / 个股分析 / ETF/QDII
- 抽屉：持仓(`#pfDrawer`) / 自选(`#wlDrawer` + `wlQuickAdd` 一键入自选)
- 跨 tab 跳转：行点击 → backtest / analysis
- K线 / 信号回测研究台 / ETF 长短清单 / ETF 数据源探针
- **`dsLoad/dsRender` 已完全退役**（仅剩一行注释，无定义无调用）——前端无死代码需清理。

### 2.6 脚本 `scripts/`（7 个，全部完成）
`backfill_lhb_history` / `diagnose_ranking` / `fill_track_returns` / `quality_diag` / `etf_source_probe` / `northbound_source_probe` / `northbound_akshare_probe`。

### 2.7 测试 `tests/`（70 个文件）
命名≈被测模块，合成数据 mock，不依赖网络。

---

## 三、打磨档 / 优化点（真实、可辩护，分优先级）

> 说明：以下均**非硬缺口**（不影响主清单输出），是体验/一致性/死代码维度的打磨。优先度 P1>P2>P3。

### P1 —— 明确决策项（需你拍板）

**3.1 双"强势"模块收敛：`daily_strong` vs `nextday`**
- 现状：`nextday.py`（2026-09-06 重构）已是用户可见主清单，`daily_strong.py` 仅保留作 `/api/daily-strong` 兼容转调。
- 遗留债：`daily_strong` step5 板块助攻被 `stock_spot` **无 `board` 列**卡死 → 生产恒 `pass=False`（`screener/daily_strong.py:180` 自述"待补列激活"）。
- 选项 A：彻底删除 `daily_strong.py` + `/api/daily-strong` 路由 + 相关 test（收敛，减少死代码）。
- 选项 B：补 `stock_spot.board` 列激活板块助攻（需 spot 快照采集带板块，影响 `collector`/`engine`，连锁较广）。
- 选项 C：维持现状（兼容入口保留）。

### P2 —— 一致性 / 文档打磨

**3.2 权重校准透明度**：`nextday` 五因子权重与 `quality` 共振权重均称"IC 校准/经验先验"，但 CLAUDE.md 自述"IC 透明度报告待续"，且 grep 未找到对应档案。可选产出简短 `docs/` 权重视角说明（记录：依据、样本数、校准日期），让权重改动可追溯。

**3.3 缓存键/默认参数一致性维护**：多模块 30s 进程缓存键含参数；`tracker.is_default_params` 白名单防污染。此为长期维护点，需靠测试守护（已有多套），暂无即时缺口。

### P3 —— 可选韧性/性能微调

- stale 回退日期标注统一；占位 diz 措辞一致性复核；`stock_spot.board` 是否值得引入（同上 3.1-B）。

---

## 四、建议的下一步（供你挑选）

1. **拍板 3.1**（daily_strong 收敛方向 A/B/C）——这是唯一真正的"完成"决策。
2. 若要"优化批量"，我建议**按领域小批、每批独立 spec/plan + TDD**（符合本仓惯例），从你关心的领域开始。
3. 若你心里有具体痛点（某页慢/某逻辑绕/某缺口难受），告诉我，仅对它单独头脑风暴——比泛改全仓高效得多。

---

*本报告由阶段0 只读审计产出，未改动任何产品代码。*
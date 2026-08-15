# A股板块/ETF 筛选器 · 本地研究工具

基于公开数据（AKShare）的 A 股**板块/ETF 条件筛选 + 历史回测 + 主力动向观察 + 优质筛选**本地工具。FastAPI 后端 + 原生 JS 单页前端，Docker 一键起。

> **只筛选不荐股、不承诺收益、不输出买卖点、不自动下单。** 无证券投资咨询资质。所有输出为"机械排序观察清单/研究优先级"，非投资建议。

## 合规声明

- 本工具是**数据筛选/研究工具，非投资咨询**。
- 仅基于公开行情/财报/资金流数据做阈值过滤与机械排序，附 `disclaimer` + `update_time`。
- **不荐股、不承诺收益（含"月 X%"表述）、不输出实时买卖点、不自动下单。**
- "候选池/优质筛选/主力动向"是按因子机械排序的观察清单，措辞用"筛选/排序/观察清单"，不得用"推荐/买入/卖出"。
- 市场有风险，投资决策请独立判断，盈亏自负。

## 功能（5 tab + 持仓 + 自选 + 数据健康）

- **实时筛选**：板块/ETF/个股 spot 快照，按字段+运算符条件筛选排序；板块成分股按需实时拉取（东财优先，THS 备援）。
- **历史回测**：IC/分档/topN 回测、walk-forward/bootstrap 风控、机械信号扫描（ma_breakout/golden_cross/volume_surge 等）+ 信号历史胜率（多 k 矩阵）。
- **主力动向**：6 通道观察清单（龙虎榜/十大股东/北向/资金流/高管增减持/限售解禁），通道状态三态灯（绿 ok/黄 stale/灰 从未成功），席位/股东聚合、主力行为序列、主力阶段判定（吸筹/洗盘/拉升/出货计分制+观望）、席位历史胜率、板块-个股资金联动，表头可点击排序。
- **优质筛选**：四口径（风险调整/价值质量/资金流向/多信号）横截分位 → 共振排序 → 组合层（行业分散+相关性+最小方差权重）+ 盘口精排（盘中流动性深度）。
- **个股分析**：单股深度卡聚合基本面+评分+主力动向+研报评级+千股千评+技术信号+多因子机械预判 outlook。
- **持仓跟踪**：本地记录买入，按 spot 最新价算浮盈，到价提醒（浮窗抽屉）。
- **自选股**：未买入跟踪观察清单（独立抽屉），信号/分析卡一键"＋自选"。
- **数据健康**：顶部 banner + 抽屉，全局数据新鲜度三态总览（7 域 + overall），60s 轮询，手动刷新采集。

## 快速开始

```bash
# Docker（推荐；Dockerfile 配清华 PyPI 镜像）
docker compose up --build -d            # 构建并后台启动
curl -X POST http://localhost:8000/api/refresh   # 首次必做：全量采集
# 前端：http://localhost:8000/web/index.html
# API 文档(Swagger)：http://localhost:8000/docs   ReDoc：/redoc

# 或本地宿主跑（需 pandas/akshare）
uvicorn api.server:app --reload --port 8000
```

停止/清理：
```bash
docker compose down          # 停止(保留 SQLite 数据卷)
docker compose down -v       # 连同数据卷清除
```

## 路由速查（按域分组）

所有数据响应经 `_wrap()` 附 `disclaimer` + `update_time`；回测/候选池类附 `bt_disclaimer`/`cand_disclaimer`。

| 域 | 路由 | 说明 |
|---|---|---|
| 元 | `GET /api/meta`、`/api/health`、`/api/market` | 最近更新时间 / 数据健康聚合(7域三态+overall) / 市场温度快照+趋势 |
| 筛选 | `GET /api/fields` `/api/boards` `/api/etfs` `/api/board-stocks` `/api/screen` `/api/stock-search` | 字段目录 / 板块·ETF·个股排名 / 板块成分股 / 条件筛选 / 代码·名称搜索 |
| 采集 | `GET\|POST /api/refresh` | 手动全量采集 |
| 历史/回测 | `POST /api/backtest/fetch` `/api/backtest/eval` `/api/backtest/run` `/api/backtest/walkforward` `GET /api/history` | 拉历史日线 / IC分档 / topN回测 / walk-forward / 历史查询 |
| 候选/信号 | `GET /api/candidates` `POST /api/signals` `/api/signals/backtest` | 候选池排序 / 机械信号扫描 / 信号历史胜率 |
| 主力动向 | `GET /api/smart-money/today` `POST /api/smart-money/refresh` `/api/smart-money/channels` `/api/smart-money/seats` `/api/smart-money/seats-stocks` `/api/smart-money/behavior` `/api/smart-money/phase` `/api/smart-money/seat-winrate` `/api/smart-money/board-link` `/api/management` `/api/share-unlock` `/api/st-list` | 当日清单 / 采集 / 通道状态 / 游资席位统计 / 席位逐股 / 主力行为序列 / 主力阶段判定 / 席位胜率 / 板块联动 / 高管增减持 / 限售解禁 / ST名单 |
| 优质/基本面 | `GET /api/quality` `/api/buffett` `/api/buffett/top` `/api/chip` `/api/tdx/quote` `/api/tdx/company-info` | 多口径共振 / 巴菲特式基本面评分 / 筹码分布 / 通达信实时五档 / 公司信息文本 |
| 研报 | `GET /api/research` `/api/comments` | 研报评级 / 千股千评 |
| 个股分析 | `GET /api/stock-analysis?code=` | 单股深度卡（聚合基本面+主力+研报+信号+预判） |
| 持仓 | `GET\|POST /api/portfolio` `DELETE /api/portfolio/{pid}` `PATCH /api/portfolio/{pid}` | 本地持仓 / 平仓 / 到价提醒 |
| 自选 | `GET\|POST /api/watchlist` `DELETE /api/watchlist/{wid}` | 自选股观察清单 |

## 架构

```
data/        采集与存储
  models.py(规范字段/AKShare别名/SQLite schema) db.py(连接/建表/迁移/upsert/query/meta)
  collector.py(spot快照:板块/ETF/ST) history.py(历史日线,通达信主源+本地qfq)
  adjust.py(前复权本地计算:pytdx xdxr归一化) pytdx_client.py(通达信直连薄客户端:实时五档/日K/公司信息)
  board_stocks.py(板块成分股按需,东财优先THS备援) market.py(市场温度:涨跌家数/估值/两融)
  fundamentals.py(三大财报按需+缓存7天TTL) research.py(研报+千股千评)
  portfolio.py(本地持仓) watchlist.py(自选股观察清单) smart_money.py(主力动向6通道+stale降级)
screener/    实时筛选
  conditions.py(因子目录/运算符) engine.py(快照过滤排序/派生因子)
  smart_money.py(主力动向查询/聚合+筹码分布+行为序列,只读不触网)
backtest/    历史研究
  eval.py(IC/IR/分档) engine.py(topN回测) risk.py robust.py(风控/walk-forward/bootstrap)
  candidates.py(候选池) buffett.py(巴菲特基本面v2+剩余收益估值) signals.py(机械信号)
  quality.py(四口径共振优质筛选编排层,只读因子源不新增表)
api/server.py  FastAPI 单文件入口(所有路由 + _wrap；个股分析卡逻辑inline在此)
web/index.html 单页前端(原生JS,5 tab + 持仓/自选抽屉 + 数据健康banner,从/api/fields动态渲染)
```

**四层 + 两个领域模块**：采集层只取数入库；筛选/查询层只读库不触网；回测层只读历史；`api/server.py` 入口经 `_wrap` 统一附 disclaimer。`quality.py` 是编排层，复用 buffett/signals/smart_money/candidates 结果，不新增表。`adjust.py`+`pytdx_client.py` 使通达信成历史日 K 自洽主源（不再依赖新浪/东财 qfq 接口）。

## 数据源与已知限制

- 数据源：AKShare（聚合东方财富/新浪/同花顺）。
- **东财 IP 封禁**：当前出口 IP 被东财封（`RemoteDisconnected`/502）。代码已加备援：板块东财失败→同花顺；资金流走同花顺直取（绕 akshare 列名 bug）；北向实时端点 2024-08 起已下线 → 改走**盘后沪深股通十大成交股**备援（前端表头标注）。
- **北向口径**：盘后十大成交股（20只/日），非实时个股净买额。
- 概念板块（同花顺备援）无涨跌幅/资金流（已知限制）。
- 历史日线需先 `/api/backtest/fetch` 拉指定 codes 才能回测/扫信号。
- 财报按需实时拉取（buffett），`_AK_OK=False` 时整路由返回 None；quality 口径2 buffett 失败时降级为 spot 估值代理。

## 测试与开发

```bash
# 单测（仓库根目录跑；tests/ 被 .dockerignore 排除，镜像里没有）
python -m pytest tests/ -q
python -m pytest tests/test_engine.py::test_topn_asc -q   # 单个测试

# 环境：Python 3.12 + pandas + akshare（采集层）+ fastapi + uvicorn
```

## 开发指引

- 改某模块前先读 `CLAUDE.md`（路由速查/架构/关键设计决策/改动检查清单）——那是给 Claude Code 的开发指引，含非显而易见的约束（NaN→None、日期破折号、DB 迁移、pandas 3.0 兼容、回测因子可重建性等）。
- 设计文档：`docs/superpowers/specs/`（spec）与 `docs/superpowers/plans/`（实施 plan），按域存档。

## 免责

数据来自公开接口（AKShare 聚合东方财富/新浪/同花顺等），可能存在延迟、缺失或接口变更。本工具不构成任何投资建议，市场有风险，投资决策请独立判断，盈亏自负。
# stock-screener

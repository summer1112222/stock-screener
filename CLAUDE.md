# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **README.md 已更新**（含路由速查/架构/合规/数据源限制，面向用户）。**开发仍以本文件的"路由速查"和"架构"为准**——CLAUDE.md 含非显而易见的设计决策与改动检查清单，README 面向使用者。

## 合规硬约束（最高优先级）

本项目是**数据筛选/回测研究工具，非投资咨询**。任何改动都必须守住：

- **不荐股、不输出实时买卖点、不承诺收益（含"月 10-20%"这类表述）、不自动下单**。无证券投资咨询资质。
- 所有数据响应经 `api/server.py` 的 `_wrap()` 附 `disclaimer` + `update_time`；回测响应附 `bt_disclaimer`；候选池附 `cand_disclaimer`。
- "候选池"是按因子**机械排序的观察清单**，不是"推荐买入"。措辞必须用"筛选/排序/观察清单"，不得用"推荐/买入/卖出"。
- `config.yaml` 注释、每个模块 docstring 都重申此边界——改它们时保持一致。

## 常用命令

```bash
# 本地运行(宿主需装 pandas；akshare 仅采集层需要)
uvicorn api.server:app --reload --port 8000

# Docker（推荐；Dockerfile 配了清华 PyPI 镜像，否则国内 pip 下载仅 17kB/s）
docker compose up --build -d            # 重建并后台启动
docker compose down                     # 停止(保留数据卷)
docker compose down -v                  # 连同 SQLite 卷一起清

# 单测（宿主跑；tests/ 被 .dockerignore 排除，镜像里没有）
# 环境：Python 3.12.10 + pandas 3.0.3 + requests 2.34.2（见 requirements.txt）
# 无 conftest.py / pytest.ini / pyproject.toml——必须在仓库根目录跑，子目录里跑找不到 data 包
python -m pip install -q pytest         # 宿主若无 pytest
python -m pytest tests/ -q
python -m pytest tests/test_engine.py::test_topn_asc -q   # 单个测试

# 触发数据采集（GET 与 POST 均可，浏览器地址栏可直接访问）
curl -X POST http://localhost:8000/api/refresh

# 前端：http://localhost:8000/web/index.html  （FastAPI 同源托管 /web + /api）
```

## 架构

四层 + 两个领域模块：

- `data/` 采集与存储。`models.py`（规范字段集、AKShare 别名映射、SQLite schema）、`db.py`（连接/建表/迁移/upsert/query/meta）、`collector.py`（spot 快照采集：板块/ETF）、`history.py`（历史日线采集）、`portfolio.py`（本地持仓表：记录买入、按 spot 最新价算浮盈）、`fundamentals.py`（完整三大财报按需+缓存，7天TTL，超时降级 stale）、`research.py`（研报评级+千股千评）、`smart_money.py`（主力动向采集：龙虎榜席位/十大流通股东/陆股通/个股资金流/高管增持仓/限售解禁 6 通道，每通道 `(records,ok,err)`，东财优先+备援+失败标不可用不崩；`refresh_today` 编排 + `CHANNEL_STATUS`/`NATIONAL_TEAM`）。
- `screener/` 实时筛选。`conditions.py`（因子目录 `BOARD_FIELDS_CAT/ETF_FIELDS_CAT`、运算符 `OPS`）、`engine.py`（快照过滤/排序/资金流合并、派生因子 `DERIVED_FIELDS`）、`smart_money.py`（主力动向查询/聚合层，只读 `db.query_rows`：`today_list`/`by_actor`(含"国家队"保留词 LIKE 展开)/`top_by_amount`）。
- `backtest/` 历史研究。`eval.py`（IC/IR/分档/因子计算）、`engine.py`（topN 回测，注意与 `screener/engine.py` 同名不同域）、`risk.py`（风控指标）、`robust.py`（walk-forward/bootstrap）、`candidates.py`（候选池排序）、`buffett.py`（巴菲特式基本面分析 v2：年报 EPS + 横截相对估值分位 + FCF 代理 + 杠杆校正 ROE + 负面预筛，**仅个股**，ETF 不适用；FCF 优先用 `fundamentals_cache` source=cashflow 算真实 FCF（经营现金流-资本开支），失败降级摘要代理（经营现金流量净额），`ratios.fcf_source` 字段标注来源；附带修复 `_annual` 兼容带破折号报告期 2024-12-31）、`signals.py`（买卖信号扫描：ma_breakout/golden_cross/volume_surge/rsi_oversold/momentum_up，依赖 `*_daily` 历史表，复用 `data.history._UNIVERSE`）、`quality.py`（多口径共振编排层：四口径分位[风险调整/价值质量/资金流向/多信号]→共振分 hits×10+avg→min_dims 门槛→组合层[行业分散/相关性/容量]；**只读** buffett/signals/smart_money/candidates 结果，不新增表，个股/ETF 不混跑，ETF 口径2恒空）。
- `api/server.py` FastAPI 单文件入口，所有路由 + `_wrap`。`web/index.html` 单页前端（原生 JS，无构建步骤，从 `/api/fields` 动态渲染字段/运算符下拉）。4 tab 分组（实时筛选/历史回测/主力动向/优质筛选）+ 持仓右上浮窗抽屉（`#pfDrawer`，`togglePortfolio()`）+ K线跨 tab 跳转（行点击 handler 调 `switchTab('backtest')` 再 `hkRun(c)`）；`.tab-panel[data-tab]` 显隐，状态保持不重新 fetch。

### 路由速查（server.py 全量）

`/api/meta`（最近一次 refresh 的 `update_time`，无数据时为空）`/api/fields` `/api/boards` `/api/etfs` `/api/screen`（实时筛选，附 `disclaimer`）→ `/api/refresh`（手动全量采集）→ `/api/history`（历史日线查询）→ `/api/backtest/fetch`（拉历史进 `*_daily` 表）`/api/backtest/eval`（IC/分档）`/api/backtest/run`（topN 回测）`/api/backtest/walkforward`（附 `bt_disclaimer`）→ `/api/candidates`（候选池排序，附 `cand_disclaimer`）→ `/api/signals`（机械信号扫描，附 `cand_disclaimer`："规则机械触发，非AI推荐"）`/api/signals/backtest`（信号历史胜率回测：每信号扫历史触发点 t→t+k 收益统计，附 `cand_disclaimer`："历史触发统计事实，非预测"）→ `/api/portfolio` GET/POST + `/api/portfolio/close`（本地持仓跟踪，用默认 `_wrap` 即附 `disclaimer`，无额外 disclaimer）→ `/api/buffett` `/api/buffett/top`（基本面评分，附 `bt_disclaimer`："基于公开财务摘要的机械评分，护城河来源需读年报人工判断，研究优先级非买卖信号"）→ `/api/smart-money/today` `/api/smart-money/refresh` `/api/smart-money/channels`（主力动向观察清单，`today`/`refresh` 附 `cand_disclaimer`："主力动向观察清单，机械归类，非荐股非买卖信号"；`channels` 返回 `smart_money.channel_status()` 各通道 ok/err/rows/date，叠加 DB 实况——最新日期有行即 ok=True，防容器重启后内存态 `CHANNEL_STATUS` 全归零「未采集」明明有数据却全显灰）→ `/api/st-list`（ST 全名单快照，默认 disclaimer）→ `/api/management`（高管增减持，channel=高管增减持，附 `cand_disclaimer`）→ `/api/share-unlock`（限售解禁按 as_of 月份查，附 `cand_disclaimer`）→ `/api/research`（研报评级，近 N 日，附 `cand_disclaimer`："机构研报评级机械汇总，非荐股非买卖信号"）→ `/api/comments`（千股千评 per-code 按需+缓存 source=comments，附 `cand_disclaimer`）→ `/api/quality`（多口径共振优质筛选，附 `cand_disclaimer`："多口径共振机械排序观察清单，非荐股非买卖信号"；复用 buffett/signals/smart_money/candidates 因子，不新增表；依赖 `*_daily` 历史的口径[风险调整/多信号]未 fetch 则空并降级 `min_dims`）。新增路由时按所属域挂对应 disclaimer。根路由 `/` 返回启动信息 + `/docs`/`/web` 指针 + `disclaimer`；`/web` 静态托管前端（同源，免 CORS）。

> 交互调试：FastAPI 默认 Swagger UI 在 `/docs`（ReDoc 在 `/redoc`），可手工调任意路由、看 schema 与 `disclaimer` 字段。

### 关键设计决策（非显而易见，改前必读）

**AKShare 数据源在当前出口 IP 被东财封**（`RemoteDisconnected`/502）。代码已加备援，改动时勿破坏：

- `data/collector.py` 顶部 `_install_http_patch()` 全局给 eastmoney 域名注入浏览器 UA + Referer，并对 `RemoteDisconnected`/502/503/504 退避重试 4 次。**必须保留**——akshare 的板块接口 `requests.get(url, params=params)` 不带 headers，默认 `python-requests/*` UA 被东财 502。
- 板块采集：东财 `stock_board_industry_name_em` 失败 → `stock_board_industry_summary_ths`（同花顺，字段：板块/涨跌幅/净流入/上涨家数/下跌家数/领涨股/领涨股涨跌幅）。概念：东财失败 → `stock_board_concept_summary_ths`（仅龙头股/成分股数量/驱动事件，**无涨跌幅/资金流**——这是已知限制）。
- 资金流 `ak.stock_sector_fund_flow_rank`：`sector_type` 合法值是 `行业资金流/概念资金流/地域资金流`（**不是** `行业/概念`），`indicator` 只支持 `今日/5日/10日`（**无 20日**）。`collector._SECTOR_TYPE_MAP` 做了简称映射，对外仍接受 `行业/概念`。
- 历史日线：ETF 走 `fund_etf_hist_sina`（需 `_sina_symbol` 加 sh/sz 前缀）、个股走 `stock_zh_a_daily`（symbol 需 `sz/sh` 前缀）、板块走 `stock_board_industry_index_ths`（best-effort，THS symbol 映射可能不全）、基准 `stock_zh_index_daily`。东财历史接口全部被封。

**NaN→None 序列化**：`screener/engine.py` 两处与 `collector._to_records` 必须用 `df.astype(object).where(pd.notna(df), None)`。**不能**用 `df.where(pd.notna(df), None)`——对 float64 列 None→NaN 又变回 NaN，starlette 的 JSONResponse `allow_nan=False` 会直接 500（`Out of range float values`）。

**日期范围过滤**：`data/history._filter_range` 与 `backtest/eval.load_panel` 用 `pd.to_datetime` 比较，**不能**用字符串比较——数据日期是 `2024-06-01` 带破折号、API 参数是 `20240601` 无破折号，字符串比较会让 2023 全排除/2024 全纳入。

**DB 迁移**：`db.init_db` 先 `executescript(SCHEMA_SQL)`（`CREATE TABLE IF NOT EXISTS` 不会给已存在的表补列），再 `_migrate` 对旧库 `ALTER TABLE ADD COLUMN`（try/except 忽略 duplicate column）。新增列时同时扩 `_BOARD_MIGRATIONS`（或类比的新表迁移）——旧库在持久化卷里，否则字段缺失。

**pandas 3.0 兼容**：`pd.Grouper(freq="M")` 已弃用→用 `ME`/`QE`（`backtest/engine.rebalance_dates` 有 `_map` 归一）；`Series.reindex` 的 fill_value 必须用关键字（`b.reindex(comb, fill_value=0)`，不能位置参数）。

**回测因子可历史重建的只有 OHLCV 派生**（`momentum_n/volatility_n/turnover_n/activity/momentum`）。spot 快照因子（`up_count/down_count/main_net_inflow/leading_stock_change`）是当日盘后聚合、无历史序列，**无法回测**——改 `BACKTEST_FACTORS` 时勿把 spot 因子塞进去。

**signals/portfolio/buffett 的数据依赖链**：
- `signals.scan` 依赖 `*_daily` 历史表（个股 `stock_daily` 等），**必须先 `/api/backtest/fetch` 拉过对应 codes 的历史**，否则空表无信号。RSI/MA 全部在 `signals.py` 本地重算，不入库。
- `buffett.fetch_abstract` 走 `ak.stock_financial_abstract`（个股财务摘要），是**按需实时拉取**、不入库——`_AK_OK=False` 时整个 buffett 路由返回 None，不要假设它有缓存兜底。`/api/buffett/top` 先从候选池 shortlist（依赖 `/api/refresh` 的 spot 快照排除 ST/涨停/停牌）取标的，再逐个拉财务摘要评分。
- `portfolio` 是独立的 `portfolio` 表（`id/code/name/buy_date/buy_price/shares/note/ts`），与行情表解耦；浮盈靠 `stock_spot` 最新价现算，无 spot 时浮盈字段为空。

## 改动检查清单

> **设计文档**：`docs/superpowers/specs/` 存 7 份设计 spec、`docs/superpowers/plans/` 存对应 6 份实施 plan（`backtest-realism` 仅有 spec 无 plan）（smart-money-tracker / quality-screener / frontend-tabs / akshare-more-sources / backtest-factors / signals-etf-combo / backtest-realism）。改某模块前先读对应 spec，拿到"为什么这么设计"的上下文比读代码回溯快。

- 新增 SQLite 列/表 → 同步 `models.SCHEMA_SQL` + `TABLE_FIELDS` + `db._BOARD_MIGRATIONS`（若旧表补列）。
- 新增采集源 → 复用 `(df, ok, err)` 返回约定 + `_to_records`（NaN→None）+ 异常不抛崩；eastmoney 域名自动受 HTTP patch 保护。
- 新增 AKShare 采集源 → 列表型(stock_zh_a_st_em/stock_hold_management_detail_em/stock_restricted_release_detail_em/stock_research_report_em)进 refresh_all/refresh_today;per-code(stock_*_sheet_by_report_em/stock_comment_detail)走 fundamentals.fetch + fundamentals_cache 缓存(7天 TTL,超时降级 stale);复用 `_first_col` 候选名容错,单测 mock 验证字段映射。高管增持仓/限售解禁并入 smart_money_action 复用 channel 零加列;限售解禁 as_of=真实解禁日期(可能未来),查询按 as_of 月份(unlock_by_month)。

  > **AKShare 版本适配（2026-07-23 修正，akshare 1.18.64）**：旧函数大面积改名，按旧名调用直接 `AttributeError` 导致 5 通道零写入（DB 只剩资金流）。映射：龙虎榜 `stock_lhb_detail_em(start_date,end_date)` 日期须**无破折号 YYYYMMDD**（带破折号内部 NoneType 崩）；席位明细 `stock_lhb_stock_detail_em(symbol,date,flag)` 现需 date+flag、逐股两次请求太慢，**改用主榜单"龙虎榜净买额"出个股级记录**。十大股东 `stock_gdfx_free_top_10`→`stock_gdfx_free_top_10_em(symbol=sh/sz+code, date=报告期YYYYMMDD)`（`_prefix_code`/`_latest_report_period`）。高管 `stock_hold_management_em`→`stock_hold_management_detail_em()`（无参、17 万行全历史、拉取约 4-5 分钟，`_filter_recent` 按"日期"列取近 7 日，记录 date 取行内变动日期）。限售 `stock_share_change_em(symbol=月)` 已下线→`stock_restricted_release_detail_em(start_date,end_date)` 按日期范围拉个股清单（actor 取"限售股类型"，无"解禁股东"列）。北向两东财端点均 `NoneType` 崩（反爬），无备援，保持优雅失败灰显。`_clean` 已支持 date/Timestamp→字符串（raw 列含上榜日/解禁时间等 date 对象，否则 JSON 序列化 500）。
  >
  > **资金流通道 THS 直取（2026-07-23）**：原口径复用 `stock_spot.main_net_inflow`，但该字段来自东财个股资金流（`stock_individual_fund_flow_rank` 出口 IP 被封 RemoteDisconnected），新浪 `stock_zh_a_spot` 又无该列 → 资金流通道长期停摆。`collect_fund_flow` 改走**同花顺直取**：`_fetch_ths_individual_fund_flow` 绕过 akshare `stock_fund_flow_individual` 的列名 bug（akshare 硬编 10 列、THS 即时表实际列数已变致 `ValueError: Length mismatch`），直接取 `data.10jqka.com.cn/funds/ggzjl/...` ajax 分页，`hexin-v` token 复用 akshare 内部 `_get_file_content_ths('ths.js')`+`py_mini_racer`（每页重算），`read_html` 按表头取 `股票代码`/`股票简称`/`净额(元)`，`_parse_cn_amount` 解析"822.74万"/"1.63亿"/负数。THS 失败再回落 spot.main_net_inflow（东财残量，通常空）。THS 不封东财 IP，是资金流通道唯一可靠源（~5200 行/当日，拉取约 1 分钟）。
- 新增 API 路由 → 用 `_wrap()`，回测/候选池类附对应 disclaimer。
- 新增 quality 编排层 → **不新增表**（复用 stock_spot/etf_spot/*_daily/smart_money_action），因子源文件 buffett/signals/smart_money/candidates **只读结果不改**；口径分位缺失为 None（`hits` 不计），任一因子源失败标 `dim_status=err` 不崩；`*_daily` 未 fetch 时口径1/4 空并 `min_dims` 自动 clamp。
- 新增前端字段/运算符 → 下拉已从 `/api/fields` 动态渲染，通常无需改 JS；但 `LABEL` 字典要补中文标签。
- 新增 signals/buffett 信号规则 → signals 规则在 `signals.py` 内本地重算（不入库），buffett 评分维度改 `buffett.py` 的 v2 逻辑；两者措辞保持"机械评分/研究优先级/非买卖信号"，按域挂 `cand_disclaimer`/`bt_disclaimer`。
- 单测放 `tests/`，合成数据 mock `db.query_rows`，不依赖网络。
- 勿提交噪声文件：`*.stackdump`（bash 崩溃转储）、`*.db`/`*.log`/`__pycache__/`（`*.stackdump` 与 `*.db`/`*.db-journal` 已在 `.gitignore`；仓库里曾误提交一个 `bash.exe.stackdump`，已删除）。

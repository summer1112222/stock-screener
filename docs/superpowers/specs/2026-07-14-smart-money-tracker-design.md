# 主力资金动向跟踪 · 设计文档

**日期**: 2026-07-14
**状态**: 设计已确认，待转实施计划
**范围**: `data/smart_money.py`（采集）+ `screener/smart_money.py`（查询/聚合）+ `api/server.py` 路由 + `web/index.html` 页签

---

## 1. 背景与合规边界

需求：观察每日（或一段时间内）**游资 / 国家队 / 外资 / 主力资金流**买入的股票与 ETF。

本项目是**数据筛选/观察工具，非投资咨询**。本功能定位为"**主力动向观察清单 + 机械归类**"：

- 只记录公开事实（龙虎榜席位名、十大流通股东名、陆股通持股变动、大单资金流净额），不输出买卖点、不承诺收益、不荐股。
- 措辞统一用"动向/动作/净额/观察清单/排序"，**禁**"推荐/买入信号/卖点/强势股"。
- 清单与排序类响应附 `cand_disclaimer`；前端顶部固定 disclaimer 条，表头列名中性。

---

## 2. 架构与模块边界

复用现有 `db`（建表/迁移/upsert/query/meta）、`collector._to_records`（NaN→None）、`_install_http_patch()`（全局东财 UA + 502 退避重试）、`_wrap` / `cand_disclaimer`。

```
data/smart_money.py        # 采集层：4 通道 collector，统一 (records, ok, err)
  collect_dragon_tiger()   # 龙虎榜席位（东财→备援），每日
  collect_holders()        # 十大流通股东（东财→备援），季频，国家队关键字匹配
  collect_northbound()     # 陆股通持股变动（东财→备援），每日
  collect_fund_flow()      # 个股/ETF 大单资金流（东财→备援），每日
  refresh_today()          # 编排：调 4 通道 → upsert smart_money_action → 写 meta
  CHANNEL_STATUS           # 各通道最近 {ok, source, err, at}，供前端标"不可用"
  NATIONAL_TEAM           # 国家队关键字常量，查询层 LIKE 展开

screener/smart_money.py    # 查询/聚合层（不触网，纯 db.query_rows）
  today_list(date, channel, market)   # 用法 A：某日主力动向清单（4 通道 union）
  by_actor(actor, days)                # 用法 B：某席位/股东 N 日累计
  top_by_amount(days, market, channel) # 用法 C：按累计主力净额排序的观察池
  _expand_national_team()              # 保留词"国家队"→国家关键字 LIKE 多名匹配

api/server.py             # 路由（见 §5）
web/index.html            # "主力动向"页签（见 §6）
```

**两层边界**：采集层只负责"取数→规范化→入库"，不知道用法；查询层只读库、不触网（与现有 `screener/engine.py` 一致，便于单测 mock `db.query_rows`）。`smart_money_action` 是唯一新增表，三类用法都从它查。

---

## 3. 底表 schema + 迁移

新增到 `data/models.py` 的 `SCHEMA_SQL` + `TABLE_FIELDS`。**全新表**，`CREATE TABLE IF NOT EXISTS` 即可，无需像 `_BOARD_MIGRATIONS` 那样给旧表补列（旧卷无此表则 executescript 直接建）。

```sql
CREATE TABLE IF NOT EXISTS smart_money_action (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,          -- 动作日；龙虎榜/北向/资金流=交易日，十大股东=季报披露日
  code TEXT NOT NULL,          -- 6 位代码
  name TEXT,
  market TEXT NOT NULL,        -- 股票 / ETF
  channel TEXT NOT NULL,       -- 龙虎榜 / 十大股东 / 北向 / 资金流
  actor TEXT,                  -- 席位名/股东名；ETF 资金流行=空
  action TEXT,                 -- 净买入 / 新进 / 增持 / 减持 / 上榜
  amount REAL,                 -- 净额(元)，可空
  rank INTEGER,
  as_of TEXT,                  -- 季频快照基准日(仅十大股东有值)，标数据时点
  raw TEXT,                    -- 原始行 JSON，备查
  ts TEXT,                     -- 入库时间
  UNIQUE(date, code, channel, actor, action)
);
CREATE INDEX IF NOT EXISTS idx_sm_date  ON smart_money_action(date);
CREATE INDEX IF NOT EXISTS idx_sm_code ON smart_money_action(code);
CREATE INDEX IF NOT EXISTS idx_sm_actor ON smart_money_action(actor);
```

UNIQUE 约束 `(date, code, channel, actor, action)` + `INSERT OR REPLACE` 实现 upsert。ETF 行 `actor` 为 NULL（schema 允许）。

**国家队识别**：不单独建列。`data/smart_money.py` 维护常量：

```python
NATIONAL_TEAM = ["中国证券金融", "中央汇金", "全国社保基金",
                 "中证金融", "梧桐树", "国家外汇管理局"]
```

holders collector 命中即 `channel='十大股东'`、`actor`=股东全名；查询层 `by_actor("国家队")` 展开为多名 `LIKE` 匹配。

---

## 4. 4 通道 collector 契约 + 数据流

每个 collector 统一签名（复用 `(records, ok, err)` + `_to_records` + 异常不崩）：

```python
def collect_<channel>(date_str) -> tuple[list[dict], bool, str]:
    # (records, ok, err)  records 已规范化为 smart_money_action 字段
```

| 通道 | akshare 接口 | 入表粒度 | actor 来源 | action | amount |
|---|---|---|---|---|---|
| 龙虎榜 | `stock_lhb_detail_em`（股票列表）+ 逐股 `stock_lhb_stock_detail_em`（席位明细） | 每股×每席位 | 席位名（营业部/机构专用） | `上榜` | 席位净额 |
| 十大股东 | `stock_gdfx_free_top_10(symbol, date)` | 每股×每股东 | 股东全名 | `新进/增持/减持`（与上季对比） | 持股变动(股) |
| 北向 | `stock_hsgt_individual_em` | 每股 | 空（类别"外资"） | `净买入` | 陆股通净额 |
| 资金流 | `stock_individual_fund_flow_rank` | 每股 | 空 | `净买入` | 主力净额 |

**4 通道可达性初判**（落地时按"东财优先→备援→全失败标不可用"实测）：

| 通道 | 频次 | 主源(东财) | 备援 | 可达性 |
|---|---|---|---|---|
| 龙虎榜 | 每日 | `stock_lhb_detail_em` | 新浪 `stock_lhb_*`（席位明细可能不全） | 中风险（席位名无备援源，东财封则整通道灰） |
| 十大股东 | 季频 | `stock_gdfx_free_top_10` | akshare 内备援少 | **高风险，可能整通道不可用** |
| 北向 | 每日 | `stock_hsgt_individual_em` | 新浪北向总额（个股明细弱） | 中风险 |
| 资金流 | 每日 | `stock_individual_fund_flow_rank` | 弱 | 中风险 |

**数据流**（`refresh_today` 编排，沿用 `collector.refresh_all` 风格）：

```
/api/smart-money/refresh (GET/POST，浏览器可直访)
  └─ refresh_today(date=今日)
       ├─ for ch in [龙虎榜, 北向, 资金流, 十大股东(季频仅披露日跑)]:
       │     records, ok, err = collect_<ch>(date)
       │     ok → db.upsert_smart_money(records)        # INSERT OR REPLACE on UNIQUE
       │     CHANNEL_STATUS[ch] = {ok, source, err, at}
       └─ db.set_meta("smart_money_last_update", now)
  └─ _wrap({channels: CHANNEL_STATUS, update_time}, {"cand_disclaimer": ...})
```

- 龙虎榜逐股拉席位 = N 次请求（每日上榜票 ~10-30 只，可接受）；东财全封则该通道整体灰掉，仅记 `CHANNEL_STATUS.龙虎榜.ok=False`，不阻塞其他通道。
- 十大股东只在季报披露日前后跑（`refresh_today` 判断"距上次披露 > 60 天"才重拉，非每日空转）。
- `as_of` 仅十大股东行填季报基准日，其余通道为空（每日 `date` 即时点）。

---

## 5. API 路由（分期 P1/P2/P3）

所有路由走 `_wrap`；清单/排序类附 `cand_disclaimer`（"主力动向观察清单，机械归类，非荐股非买卖信号，盈亏自负"）。

**P1 — 每日清单 + 采集**（用法 A）
```
GET  /api/smart-money/today?date=&channel=&market=   # 当日主力动向，可按通道/市场筛
POST /api/smart-money/refresh                         # 触发 refresh_today，回 CHANNEL_STATUS
GET  /api/smart-money/channels                        # 各通道 ok/err/最近更新
```
返回行：`{date, code, name, market, channel, actor, action, amount, as_of}`，按 `amount` 降序。

**P2 — 按主力名跟踪**（用法 B）
```
GET /api/smart-money/by-actor?actor=&days=30
```
`actor` 传席位名/股东名子串，或保留词 `国家队`（查询层展开为国家关键字 `LIKE` 多名匹配）。返回该主体 N 日内 `{date, code, name, channel, action, amount}` + 末尾汇总 `{出现次数, 累计净额}`。措辞用"主体动向记录"。

**P3 — 按额排序观察池**（用法 C，复用 `candidates.py` 排序风格）
```
GET /api/smart-money/pool?days=5&market=股票&limit=30&channel=
```
`group by code` 跨 N 日累计主力净额，降序出池，附 `cand_disclaimer`。可按通道过滤。

---

## 6. 前端 `web/index.html`

原生 JS，无构建步骤（沿用现有模式）。**本期（P1）前端只做当日清单 + 通道状态灯**，B/C 留 API、前端下期补。

- 新增"主力动向"页签：上方 4 个通道状态灯（绿=ok / 灰=不可用，hover 显示 err 串），主体是当日清单表（可切通道/市场，列含 code/name/actor/action/amount）。
- 顶部固定 disclaimer 条；表头列名中性（"动作/净额(元)"，不写"建议买入额"）。
- P2/P3 前端表单下期再加。

---

## 7. 错误处理与降级

- 每通道 `collect_<ch>` 内部 try/except，异常返回 `( [], False, err_str )`，不抛到 `refresh_today`——单通道崩不影响其他通道入库。
- 东财 `RemoteDisconnected`/502/503/504 自动受 `_install_http_patch()` 全局退避重试 4 次保护（已有，勿破坏）。
- 龙虎榜逐股拉席位：单股失败记 `err` 跳过，不整批回滚（部分入库 + CHANNEL_STATUS 标 `partial`）。
- **NaN→None**：collector 出口必须 `df.astype(object).where(pd.notna(df), None)`，**不能**用 `df.where(pd.notna(df), None)`——float64 列 None→NaN 又变回 NaN，JSONResponse `allow_nan=False` 会 500。
- `CHANNEL_STATUS` 不静默：任何通道 `ok=False` 在 `/api/smart-money/channels` 与 refresh 响应显式返回 `err` 串，前端灰掉并 hover 显示原因。

---

## 8. 测试 `tests/test_smart_money.py`

合成数据 mock `db.query_rows` / `db.upsert_smart_money`，不触网（沿用现有 `tests/` 模式）：

- `test_today_list_filters_by_channel` — 4 通道混合入库后按通道过滤正确。
- `test_by_actor_national_team_keyword` — 传 `actor=国家队` 展开为国家关键字 LIKE 匹配，命中汇金/证金行。
- `test_by_actor_summary` — N 日累计出现次数/净额聚合正确。
- `test_top_by_amount_desc` — 累计净额降序、ETF 行 actor 为空不报错。
- `test_collect_nan_to_none` — 合成含 NaN 的 df，校验出口无 NaN（防 500 回归）。
- `test_partial_channel_failure` — mock 某 collect 抛异常，`refresh_today` 仍入其他通道、CHANNEL_STATUS 标 partial。

---

## 9. 分期交付

- **P1**：底表 + 迁移 + 4 通道 collector（按可达性优先）+ `today` / `refresh` / `channels` 路由 + 前端当日清单页签 + 通道状态灯 + 6 条单测。
- **P2**：`by-actor` 路由（含国家队保留词）。
- **P3**：`pool` 路由（按额排序观察池）。
- P2/P3 前端表单下期补。

---

## 10. 改动检查清单（对齐 CLAUDE.md）

- 新增 `smart_money_action` 表 → 同步 `models.SCHEMA_SQL` + `TABLE_FIELDS`（全新表，无需 `_BOARD_MIGRATIONS` 旧表补列）。
- 新增采集源 → 复用 `(records, ok, err)` + `_to_records`（NaN→None）+ 异常不崩；eastmoney 域名自动受 HTTP patch 保护。
- 新增 API 路由 → 用 `_wrap()`，清单/排序/跟踪类附 `cand_disclaimer`。
- 新增前端 → P1 当日清单 + 通道状态灯；`LABEL` 字典若涉新字段补中文标签。
- 单测放 `tests/`，合成数据 mock，不依赖网络。

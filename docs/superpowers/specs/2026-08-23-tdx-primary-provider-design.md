# TDX 主源统一数据层设计

## 1. 背景与目标

当前项目已经在行情、历史日 K、除权除息和公司资料等场景接入通达信（TDX），但部分模块仍直接依赖 AKShare、同花顺、finshare 或本地快照，来源协议不统一，模块在数据为空、源失败和缓存命中时缺少一致的可观测信息。

本设计采用“TDX 主源”口径：

- TDX 能覆盖的数据，统一以 TDX 为优先来源；
- TDX 未覆盖的数据，暂保留真实备援源；
- 备援数据必须显式标注，不能伪装成 TDX；
- 没有可用数据时返回结构化不可用状态，不能用默认值、随机值或静默空数组冒充数据；
- 不改变现有筛选、回测和研究算法的业务含义。

## 2. 非目标

本阶段不做以下事情：

- 不强行把 TDX 公司资料文本解析成研报评级、千股千评或完整资金流；
- 不移除现有备援源；
- 不把所有模块改写为单一数据表；
- 不自动下单、不输出投资建议、不改变项目合规边界；
- 不为了满足“每个模块有数据”而制造缺失数据。

## 3. 数据来源分层

### 3.1 统一 Provider 协议

新增统一 TDX Provider/来源结果协议，建议放在 `data/tdx_provider.py` 或 `data/providers.py`。底层复用现有 `data/pytdx_client.py` 与 `data/adjust.py`，不让业务模块直接管理 TDX 连接对象。

每次读取返回统一结果：

```python
ProviderResult(
    data=...,                 # DataFrame、dict 或 list
    source="tdx",            # tdx/cache/fallback/unavailable
    status="ok",             # ok/stale/empty/error
    fetched_at="...",
    data_date="...",
    rows=120,
    error=None,
    original_source="tdx",   # cache 时保留原始来源
)
```

语义约定：

- `tdx`：本次请求直接来自通达信；
- `cache`：命中缓存，必须保留 `original_source`、`data_date` 和新鲜度；
- `fallback`：TDX 不可用后使用备援；
- `unavailable`：TDX 与备援均没有可用数据；
- `ok`：数据可用且符合请求周期；
- `stale`：有数据但不是当前周期/最近交易日；
- `empty`：源请求成功但确实没有记录；
- `error`：请求异常或解析失败。

### 3.2 领域覆盖矩阵

| 模块 | TDX 主数据 | 备援/限制 |
|---|---|---|
| 实时筛选 | 实时行情快照 | TDX 失败时使用已有快照并标记 stale/fallback |
| 次日强势、每日强势 | 实时行情 + TDX 历史日 K | 缺历史时返回明确的历史依赖状态 |
| 回测、信号 | TDX raw 日 K + 本地前复权 | TDX 历史失败时保留现有新浪等备援 |
| 持仓、自选股 | TDX 实时行情 | 非交易时段标注收盘/缓存状态 |
| 盘口分析 | TDX 五档行情 | 无可用报价时结构化 unavailable |
| 个股分析 | TDX 行情、日 K、公司资料文本 | 研报/评论等字段使用真实备援并字段级标注 |
| 财务分析、Buffett | TDX“财务分析”文本解析 | TDX 解析失败时 AKShare 备援 |
| 筹码分布 | TDX 历史日 K，本地计算 | 无历史时 `need_history=True` |
| 市场温度 | TDX 全市场行情聚合 | 估值等 TDX 不覆盖字段保留现有来源 |
| 板块、ETF | TDX 可取得的行情/资料优先 | 成分股和部分板块字段保留 THS/AKShare，并标注来源 |
| 主力资金 | TDX 可取得的公司资料/行情字段优先 | 细分资金流、北向等未完整覆盖部分保留备援 |
| 龙虎榜历史胜率 | TDX 可取得的资料优先 | 完整历史回溯暂保留历史库/finshare |
| 研报、千股千评 | TDX 无完整等价能力 | 保留现有源，标注 `fallback` |
| 质量筛选 | 复用各领域结果及来源元数据 | 混合模块返回字段级 `sources` |

## 4. API 来源元数据

所有经 `api/server.py` 返回的领域响应继续使用 `_wrap()`、`disclaimer` 和 `update_time`，并增加来源元数据：

```json
{
  "source": "tdx",
  "source_status": "ok",
  "source_rows": 120,
  "source_time": "2026-08-23T10:00:00",
  "fallback_used": false,
  "data": []
}
```

混合模块使用字段级来源：

```json
{
  "sources": {
    "price": "tdx",
    "daily_bars": "tdx",
    "research": "fallback",
    "money_flow": "fallback"
  },
  "fallback_fields": ["research", "money_flow"]
}
```

规则：

- 备援数据不能写成 `source="tdx"`；
- 缓存不能丢失原始来源和数据日期；
- 空结果必须说明是 `empty` 还是 `unavailable`；
- 仍须遵守 NaN→None 约束；
- 回测响应继续附加 `bt_disclaimer`，候选池/信号/主力等继续附加 `cand_disclaimer`。

## 5. 实施阶段

### 阶段一：Provider 与来源协议

涉及：

- `data/pytdx_client.py`
- 新增 `data/tdx_provider.py` 或 `data/providers.py`
- `data/history.py`
- `data/portfolio.py`
- `api/server.py`

先统一实时行情、日 K、除权除息和公司资料的读取与来源元数据，不改变业务算法。

### 阶段二：确定 TDX 覆盖模块

按以下顺序迁移：

1. portfolio/watchlist 实时行情；
2. screen 实时筛选；
3. backtest/fetch 历史日 K；
4. signals、nextday、daily_strong；
5. chip_distribution；
6. tdx/quote 与 quote-analysis；
7. stock-analysis；
8. fundamentals 与 buffett。

每个模块必须通过 Provider 访问数据，测试注入 Provider，不触网。

### 阶段三：部分覆盖模块

处理 `market`、`board_stocks`、`smart_money`、`research`、`quality`：

- TDX 能提供的字段改用 TDX；
- 无 TDX 等价数据的字段继续使用现有备援；
- 一个模块内允许字段级来源不同；
- 备援和数据缺口通过 `sources`、`fallback_fields` 和状态字段展示。

### 阶段四：健康检查与前端

涉及：

- `api/server.py`
- `data/market.py`
- `web/index.html`
- 健康检查及路由测试

健康状态按模块记录：`tdx_ok`、`fallback`、`unavailable`、最近成功时间、最近数据日期、行数和最近错误。前端展示 TDX、TDX 缓存、备援、暂无数据、数据源异常等状态标签。

## 6. 错误处理与新鲜度

Provider 必须捕获连接失败、超时、空响应、解析失败和缓存异常，并返回结构化结果。批量请求必须避免逐股触发外部请求；缺失历史应快速返回，不得阻塞整个路由。

新鲜度约定：

- 实时行情/盘口：交易时段优先 TDX 实时，非交易时段标记收盘；
- 日 K：标注最近交易日；
- 财务：标注报告期；
- 研报/评论：标注发布日期；
- 解禁：按真实事件日期记录，查询不得将未来事件当作今日行情；
- 回测：显式标注数据截止日，禁止读取未来数据。

## 7. 测试与验收

新增或调整：

- `tests/test_tdx_provider.py`
- `tests/test_data_source_metadata.py`
- `tests/test_health.py`
- `tests/test_server_new_routes.py`
- `tests/test_pytdx_client.py`
- `tests/test_nextday.py`
- `tests/test_engine_execution.py`
- `tests/test_factor_research.py`

必须覆盖：

1. TDX 成功时标记 `tdx`；
2. TDX 空结果不误标成功；
3. TDX 异常、备援成功时标记 `fallback`；
4. 双源失败时返回 `unavailable`；
5. 缓存保留原始来源、数据日期和 stale 状态；
6. 混合模块返回字段级来源；
7. JSON 响应不含 NaN；
8. 批量模块不逐股触网；
9. 回测不使用未来数据；
10. 健康检查能区分 `ok`、`stale`、`fallback`、`unavailable`。

## 8. 合规与数据真实性

本项目仍是数据筛选/回测研究工具，不是投资咨询。TDX 主源只代表数据来源，不代表数据质量保证或投资结论。所有候选池、次日强势、主力行为、盘口和质量排序继续使用“机械排序/观察清单/研究统计”措辞，不输出实时买卖点、不承诺收益、不自动下单。

任何源不可用时必须诚实呈现，禁止填充伪造行情、虚构财务值或把第三方数据标成 TDX。

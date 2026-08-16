# 通达信财报+spot 数据源主源化 设计

> 合规边界不变：工具提供数据+机械统计，用户自做买卖决策；所有响应挂 disclaimer，不输出买卖点。tdx TCP 直连取代被封的 akshare/新浪，是数据源层替换，不改变应用合规属性。

## 背景

用户 2026-08-12 定调"所有数据优先通达信(pytdx)，再补充项目没有的"（memory: tdx-primary-directive）。历史日 K 已落地 tdx 主源。本轮推进两块最痛：

1. **财报**：`buffett.analyze` 调 `fetch_abstract`(akshare `stock_financial_abstract` 摘要) + `fundamentals.fetch`(akshare 三大表)。akshare 东财出口 IP 被封→优质筛选口径2 超时(80只×15-20s/8worker≈150-200s，前端75s+90s 双超时)。tdx `get_company_info` "财务分析"类别 TCP 直连不受封影响，实测返回完整三大表摘要+财务指标多年序列。
2. **spot 全市场快照**：`collector.collect_spot` 走新浪 `stock_zh_a_spot` ~60s。tdx `get_security_quotes` 批量取价+`get_security_list` 分页取 name，更快且免 key。

## 目标

- 财报：tdx 解析"财务分析"文本产出兼容 `fetch_abstract`/`fundamentals.fetch` 的双结构，buffett 完全脱 akshare 财报依赖；优质筛选口径2 首次 cache-miss 从 >75s 降至 tdx 单只 ~1-2s(80只×2s/8worker≈20s)。
- spot：tdx 批量取价 + name 映射分页缓存，collect_spot 脱新浪，全市场 ~5200 股 ~65 批 ~10-20s。
- akshare 降为备援（tdx 失败时兜底），不删除（保留熔断/降级链）。

## 子系统 A：财报 tdx 解析器

### A1. 文本结构（实测，600519）

`get_company_info(code, "财务分析")` 返回 content 文本，按 `【N.标题】` 分块：

- `【1.财务指标】` → `【主要财务指标】`/`【偿债能力指标】`/`【运营能力指标】`/`【盈利能力指标】`/`【发展能力指标】` 各一表
- `【2.报表摘要】` → `【资产负债表摘要】`/`【利润表摘要】`/`【现金流量表摘要】` 各一表
- `【3.异动科目】`/`【4.环比分析】`（本轮不解析）

每个表是制表符绘制的宽表：
```
┌───┬───┬───┐
｜指标｜ 2026-06-30｜ 2025-12-31｜...
├───┼───┼───┤
｜净利润(元)｜ 445.1688亿｜ 823.2007亿｜...
└───┴───┴───┘
```
- 行=科目/指标，列=报告期(YYYY-MM-DD，最新在前)
- 数值后缀"亿"/"万"，空值为"-"，负数带负号
- 每表前两行：表头行(财务指标｜报告期...) + 分隔行；之后数据行

### A2. 解析器设计

新函数 `data/fundamentals.py` 内（财报归 fundamentals 域，不新建模块）：

```python
def parse_tdx_financial(code: str) -> dict[str, pd.DataFrame | None]:
    """解析 tdx '财务分析' 文本。返 {abstract, balance, cashflow, profit}。
    
    - abstract: 摘要宽表(行=指标,列=报告期,首列'指标')，兼容 buffett._row_pairs
    - balance/cashflow/profit: 三大表(行=报告期,列=科目,含'报告期'日期列)，
      兼容 buffett._pick_col_sum/_pick_row_fields（tdx 摘要为'科目×报告期'宽表，
      需转置为'报告期×科目'+加'报告期'列）
    各块缺失/解析失败→对应 None，不崩。
    """
```

解析步骤：
1. `get_company_info(code, "财务分析")` 取 content；`ok=False` 返全 None。
2. 按 `\r\n` 分行，`【...】` 标记定位各表起点。
3. 表内：跳过 `┌/├/└` 分隔行，`｜` 列分隔；首数据行=表头(报告期)，后续=科目行。
4. 数值解析 `_parse_cn_amount(s)`：`"445.1688亿"`→44516800000.0，`"57.0895万"`→570895.0，`"-"`/空→None，负数→负。
5. abstract：合并所有"财务指标"子表为单宽表(指标列+报告期列)，去重(同指标名多表只留首次)。
6. balance/cashflow/profit：取对应"报表摘要"子表宽表(科目×报告期)→`.T` 转置为(报告期×科目)→首列改名"报告期"。

### A3. 单位解析

复用 `screener/smart_money._parse_cn_amount` 的逻辑（提取到 `data/fundamentals.py` 内联或共享 utils；本轮内联避免跨域耦合）。处理：亿×1e8、万×1e4、纯数字、`-`→None、含 `%` 去符号。

### A4. 主源切换

**`buffett.fetch_abstract(code)`**（line 140）改造：
```python
def fetch_abstract(code):
    # 1. 缓存命中
    df, status = _cache_get(code, allow_stale=False)
    if status == "hit": return df, False
    # 2. tdx 主源
    parsed = parse_tdx_financial(code)  # 一次调用含全部
    if parsed:
        _cache_set_abstract(code, parsed["abstract"])  # 缓存
        # 同步缓存 balance/cashflow/profit 供 fundamentals.fetch 命中
        for s in ("balance","cashflow","profit"):
            if parsed[s] is not None: _cache_set(code, s, parsed[s])
        _note_fetch(True)
        return parsed["abstract"], False
    _note_fetch(False)  # tdx 失败计熔断
    # 3. akshare 备援(原逻辑)+ stale
    ...
```

**`fundamentals.fetch(code, source)`** 改造：缓存命中→返；否则先试 tdx `parse_tdx_financial`（一次解析缓存全部 source，命中即返）→akshare 备援。

关键：**tdx 一次调用含 abstract+三大表**，解析后**分解缓存为4个 source**（abstract/balance/cashflow/profit），后续 `fundamentals.fetch(code, source)` 与 `fetch_abstract` 命中缓存秒回——与现有 `_cache_get`/`_cache_set` 7天TTL 完全兼容，无需改缓存 schema。

### A5. 熔断复用

`_note_fetch`/`akshare_blocked()` 不变。tdx 失败也经 `_note_fetch`（tdx 失败通常是服务器全不可用，akshare 同样会失败，熔断后 quality 口径2 跳 buffett 省 deadline）。但注意：tdx 失败≠akshare 被封，熔断语义微调——`_note_fetch` 记 tdx 失败后 `akshare_blocked()` 仍会熔断跳 buffett，此时 buffett 完全无源。spec 保留此行为（tdx 都挂时跳 buffett 合理，spot 估值代理接管），但 `_BLOCK_WINDOW` 不变。

## 子系统 B：spot 全市场 tdx 主源

### B1. name 映射

新函数 `data/collector.py`（或 `pytdx_client.py`）：
```python
def tdx_name_map() -> dict[str, str]:
    """全市场 code→name。get_security_list(market, start) 分页(每批1000),
    过滤6位股票代码(0/3/6开头个股;排除指数395xxx/基金/债券/北交8开头待定)。
    深市~23999+沪市~27691条→过滤后~5200股。返回 {code:name}。"""
```
- 缓存：模块级 dict + 时间戳，TTL 7天（name 几乎不变）；冷启动/refresh 时重建。失败返空 dict（不崩，spot 标 name 缺）。
- 不入库（name 非行情，内存即可；stock_spot 表 name 字段由 collect_spot 写入时携带）。

### B2. 价格批量

`collect_spot` 改造（个股路径）：
1. `tdx_name_map()` 取 name 映射。
2. `pytdx_client.get_quote(all_codes)` 批量(≤80/批)取 price/last_close/open/high/low/vol/amount/b_vol/s_vol/五档。
3. name 从 name_map 补；change_pct = price/last_close-1 算。
4. 落 stock_spot（NaN→None 复用 `_to_records`）。
5. tdx 全不可用→新浪 `stock_zh_a_spot` 备援(原逻辑)。

### B3. 板块/ETF spot

本轮**聚焦个股 spot**（5200股，痛点所在）。板块 spot（`stock_board_*`）走 akshare/THS 现状不变（板块是另一套采集逻辑，tdx 板块行情接口成分股映射不直接，留后续）。ETF spot：ETF 代码(1/5开头)在 get_security_list 里，可同批取价，name 同 name_map 补——spec 纳入（与个股同路径，零额外成本）。

### B4. refresh_all 集成

`collector.collect_spot` 在 `refresh_all` 被调。改 tdx 主源后，refresh 流程不变（collect_spot 内部源切换透明）。name_map 重建挂 refresh（首次/每7天）。

## 数据流

```
refresh_all → collect_spot → tdx_name_map(分页缓存) + get_quote(批量) → stock_spot
                            └失败→ 新浪 stock_zh_a_spot 备援

quality/buffett → fetch_abstract → parse_tdx_financial(一次) → 缓存4 source → 返 abstract
                                  └失败→ akshare 摘要备援 → stale
                 → fundamentals.fetch(source) → 缓存命中(由 parse 预填) → 返
                                              └miss→ parse_tdx_financial → 缓存 → 返
```

## 错误处理

- tdx 服务器全不可用：`_get_api` 返 None，各接口返空/None；spot 降级新浪，财报降级 akshare。
- tdx 解析失败（文本格式变动/空 content）：`parse_tdx_financial` 返全 None，走 akshare 备援，不崩。
- 单位解析异常：`_parse_cn_amount` try/except 返 None。
- NaN→None：所有数值出口 `_nan`/`_to_records` 守卫（防 allow_nan=False 500）。

## 测试策略

- `parse_tdx_financial`：用真实 600519 文本快照(存 tests/fixtures)做 golden test，断言 abstract 含"净利润/营业总收入/加权净资产收益率"行、balance 含"资产总额/负债总额/股东权益合计"、cashflow 含"经营活动现金净额"、报告期降序、数值单位正确(亿/万/None)。另测空 content/格式残缺→全 None。
- `tdx_name_map`：mock `get_security_list` 返混合(指数+股票+基金)，断言过滤后只留6位股票 code、name 映射正确、空返{}。
- `fetch_abstract`/`fundamentals.fetch` 主源切换：mock `parse_tdx_financial` 成功→不走 akshare；mock 返 None→走 akshare 备援(原 test 适配)。
- `collect_spot`：mock `get_quote`+`tdx_name_map`，断言 stock_spot 行含 name+price+change_pct；mock tdx 空→走新浪备援。
- 回归：现有 `tests/test_buffett_value.py`(15) mock 三大表+摘要降序，tdx 解析产出须兼容(转置后行=报告期降序)；`tests/test_fundamentals.py` 适配；`tests/test_collector.py` spot 路径适配。

## 改动文件

- `data/fundamentals.py`：加 `parse_tdx_financial`/`_parse_cn_amount`；`fetch` 改 tdx 主源+分解缓存。
- `data/buffett.py`：`fetch_abstract` 改 tdx 主源（调 parse_tdx_financial 分解缓存）。
- `data/collector.py`：加 `tdx_name_map`；`collect_spot` 改 tdx 主源+新浪备援。
- `data/pytdx_client.py`：可能加 `get_security_list_paged` 包装（分页拉全市场）。
- 测试：新增 tdx 解析/name_map/主源切换测试；适配现有 buffett/fundamentals/collector 测试。
- CLAUDE.md/README：财报+spot tdx 主源化文档同步。

## 不做

- 资金流/龙虎榜席位/高管/限售：tdx 无结构化，保持 THS/finshare/akshare。
- 板块 spot：另套逻辑，留后续。
- 研报/千股千评：留后续小迭代。
- IC 透明度报告/评级校准/权重回归：独立 spec。

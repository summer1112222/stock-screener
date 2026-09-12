# ETF/QDII 条件筛选器设计

> 日期：2026-09-13
> 路径：`docs/superpowers/specs/2026-09-13-etf-condition-screener-design.md`
> 状态：已确认设计，待实施计划

## 背景与动机

仓库已有两条筛 ETF/QDII 的路：

- **实时筛选 tab**（`screen`，category 选 `ETF`）：用 `ETF_FIELDS_CAT` 字段 + 运算符动态条件筛，但字段只覆盖 `etf_spot` 量价（涨跌幅/成交额/换手率/最新价 + 派生活跃度/动量），**没有规模/溢价/估值这些维度**。
- **ETF/QDII 筛选 tab**（`etfscreen`）：有估值分位/质量(规模/费率/跟踪)/QDII 溢价/量价因子，但**固定长/短两个清单，不能自由组合条件**。

用户需求（brainstorming 已确认）：
1. 把 etfscreen tab 升级成**完全通用条件筛选器**（像实时筛选那样字段+运算符+值，多条 AND 过滤，筛完可选排序维度）。
2. **接线 csindex 指数估值分位**，使"估值分位"成为可筛字段。
3. 费率/跟踪误差无可靠源 → 本次**暂不纳入**。
4. 现有长/短清单保留，tab 内两种模式切换。

路径：architectural。方案选定为**方案 1**——在 `screener/etf_screen.py` 内加 `etf_screen_filter()`，复用现有 `_fetch_*`/量价因子/缓存/名称回退/数据诚实抽象，前端 etfscreen tab 加"条件筛选"模式。

## 范围

**做：**
- `_fetch_index_valuation` 接线（csindex PE 分位，code→指数映射 + 名称兜底）
- 新入口 `etf_screen_filter(conditions, sort, asc, limit, universe, days, codes)` 与 `etf_screen_rank` 并存
- 新字段目录 `ETF_FILTER_FIELDS`（含字段来源标注）+ 字段目录路由
- 扩展 `/api/etf-screen` 加 `mode=filter`/`conditions`/`sort`/`asc`（向后兼容）
- 前端 etfscreen tab 加"条件筛选"模式（字段/运算符动态下拉 + 多条件行 + 排序）
- 扩展 `tests/test_etf_screen.py`（全 mock 不触网）

**不做：**
- 费率/跟踪误差接线（无可靠源）
- `etf_spot` 表新增列（硬约束：规模/溢价/估值不落库）
- 动现有长/短清单语义
- 动 `engine.py`/实时筛选 tab

## 合规硬约束

本项目是数据筛选/回测研究工具，非投资咨询。本功能必须守住：
- **不荐股、不出实时买卖点、不承诺收益、不自动下单**。
- `etf_spot` 表**不新增列**——规模/溢价/估值分位仍经 `_fetch_*` 抽象按需取，绝不读不存在的表列。
- 措辞全程"条件机械筛选观察清单/机械排序/非荐股非买卖信号"，挂 `cand_disclaimer`。
- 数据诚实：任何 `_fetch_*` 失败/无数据 → `None`（该维度中性/不判门槛），不崩不伪造。

## 数据源现状与约束

| 数据 | 现状 | 可筛？ |
|---|---|---|
| 涨跌幅/成交额/换手率/最新价 | `etf_spot` 表 | ✅ 已在库 |
| 规模 fund_scale | `fund_etf_spot_em` 总市值/1e8，一次全市场拉，300s 缓存 | ✅ |
| QDII 溢价 premium | `fund_etf_spot_em` (最新价-IOPV)/IOPV | ✅ |
| 估值分位 | `_fetch_index_valuation` 现返 `None`（csindex 未接线） | ⚠️ 本次接线 |
| 量价因子(动量/波动/量能) | `etf_daily` 历史表 | ✅ 需先 fetch 历史 |
| 费率/跟踪误差 | 无可靠源 | ❌ 本次不做 |

Phase 0 实测：`stock_zh_index_value_csindex(symbol=指数code)` 可达（True, rows=20），是估值分位主源。

## 数据接线：csindex 指数估值分位

**目标**：`_fetch_index_valuation(code)` 从返 `None` 变成真能返回 `{'pe_pct': 0..1, 'div_yield': float|None}`。

**code→指数映射**（3 层，成本递增、覆盖递增）：
1. **手动映射表 `_INDEX_MAP`**：覆盖主流宽基/行业/QDII（沪深300→000300、上证50→000016、中证500→000905、中证1000→000852、上证综指→000001、创业板指→399006、科创50→000688、恒生指数→HSI、纳斯达克100→NDX、标普500→SPX、日经225→N225 等，约几十条可维护）。映射表内 → 返回分位。
2. **名称模糊兜底**：未在映射表的，用 ETF 名含指数名近似匹配（`510300 华泰柏瑞沪深300ETF` → 含"沪深300" → 000300）。兜底成功 → 返回。
3. 仍找不到 → **诚实 `None`**（该 code 估值维度中性）。

**性能关键**：估值分位是**指数层面**的，同一指数多只 ETF 相同。**按 code→指数去重**，一个指数只拉一次 `stock_zh_index_value_csindex`，映射到名下所有 ETF；缓存 300s。候选子集（先按规模/溢价/量价粗筛后的几十只）逐指数拉。**绝不全市场逐个拉**。

**数据诚实**：映射失败/源失败/无分位 → `None`，与现有 `_fetch_qdii_premium` 完全同构。

## 条件引擎

**新入口**：
```python
def etf_screen_filter(conditions, sort=None, asc=False, limit=50,
                      universe="ETF", days=365, codes=None) -> dict
```
与 `etf_screen_rank` 并存，互不影响。

**`conditions` 参数**：复用 `/api/screen` 同款格式——`[{"field": key, "op": gt/lt/gte/lte/topn, "value": x}, …]`，多条 **AND** 过滤。

**字段目录 `ETF_FILTER_FIELDS`**（含来源标注，供前端动态渲染）：
| key | label | 来源 |
|---|---|---|
| change_pct | 涨跌幅(%) | etf_spot |
| turnover_amount | 成交额(元) | etf_spot |
| turnover_rate | 换手率(%) | etf_spot |
| latest_price | 最新价(元) | etf_spot |
| fund_scale | 规模(亿) | fund_etf_spot_em |
| premium | QDII溢价 | fund_etf_spot_em |
| valuation_percentile | 估值分位 | csindex(本次接线) |
| momentum_5 / momentum_20 / volatility_20 / vol_corr_20 | 量价因子 | etf_daily 历史 |

**过滤流程**：
1. 取候选 code 集（全市场 `etf_spot`，或 `codes` 限定）。
2. 按需字段**先批量补**：规模/溢价一次全市场拉；估值分位只对候选集按指数去重拉。
3. 逐条 AND 应用 `conditions` 过滤。
4. 按 `sort`/`asc` 排序——排序字段缺失该维度（None）→ 排最后。
5. `limit` 截断 + `_to_record` 净化 + `_display_name` 名称回退。

**缺失诚实**：某 code 缺估值 → 该估值条件判该 code 为 None（**不误筛掉**，标 skipped），而非当 0。

**缓存**：复用 `_CACHE`，键含 `(universe, tuple(conditions), sort, asc, limit, days, tuple(codes))`，30s TTL。

**合规**：挂 `cand_disclaimer`，措辞"条件机械筛选观察清单，非荐股非买卖信号"。

## 接口 + 前端

**后端路由**——扩展现有 `/api/etf-screen`，加条件参数（向后兼容，现有长/短清单调用不破坏）：

```
GET /api/etf-screen?mode=long|short|filter
                  &universe=ETF|QDII
                  &conditions=<JSON 数组>   # mode=filter 时生效
                  &sort=<字段> &asc=<bool>
                  &limit= &days= &codes=
```

- `mode=filter` → 走 `etf_screen_filter`；`long`/`short` → 走现有 `etf_screen_rank`（不变）。
- 返回结构对齐现有：`{universe, mode, count, limit, ts, items}`，每项 `{code, name, score/sort 值, fund_scale, premium, valuation_percentile, factor_scores, source}` + `_to_record` 净化 + `_display_name`。
- 新增**字段目录路由**：复用 `/api/fields` 加 `etf_filter` 分类（与实时筛选 tab 的字段目录同源），前端动态渲染新字段下拉。

**前端 etfscreen tab**——两种模式切换（保留现有长/短清单）：
- 现有：`universe` + `mode(long/short)` + `limit/days` 下拉。
- 新增**"条件筛选"模式**：`universe` + `limit/days` + 动态条件行列表（每行：字段下拉 + 运算符下拉 + 值输入，"添加条件"按钮可删），`sort` 下拉（排序字段 + 升降序）。
- 字段/运算符下拉**从字段目录路由动态渲染**（与实时筛选 tab 同模式，不硬编码）。
- 结果表渲染：code/name/规模/溢价/估值分位/量价因子列。
- 状态保持（`.tab-panel[data-tab]` 显隐不重 fetch），30s 缓存命中秒回。

## 测试

`tests/test_etf_screen.py` 扩展（全 mock 不触网）：
- `_fetch_index_valuation` 接线三态：映射表命中 / 名称兜底命中 / **无映射→None**。
- `etf_screen_filter`：AND 多条件过滤正确、`topn` 运算符、排序（缺失排序字段排最后）、limit 截断。
- 数据诚实：缺估值源 → 该 code 不被误筛掉、不崩。
- 缓存：同条件二次命中不重算源、键含 conditions/sort/asc。
- 后端路由：`mode=filter` 正确转调 + disclaimer。

## 改动文件清单

- `screener/etf_screen.py`：`_fetch_index_valuation` 接线 + `ETF_FILTER_FIELDS` + `etf_screen_filter` + `_INDEX_MAP`。
- `api/server.py`：`/api/etf-screen` 加参数 + `/api/fields` 加 `etf_filter` 分类。
- `web/index.html`：etfscreen tab 加"条件筛选"模式。
- `tests/test_etf_screen.py`：扩展。
- `CLAUDE.md`：同步架构/路由/改动检查清单。

## 未来可能（不在本次范围）

- 费率/跟踪误差可靠源接线。
- IC 校准量价因子权重。
- 估值分位指数映射自动发现（需额外源）。

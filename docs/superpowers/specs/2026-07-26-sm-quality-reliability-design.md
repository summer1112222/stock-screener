# 主力动向 + 优质筛选 · 数据可靠性加固 · 设计文档

**日期**: 2026-07-26
**状态**: 设计已确认，待转实施计划
**范围**: `data/smart_money.py`（采集层备援+stale）+ `screener/smart_money.py`（只读层不变）+ `backtest/quality.py`（口径2降级）+ `api/server.py`（路由不变，响应增 stale/source_health）+ `web/index.html`（三态灯+口径标注）+ `tests/`

---

## 1. 背景与问题

两个模块功能已落地，但**数据可靠性**存在硬缺口：

### 主力动向采集层（`data/smart_money.py`）

1. **北向通道长期灰**：当前主源 `stock_hsgt_individual_em(stock="北向资金")` 与备援 `stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")` 均取"今日排行/实时"口径。**2024-08 起沪深交易所停止披露北向实时流向**（仅保留盘后），东财对应端点返回异常致 akshare 内部 `NoneType` 崩（见 CLAUDE.md AKShare 适配记录）。两主源已下线，**无备援**，北向为唯一长期灰通道。
2. **拉取失败即整通道灰**：高管增减持/限售解禁/龙虎榜/十大股东任一通道当日拉取失败 → `ok=False` → 前端灰灯。即便 DB 有昨日/近 N 日旧数据，前端仍显示"不可用"，用户无法区分"今日暂失败"与"从未采集"。
3. **十大股东 shortlist 漏小盘国家队**：`collect_holders` 候选=成交额前 200，国家队重仓的**小盘/低成交额**股可能落在 shortlist 之外，季频刷新覆盖不全。

### 优质筛选编排层（`backtest/quality.py`）

4. **口径2 buffett 失败即整口径 None**：`_AK_OK=False`（akshare 不可用）或 `analyze_many` 全空时，口径2 分位全 None、`dim_status["2"]="err:..."`，个股失去"价值质量"维度，`min_dims` 门槛可能因此 clamp 到 1，共振排序失真。`stock_spot` 实有 `pe/pb/circulating_market_cap/turnover_rate` 等可作降级代理，未被利用。

### 不解决什么

- 不攻性能（并行采集/load_panel 去重/结果缓存）——属方案 B，本期不做，留作后续叠加。
- 不新增采集通道（大宗交易/股权质押等）——属功能扩展，超出可靠性范围。

---

## 2. 合规边界（不变）

- 本工具是**数据筛选/观察工具，非投资咨询**。stale 旧数据前端显式标注"非实时/数据日期 YYYY-MM-DD"，不构成"实时买卖点"。
- 措辞保持"动向/动作/净额/观察清单/排序/机械归类"，禁"推荐/买入信号/卖点/强势股"。
- 北向"盘后十大成交股"口径前端表头标注，避免与"实时个股净买额"混淆。
- disclaimer 措辞与 `cand_disclaimer` 不变。

---

## 3. 架构与边界

- **不新增表/不新增列**。stale 状态走 `CHANNEL_STATUS`（内存）+ `db.set_meta("sm_stale_<channel>", ...)`（持久化，防容器重启丢失）。北向备援复用 `smart_money_action` 现有列。
- 两层边界不变：采集层 `data/smart_money.py` 取数入库；查询层 `screener/smart_money.py` 只读库不触网；`backtest/quality.py` 只读因子源 buffett/signals/smart_money/candidates 不改。

---

## 4. 主力动向采集层设计

### 4.1 北向备援链

`collect_northbound(date)` 重构为多级备援：

| 级别 | 接口 | 口径 | 出表粒度 | actor | action | amount |
|---|---|---|---|---|---|---|
| 主源(探活) | `stock_hsgt_individual_em(stock="北向资金")` | 实时个股（已下线） | 每股 | "" | 净买入 | 净买额 |
| 备援1(探活) | `stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")` | 实时排行（已下线） | 每股 | "" | 净买入 | 增持市值 |
| **备援2(默认)** | `stock_hsgt_north_acc_flow_in(symbol="沪股通")` + `("深股通")` | **盘后十大成交股** | 20只/日 | "" | 上榜 | 净买额 |
| 降级3(兜底) | `stock_hsgt_north_net_flow_in(symbol="北向")` | 总额（无个股） | 1条 | "北向总额" | 净买入 | 净流入额 |

**策略**：
- 主源/备援1 **已下线**，日常不试（避免死源空等），仅每周一探活一次（meta `north_probe_date`，距上次 ≥7 天才试），看东财是否恢复；恢复则升级回主源口径。
- **默认走备援2** 十大成交股：沪股通+深股通各 10 只，`raw.source="北向十大成交股(盘后)"`，`actor=""` 保证 UNIQUE 去重，`action="上榜"`，`amount` 取净买额列（`_first_col` 候选 `["净买额","买入金额","成交金额"]`）。
- 备援2 失败/空 → 降级3 总额：一条汇总记录 `actor="北向总额"`，`raw.source="北向总额(盘后)"`，至少通道不灰。
- 全部失败 → `ok=False` → 走 §4.2 stale 降级。

**单测**：`test_northbound_fallback_acc_flow` — mock 主源抛 NoneType → 备援2 出 20 条记录、actor=""、action="上榜"、source 标注正确。

### 4.2 全通道 stale 降级

新增辅助函数：

```python
def _stale_fallback(channel: str, err: str) -> tuple[list[dict], bool, str]:
    """拉取失败时查 DB 该通道最近一次有数据日期的全部行作回退数据。
    返回 (旧rows, stale_flag, stale_note)：
      有旧数据 → (旧rows, True, "回退至 <stale_date>（采集失败: <err>）")
      无旧数据 → ([], False, err)   # stale_flag=False 表示真灰，不回退
    stale 标记落 CHANNEL_STATUS[ch].stale/stale_date/last_ok_date +
    db.set_meta("sm_stale_<ch>", "<stale_date>|<err>")。"""
```

- `refresh_today` 里每通道 `fn(date)` 返回 `ok=False` 时，调 `_stale_fallback(ch, err)`：
  - `stale_flag=True`：响应 `counts[ch]=0`、`channels[ch]={ok:True, stale:True, rows:len(旧rows), err:stale_note, at:...}`，**跳过 `db.upsert_rows`**（不入库新行，避免旧日期数据被当新采集覆盖 UNIQUE 与重复）。旧 rows 仅作响应返回供前端展示。
  - `stale_flag=False`：响应 `counts[ch]=0`、`channels[ch]={ok:False, rows:0, err, at:...}`，前端灰灯。
- `CHANNEL_STATUS[ch]` 增字段：`stale`(bool)、`stale_date`(str, 回退数据日期)、`last_ok_date`(str, 最近一次成功采集日)、`stale_note`(str)。
- `db.set_meta("sm_stale_<ch>", f"{stale_date}|{err}")` 持久化，防容器重启后 stale 信息丢失。
- `channel_status()` 读 meta 叠加 stale 态：内存 `CHANNEL_STATUS` 给 source/err/at 细节，meta 给 stale_date/last_ok_date；DB 实况给 rows/date（已有逻辑保留）。

**单测**：`test_stale_degradation_keeps_old_data` — mock `collect_fund_flow` 失败 + DB 有 3 日前资金流行 → stale=True、回退 3 行、last_ok_date 正确、meta 写入。
**单测**：`test_three_state_channel_light` — 绿(ok&!stale)/黄(stale)/灰(!ok&无旧数据) 三态判定正确。

### 4.3 十大股东国家队反向刷新

- 新增常量 `NATIONAL_TEAM_HOLDINGS_SEED`：种子代码 ~30 只，**硬编兜底**（已知国家队重仓大盘股，如沪深300成分里的证金/汇金/社保重仓股；首次部署即有候选，不依赖历史数据）。
- `collect_holders` 候选集 = 成交额前 200 ∪ 种子名单（去重）。逐股拉 `stock_gdfx_free_top_10_em`。
- 学习式扩充：每次成功拉取后，把新命中国家队关键字（`NATIONAL_TEAM` 任一）的 code 并入种子，落 meta `nt_holdings_seed`（JSON 逗号分隔），下次启动加载为种子初值（与硬编常量并集）。**不依赖 `by_actor` 冷启动**（空库无历史时 `by_actor` 本身无数据，不可靠）。
- 季频 60 天跳过逻辑（已有）保留。

**单测**：`test_holders_seed_union` — 种子 5 只 ∪ shortlist 200 只去重后逐股拉（mock），覆盖 1 只仅种子命中、shortlist 外的国家队小盘股。

---

## 5. quality 编排层设计

### 5.1 口径2 buffett 失败降级

`_dim_scores` 口径2 分支（`universe=="stock"`）末尾加降级：

```python
# buffett 主路径失败（_AK_OK=False 或 analyze_many 全空或抛异常）→ spot 估值代理
if status.get("2", "").startswith("err"):
    try:
        pe = pd.to_numeric(df.get("pe"), errors="coerce")
        pb = pd.to_numeric(df.get("pb"), errors="coerce")
        amp = pd.to_numeric(df.get("amplitude"), errors="coerce")
        tr = pd.to_numeric(df.get("turnover_rate"), errors="coerce")
        # pe/pb 负向(越低越便宜→价值)；amplitude 负向(低波动→质量)；turnover_rate 正向(流动性)
        # 不用 circulating_market_cap：负向会引入 size factor 偏差，与"价值质量"口径不符
        comp = (_zscore(-pe) + _zscore(-pb) + _zscore(-amp) + _zscore(tr)) / 4
        pct = _to_pct(comp)
        for c in codes:
            scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
        dims_avail.append(2)
        status["2"] = "ok(降级spot估值代理)"
    except Exception as e:
        status["2"] = f"err:buffett与spot代理均失败:{e}"
```

- `pe/pb/amplitude` 负向（价值+低波动=质量），`turnover_rate` 正向（流动性）。
- **不用 `circulating_market_cap`**：负向 cap 会引入 size factor 偏差（偏好小盘），与 buffett 主路径的 earnings_yield/moat/lroe 语义不符。
- 全 None 列时 `_zscore` 返回全 0（已有守护），分位均 0.5，不崩。
- ETF 口径2 不变（跟踪误差+成交额稳定）。

### 5.2 source_health 摘要

`quality_rank` 返回值增字段 `source_health: {1: <str>, 2: <str>, 3: <str>, 4: <str>}`，内容取自 `dim_status` 的简短口径标签（如 `"ok"`/`"ok(降级spot估值代理)"`/`"ok(仅spot)"`/`"ok(胜率加权)"`/`"err:无历史数据"`）。

**单测**：`test_quality_dim2_spot_proxy_fallback` — mock `buffett._AK_OK=False` + spot 有 pe/pb → 口径2 分位非 None、dim_status 标"降级spot估值代理"、source_health["2"] 正确。

---

## 6. 前端（`web/index.html`）

- `smChannelsHtml` 三态：绿(`ok && !stale`)/黄(`stale`)/灰(`!ok && !last_ok_date`)。黄灯 title 显示"回退至 <stale_date> 数据（今日采集失败: <err>）"。
- stale 行内标：当 `stale_date` 非空时，表头下方加 `<div class="skipped">北向/资金流...: 数据日期 <stale_date>（非今日，采集失败回退）</div>`。
- 北向表头/列标注"(盘后十大成交股口径)"，避免与实时个股口径混淆。
- quality 口径灯已存在，`qsMsg` 增显 `source_health` 文案（各口径是否降级）。

---

## 7. 错误处理（不变 + 补强）

- 沿用 `(records, ok, err)` + 异常不崩 + `_clean`/`_to_float` NaN→None。
- 北向备援链每级 try/except，任一级成功即返回，全失败走 stale。
- stale 回退数据**不入库新行**（仅响应返回），避免旧日期数据被当作新采集覆盖 UNIQUE。
- `_install_http_patch()` 全局东财 UA + 502 退避重试保留，北向盘后接口仍受保护。

---

## 8. 测试 `tests/`

合成数据 mock `db.query_rows`/`db.upsert_rows`/`db.set_meta`/`db.get_meta`，不触网（沿用 `tests/` 模式）：

| 测试 | 覆盖 |
|---|---|
| `test_northbound_fallback_acc_flow` | 主源 NoneType 崩 → 备援2 十大成交股 20 条、actor/action/source 正确 |
| `test_northbound_degrade_to_total` | 备援2 也失败 → 降级3 总额 1 条、actor="北向总额" |
| `test_stale_degradation_keeps_old_data` | 通道失败 + DB 有旧数据 → stale=True 保留旧、last_ok_date 正确、meta 写入 |
| `test_three_state_channel_light` | 绿/黄/灰三态判定 |
| `test_holders_seed_union` | 种子 ∪ shortlist 去重、覆盖 shortlist 外国家队小盘股 |
| `test_holders_seed_learning` | 成功拉取后新命中 code 并入种子、落 meta |
| `test_quality_dim2_spot_proxy_fallback` | buffett 失败 → 口径2 spot 代理分位非 None、dim_status/source_health 标降级 |
| `test_quality_dim2_main_path_intact` | buffett 正常时口径2 走主路径、不被 spot 代理覆盖（防回归） |

---

## 9. 改动检查清单（对齐 CLAUDE.md）

- **不新增表/列** → `models.SCHEMA_SQL`/`TABLE_FIELDS`/`db._BOARD_MIGRATIONS` 无需改。
- 新增采集源（北向十大成交股/总额）→ 复用 `(records, ok, err)` + `_clean`/`_to_float` + 异常不崩；eastmoney 域名受 HTTP patch 保护。
- 路由不变 → `_wrap`/`cand_disclaimer` 不变；响应增 `stale`/`source_health` 字段（前端读，向后兼容）。
- 前端字段/运算符 → 无新字段；`LABEL` 字典无需改；三态灯改 `smChannelsHtml` JS。
- 单测放 `tests/`，合成数据 mock，不依赖网络。
- AKShare 版本适配：北向 `stock_hsgt_north_acc_flow_in`/`stock_hsgt_north_net_flow_in` 需落地实测列名（`_first_col` 候选容错），若 akshare 1.18 改名按既有模式补映射。

## 10. 分期

- **P1**：北向备援链（§4.1）+ stale 降级（§4.2）+ 三态灯前端（§6）+ 4 条单测。最高优先，攻唯一长期灰通道。
- **P2**：十大股东国家队反向刷新（§4.3）+ 2 条单测。
- **P3**：quality 口径2降级（§5）+ source_health 前端 + 2 条单测。

P1 落地后北向即不灰，stale 让全通道"今日暂失败"不再等同"从未采集"——可靠性主收益即得。

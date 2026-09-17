# 设计 spec:三清单互相穿透标注(主力启动信号链)

> 日期:2026-09-18。状态:draft。路径:architectural(brainstorming 产出)。
> 所属分支:`feat/sector-heat-layer`(行业景气层刚上线),本次在其上继续,不新建分支(同仓库个人自用)。

## 一、背景与目标

三大"机械排序观察清单"——**主力动向**(`smart_money`)、**优质筛选**(`quality`)、**次日强势**(`nextday`)——各自独立输出,互不引用对方结论。用户想把它们"真正捏在一起",使一份清单能带到另两环的**排序上下文**,形成一条"主力启动"信号链的观察入口。

**目标:** 三份清单互相**穿透标注**(annotation),不改任何清单的排序签名、min_dims、hits、缓存键、warmup。标注 = 上下文,与 `sector_heat` 同一哲学。

**产品形态(用户选定):** 三清单互相穿透标注。**不新开融合清单**,不做 O(n²) 对端全量重算。

## 二、核心设计决策(承重)

**Ruling 1(穿透走轻量,重评分不嵌入):**
三张清单不能互相对端全量重算——`quality` 全量共振依赖 buffett 财报(tdx/akshare 慢)、`nextday` 全量依赖触网 tdx + 补历史 + 五因子。把它们嵌入对端会 O(n²) 接口风暴/超时。
因此穿透字段**只取对端轻量、可批量、有缓存**的信号:

| 清单(宿主) | 注入字段 | 对端轻量源 | 是否触发重计算 |
|---|---|---|---|
| quality 行 | `mf_phase`(吸筹/洗盘/拉升/出货/观望)+`mf_confidence`+`streak_inflow`/`cum_net`/`margin_accel` | `sm.main_force_phase(code)` + `sm._behavior_batch(codes, days)` | **已实现**(`quality._enrich_main_behavior`,20s 预算+finshare 熔断)。本次**不再改** |
| nextday 行 | 同上 `mf_phase`/`mf_confidence`/`streak_inflow`(命名与 quality 一致) | `sm.main_force_phase(code)` + `sm._behavior_batch(codes, 30)` | 否(DB 只读 + 30s 进程缓存,nextday 已触网,此处不新增) |
| smart_money 行 | `quality_pct`(该股在 quality 共振清单中的分位上下文) | 复用 quality 结果缓存的 resonance rank-pct,按 code 取 | 否(不重跑 buffett) |

**Ruling 2(None 诚实缺失,不进排序):**
任一穿透字段无源(无历史/无主力记录/无共振分位)→ 该字段 `None`,不崩、不伪造零分、**不参与排序**,仅作展示。与 `sector_heat` 的 `None` 语义一致。

**Ruling 3(措辞合规):**
穿透字段是"跨模块机械标注上下文,非买卖信号"。`/api/quality`、`/api/nextday-strong`、`/api/smart-money/today`、`/api/smart-money/top` 各自仍挂 `cand_disclaimer`。字段名(如 `main_phase`/`quality_pct`)是机械状态描述,不用"主升浪/买入"等建议词。

**Ruling 4(反向重评分用措辞替代,不硬算):**
quality↔nextday 两端的**完整评分互不嵌入**。需要时用"见对端清单"措辞或轻量代理(如 nextday 行的质量门槛用 signals 触发,而非 quality 全量),避免重算。本次 spec **不实现** quality↔nextday 的双向全量评分互引。

## 三、改动范围

新增/修改文件(全部在既有领域模块内,不新增表):

| 文件 | 改动 |
|---|---|
| `screener/sector_heat.py` | **无**(纯函数已具备 attach;仅 nextday 复用) |
| `screener/nextday.py` | **A**:结果经 `sector_heat.attach_sector_heat` 附 `sector_heat`/`policy_hit`;**B2**:结果附 `mf_phase`/`mf_confidence`/`streak_inflow`(复用 `_enrich_main_behavior` 套路,仅 passed_items) |
| `backtest/quality.py` | **B1 已实现,不改**;仅加 B3 的 `_INDEX`(code→res_pct)写入 |
| `screener/smart_money.py` | **B3**:`top_by_amount`/`today_list` 行附 `quality_pct`(lazy import 读 quality 索引,不重跑 buffett) |
| `tests/test_nextday.py`、`tests/test_smart_money.py` | 各增穿透标注覆盖(纯 mock,不触网);`tests/test_quality.py` 增 `_INDEX` 写入断言 |
| `CLAUDE.md` | 更新三者路由字段说明 + 穿透标注检查清单 |

**不新增:** 表、采集源、API 路由(字段附在既有响应上)。

## 四、接口与数据流(穿透注入点)

### A. sector_heat 下沉到 nextday(前置)
- `nextday_strong_rank` 返回前,对 `passed_items`(或全 items)调:
  `sector_heat.attach_sector_heat(rows, fund_flow, board_rows, member_map)`
- `fund_flow` = `db.query_rows("sector_fund_flow", where="sector_type='行业' AND indicator='今日'")`
- `board_rows` = `db.query_rows("industry_board")`
- `member_map` = `_board_members_batch(scored_board_names)`(仅被评分板块,有界单次,复用 quality 接线套路)
- 失败 → `member_map={}`,诚实 None。

### B1. quality 行附主力阶段
**已实现**(`backtest/quality.py._enrich_main_behavior`,20s 预算+finshare 熔断秒退),本次不改。对外字段:`mf_phase`/`mf_confidence`/`behavior_group` + `streak_inflow`/`streak_outflow`/`cum_net`/`margin_accel`/`north_cum`。

### B2. nextday 行附主力阶段
- 对 `nextday_strong_rank` 的 `passed_items`(或 top N)注入 `mf_phase`/`mf_confidence`/`streak_inflow`。
- 复用 quality 的套路:`sm._behavior_batch(codes, 30)` 批量取 streak + `sm.main_force_phase(code, 30)` 取 phase。
- **命名与 quality 完全一致**(`mf_phase`/`mf_confidence`/`streak_inflow`),保证跨清单字段可对齐。
- 时间预算:复用 `_enrich_main_behavior` 的 20s 预算 + finshare 熔断语义(不阻塞 nextday 响应)。缺失 → None。
- **不新增触网**:nextday 已触网(tdx get_quote/补历史),主力阶段走 DB 只读 + 30s 缓存。

### B3. smart_money 行附 quality pct(**唯一新架构点**)
- `top_by_amount`/`today_list` 行附 `quality_pct`:该股在 quality 共振清单中的分位(0-1,越大越靠前)。
- **循环依赖处理(关键)**:quality(backtest)已 lazy-import `screener.smart_money`;smart_money 不能顶层 import backtest.quality。
  - 做法:`quality_rank` 产出后在模块级 `_INDEX` 刷新一张 `code→res_pct` 只读索引(随 `_RESULT_CACHE` 写入,不进返回值避免序列化);smart_money 提供 `_attach_quality_pct(rows)`,内部 **lazy import** `backtest.quality` 读该索引,读不到 → None。
  - `res_pct` 用精排池 `_to_pct` 后的 resonance 分位(与 `_final` 里的 rp 同源),保证与 quality 排序一致。
- **不重跑 buffett**:仅读已缓存索引。索引空 → None(标注,不进排序)。

## 五、失败/降级语义

- 任何穿透源查询异常 → 该字段 None,不崩(与 sector_heat 的 try/except 一致)。
- 无历史/无主力记录 → None。
- 所有穿透字段**只读展示 + 可选排序上下文,不改变宿主清单的共振签名/min_dims/hits/缓存键**。

## 六、测试

- `tests/test_nextday.py`:新增 → `passed_items` 含 `sector_heat`/`policy_hit`(A)+ `mf_phase`/`streak_inflow`(B2);缺历史/缺主力记录 → None 不崩(纯 mock db,不触网)。
- `tests/test_quality.py`:新增 → `quality_rank` 产 `_INDEX`(code→res_pct),含 main 里 code,取值与排序一致。
- `tests/test_smart_money.py`:新增 → `top_by_amount`/`today_list` 行含 `quality_pct`;索引空 → None(不重跑 buffett,mock `_attach_quality_pct` 或索引)。
- 全 mock `db.query_rows`/`main_force_phase`/`_behavior_batch`/`_board_members_batch`/quality 索引,不依赖网络。

## 七、合规红线(全程保持)

不荐股、不输出实时买卖点、不承诺收益、不自动下单;无投资咨询资质。措辞"筛选/排序/观察清单/机械标注上下文",不用"推荐/买入/卖出/主升浪"。字段名机械化(`main_phase`/`quality_pct`),挂 `cand_disclaimer`。个人自用放松仅限措辞风格,disclaimer 管道不拆。

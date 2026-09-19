# 三清单排序因子诊断（先诊断再改）

> 主题：主力动向 / 优质筛选 / 次日强势 三模块排序因子优化
> 路线：先诊断再改（用户选定）——用真实 `list_track` 前视收益评估现有排序，定位失灵点/因子贡献/穿透信号区分度，再据报告定排序改造方案。
> 合规：诊断只输出**历史收益统计事实**，不预测、不荐股、不产出买卖点。措辞"机械历史统计，非预测"。沿用 cand_disclaimer/bt_disclaimer 管道。

## 1. 目标

回答三个问题，作为第二阶段"改什么"的数据依据：

1. **现有排序灵不灵**：各清单头部 vs 整体前视收益是否跑赢。
2. **哪个因子在拖后腿**：五因子/口径分档收益是否单调，找失效因子。
3. **穿透信号有无区分度**：`sector_heat`/`mf_phase`/`quality_pct` 高分与低分的后续收益是否分层——为"穿透信号进排序"提供依据。

## 2. 数据现状（约束）

`list_track` 截至 2026-09-17/18：

| 模块 | mode | 行数 | 日期范围 | 诊断可用性 |
|------|------|------|----------|-----------|
| nextday | score | 499 | 09-09~09-17（7 交易日） | ✅ 主诊断对象 |
| nextday | strict | 70 | 同上 | 偏少，仅参考 |
| quality | strict | 14 | 09-09~09-18 | ⚠️ 样本枯竭，仅结构诊断 |
| smart_money | — | 0 | — | ❌ 需先补追踪 |

`list_track` 列含 `rank/score/ret_k1/ret_k3/ret_k5/meta_json`（meta_json 存 `factor_scores`/`step_status`/`confidence` 等）。`fill_returns` 已幂等回填 T+1 open 买入 / T+1+k close 卖出。

## 3. 阶段一：数据基建 + 现有数据诊断

### A. smart_money 补 `list_track` 追踪（today_list 头部）

给 smart_money 主力清单建立历史收益追踪，使其进入阶段二统一诊断。

- `backtest/tracker.py`：
  - `DEFAULT_PARAMS` 加 `"smart_money"`，对齐 `screener/smart_money.today_list` 实际签名；**头部 limit 定 20**（主力榜单头部观察清单）。
  - `_meta_payload` 对 smart_money 把穿透字段 `quality_pct`（及 `sector_heat`/`policy_hit` 若有）压入 meta_json，供后续穿透分层诊断。
- `api/server.py` `/api/smart-money/today`：命中 `is_default_params("smart_money", params)` 时，对头部前 20 只 `record_list("smart_money", "today", date, rows)`。复用 tracker 的 `_insert_ignore` 幂等。
- 自此 smart_money 每日清单 + T+1+k 前视收益开始积累。

### B. nextday 收益诊断（现有 7 日样本，主诊断）

新增 `scripts/diagnose_ranking.py`（一次性研究脚本，输出报告，不入采集流程、不改 API/前端）。复用 `tracker.summary` + `backtest/eval.py` 的 IC/分层工具。

1. **头部 vs 整体**：nextday score 样本，取每日 top10/top20 与整体，比较 `ret_k1/k3/k5` 中位数/胜率——头部是否跑赢整体（排序有无 alpha）。
2. **五因子分档单调性**：从 meta_json 解 `factor_scores`，将 `mom_5_1`/`rel_strength`/`sr_10`/`close_vol_corr`/`liq_turnover` 各按分位分 5 档，看收益是否单调——定位拖后腿因子（IC 低/方向错）。
3. **穿透标注分层**：meta_json 的 `sector_heat`/`policy_hit`/`mf_phase`/`streak_inflow` 与 ret 分层——穿透信号有无区分度。

样本评估：7 日 × ~71 只/日，top10 累积 ~70 样本、因子分档 ~500/5=100/档，足够做分层统计。

### C. quality 结构诊断（样本枯竭）

7 天仅 14 只 strict —— 通过率过低本身就是失灵信号。分析 `min_dims`/`dim_thresh`/`strict_quality` 门槛组合如何压榨样本，定位枯竭根因。quality 现有追踪保留，继续积累。

## 4. 阶段二：积累期后统一三模块收益分档诊断

等 smart_money（新追踪）与 quality 攒够 ~7-10 个交易日样本后：

- 三模块统一跑：头部 vs 整体、按因子分档、穿透字段分层，复用 `backtest/eval.py` 的 IC/分档。
- 据阶段一 + 阶段二报告，另立 spec/plan 定排序改造（提 IC/去噪声/穿透进排序）。

## 5. 诊断产出

`scripts/diagnose_ranking.py` 运行输出一份报告：
- 三清单排序有效性结论（头部是否跑赢整体、胜率/中位数）
- 因子贡献度（各因子分档收益是否单调、IC 符号）
- 穿透信号区分度（sector_heat/mf_phase/quality_pct 分层）
- quality 样本枯竭根因

报告驱动第二阶段"改什么"。

## 6. 改动文件清单

- `backtest/tracker.py`：`DEFAULT_PARAMS` + `_meta_payload`（smart_money）
- `api/server.py`：`/api/smart-money/today` 命中默认参数转调 `record_list`
- `scripts/diagnose_ranking.py`（新增）：三模块收益诊断脚本
- `tests/`：同步 `test_tracker*.py`；新增诊断脚本单测（mock `list_track`/`stock_daily`，不触网）

## 7. 合规约束

- 诊断/追踪只落**历史收益统计事实**，措辞"机械历史统计，非预测、非荐股"。
- 追踪记录入口沿用 `is_default_params`，仅默认参数组合落库，防任意参数污染统计。
- 阶段一 B/C 不动排序签名/权重/缓存键——纯只读诊断，零排序副作用。
- 新增 `smart_money` 追踪不改主排序、不触网，仅按需读 `list_track`。

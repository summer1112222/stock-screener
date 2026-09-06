# 优质筛选：数据可信度与风险门槛设计

**日期**：2026-09-05  
**状态**：待用户审阅  
**范围**：`backtest/quality.py`、`api/server.py` 的 `/api/quality` 参数透传、`tests/test_quality_confidence.py` 及相关既有测试。  
**目标**：在现有五口径共振之上，减少因数据缺失、财务红旗、单日资金脉冲和高风险结构造成的虚高排序。

## 1. 设计原则

- 仍是公开数据的机械排序观察清单，不输出买卖建议，不承诺收益。
- 不新增采集源、数据库表或列；只读现有 `stock_spot`、`etf_spot`、`*_daily`、`smart_money_action`、`fundamentals_cache` 与既有因子模块结果。
- 不以“增加因子数量”代替有效性验证；每个新判断必须有可解释字段和测试。
- 缺失数据与负面数据严格区分：缺失降低可信度，负面风险触发门槛或惩罚。
- 盘口微结构继续只作盘中流动性观察，不进入长期质量分。

## 2. 新筛选流水线

```text
tradable 预筛
  → 数据可信度评估
  → 硬质量门槛（红旗/数据最低要求）
  → 五口径分位与共振
  → 风险惩罚调整 resonance
  → 行业/相关性/容量组合约束
  → 输出 main、by_dim、confidence、risk_flags、warnings
```

现有 `step` 与五口径计算保留；新增层默认开启，可通过 API 参数关闭仅用于对照研究，不改变默认严格模式。

## 3. 数据可信度层

新增内部函数 `_data_confidence(df, close, dim_scores, ...)`，逐标的返回：

```json
{
  "score": 0.0,
  "level": "high|medium|low",
  "components": {
    "spot": 1.0,
    "history": 1.0,
    "fundamental": 1.0,
    "flow": 1.0,
    "research": 1.0
  },
  "warnings": []
}
```

### 3.1 评分规则

- `spot`：代码、价格、成交额、更新时间齐全为满分；缺价格或成交额为 0。
- `history`：历史有效交易日 `<20` 为 0；20–59 为 0.5；`>=60` 为 1。口径 1/4 不可用时同时写 warning。
- `fundamental`：个股财报结果命中且报告期有效为 1；仅 spot 估值代理为 0.5；ETF 不适用，按 1 处理并在组件中标记 `na`。
- `flow`：存在至少 3 个有效行为日且非单日脉冲为 1；仅 spot 当日资金流为 0.5；无资金数据为 0。
- `research`：个股存在至少 2 条近期研报为 1；1 条为 0.5；无研报为 0. ETF 标记 `na`。

整体 `score` 为适用组件的加权平均：历史 0.25、基本面 0.30、资金 0.20、spot 0.15、研报 0.10。组件不可适用时不进入分母；不能因单个可用组件被重复放大到 1。

- `high`：`score >= 0.75`；
- `medium`：`0.50 <= score < 0.75`；
- `low`：`score < 0.50`。

默认严格主清单要求 `score >= 0.50`；低可信度标的只能进入 `by_dim`，不进入 `main`。若可用数据整体不足，返回 `selection_mode="degraded"` 并保留清晰 warning，不静默制造结果。

## 4. 硬质量门槛

新增 `_quality_gate(item, confidence, value_result, behavior)`。个股默认启用，ETF 仅执行适用的交易性门槛：

- 财务红旗：商誉/净资产 >30%、资产负债率 >75%、`fcf_to_netincome <0.3`，或所有者收益持续为负 → `hard_reject`。
- 高杠杆 ROE：杠杆调整 ROE 显著低于报告 ROE，且权益乘数 >3 → `risk_flag`；默认不直接剔除，但进入风险惩罚。
- 数据质量：spot 缺价格/成交额、历史不足 20 个有效交易日、所有可用口径均来自单一数据源 → `hard_reject` 或标记不可进入严格主清单。
- 资金脉冲：资金流仅有 1 个有效日且净流入极端 → 不得单独贡献“资金质量”通过；必须同时满足至少一个非资金口径。
- 估值异常：PE/PB 缺失不判定便宜；亏损股不因负 PE 取得价值分。

所有拒绝原因写入 `risk_flags`，不靠一句总分解释。

## 5. 风险惩罚与排序

保留现有共振 `hits` 作为主要门槛，但将最终排序分拆：

```text
adjusted_resonance = resonance × confidence_multiplier - risk_penalty
```

- `confidence_multiplier`：high=1.0、medium=0.85、low=0.65。
- `risk_penalty`：高杠杆 1.5；FCF/盈利质量弱 1.5；高波动/下行风险处于横截面后 20% 2.0；单日资金脉冲 1.0；多个风险可叠加，上限 5.0。
- `hits` 不因惩罚被伪造改变，保留原始事实；新增 `raw_resonance`、`adjusted_resonance`。
- 主清单先过滤硬门槛，再按 `(hits, adjusted_resonance, confidence_score)` 降序；组合约束仍最后执行。
- `resonance_mode` 的 `greedy/penalize` 保留，风险调整作用于两者结果之后。

## 6. 输出契约

`quality_rank` 返回：

- 顶层新增 `confidence_summary`：high/medium/low 数量、低可信度剔除数量；
- 每个 `main`/`by_dim` 项新增：
  - `raw_resonance`
  - `adjusted_resonance`
  - `data_confidence`
  - `confidence_level`
  - `risk_flags`
  - `warnings`
  - `hard_gate_pass`
- 保留 `resonance` 字段，兼容现有前端；其值改为 `adjusted_resonance`，同时用 `raw_resonance` 追溯原始共振分；文档和理由明确“风险调整后机械排序”。
- `dim_status` 增加数据不足说明，不把低可信度误报为口径失败。

新增 API 可选参数：

- `strict_quality: bool = True`
- `min_confidence: float = 0.50`
- `risk_penalty: bool = True`

参数纳入缓存键；默认参数与后台 warmup 对齐。仅关闭参数用于历史对照，不作为默认用户体验。

## 7. 降级与错误处理

- 任一数据源异常：只影响对应组件，保留 `warnings`，不让请求崩溃。
- 财报源不可用：个股基本面口径按既有 spot 代理，但 `fundamental=0.5`，不能视为完整财报可信度。
- 历史为空：口径 1/4 按既有逻辑不可用；严格模式不让仅 spot/资金的低可信度标的进入 `main`。
- 全部标的都低可信度：`main=[]`，返回 `selection_mode="degraded"`、`note` 和 `confidence_summary`，不自动放宽门槛。
- 继续执行 NaN→None 防护，所有日期使用 `pd.to_datetime`。

## 8. 测试计划

新增 `tests/test_quality_confidence.py`，并更新相关 API/cache 测试：

1. 数据完整标的得到 high confidence。
2. 历史不足、财报缺失、仅单日资金流分别降低组件和总可信度。
3. 低可信度标的在 strict 模式不进入 `main`，但可出现在 `by_dim`。
4. 财务红旗硬拒绝并返回 `risk_flags`。
5. 高杠杆 ROE、弱 FCF、高波动、资金脉冲产生对应风险惩罚。
6. 风险调整后排序可改变原始共振排序，同时 `raw_resonance` 保留。
7. `strict_quality=false` / `risk_penalty=false` 可复现旧口径，缓存键不串结果。
8. ETF 不因不适用财报/研报组件被错误扣分。
9. 全部低可信度时返回 degraded 而不是静默放宽。
10. NaN/异常数据不返回 500，既有免责声明和 API 包装保持不变。

## 9. 不在本次范围

- 不新增 GitHub 外部代码或未经验证的因子权重。
- 不自动根据单次回测 IC 修改权重；后续可单独做样本外验证任务。
- 不把盘口 bid/ask、内外盘方向纳入质量排序。
- 不改 `buffett.py`、`signals.py`、`smart_money.py` 的数据采集和评分实现。

# 次日强势 V1 因子扩展设计

## 目标

在现有 `screener/nextday.py` 五步机械排序基础上，增加市场环境、板块扩散、个股结构和风险惩罚因子，输出可解释的研究字段，并保持缺数据时诚实降级。

## 范围

本版本不新增 SQLite 表、不新增全市场网络调用、不改变原有五步硬过滤语义。复用 `stock_spot`、`sector_fund_flow`、`stock_daily`、`smart_money_action` 与 `st_list`。

## 因子

- 市场环境：上涨占比、涨跌停差、全市场成交额相对历史均值、涨幅中位数，输出 `strong/neutral/weak`。
- 板块扩散：所属板块上涨占比、强势股占比、涨停数量、资金流排名与相对强度。
- 个股结构：收盘位置、上影线风险、相对市场/板块强度、换手率分位、高位距离。
- 风险惩罚：20 日涨幅拥挤、连续上涨、换手拥挤、未来解禁占比、近期减持风险；只扣分，不硬剔除。

## 排序

保留原五步分数，并以可用因子归一化的加权平均计算总分：

```text
gross_score = 0.20*量价强势 + 0.15*换手市值 + 0.15*资金连续
            + 0.10*主力阶段 + 0.10*筹码收集 + 0.10*市场环境
            + 0.10*板块扩散 + 0.10*个股结构
score = gross_score - risk_penalty
```

任一因子缺失不伪造为有效 0；聚合时仅对非空因子重新归一化。返回 `factor_scores`、`gross_score`、`risk_penalty`、`market_regime`，兼容已有 `hard_pass`、`step*_pass` 和 `step4_score`。

## API 与降级

`/api/nextday-strong` 保留原参数，增加 `enhanced` 开关，默认启用 V1；`enhanced=false` 使用原五因子排序。接口 disclaimer 继续说明机械排序观察清单、非荐股非买卖信号。无历史 OHLCV、板块映射、解禁或减持数据时对应字段为 `None`，流程继续运行。

## 验证

新增纯函数和编排测试，覆盖正常值、缺失值、空快照、风险扣分、增强开关和 API 字段。完成后运行 nextday、factor research 与全量测试；因子是否有效仅由历史 IC、Rank IC、分层收益和成本后结果判断，不在代码中宣称预测能力。

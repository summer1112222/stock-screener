# 企业所有者研究卡设计

## 目标

在现有 `/api/buffett` 与 `/api/stock-analysis` 的响应中增量加入企业所有者视角的研究字段：所有者收益质量、资本配置、情景估值、反向估值与 A 股会计/治理风险雷达。

本阶段不改回测可得日期，不新增 SQLite 表，不增加实时/短线信号，不自动判断品牌、网络效应或给出买卖动作。

## 现有边界

- 复用 `backtest/buffett.py` 已有摘要、利润表、资产负债表、现金流量表和 spot 数据。
- 缺失字段返回 `None`，并在 `missing_fields`/`data_gaps` 中说明，不用估算值冒充事实。
- 所有 API 结果继续经 `api.server._wrap()`，附 `bt_disclaimer` 与统一 disclaimer。
- 研究结论使用“研究优先级/机械估值/证据不足”，不使用买入、卖出或收益承诺。
- 保持现有字段兼容，新增字段为附加字段。

## 数据与输出

### 所有者收益质量

新增 `owner_earnings_quality`，至少包含：

- `status`: `strong|medium|weak|uncertain`
- `owner_earnings`
- `owner_earnings_to_ni`
- `fcf_to_netincome`
- `ocf_to_netincome`
- `capex_to_ocf`
- `working_capital_drag`
- `share_dilution`
- `history_years`
- `missing_fields`

对维持性资本开支不可区分的情况保留来源/代理说明。

### 资本配置

新增 `capital_allocation`，包含可由现有财报可靠提取的分红、股本、杠杆、商誉、资本开支和留存收益线索；字段缺失时返回 `uncertain` 或 `missing_fields`，不对未接入的公告、质押、问询函做无风险断言。

### 情景估值与反向估值

新增 `valuation_scenarios`，至少提供 `bear/base/bull` 三种情景，每个情景披露 `growth_g`、`cost_of_equity_r`、`sustainable_roe`、`intrinsic_value`、`margin_of_safety`。

新增 `reverse_valuation`，包含 `implied_growth_g`、`implied_roe`、`implied_pb`、`status`、`note`。无足够价格、BPS 或估值数据时显式返回 `insufficient_data`/`invalid`。

### 风险雷达

新增 `risk_radar`：

- `overall`: `low|medium|high|uncertain`
- `accounting`: 已有财报字段支持的现金流转换、商誉、杠杆、ROE、稀释等风险
- `governance`: 已有字段能支持的治理/资本配置风险
- `data_gaps`: 尚未接入的关联交易、股权质押、审计意见、交易所问询函等

风险项包含稳定 code、severity、message、value，便于前端展示和测试。

## 实现边界

- 主要计算放在 `backtest/buffett.py` 的小型纯函数中，`analyze()` 负责组装。
- API 不新增路由，仅验证新增字段能透传现有 `/api/buffett` 与 `/api/stock-analysis`。
- 前端个股分析页新增四个研究区块：所有者收益、资本配置、估值情景、风险雷达。
- 新增/扩展单元测试覆盖完整数据、缺失数据、风险触发、情景估值、反向估值和 API disclaimer。

## 非目标

- 公告披露日时间轴与回测防未来函数。
- 新增数据库表或财报历史快照表。
- 自动抓取/理解年报全文并生成护城河证据。
- 把实时盘口、次日强势与企业质量混成一个总分。

# 回测真实性（成本 + 涨停约束 + 滑点）设计

- 日期：2026-07-13
- 范围：优先级 1（回测真实性补全），属"选股池优化"五优先级之首
- 合规：本设计仅增强历史回测的真实性，不输出买卖点、不承诺收益、不自动下单。所有响应仍附 `bt_disclaimer`。

## 1. 背景与动机

当前 `backtest/engine.py::run_backtest` 是 topN 等权回测，存在三处不真实，导致回测收益系统性虚高：

1. **无交易成本**：算了 `turnover` 比率但未扣金额。A股印花税(卖单边0.05%)+佣金(双边万2.5)+过户费(沪市万0.1)在月调仓下年化拖累约 1–2%。
2. **涨停可买入**：调仓日按因子 `nlargest(topn)` 选组合，不看当日是否涨停封板。A股涨停封板买不进，回测却算买进了——这是回测"月 20%"的主要虚假来源之一。
3. **无滑点**：成交价按收盘价，忽略冲击成本。

不做这三项，后续任何"IC 加权让收益更好"的结论都建立在虚高回测上。故本优先级是后续因子优化的可信度前提。

## 2. 目标与非目标

**目标**
- 调仓日选组合时，封板（涨停价封死）与停牌标的不可买入，自动补位到下一个可买标的。
- 换手日按真实 A 股成本结构扣减成本（印花税/佣金/过户费，按市场区分），加固定 bp 滑点。
- 参数可经 `config.yaml` 默认 + API 覆盖。
- 全部纯函数化、可单测、不依赖网络。

**非目标**
- 不改 `candidates.py`（候选池只排序不模拟，真实性层只在回测引擎）。
- 不做成交额反比冲击成本模型（本期固定 bp；见优先级后续）。
- 不做佣金最低 5 元、日内滑点、逐笔撮合（已知简化，§6 列明）。
- 不承诺任何收益率。"保证月 20%"既违反合规也不现实，本设计目标是让回测更可信，而非追求某收益率。

## 3. 架构

新增两个纯函数模块，`run_backtest` 调用；改动集中在回测引擎，不影响其它层。

```
backtest/
  costs.py      (新) 成本率→金额，按市场区分
  limits.py     (新) 涨停价/封板判定/补位选择器
  engine.py     (改) run_backtest 接入补位+成本扣减
api/server.py   (改) BTRunReq 增可选参数透传
config.yaml     (改) 新增 backtest 段默认参数
tests/test_backtest.py (改) 新增 11 个用例
```

## 4. 模块设计

### 4.1 `backtest/limits.py`

涨停幅度按 A 股现行规则分档：

| 标的 | 涨停幅度 | 判定 |
|---|---|---|
| ST / *ST | 5% | name 含 "ST"（优先，覆盖板块规则） |
| 创业板 300/301 | 20% | code 前缀 |
| 科创板 688 | 20% | code 前缀 |
| 北交所 83/87/92/43 | 30% | code 前缀 |
| 沪深主板 60/00/001 | 10% | code 前缀 |
| ETF / 基金（5 开头） | 不约束 | 返回 inf，不参与涨停过滤 |

接口：

```python
def limit_pct(code: str, name: str | None = None) -> float
    # ST 优先；无 name 则仅按 code 前缀（ST 判定失效，记为已知限制）

def limit_price(prev_close: float, pct: float) -> float
    # = round(prev_close * (1 + pct), 2)   A股涨停价2位小数四舍五入
    # prev_close 为 NaN/None → 返回 None

def is_capped(close: float, prev_close: float, code: str,
              name: str | None = None) -> bool
    # 封板：close == limit_price（容差 0.001 防浮点）
    # close NaN（停牌）→ False（停牌由 select_buyable 另判）
    # prev_close 缺失 → False（无法判定则不排除）

def select_buyable(factor_row: pd.Series, close_row: pd.Series,
                   prev_close_row: pd.Series, name_map: dict[str,str] | None,
                   topn: int) -> list[str]
    # 在【执行日 d】调用：factor_row 取自决策日 r（因子序固定），
    #   close_row/prev_close_row 取自执行日 d（封板/停牌按 d 日判定，符合 T+1）。
    # 按因子值降序遍历候选，跳过封板(is_capped, d 日)与停牌(close NaN, d 日)，
    # 取前 topn 个可买入 code（不足 topn 则按实际数量，不报错；未填满 slot 持现金）。
    # 注意：函数本身不持状态，时点由调用方在日循环里传入正确的 d 日行情决定。
```

### 4.2 `backtest/costs.py`

市场判定与成本率：

```python
def _market(code: str) -> str   # "sh" | "sz" | "bj" | "fund"
    # 6/9 → sh；0/3 → sz；8/4 → bj；5 → fund

def cost_rate(code: str, side: str, params: dict) -> float
    # side="buy":  commission + transfer_fee(仅 sh/bj) + slippage_bp
    # side="sell": stamp_tax(非 fund) + commission + transfer_fee(仅 sh/bj) + slippage_bp
    # ETF(fund): 无 stamp_tax、无 transfer_fee
    # 北交所现行免印花税——本期不特殊处理（按非 fund 收 stamp_tax），
    #   记为已知简化，后续按需细化

def apply_cost(buy_amount: float, sell_amount: float, code: str,
               params: dict) -> float
    # 返回该标的总成本金额（供 run_backtest 累加）
```

### 4.3 `run_backtest` 接入

签名扩展：

```python
def run_backtest(close, factor, topn=10, freq="M", benchmark=None,
                 cost_params: dict | None = None,
                 limit_params: dict | None = None) -> dict
```
- `cost_params` / `limit_params` 为 None 时从 `config.yaml` 读默认并合并。

**调仓决策日 r：存候选名单，不存最终权重**
不再在 r 日就定 topN 等权（那是决策日判定，时点错）。改为存按因子降序的候选名单，供执行日补位：
```python
f = factor.loc[r].dropna()
k = max(topn * 3, topn + 5)                  # 多取备补位
weight_map[r] = f.nlargest(k).index.tolist()  # 候选名单(list[code])
```

**执行日 d（governing 切换到 r 的那天，T+1）：生成实际权重 + 扣成本**
```python
# d 日开盘后执行：用 d 日行情判可买性，用 r 日因子序选前 topn 可买
governing_r = governing[d]                    # None 则无持仓
if governing_r is None or d == idx[0]:
    w = pd.Series(dtype=float)               # 空仓
else:
    cand = weight_map[governing_r]            # 候选名单(按 r 日因子序)
    buyable = select_buyable(factor.loc[governing_r],   # r 日因子序
                             close.loc[d],              # d 日行情
                             close.shift(1).loc[d],     # d 日前收
                             name_map, topn)
    w = pd.Series(1.0/topn, index=buyable)    # 每只 1/topn；不足 topn → 未满 slot 持现金(L2)
                                               #   组合权重和 = len(buyable)/topn ≤ 1，现金=1-和

running_nav = nav[d-1]                         # 累计净值(昨日)
gross_ret = (w * daily_ret.loc[d].reindex(w.index).fillna(0)).sum()  # 现金部分收益0
cost = 0.0
prev_r = governing[d-1] if d-1 in governing else None   # 上一执行 governing
if prev_r is not None and governing_r != prev_r:        # governing 切换=实际换手
    w_old = realized_weights.get(prev_r, pd.Series(dtype=float))  # 上一组合实际权重
    for code in w.index.union(w_old.index):
        delta = w.get(code, 0.0) - w_old.get(code, 0.0)
        notional = running_nav * abs(delta)
        if delta > 0:   cost += notional * cost_rate(code, "buy", params)
        elif delta < 0: cost += notional * cost_rate(code, "sell", params)
    realized_weights[governing_r] = w         # 缓存本组合实际权重
daily_port[d] = gross_ret - cost / running_nav
```
- 时点自洽：决策日 r 只定候选序，执行日 d 才用 d 日行情判可买性 + 扣成本，符合 T+1。
- 等权 `1/topn` 不变，未填满 slot 自动持现金（收益 0），避免 L2 的集中度问题。
- `realized_weights` 缓存每个 governing r 对应的实际权重（含现金/补位结果），供下次换手算 delta。
- `running_nav` 用累计净值，成本随净值真实放大。
- 返回新增 `total_cost`（累计成本金额，诊断用），保留 `turnover`。

**name_map 来源**：`load_panel` 时一并取 name 列拼成 `{code: name}`（stock_spot/etf_spot 有 name），传给 `select_buyable`。无 name 时降级为仅 code 前缀判板块（ST 判定失效，已知限制）。

## 5. config / API

### `config.yaml` 新增段
```yaml
# 回测真实性参数（仅回测引擎用，不影响候选池排序）
backtest:
  stamp_tax: 0.0005        # 印花税，卖出单边
  commission: 0.00025      # 佣金，双边
  transfer_fee: 0.00001    # 过户费，双边（仅沪市 6/9 开头）
  slippage_bp: 0.0005      # 滑点，双边
  respect_limit: true       # 涨停封板不可买入+补位
  respect_suspend: true      # 停牌不可买入+补位
```
`engine.py` 顶部 `_load_bt_config()` 读 `config.yaml`，缺文件/缺键降级为硬编码默认，与函数参数合并（API 传入覆盖默认）。

### `api/server.py` 改动
`BTRunReq` 增可选字段：
```python
cost_params: dict | None = None
respect_limit: bool | None = None
respect_suspend: bool | None = None
```
`bt_run` 透传给 `run_backtest`。响应不变（`_wrap` + `bt_disclaimer`）。

## 6. 已知简化（显式，不藏）

1. 佣金最低 5 元忽略（月调仓、净值归一假设下金额足够大）。
2. 成本按收盘价估算、忽略日内（标准回测简化）。
3. 涨停价用前收×(1+pct) 四舍五入，封板判定 `close == limit_price ± 0.001`。
4. ST 判定依赖 name；name 缺失则 ST 规则失效（仍按板块幅度）。
5. 停牌=当日 close NaN，买入侧补位跳过；持仓中停牌维持 NaN→0 收益（现有行为不变）。
6. **卖出侧停牌锁定不处理（L3）**：持仓股停牌时下次调仓想卖也卖不掉，实际组合会偏离目标；本期不维护"被迫持有"状态机，留待优先级 5 风控层。二阶影响。
7. 北交所印花税现行免征——本期按非 fund 统一收，后续按需细化。
8. 滑点为固定 bp，未做成交额反比冲击成本模型（留待后续优先级）。

## 7. 单测清单（`tests/test_backtest.py` 新增）

全部合成数据 mock `db.query_rows`，不依赖网络（符合 CLAUDE.md 测试约定）。

| 用例 | 验证 |
|---|---|
| `test_limit_pct_by_prefix` | 600→10%, 300→20%, 688→20%, 83→30%, ST name→5%, ETF 5开头→inf |
| `test_limit_price_rounding` | `10.00×1.1=11.00`、`9.99×1.1=10.99` |
| `test_is_capped` | close==limit_price→True；略低→False；prev_close NaN→False |
| `test_select_buyable_filters_capped` | 候选含1只封板（按执行日 d 行情判），补位取到第 topn+1 名 |
| `test_select_buyable_fewer_holds_cash` | 可买 < topn 时只返回可买数，权重 1/topn 未满 slot 持现金（组合权重和<1） |
| `test_select_buyable_uses_execution_day_not_decision_day` | r 日封板但 d 日未封板→可买；r 日未封板但 d 日封板→不可买（T+1 时点正确） |
| `test_cost_rate_market_split` | 沪股 sell 含印花税+过户费；深股 sell 无过户费；ETF sell 无印花税 |
| `test_apply_cost_buy_sell` | buy 只算佣金+滑点(+过户费)；sell 多印花税 |
| `test_run_backtest_with_cost_lower_than_without` | 开成本净值≤关成本，total_cost>0 |
| `test_run_backtest_respects_limit_skips_capped` | 构造执行日某 top 标的封板，确认该标的不在实际权重，slot 持现金或补位 |
| `test_run_backtest_no_cost_when_no_turnover` | 单标的持有不换手（governing 不切换），total_cost≈0 |

## 8. 合规边界

- 本设计不引入任何买卖点输出、不承诺收益、不自动下单。
- 所有回测响应仍附 `BT_DISCLAIMER`（"历史回测/因子评价结果不预示未来表现，不构成投资建议，不承诺收益，不输出买卖点"）。
- `config.yaml` 与模块 docstring 重申此边界（改时保持一致）。
- 候选池措辞维持"筛选/排序/观察清单"，不得用"推荐/买入/卖出"。

## 9. 后续承接（不在本期实现）

- 优先级 2：多因子 IC 加权 + 正交化（改 `candidates.py` + `eval.py`）。
- 优先级 3：因子库扩充（价值+质量，复用 `stock_spot` 的 PE/PB 与 `buffett.py` 的财务质量）。
- 优先级 4：样本外滚动验证接入候选池（`robust.py`）。
- 优先级 5：风控/仓位层 + 择时层（新模块）。
- 成交额反比冲击成本模型（本设计 §6.7 留待后续）。

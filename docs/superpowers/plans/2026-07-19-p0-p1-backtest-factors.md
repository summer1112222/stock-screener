# P0+P1 交易成本/防过拟合/因子增强 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 stock-screener 落地 P0（交易成本、滚动 walk-forward、幸存者偏差结构化）+ P1（反转/流动性因子、个股 conditions AND 过滤、buffett 缓存）共 6 项改动，作为研究工具提升回测严谨度与因子覆盖。

**Architecture:** 在现有四层（data/screener/backtest/api）上原地增强，不新增领域模块。回测引擎加成本扣减与退市记账钩子；robust 加滚动 walk-forward 与结构化幸存者告警；eval 加反转/Amihud 因子分支；screener 加个股 AND 过滤；data 加 buffett 财务摘要缓存表。

**Tech Stack:** Python 3、pandas、FastAPI、SQLite、pytest。复权统一 qfq（前复权）。

## Global Constraints

- **项目非 git 仓库**：每个 task 的 Step 5 用"运行全量测试通过"作为检查点，不执行 `git commit`。
- **合规硬约束**：不荐股、不输出买卖点、不承诺收益（含"月 10-20%"）。新因子/钩子沿用"机械筛选/研究优先级/非买卖信号"措辞；回测类路由复用 `BT_DISCLAIMER`，个股筛选走 `_wrap` 附 `DISCLAIMER`，**不新增 disclaimer 文本**。
- **NaN→None 序列化**：新增 DataFrame→records 必须用 `df.astype(object).where(pd.notna(df), None)`，不能用 `df.where(pd.notna(df), None)`（starlette `allow_nan=False` 会 500）。
- **pandas 3.0 兼容**：`pd.Grouper(freq="M")` 弃用，用 `ME`/`QE`；`Series.reindex` 的 `fill_value` 必须关键字传。
- **测试**：放 `tests/`，合成数据 mock `db.query_rows`，不依赖网络。宿主跑 `python -m pytest tests/ -q`（tests/ 被 .dockerignore 排除）。
- **新增 SQLite 表**：同步 `models.SCHEMA_SQL` + `TABLE_FIELDS`（`db.upsert_rows` 依赖 `TABLE_FIELDS`，漏加会 KeyError）。

## File Structure

| 文件 | 责任 | 本计划改动 |
|---|---|---|
| `backtest/engine.py` | topN 回测引擎 | Task1 加 cost_bps；Task3 加 delisted_codes 记账 |
| `backtest/robust.py` | 防过拟合 | Task2 加 rolling_walk_forward；Task3 加 survivorship_status |
| `backtest/eval.py` | 因子计算/IC | Task4 加 reversal/amihud 分支 |
| `backtest/candidates.py` | 候选池排序 | Task4 扩 history_factors |
| `backtest/buffett.py` | 基本面评分 | Task5 fetch_abstract 加缓存 |
| `data/models.py` | schema | Task5 加 financial_abstract_cache 表 |
| `screener/conditions.py` | 字段目录 | Task6 加 STOCK_FIELDS_CAT |
| `screener/engine.py` | 实时筛选 | Task6 加 filter_stocks |
| `api/server.py` | 路由 | Task1/2/3/4/5/6 各加参数/分支 |
| `tests/test_*.py` | 测试 | 每个 task 新增一个测试文件 |

---

## Task 1: P0-1 回测交易成本模型

**Files:**
- Modify: `backtest/engine.py`（`run_backtest` 函数，约 24-98 行）
- Modify: `api/server.py:156-166`（`BTRunReq` 加字段）、`api/server.py:200-224`（`bt_run` 传参）
- Test: `tests/test_backtest_cost.py`

**Interfaces:**
- Produces: `run_backtest(close, factor, topn, freq, benchmark, cost_bps=30.0, delisted_codes=None) -> dict`，响应新增字段 `cost_bps: float`、`total_cost_drag: float`。Task3 会复用同函数的 `delisted_codes` 参数。

**关键语义（§9.A）**：`cost_bps` 是**双边换手成本率**（基点），`turnover_d` 已含买+卖两侧，扣减 = `turnover_d * cost_bps / 10000`，不重复乘 2。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_backtest_cost.py`：

```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import engine as bt_engine


def _synth(months=24, n_codes=20, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=months*21)
    px = 10 + np.cumsum(rng.normal(0, 0.2, (len(dates), n_codes)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=[f"c{i}" for i in range(n_codes)])
    close = close.where(close > 1, 1.0)
    # 因子：两期排名差异大，保证换手>0
    factor = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    half = len(dates)//2
    factor.iloc[:half] = np.tile(np.arange(n_codes)[::-1], (half,1))
    factor.iloc[half:] = np.tile(np.arange(n_codes), (len(dates)-half,1))
    return close, factor


def test_cost_reduces_nav():
    close, factor = _synth()
    r0 = bt_engine.run_backtest(close, factor, topn=5, freq="M", cost_bps=0.0)
    r30 = bt_engine.run_backtest(close, factor, topn=5, freq="M", cost_bps=30.0)
    eq0 = pd.Series(r0["equity_curve"]).astype(float).sort_index()
    eq30 = pd.Series(r30["equity_curve"]).astype(float).sort_index()
    assert eq30.iloc[-1] < eq0.iloc[-1], "有成本时净值应低于无成本"
    assert r30["total_cost_drag"] > 0
    assert r30["cost_bps"] == 30.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_backtest_cost.py -q`
Expected: FAIL（`run_backtest() got an unexpected keyword argument 'cost_bps'` 或 KeyError `total_cost_drag`）

- [ ] **Step 3: 实现**

修改 `backtest/engine.py` 的 `run_backtest` 签名与循环。新签名：

```python
def run_backtest(close: pd.DataFrame, factor: pd.DataFrame,
                 topn: int = 10, freq: str = "M",
                 benchmark: pd.Series | None = None,
                 cost_bps: float = 30.0,
                 delisted_codes: list[str] | None = None) -> dict:
```

把现有 `daily_port = []` / `turnovers = []` / `prev_r = None` 循环段（约 59-79 行）替换为：

```python
    daily_port = []
    turnovers = []
    total_cost_drag = 0.0
    cost_rate = max(0.0, float(cost_bps)) / 10000.0  # §9.A：双边成本率，turnover_d 已含买卖两侧
    prev_r = None
    for d in idx:
        r = governing.get(d)
        w = weight_map.get(r) if r is not None else None
        if w is None or w.empty or d == idx[0]:
            daily_port.append(0.0)
        else:
            dr = daily_ret.loc[d].reindex(w.index).fillna(0.0)
            daily_port.append(float((w * dr).sum()))
        # 换手 + 成本扣减（调仓日）
        if d in rebal_set and prev_r is not None and d in weight_map:
            a = weight_map[prev_r]
            b = weight_map[d]
            comb = a.index.union(b.index)
            tov = float((b.reindex(comb, fill_value=0)
                        - a.reindex(comb, fill_value=0)).abs().sum())
            turnovers.append(tov)
            if cost_rate > 0:
                drag = tov * cost_rate
                daily_port[-1] -= drag
                total_cost_drag += drag
        if d in rebal_set:
            prev_r = d
```

在 return dict 里加字段：

```python
    return {
        "equity_curve": equity,
        "benchmark_curve": bench_curve,
        "daily_returns": dict(zip(str_idx, [round(float(r), 6) for r in daily_port])),
        "turnover": round(float(np.mean(turnovers)), 4) if turnovers else None,
        "rebalance_dates": [str(d.date()) for d in rebal],
        "topn": topn,
        "cost_bps": float(cost_bps),
        "total_cost_drag": round(total_cost_drag, 6),
        "delisted_declared": len(delisted_codes or []),
    }
```

修改 `api/server.py` `BTRunReq` 加字段：

```python
class BTRunReq(BaseModel):
    universe: str
    codes: list[str]
    factor: str = "momentum_n"
    n: int = 20
    topn: int = 10
    freq: str = "M"
    start: str = "20200101"
    end: str = "20240101"
    benchmark: str | None = "sh000300"
    cost_bps: float = 30.0
```

`bt_run` 调用处（约 215 行）改：

```python
    res = bt_engine.run_backtest(close, factor, topn=req.topn,
                                 freq=req.freq, benchmark=bench,
                                 cost_bps=req.cost_bps)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_backtest_cost.py -q`
Expected: PASS

- [ ] **Step 5: 检查点**

Run: `python -m pytest tests/ -q`
Expected: 全绿。无 git，跳过 commit。

---

## Task 2: P0-2 滚动 walk-forward

**Files:**
- Modify: `backtest/robust.py`（新增 `rolling_walk_forward`）
- Modify: `api/server.py:397-408`（`bt_walkforward` 路由切到滚动版）
- Test: `tests/test_rolling_walkforward.py`

**Interfaces:**
- Consumes: `backtest.eval.ic_series`/`ic_summary`/`forward_returns`（已存在）
- Produces: `rolling_walk_forward(factor, close, n, train_months=12, test_months=2, step_months=2) -> dict`，返回 `{segments, n_segments, oos_ic_mean, oos_ic_median, overfit_frac}` 或 `{"error":..., "n_segments":0}`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_rolling_walkforward.py`：

```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import robust as bt_robust


def _synth(months=24, n=20):
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2022-01-01", periods=months*21)
    factor = pd.DataFrame(rng.normal(0,1,(len(dates),n)), index=dates,
                          columns=[f"c{i}" for i in range(n)])
    px = 10 + np.cumsum(rng.normal(0,0.2,(len(dates),n)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=factor.columns)
    return factor, close


def test_rolling_wf_returns_segments():
    factor, close = _synth(months=24)
    res = bt_robust.rolling_walk_forward(factor, close, n=5)
    assert "error" not in res
    assert res["n_segments"] >= 1
    assert len(res["segments"]) == res["n_segments"]
    assert res["oos_ic_median"] is None or isinstance(res["oos_ic_median"], (int,float))
    assert 0.0 <= res["overfit_frac"] <= 1.0


def test_rolling_wf_too_short():
    factor, close = _synth(months=10)
    res = bt_robust.rolling_walk_forward(factor, close, n=5)
    assert res.get("n_segments") == 0
    assert "error" in res
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_rolling_walkforward.py -q`
Expected: FAIL（`AttributeError: module 'backtest.robust' has no attribute 'rolling_walk_forward'`）

- [ ] **Step 3: 实现**

在 `backtest/robust.py` 加函数（文件已 `import numpy as np`、`import pandas as pd`、`from . import eval as bt_eval`）：

```python
def rolling_walk_forward(factor: pd.DataFrame, close: pd.DataFrame,
                         n: int = 5, train_months: int = 12,
                         test_months: int = 2, step_months: int = 2) -> dict:
    """滚动 walk-forward：训练段 vs 测试段 IC 衰减 + 过拟合段占比。
    §9.B：train_ic=0 时 decay=None（防除零）。"""
    fwd = bt_eval.forward_returns(close, n)
    idx = factor.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx)
    months = pd.PeriodIndex(idx.to_period("M")).drop_duplicates().sort_values()
    total = len(months)
    need = train_months + test_months
    if total < need:
        return {"error": f"样本不足(需≥{need}月，现有{total}月)", "n_segments": 0}

    segments = []
    oos_ics = []
    start = 0
    while start + need <= total:
        train_per = months[start:start + train_months]
        test_per = months[start + train_months:start + need]
        train_idx = idx[(idx >= train_per.start_time) & (idx < test_per.start_time)]
        test_idx = idx[(idx >= test_per.start_time) & (idx < test_per.end_time)]
        ic_tr = bt_eval.ic_series(factor.loc[train_idx], fwd.loc[train_idx]) if len(train_idx) else pd.Series(dtype=float)
        ic_te = bt_eval.ic_series(factor.loc[test_idx], fwd.loc[test_idx]) if len(test_idx) else pd.Series(dtype=float)
        s_tr = bt_eval.ic_summary(ic_tr)
        s_te = bt_eval.ic_summary(ic_te)
        tr_ic, te_ic = s_tr.get("ic"), s_te.get("ic")
        decay = None
        if tr_ic not in (None, 0) and te_ic is not None:
            decay = (tr_ic - te_ic) / abs(tr_ic)
        segments.append({
            "train_range": [str(train_per[0]), str(train_per[-1])],
            "test_range": [str(test_per[0]), str(test_per[-1])],
            "train_ic": tr_ic, "test_ic": te_ic,
            "decay": round(decay, 4) if decay is not None else None,
        })
        if te_ic is not None:
            oos_ics.append(te_ic)
        start += step_months

    over = [s for s in segments if s["decay"] is not None and s["decay"] > 0.5]
    overfit_frac = round(len(over) / len(segments), 4) if segments else 0.0
    return {
        "segments": segments,
        "n_segments": len(segments),
        "oos_ic_mean": round(float(np.mean(oos_ics)), 4) if oos_ics else None,
        "oos_ic_median": round(float(np.median(oos_ics)), 4) if oos_ics else None,
        "overfit_frac": overfit_frac,
    }
```

修改 `api/server.py` `bt_walkforward`（约 407 行）：

```python
    wf = bt_robust.rolling_walk_forward(factor, close, n=req.n)
    return _wrap(wf, {"bt_disclaimer": BT_DISCLAIMER})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_rolling_walkforward.py -q`
Expected: PASS

- [ ] **Step 5: 检查点**

Run: `python -m pytest tests/ -q`，全绿。无 git commit。

---

## Task 3: P0-3 幸存者偏差结构化 + 退市记账钩子

**Files:**
- Modify: `backtest/robust.py`（新增 `survivorship_status`）
- Modify: `backtest/engine.py`（`delisted_declared` 已在 Task1 占位，此 task 确认行为不变）
- Modify: `api/server.py`（`bt_run`/`bt_eval_route` 响应 `survivorship` 改用 `survivorship_status()`；`BTRunReq` 加 `delisted_codes` 字段）
- Test: `tests/test_survivorship.py`

**Interfaces:**
- Produces: `survivorship_status() -> dict`；`run_backtest` 的 `delisted_codes` 参数（Task1 已加签名占位，记账字段 `delisted_declared` 已在响应）。

**语义（spec P0-3 + §9）**：`delisted_codes` **仅记账**，不改收益算法——现有 `fillna(0.0)` 已处理停牌/退市早停。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_survivorship.py`：

```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import engine as bt_engine, robust as bt_robust


def test_survivorship_status_shape():
    s = bt_robust.survivorship_status()
    assert isinstance(s, dict)
    assert s["universe_approximation"] is True
    assert "delisted_coverage" in s


def test_delisted_codes_only_bookkeeping():
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2022-01-01", periods=60)
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0,0.2,(60,5)),axis=0),
                         index=dates, columns=[f"c{i}" for i in range(5)])
    factor = pd.DataFrame(rng.normal(0,1,(60,5)), index=dates, columns=close.columns)
    r0 = bt_engine.run_backtest(close, factor, topn=3, freq="M", delisted_codes=None)
    r1 = bt_engine.run_backtest(close, factor, topn=3, freq="M", delisted_codes=["c0"])
    assert r0["delisted_declared"] == 0
    assert r1["delisted_declared"] == 1
    # 算法不变：equity_curve 完全一致（delisted_codes 仅记账）
    assert r0["equity_curve"] == r1["equity_curve"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_survivorship.py -q`
Expected: FAIL（`survivorship_status` 不存在）

- [ ] **Step 3: 实现**

在 `backtest/robust.py` 加：

```python
def survivorship_status() -> dict:
    """结构化幸存者偏差告警（轻量方案：标注覆盖盲区，不建退市表）。"""
    return {
        "note": ("universe 用当前成分近似时点成分(akshare 时点成分不全)；"
                 "已退市/ST 标的可能缺失，结果存在幸存者偏差，仅供参考。"),
        "universe_approximation": True,
        "delisted_coverage": "akshare 不提供退市清单，已退市标的多缺失",
    }
```

`backtest/engine.py` Task1 已在签名加 `delisted_codes` 并在响应加 `delisted_declared`——此 task 无需再改 engine。

修改 `api/server.py` `BTRunReq` 加：

```python
    delisted_codes: list[str] | None = None
```

`bt_run`（约 215 行）传参 + 响应 survivorship：

```python
    res = bt_engine.run_backtest(close, factor, topn=req.topn,
                                 freq=req.freq, benchmark=bench,
                                 cost_bps=req.cost_bps,
                                 delisted_codes=req.delisted_codes)
    eq = pd.Series(res.get("equity_curve", {})).astype(float).sort_index()
    bench_nav = pd.Series(res.get("benchmark_curve", {}))
    bench_nav = bench_nav.astype(float).sort_index() if len(bench_nav) else None
    res["risk"] = bt_risk.risk_metrics(eq, bench_nav)
    res["factor"] = req.factor
    res["factors"] = BACKTEST_FACTORS
    res["survivorship"] = bt_robust.survivorship_status()
```

`bt_eval_route`（约 196 行）：

```python
        "survivorship": bt_robust.survivorship_status(),
```

（原 `bt_robust.survivorship_note()` 改为 `survivorship_status()`，字符串→dict）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_survivorship.py -q`
Expected: PASS

- [ ] **Step 5: 检查点**

Run: `python -m pytest tests/ -q`，全绿。无 git commit。

---

## Task 4: P1-1 反转 + 流动性因子

**Files:**
- Modify: `backtest/eval.py:45-75`（`compute_factor` 加分支）
- Modify: `backtest/candidates.py:137`（`history_factors` 扩）
- Modify: `api/server.py:136`（`BACKTEST_FACTORS` 扩）+ `LABEL` 字典
- Test: `tests/test_factors_reversal_amihud.py`

**Interfaces:**
- Produces: `compute_factor` 支持 factor_key 前缀 `reversal`/`amihud`；`BACKTEST_FACTORS` 含 `reversal_5`/`reversal_20`/`amihud_20`。

**方向语义（§9.C）**：`amihud_20` 越小=流动性越好=越优，排序用 `sort=asc`；LABEL 注明"越小越好"。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_factors_reversal_amihud.py`：

```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import eval as bt_eval


def _panels():
    rng = np.random.default_rng(5)
    dates = pd.bdate_range("2022-01-01", periods=40)
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0,0.2,(40,4)),axis=0),
                         index=dates, columns=[f"c{i}" for i in range(4)])
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, (40,4)),
                         index=dates, columns=close.columns)
    return close, amount


def test_reversal_5():
    close, amount = _panels()
    r = bt_eval.compute_factor(close, "reversal_5", params={"n": 5}, amount=amount)
    expected = -close.pct_change(5)
    pd.testing.assert_series_equal(r.iloc[-1].dropna(), expected.iloc[-1].dropna())


def test_amihud_shape_and_direction():
    close, amount = _panels()
    a = bt_eval.compute_factor(close, "amihud_20", params={"n": 20}, amount=amount)
    assert a.shape == close.shape
    assert (a.dropna() >= 0).all().all()


def test_amihud_no_amount_returns_empty():
    close, _ = _panels()
    a = bt_eval.compute_factor(close, "amihud_20", params={"n": 20}, amount=None)
    assert a.empty or a.shape == close.shape
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_factors_reversal_amihud.py -q`
Expected: FAIL（`ValueError: 未知 factor_key: reversal_5`）

- [ ] **Step 3: 实现**

在 `backtest/eval.py` `compute_factor` 的 `raise ValueError(...)` 之前插入：

```python
    if factor_key.startswith("reversal"):
        return -close.pct_change(n)
    if factor_key.startswith("amihud"):
        if amount is None:
            return pd.DataFrame(index=close.index, columns=close.columns)
        dr = close.pct_change()
        return (dr.abs() / amount).rolling(n).mean()
```

修改 `backtest/candidates.py:137`：

```python
    history_factors = ("momentum_n", "volatility_n", "turnover_n", "activity", "momentum",
                       "reversal_5", "reversal_20", "amihud_20")
```

修改 `api/server.py:136`：

```python
BACKTEST_FACTORS = ["momentum_n", "volatility_n", "turnover_n", "activity", "momentum",
                    "reversal_5", "reversal_20", "amihud_20"]
```

`LABEL` 字典补（在 server.py 的 LABEL 定义处加）：

```python
    "reversal_5": "5日反转",
    "reversal_20": "20日反转",
    "amihud_20": "Amihud非流动性(20日,越小越好)",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_factors_reversal_amihud.py -q`
Expected: PASS

- [ ] **Step 5: 检查点**

Run: `python -m pytest tests/ -q`，全绿。无 git commit。

---

## Task 5: P1-3 buffett 财务摘要缓存

**Files:**
- Modify: `data/models.py`（`SCHEMA_SQL` 加表 + `TABLE_FIELDS`）
- Modify: `backtest/buffett.py`（`fetch_abstract` 加缓存；`analyze` 标 stale）
- Test: `tests/test_buffett_cache.py`

**Interfaces:**
- Produces: `fetch_abstract(code) -> tuple[pd.DataFrame | None, bool]`（df, stale_flag）；`analyze(code)` 响应含 `stale_data: bool`。
- 缓存表 `financial_abstract_cache(code TEXT PRIMARY KEY, payload_json TEXT, ts TEXT)`（§9.E：按 code 单行存整张摘要，7 天 TTL）。

**注意**：`db.py` **无需改迁移**——`init_db` 先 `executescript(SCHEMA_SQL)` 含 `CREATE TABLE IF NOT EXISTS financial_abstract_cache`，新旧库都会建。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_buffett_cache.py`：

```python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
import pandas as pd
import backtest.buffett as bt_buf


def _df():
    return pd.DataFrame({"指标": ["净资产收益率"], "2023-12-31": [15.0], "2022-12-31": [12.0]})


def _set_cache(code, df, ts):
    bt_buf.db.upsert_rows("financial_abstract_cache",
        [{"code": code, "payload_json": df.to_json(orient="records", force_ascii=False), "ts": ts}])


def test_cache_hit_no_network(monkeypatch):
    code = "000001"
    bt_buf.db.init_db()
    _set_cache(code, _df(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    called = {"net": False}
    def _boom(*a, **k):
        called["net"] = True
        raise RuntimeError("should not hit net")
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract", _boom)
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None and not df.empty
    assert stale is False
    assert called["net"] is False


def test_cache_miss_then_write(monkeypatch):
    code = "600000"
    bt_buf.db.init_db()
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract", lambda symbol: _df())
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None and not df.empty
    assert stale is False
    rows = bt_buf.db.query_rows("financial_abstract_cache", where="code=?", params=(code,))
    assert rows and rows[0]["code"] == code


def test_cache_expired_refetch(monkeypatch):
    code = "000002"
    bt_buf.db.init_db()
    old_ts = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    _set_cache(code, _df(), old_ts)
    monkeypatch.setattr(bt_buf, "_AK_OK", True)
    monkeypatch.setattr(bt_buf.ak, "stock_financial_abstract", lambda symbol: _df())
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None
    assert stale is False


def test_ak_off_falls_back_to_stale(monkeypatch):
    code = "000003"
    bt_buf.db.init_db()
    _set_cache(code, _df(), (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"))
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    df, stale = bt_buf.fetch_abstract(code)
    assert df is not None
    assert stale is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_buffett_cache.py -q`
Expected: FAIL（`fetch_abstract` 返回 DataFrame 不是 tuple，或表不存在）

- [ ] **Step 3: 实现**

`data/models.py` `SCHEMA_SQL` 在 `smart_money_action` 索引后加：

```sql
CREATE TABLE IF NOT EXISTS financial_abstract_cache (
    code TEXT PRIMARY KEY,
    payload_json TEXT,
    ts TEXT
);
```

`models.py` 加字段集并注册：

```python
FINANCIAL_CACHE_FIELDS = {"code", "payload_json", "ts"}
```

`TABLE_FIELDS` dict 加 `"financial_abstract_cache": FINANCIAL_CACHE_FIELDS,`

`backtest/buffett.py` 顶部 import 加 `import io`，并在文件顶部（`from data import db` 之后）加：

```python
from datetime import datetime

_CACHE_TTL_DAYS = 7


def _cache_get(code: str, allow_stale: bool = False):
    """返回 (df_or_None, status)。status ∈ hit/stale/miss。allow_stale 时 stale 也返回 df。"""
    rows = db.query_rows("financial_abstract_cache", where="code=?", params=(code,))
    if not rows:
        return None, "miss"
    r = rows[0]
    payload, ts = r.get("payload_json"), r.get("ts")
    if not payload or not ts:
        return None, "miss"
    try:
        df = pd.read_json(io.StringIO(payload))
    except Exception:
        return None, "miss"
    try:
        age = datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None, "miss"
    if age.days <= _CACHE_TTL_DAYS:
        return df, "hit"
    if allow_stale:
        return df, "stale"
    return None, "stale"


def _cache_set(code: str, df: pd.DataFrame) -> None:
    payload = df.to_json(orient="records", force_ascii=False)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.upsert_rows("financial_abstract_cache",
                   [{"code": code, "payload_json": payload, "ts": ts}])
```

替换原 `fetch_abstract`（约 35-42 行）为：

```python
def fetch_abstract(code: str) -> tuple[pd.DataFrame | None, bool]:
    """返回 (df, stale)。缓存7天TTL；_AK_OK=False 时降级返回过期缓存(stale=True)。"""
    df, status = _cache_get(code, allow_stale=False)
    if status == "hit":
        return df, False
    if _AK_OK:
        try:
            net = ak.stock_financial_abstract(symbol=_strip_prefix(code))
            if net is not None and not net.empty:
                _cache_set(code, net)
                return net, False
        except Exception:
            pass
    # 网络失败或 _AK_OK=False：降级 stale
    df_s, _ = _cache_get(code, allow_stale=True)
    if df_s is not None:
        return df_s, True
    return None, False
```

`analyze(code)` 调用处（约 88 行 `df = fetch_abstract(code)`）改：

```python
    df, stale = fetch_abstract(code)
    res["stale_data"] = bool(stale)
```

> 注：`analyze_many`/`quality._dim_scores` 调 `analyze`，自动透传 `stale_data` 字段，无需改。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_buffett_cache.py -q`
Expected: PASS

- [ ] **Step 5: 检查点**

Run: `python -m pytest tests/ -q`，全绿。无 git commit。

---

## Task 6: P1-2 个股 conditions AND 过滤

**Files:**
- Modify: `screener/conditions.py`（新增 `STOCK_FIELDS_CAT`）
- Modify: `screener/engine.py`（新增 `filter_stocks` + `_tradable_stocks`）
- Modify: `api/server.py`（`/api/screen` 加 stock 分支、`/api/fields` 加 stock_fields、import `STOCK_FIELDS_CAT`）
- Test: `tests/test_filter_stocks.py`

**Interfaces:**
- Produces: `filter_stocks(conditions, sort, asc, limit, min_turnover, limit_pct) -> dict`（与 `filter_etfs` 同结构）；`STOCK_FIELDS_CAT` 字段目录。

**已知限制（§9.D）**：`limit_pct=9.9` 主板涨停阈值，科创板/创业板 20%、北交所 30% 会被误杀，本 task 不修。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_filter_stocks.py`：

```python
# -*- coding: utf-8 -*-
from screener import engine
from data import db


def _seed():
    db.init_db()
    rows = [
        {"code":"000001","name":"平安银行","latest_price":10.0,"change_pct":2.0,
         "turnover_amount":2e8,"turnover_rate":1.5,"pe":8,"pb":0.9,"total_market_cap":2e10},
        {"code":"000002","name":"万科A","latest_price":9.0,"change_pct":-1.0,
         "turnover_amount":1.5e8,"turnover_rate":1.0,"pe":7,"pb":0.8,"total_market_cap":1.5e10},
        {"code":"000003","name":"*ST某某","latest_price":5.0,"change_pct":5.0,
         "turnover_amount":3e8,"turnover_rate":3.0,"pe":None,"pb":None,"total_market_cap":5e9},
        {"code":"000004","name":"涨停股","latest_price":11.0,"change_pct":10.0,
         "turnover_amount":5e8,"turnover_rate":5.0,"pe":20,"pb":2.0,"total_market_cap":1e10},
    ]
    db.upsert_rows("stock_spot", rows)


def test_filter_stocks_excludes_st_and_limit():
    _seed()
    res = engine.filter_stocks(conditions=[], sort="turnover_amount", asc=False, limit=10)
    names = [r["name"] for r in res["rows"]]
    assert "*ST某某" not in names
    assert "涨停股" not in names
    assert "平安银行" in names


def test_filter_stocks_between():
    _seed()
    res = engine.filter_stocks(conditions=[
        {"field":"pe","op":"between","value":[7.5, 9]},
    ], sort="pe", asc=False, limit=10)
    codes = [r["code"] for r in res["rows"]]
    assert "000001" in codes
    assert "000002" not in codes
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_filter_stocks.py -q`
Expected: FAIL（`module 'screener.engine' has no attribute 'filter_stocks'`）

- [ ] **Step 3: 实现**

`screener/conditions.py` 在 `ETF_FIELDS_CAT` 后加：

```python
# 个股可筛选字段(取自 STOCK_SPOT_FIELDS)
STOCK_FIELDS_CAT = [
    {"key": "change_pct", "label": "涨跌幅(%)", "ops": ["gt", "gte", "lt", "lte", "eq", "ne", "between"]},
    {"key": "turnover_amount", "label": "成交额(元)", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "turnover_rate", "label": "换手率(%)", "ops": ["gt", "lt", "between"]},
    {"key": "total_market_cap", "label": "总市值(元)", "ops": ["gt", "lt", "between"]},
    {"key": "circulating_market_cap", "label": "流通市值(元)", "ops": ["gt", "lt", "between"]},
    {"key": "pe", "label": "市盈率", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "pb", "label": "市净率", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "amplitude", "label": "振幅(%)", "ops": ["gt", "lt", "between"]},
    {"key": "volume_ratio", "label": "量比", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "latest_price", "label": "最新价(元)", "ops": ["gt", "lt", "between"]},
]
```

`screener/engine.py` 加（在 `filter_etfs` 之后）：

```python
def _tradable_stocks(df: pd.DataFrame, min_turnover: float,
                     limit_pct: float) -> pd.DataFrame:
    """个股可交易预筛：排除 ST/停牌/涨停/低成交额。§9.D：limit_pct=9.9 对科创/创业/北交误杀，已知。"""
    if df is None or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if "name" in df.columns:
        mask &= ~df["name"].astype(str).str.contains("ST", case=False, na=False)
    if "latest_price" in df.columns:
        lp = pd.to_numeric(df["latest_price"], errors="coerce")
        mask &= lp.notna() & (lp > 0)
    if "turnover_amount" in df.columns:
        mask &= pd.to_numeric(df["turnover_amount"], errors="coerce").fillna(0) >= min_turnover
    if "change_pct" in df.columns:
        mask &= pd.to_numeric(df["change_pct"], errors="coerce").fillna(-99) < limit_pct
    return df[mask]


def filter_stocks(conditions: list | None = None,
                  sort: str | None = "turnover_amount",
                  asc: bool = False,
                  limit: int = 50,
                  min_turnover: float = 5e7,
                  limit_pct: float = 9.9) -> dict:
    """筛选个股：stock_spot → 可交易预筛 → 条件 AND → 排序 → 截断。
    返回结构与 filter_etfs 一致：{rows, total, skipped, category}。"""
    conditions = conditions or []
    rows = db.query_rows("stock_spot")
    if not rows:
        return {"rows": [], "total": 0, "skipped": ["个股数据为空，先 /api/refresh"],
                "category": "个股"}
    df = pd.DataFrame(rows)
    df = _tradable_stocks(df, min_turnover, limit_pct)
    df = _add_derived(df)
    df, skipped = _apply_conditions(df, conditions)
    df = _sort_df(df, sort, asc)
    if limit:
        df = df.head(int(limit))
    out = df.astype(object).where(pd.notna(df), None).to_dict("records")
    return {"rows": out, "total": len(out), "skipped": skipped, "category": "个股"}
```

`api/server.py` import 改（约 27 行）：

```python
from screener.conditions import BOARD_FIELDS_CAT, ETF_FIELDS_CAT, STOCK_FIELDS_CAT, OPS
```

`/api/fields`（约 64-73 行）加 `stock_fields` 与 categories 加"个股"：

```python
@app.get("/api/fields")
def fields():
    return _wrap({
        "board_fields": BOARD_FIELDS_CAT,
        "etf_fields": ETF_FIELDS_CAT,
        "stock_fields": STOCK_FIELDS_CAT,
        "ops": OPS,
        "categories": ["行业", "概念", "个股"],
        "indicators": ["今日", "5日", "10日"],
    })
```

`/api/screen`（约 110-117 行）在 ETF 分支后加 stock 分支：

```python
    if category in ("ETF", "etf"):
        res = engine.filter_etfs(conditions=conds, sort=sort, asc=asc, limit=limit)
        return _wrap(res["rows"], {"category": "ETF", "total": res["total"],
                                    "skipped": res["skipped"]})
    if category in ("stock", "个股"):
        res = engine.filter_stocks(conditions=conds, sort=sort, asc=asc, limit=limit)
        return _wrap(res["rows"], {"category": "个股", "total": res["total"],
                                    "skipped": res["skipped"]})
    res = engine.filter_boards(category=category, conditions=conds, sort=sort,
                               asc=asc, limit=limit, indicator=indicator)
    return _wrap(res["rows"], {"category": category, "indicator": indicator,
                               "total": res["total"], "skipped": res["skipped"]})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_filter_stocks.py -q`
Expected: PASS

- [ ] **Step 5: 检查点**

Run: `python -m pytest tests/ -q`，全绿。无 git commit。

---

## Self-Review

**1. Spec 覆盖**：
- P0-1 交易成本 → Task1 ✓
- P0-2 滚动 walk-forward → Task2 ✓
- P0-3 幸存者偏差结构化+记账钩子 → Task3 ✓
- P1-1 反转/Amihud → Task4 ✓
- P1-2 个股 AND 过滤 → Task6 ✓
- P1-3 buffett 缓存 → Task5 ✓
- §9.A-G 边界备忘均在对应 task 注明 ✓

**2. 占位符扫描**：无 TBD/TODO。每个 Step 含完整可粘贴代码。

**3. 类型一致性**：
- `run_backtest` 签名 Task1 加 `cost_bps` + `delisted_codes`（一次性合并），Task3 不再改签名仅改 server 传参。✓
- `fetch_abstract` 返回 `tuple[DataFrame|None, bool]`，`analyze` 调用处同步改。✓
- `survivorship_status()` Task3 定义并同 task 在 server 调用。✓
- `STOCK_FIELDS_CAT` Task6 定义、server 同 task import。✓

**4. 实现顺序**：Task1→2→3→4→5→6，与 spec §8 一致（P1-3 schema 先于 P1-2）。✓

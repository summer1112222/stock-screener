# 优质选股筛选 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `backtest/quality.py` 新增多口径共振编排层，复用 buffett/signals/smart_money/candidates 现有因子，输出"分口径并列 + 共振主清单 + 组合约束"的优质观察清单。

**Architecture:** 方案 B —— 不强行合成一个总分。四口径各算 0-1 横截分位（tradable 集内 z-score→rank pct），共振分 `resonance=hits×10+命中口径平均分位`（命中数为主键），`min_dims` 门槛筛主清单，组合层贪心应用行业分散+相关性+容量约束。个股/ETF 不混跑，ETF 口径 2 恒空。

**Tech Stack:** Python 3 / pandas 3.0.3 / FastAPI / 复用 `data.db` / `backtest.{eval,signals,buffett,candidates}` / `screener.smart_money` / `data.smart_money`。

**Spec:** `docs/superpowers/specs/2026-07-15-quality-screener-design.md`

## Global Constraints

- **合规**（最高优先级）：措辞用"优质筛选/观察清单/共振/分位/命中口径/通过预筛"，禁"推荐/买入/卖出/买卖点/强势股/必涨/看好"；`resonance` 是"多口径同靠前"事实陈述非收益预测；权重默认等权不预设风格；清单不输出买卖点/仓位；路由用 `_wrap`+`cand_disclaimer`："多口径共振机械排序观察清单，非荐股非买卖信号，盈亏自负。"
- **NaN→None**：数值出口必须 `df.astype(object).where(pd.notna(df), None)` 或标量 `_to_float`+NaN 检查（防 starlette `allow_nan=False` 500）；分位缺失为 None，`hits` 计算时 None 不算命中不计分母。
- **异常不崩**：每因子源调用包 try/except，异常→该口径 `dim_status="err:..."` 分位 0，不抛到 `quality_rank` 外。
- **不新增表/采集源/SQLite 列**：复用 `stock_spot`/`etf_spot`/`*_daily`/`smart_money_action`；因子源文件 buffett.py/signals.py/smart_money.py/candidates.py **不动**，只读结果。
- **日期过滤**用 `pd.to_datetime` 比较（非字符串）；pandas 3.0 用 `ME`/`QE`。
- **默认值**：`min_turnover=5e7` / `max_per_board=3` / `max_corr=0.85` / `limit=20` / `min_dims=2` / 分位阈值 `dim_thresh=0.6` / `min_signals=2` / `days=20`。

---

## File Structure

- **Create:** `backtest/quality.py` — 编排层（tradable 预筛→口径分位→共振→组合层），单文件内聚。
- **Modify:** `api/server.py` — 新增 `/api/quality` 路由。
- **Modify:** `web/index.html` — 新增"优质筛选" card（HTML + JS）。
- **Create:** `tests/test_quality.py` — 11 条合成单测。
- **Modify:** `CLAUDE.md` — 架构/路由速查补 quality。

**接口（跨 task 一致）：**
```python
def quality_rank(
    universe: str,                 # "stock" | "etf"
    days: int = 20,
    weights: dict[int, float] | None = None,  # {1:w,2:w,3:w,4:w} 默认等权
    min_dims: int = 2,
    dim_thresh: float = 0.6,
    min_turnover: float = 5e7,
    max_per_board: int = 3,
    max_corr: float = 0.85,        # 0 关闭
    limit: int = 20,
    min_signals: int = 2,
    limit_pct: float = 9.9,
) -> dict
# 返回: {"main":[...], "by_dim":{"1":[...],...}, "dims_available":[..],
#        "dim_status":{...}, "min_dims":int, "cand_disclaimer":str, "error":str|None}
```

---

### Task 1: quality.py 骨架 + tradable 预筛 + 分位框架

**Files:** Create `backtest/quality.py`; Test `tests/test_quality.py`

- [ ] **Step 1: Write failing test**

`tests/test_quality.py`:
```python
# -*- coding: utf-8 -*-
"""优质选股筛选单测：合成数据 mock db.query_rows / 因子源，不触网。
合规：只验分位/共振/组合逻辑，不涉买卖点/收益。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from backtest import quality
from data import db


SPOT_STOCK = [
    {"code": "000001", "name": "甲", "latest_price": 10.0, "change_pct": 2.0,
     "turnover_amount": 1e8, "turnover_rate": 3.0, "main_net_inflow": 5e7},
    {"code": "000002", "name": "乙ST", "latest_price": 5.0, "change_pct": 1.0,
     "turnover_amount": 1e7, "turnover_rate": 1.0, "main_net_inflow": 0},
    {"code": "000003", "name": "丙涨停", "latest_price": 8.0, "change_pct": 10.0,
     "turnover_amount": 2e8, "turnover_rate": 5.0, "main_net_inflow": 1e8},
]


def test_tradable_filter_applied(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_turnover=5e7, limit_pct=9.9)
    codes = {r["code"] for r in res["main"]}
    assert "000001" in codes
    assert "000002" not in codes   # ST + 低成交额
    assert "000003" not in codes   # 涨停
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality.py::test_tradable_filter_applied -q`
Expected: FAIL `ModuleNotFoundError: No module named 'backtest.quality'`

- [ ] **Step 3: Write minimal implementation**

`backtest/quality.py`:
```python
# -*- coding: utf-8 -*-
"""优质选股筛选编排层：四口径分位 + 共振层 + 组合层。

合规：多口径共振机械排序观察清单，非荐股非买卖信号，不承诺收益。
      resonance 是"多口径同靠前"事实陈述，非收益预测/推荐强度。
      权重默认等权，不预设风格。
不新增表/采集源：复用 stock_spot/etf_spot/*_daily/smart_money_action，
      因子源(buffett/signals/smart_money/candidates)只读结果，不改。
"""
from __future__ import annotations

import pandas as pd

from data import db

_SPOT_TABLE = {"stock": "stock_spot", "etf": "etf_spot"}
_CAND_DISCLAIMER = ("多口径共振机械排序观察清单，非荐股非买卖信号，"
                    "不构成投资建议、不承诺收益。市场有风险，盈亏自负。")


def _to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    try:
        if pd.isna(f):
            return None
    except (TypeError, ValueError):
        pass
    return f


def _zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    std = s.std(ddof=0)
    return (s - s.mean()) / std if std else s * 0


def _to_pct(s: pd.Series) -> pd.Series:
    """横截分位 0-1（越大越好）。"""
    return s.rank(pct=True, method="average")


def _tradable(df: pd.DataFrame, min_turnover: float, limit_pct: float) -> pd.DataFrame:
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


def quality_rank(universe="stock", days=20, weights=None, min_dims=2,
                 dim_thresh=0.6, min_turnover=5e7, max_per_board=3,
                 max_corr=0.85, limit=20, min_signals=2, limit_pct=9.9) -> dict:
    table = _SPOT_TABLE.get(universe)
    if not table:
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": min_dims, "cand_disclaimer": _CAND_DISCLAIMER,
                "error": f"不支持的 universe={universe}"}
    rows = db.query_rows(table)
    if not rows:
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": min_dims, "cand_disclaimer": _CAND_DISCLAIMER,
                "error": f"{table} 为空，先 /api/refresh"}
    df = _tradable(pd.DataFrame(rows), min_turnover, limit_pct)
    if df.empty:
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": min_dims, "cand_disclaimer": _CAND_DISCLAIMER,
                "error": "tradable 预筛后为空"}
    codes = df["code"].astype(str).tolist() if "code" in df.columns else []
    out = [{"code": c, "name": df.loc[df["code"].astype(str) == c, "name"].iloc[0]
            if "name" in df.columns else c,
            "resonance": None, "hits": 0, "dim_scores": {}, "reasons": []}
           for c in codes]
    return {"main": out[:limit], "by_dim": {}, "dims_available": [],
            "dim_status": {}, "min_dims": min_dims,
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_quality.py::test_tradable_filter_applied -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): skeleton + tradable filter + percentile framework"
```

---

### Task 2: 四口径分位计算 + 因子源对接 + 降级

**Files:** Modify `backtest/quality.py`; Test `tests/test_quality.py`

- [ ] **Step 1: Write failing tests**（追加到 `tests/test_quality.py`）

```python
def test_degrade_no_history(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    res = quality.quality_rank("stock", min_dims=2)
    assert 1 not in res["dims_available"]
    assert 4 not in res["dims_available"]
    assert "err" in res["dim_status"].get("1", "")
    assert res["min_dims"] <= len(res["dims_available"])
    assert res["error"] is None


def test_dim_scores_percentile(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    close = pd.DataFrame(
        {"000001": [10.0]*20 + [11.0], "000003": [10.0]*20 + [10.5]},
        index=pd.date_range("2026-06-01", periods=21))
    monkeypatch.setattr(bt_eval, "load_panel",
                        lambda uni, codes, s, e, field: close if field == "close" else pd.DataFrame())
    res = quality.quality_rank("stock", min_dims=1, dim_thresh=0.0)
    ds = {r["code"]: r["dim_scores"] for r in res["main"]}
    m1, m3 = ds.get("000001", {}).get(1), ds.get("000003", {}).get(1)
    if m1 is not None and m3 is not None:
        assert m1 >= m3   # 000001 涨多=动量高=风险口径分位高
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quality.py::test_degrade_no_history tests/test_quality.py::test_dim_scores_percentile -q`
Expected: FAIL（`_dim_scores` 未实现）

- [ ] **Step 3: Write implementation**（追加到 `backtest/quality.py`，并改 `quality_rank`）

```python
def _dim_scores(df, universe, days, min_signals):
    """四口径分位。返回 (code->{dim:pct}, dims_available, dim_status)。
    任一因子源失败→该口径 err、分位 None、不崩。"""
    codes = df["code"].astype(str).tolist() if "code" in df.columns else []
    scores = {c: {} for c in codes}
    dims_avail, status = [], {}

    # 口径1 风险调整(历史)
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        if close is None or close.empty:
            status["1"] = "err:无历史数据，先 /api/backtest/fetch"
        else:
            ret = close.pct_change().dropna(how="all")
            vol = ret.std().clip(lower=1e-9)
            mom = close.pct_change(days).iloc[-1]
            sharpe = ret.mean() / vol
            dd = (close / close.cummax() - 1).min()
            comp = (_zscore(-vol) + _zscore(mom) + _zscore(sharpe) + _zscore(-dd)) / 4
            pct = _to_pct(comp)
            for c in codes:
                scores[c][1] = _to_float(pct.get(c)) if c in pct.index else None
            dims_avail.append(1); status["1"] = "ok"
    except Exception as e:
        status["1"] = f"err:{e}"

    # 口径2 价值质量(仅个股,buffett 按需)
    if universe == "stock":
        try:
            import backtest.buffett as bt_buf
            if not getattr(bt_buf, "_AK_OK", False):
                status["2"] = "err:_AK_OK=False"
            else:
                results = bt_buf.analyze_many(codes)
                def _bad(r):
                    rt = r.get("ratios", {})
                    if rt.get("goodwill_to_equity_pct") and rt["goodwill_to_equity_pct"] > 30:
                        return True
                    if rt.get("debt_ratio_latest") is not None and rt["debt_ratio_latest"] > 75:
                        return True
                    if rt.get("fcf_to_netincome") is not None and rt["fcf_to_netincome"] < 0.3:
                        return True
                    return False
                results = [r for r in results if not _bad(r)]
                ey = pd.Series({r["code"]: r.get("earnings_yield_pct") for r in results}).dropna()
                moat = pd.Series({r["code"]: r.get("moat_score") for r in results}).dropna()
                lroe = pd.Series({r["code"]: (r.get("ratios") or {}).get("leverage_adj_roe")
                                  for r in results}).dropna()
                comp = (_zscore(ey) + _zscore(moat) + _zscore(lroe)) / 3
                pct = _to_pct(comp)
                for c in codes:
                    scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
                dims_avail.append(2); status["2"] = "ok"
        except Exception as e:
            status["2"] = f"err:{e}"
    else:
        status["2"] = "err:ETF 无基本面(口径2恒空)"

    # 口径3 资金流向(smart_money 累计 + spot 当日 + 换手)
    try:
        import screener.smart_money as sm_q
        sm = sm_q.top_by_amount(days=days, market=None, channel=None, limit=10000)
        sm_amt = {r.get("code"): r.get("amount") for r in sm.get("rows", [])}
        spot_amt = pd.to_numeric(df.get("main_net_inflow"), errors="coerce") \
            if "main_net_inflow" in df.columns else pd.Series(dtype=float)
        spot_tr = pd.to_numeric(df.get("turnover_rate"), errors="coerce") \
            if "turnover_rate" in df.columns else pd.Series(dtype=float)
        sm_s = pd.Series([sm_amt.get(c) for c in codes], index=codes, dtype=float)
        comp = (_zscore(sm_s) + _zscore(spot_amt.reindex(codes)) +
                _zscore(spot_tr.reindex(codes))) / 3
        pct = _to_pct(comp)
        for c in codes:
            scores[c][3] = _to_float(pct.get(c)) if c in pct.index else None
        dims_avail.append(3)
        status["3"] = "ok(仅spot)" if not sm.get("rows") else "ok"
    except Exception as e:
        status["3"] = f"err:{e}"

    # 口径4 多信号(历史)
    try:
        import backtest.signals as bt_sig
        scan = bt_sig.scan_signals(universe, codes)
        if scan.get("error"):
            status["4"] = f"err:{scan['error']}"
        else:
            trig = {r["code"]: len(r["signals"]) for r in scan.get("rows", [])}
            s = pd.Series([trig.get(c, 0) for c in codes], index=codes, dtype=float)
            pct = (s / 5).clip(0, 1)
            for c in codes:
                scores[c][4] = _to_float(pct.get(c)) if c in pct.index else None
            dims_avail.append(4); status["4"] = "ok"
    except Exception as e:
        status["4"] = f"err:{e}"

    return scores, dims_avail, status
```

改 `quality_rank`：在 `_tradable` 后、`out` 构造前插入调用 + 用 scores 填 dim_scores：
```python
    scores, dims_avail, dim_status = _dim_scores(df, universe, days, min_signals)
    out = []
    for c in codes:
        out.append({"code": c,
                    "name": df.loc[df["code"].astype(str) == c, "name"].iloc[0]
                    if "name" in df.columns else c,
                    "resonance": None, "hits": 0,
                    "dim_scores": scores.get(c, {}), "reasons": []})
    eff_min_dims = min(min_dims, len(dims_avail)) if dims_avail else 0
    return {"main": out[:limit], "by_dim": {}, "dims_available": dims_avail,
            "dim_status": dim_status, "min_dims": eff_min_dims,
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): four-dimension percentile + degrade-on-failure"
```

---

### Task 3: 共振层（hits + resonance + min_dims 门槛）

**Files:** Modify `backtest/quality.py`; Test `tests/test_quality.py`

- [ ] **Step 1: Write failing tests**（追加）

```python
def test_resonance_hits_formula():
    a = quality._resonance({1: 0.9, 2: 0.8, 3: 0.7}, 0.6)   # hits=3
    b = quality._resonance({1: 0.99}, 0.6)                  # hits=1
    assert a > b
    assert a == 3 * 10 + (0.9 + 0.8 + 0.7) / 3


def test_resonance_hits_priority(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount",
                        lambda **kw: {"rows": [{"code": "000001", "amount": 1e9},
                                               {"code": "000003", "amount": 1e3}],
                                      "total": 2})
    res = quality.quality_rank("stock", min_dims=1, dim_thresh=0.6)
    if res["main"]:
        assert res["main"][0]["code"] == "000001"   # 资金分位高排前


def test_min_dims_gate(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount",
                        lambda **kw: {"rows": [{"code": "000001", "amount": 1e9}],
                                      "total": 1})
    res = quality.quality_rank("stock", min_dims=2)   # clamp→1
    assert any(r["code"] == "000001" for r in res["main"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quality.py::test_resonance_hits_formula -q`
Expected: FAIL（`_resonance` 未实现）

- [ ] **Step 3: Write implementation**（追加 `_resonance` + 改 `quality_rank`）

```python
def _resonance(dim_scores, dim_thresh, weights=None):
    """resonance = hits×10 + 命中口径加权平均分位。返回 (resonance, hits)。"""
    w = weights or {}
    hits, wsum, wtot = 0, 0.0, 0.0
    for d, pct in dim_scores.items():
        if pct is None:
            continue
        if pct >= dim_thresh:
            hits += 1
            wi = w.get(d, 1.0)
            wsum += pct * wi; wtot += wi
    avg = (wsum / wtot) if wtot > 0 else 0.0
    return hits * 10 + avg, hits
```

改 `quality_rank`：在 `_dim_scores` 后替换 `out`/`return` 块为：
```python
    scores, dims_avail, dim_status = _dim_scores(df, universe, days, min_signals)
    eff_min_dims = min(min_dims, len(dims_avail)) if dims_avail else 0
    enriched, by_dim = [], {d: [] for d in (1, 2, 3, 4)}
    for c in codes:
        ds = scores.get(c, {})
        res, hits = _resonance(ds, dim_thresh, weights)
        name = df.loc[df["code"].astype(str) == c, "name"].iloc[0] if "name" in df.columns else c
        item = {"code": c, "name": name, "resonance": _to_float(res),
                "hits": hits, "dim_scores": ds, "reasons": []}
        enriched.append(item)
        for d in (1, 2, 3, 4):
            if ds.get(d) is not None:
                by_dim[d].append({**item, "_pct": ds[d]})
    for d in by_dim:
        by_dim[d].sort(key=lambda x: x.get("_pct") or 0, reverse=True)
        by_dim[d] = [{k: v for k, v in x.items() if k != "_pct"} for x in by_dim[d]]
    main = [it for it in enriched if it["hits"] >= eff_min_dims]
    main.sort(key=lambda x: x["resonance"] or 0, reverse=True)
    # 组合层 Task 4 在此裁剪
    return {"main": main[:limit], "by_dim": by_dim, "dims_available": dims_avail,
            "dim_status": dim_status, "min_dims": eff_min_dims,
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): resonance layer (hits×10 + weighted avg, min_dims gate)"
```

---

### Task 4: 组合层（max_per_board 贪心 + max_corr 贪心）

**Files:** Modify `backtest/quality.py`; Test `tests/test_quality.py`

- [ ] **Step 1: Write failing tests**（追加）

```python
def test_max_per_board_greedy(monkeypatch):
    spot = [
        {"code": "000001", "name": "甲", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 1e8, "board": "银行"},
        {"code": "000002", "name": "乙", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 9e7, "board": "银行"},
        {"code": "000003", "name": "丙", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 8e7, "board": "银行"},
        {"code": "600001", "name": "丁", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 5e7, "board": "地产"},
    ]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: spot if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount", lambda **kw: {"rows": [], "total": 0})
    res = quality.quality_rank("stock", min_dims=1, max_per_board=2, limit=10, dim_thresh=0.0)
    codes = [r["code"] for r in res["main"]]
    bank = [c for c in codes if c in ("000001", "000002", "000003")]
    assert len(bank) <= 2


def test_max_corr_greedy(monkeypatch):
    spot = [
        {"code": "000001", "name": "甲", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 1e8},
        {"code": "000002", "name": "乙", "latest_price": 10, "change_pct": 1,
         "turnover_amount": 1e8, "turnover_rate": 3, "main_net_inflow": 9e7},
    ]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: spot if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    close = pd.DataFrame({"000001": list(range(1, 31)), "000002": list(range(1, 31))},
                         index=pd.date_range("2026-06-01", periods=30))
    monkeypatch.setattr(bt_eval, "load_panel",
                        lambda uni, codes, s, e, field: close if field == "close" else pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    import screener.smart_money as sm_q
    monkeypatch.setattr(sm_q, "top_by_amount", lambda **kw: {"rows": [], "total": 0})
    res = quality.quality_rank("stock", min_dims=1, max_corr=0.5, limit=10, dim_thresh=0.0)
    assert len(res["main"]) <= 1   # 完全相关(=1.0) 超 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quality.py::test_max_per_board_greedy tests/test_quality.py::test_max_corr_greedy -q`
Expected: FAIL（组合层未裁剪）

- [ ] **Step 3: Write implementation**（追加 + 改 `quality_rank`）

```python
def _board_of(code, universe, df_spot):
    if "board" in df_spot.columns:
        row = df_spot[df_spot["code"].astype(str) == str(code)]
        if not row.empty and pd.notna(row["board"].iloc[0]):
            return str(row["board"].iloc[0])
    if universe == "stock":
        try:
            for b in db.query_rows("industry_board"):
                members = b.get("members") or b.get("stocks") or []
                if str(code) in [str(m) for m in members]:
                    return b.get("name", "未知")
        except Exception:
            pass
    return "未知"


def _corr_matrix(universe, codes):
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        if close is None or close.empty:
            return None
        return close.pct_change().corr()
    except Exception:
        return None


def _apply_combo(main, universe, df_spot, max_per_board, max_corr, limit):
    corr = _corr_matrix(universe, [r["code"] for r in main]) if max_corr > 0 else None
    kept, board_cnt = [], {}
    for it in main:
        b = _board_of(it["code"], universe, df_spot)
        if board_cnt.get(b, 0) >= max_per_board:
            continue
        if corr is not None and kept:
            mx = 0.0
            for k in kept:
                try:
                    v = corr.loc[it["code"], k["code"]]
                    if pd.notna(v) and abs(v) > mx:
                        mx = abs(v)
                except (KeyError, IndexError):
                    pass
            if mx > max_corr:
                continue
        it["constraints"] = {"board": b, "board_count_in_pool": board_cnt.get(b, 0) + 1}
        kept.append(it); board_cnt[b] = board_cnt.get(b, 0) + 1
        if len(kept) >= limit:
            break
    return kept
```

改 `quality_rank`：`main.sort(...)` 后、`return` 前插入：
```python
    main = _apply_combo(main, universe, df, max_per_board, max_corr, limit)
```
并把 `return` 的 `"main": main[:limit]` 改为 `"main": main`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): combo layer (max_per_board + max_corr greedy)"
```

---

### Task 5: ETF 分层 + NaN→None + reasons + disclaimer

**Files:** Modify `backtest/quality.py`; Test `tests/test_quality.py`

- [ ] **Step 1: Write failing tests**（追加）

```python
def test_etf_dim2_empty(monkeypatch):
    etf = [{"code": "510300", "name": "沪深300ETF", "latest_price": 4.0,
            "change_pct": 1.0, "turnover_amount": 1e8, "turnover_rate": 2.0,
            "main_net_inflow": 5e7}]
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: etf if table == "etf_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    res = quality.quality_rank("etf", min_dims=2)
    assert 2 not in res["dims_available"]
    assert res["min_dims"] <= len(res["dims_available"])
    assert "ETF" in res["dim_status"].get("2", "")


def test_nan_to_none(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    import backtest.eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda *a, **k: pd.DataFrame())
    import backtest.buffett as bt_buf
    monkeypatch.setattr(bt_buf, "_AK_OK", False)
    res = quality.quality_rank("stock", min_dims=1)
    for r in res["main"] + sum(res["by_dim"].values(), []):
        assert isinstance(r.get("resonance"), (int, float, type(None)))
        for v in r.get("dim_scores", {}).values():
            assert v is None or isinstance(v, (int, float))


def test_disclaimer_attached(monkeypatch):
    monkeypatch.setattr(db, "query_rows",
                        lambda table, **kw: SPOT_STOCK if table == "stock_spot" else [])
    res = quality.quality_rank("stock", min_dims=1)
    assert "cand_disclaimer" in res
    assert "非荐股" in res["cand_disclaimer"]
```

- [ ] **Step 2: Run tests to verify they fail/partial**

Run: `python -m pytest tests/test_quality.py::test_etf_dim2_empty tests/test_quality.py::test_nan_to_none tests/test_quality.py::test_disclaimer_attached -q`
Expected: 部分 FAIL（reasons/ETF 文案/NaN 边界）

- [ ] **Step 3: Write implementation**（追加 `_build_reasons` + 改 `quality_rank` 出口）

```python
def _build_reasons(item):
    ds = item.get("dim_scores", {})
    names = {1: "风险调整", 2: "价值质量", 3: "资金流向", 4: "多信号"}
    parts = [f"{names[d]}(分位{round(p, 2)})"
             for d, p in sorted(ds.items()) if p is not None]
    base = "命中 " + " + ".join(parts) if parts else "无口径命中"
    return [f"{base}，共振{item.get('hits', 0)}档"]
```

改 `quality_rank` 出口（`return` 前插入）：
```python
    def _clean_item(it):
        it["reasons"] = _build_reasons(it)
        it["dim_scores"] = {d: _to_float(v) for d, v in it.get("dim_scores", {}).items()}
        it["resonance"] = _to_float(it.get("resonance"))
        return it
    main = [_clean_item(it) for it in main]
    by_dim = {d: [_clean_item(it) for it in lst] for d, lst in by_dim.items()}
    return {"main": main, "by_dim": by_dim, "dims_available": dims_avail,
            "dim_status": dim_status, "min_dims": eff_min_dims,
            "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality.py -q`
Expected: PASS（全 11 条）

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality.py
git commit -m "feat(quality): etf layering + nan-to-none + reasons + disclaimer"
```

---

### Task 6: API 路由 + 前端 card + 全量单测 + CLAUDE.md

**Files:** Modify `api/server.py`, `web/index.html`, `CLAUDE.md`

- [ ] **Step 1: Add API route**（smart-money 路由后、buffett 前）

```python
# ------------------------------------------------------------------
# 优质选股筛选（多口径共振机械排序，非荐股）
# ------------------------------------------------------------------
@app.get("/api/quality")
def quality_screen(universe: str = Query("stock"), days: int = Query(20),
                   min_dims: int = Query(2), min_turnover: float = Query(5e7),
                   max_per_board: int = Query(3), max_corr: float = Query(0.85),
                   limit: int = Query(20)):
    from backtest import quality
    res = quality.quality_rank(
        universe=universe, days=days, min_dims=min_dims,
        min_turnover=min_turnover, max_per_board=max_per_board,
        max_corr=max_corr, limit=limit)
    return _wrap(res, {"cand_disclaimer": res.get("cand_disclaimer",
                       "多口径共振机械排序观察清单，非荐股非买卖信号，盈亏自负。")})
```

- [ ] **Step 2: py_compile check**

Run: `python -m py_compile api/server.py backtest/quality.py`
Expected: 无输出

- [ ] **Step 3: Add frontend card**（主力动向 card 后）

HTML：
```html
  <div class="card">
    <div class="card-t">优质筛选 <span style="color:var(--warn);font-weight:400;margin-left:8px">多口径共振·观察清单·非推荐</span></div>
    <div class="row" style="align-items:center;flex-wrap:wrap;gap:8px">
      <select id="qsUniverse" class="btn ghost"><option value="stock">个股</option><option value="etf">ETF</option></select>
      <button class="btn ghost" id="qsRun" type="button">筛选优质清单</button>
      <span id="qsDims" style="font-size:12px;color:var(--muted)"></span>
    </div>
    <div class="skipped" id="qsMsg"></div>
    <div id="qsOut" style="margin-top:10px"></div>
    <div class="disc"><b>优质筛选免责</b>：多口径(风险调整/价值质量/资金流向/多信号)共振<b>机械排序观察清单，非推荐、非买卖点、不构成投资建议、不承诺收益</b>。共振=多口径同靠前的事实，非收益预测。市场有风险，盈亏自负。</div>
  </div>
```
JS（smart-money JS 后、`</script>` 前加 `<script>...</script>`）：
```javascript
async function qsLoad(){
  const uni=document.getElementById('qsUniverse').value;
  const r=await fetch(`${API}/api/quality?universe=${uni}&days=20&min_dims=2&limit=20`).then(r=>r.json());
  const d=r.data||{};
  document.getElementById('qsDims').textContent='可用口径: '+((d.dims_available||[]).join(',')||'无')+'；主清单'+(d.main||[]).length+'只';
  document.getElementById('qsMsg').textContent=d.error||Object.entries(d.dim_status||{}).map(([k,v])=>`${k}:${v}`).join(' | ')||'';
  const main=d.main||[];
  const ths=['代码','名称','共振','命中','分位(1/2/3/4)','理由'].map(h=>`<th>${h}</th>`).join('');
  const trs=main.length?main.map(x=>{
    const ds=x.dim_scores||{};
    return `<tr class="clk" data-code="${x.code||''}"><td>${x.code||''}</td><td>${x.name||''}</td><td class="num">${x.resonance!=null?x.resonance.toFixed(2):'—'}</td><td class="num">${x.hits||0}</td><td>${[1,2,3,4].map(k=>ds[k]!=null?ds[k].toFixed(2):'—').join('/')}</td><td style="font-size:12px">${(x.reasons||[]).join('；')}</td></tr>`;
  }).join(''):'';
  document.getElementById('qsOut').innerHTML=trs?`<table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`:'<div class="empty">无共振清单(口径不足或历史未拉，先 /api/backtest/fetch)</div>';
}
document.getElementById('qsRun').onclick=qsLoad;
document.getElementById('qsUniverse').onchange=qsLoad;
qsLoad();
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS（原 32 + 新 11 = 43）

- [ ] **Step 5: Update CLAUDE.md**

`backtest/` 模块列表加 `quality.py`（多口径共振编排层）；路由速查加 `/api/quality`（附 `cand_disclaimer`："多口径共振机械排序观察清单，非荐股非买卖信号"）；改动检查清单加"新增 quality 编排层 → 不新增表，复用 stock_spot/etf_spot/*_daily/smart_money_action，因子源文件不动"。

- [ ] **Step 6: Commit**

```bash
git add api/server.py web/index.html CLAUDE.md
git commit -m "feat(quality): /api/quality route + frontend card + CLAUDE.md"
```

---

## Self-Review Notes

- **Spec coverage**: §2骨架→T1; §3因子库→T2; §4共振→T3; §5组合层→T4; §6 ETF分层→T5; §7错误降级→T2+T5; §8测试→各task; §9检查清单→T6.
- **Type consistency**: `quality_rank` 签名全 task 一致; `_resonance`/`_dim_scores`/`_apply_combo` 跨 task 名不变.
- **Default values**: 全 task 用 Global Constraints 默认值, 无漂移.
- **Runtime verify left to Docker**: 宿主无 fastapi/akshare, T6 路由用 py_compile; 实际采集+前端渲染留 `docker compose up --build -d` 后 `http://localhost:8000/web/index.html`.

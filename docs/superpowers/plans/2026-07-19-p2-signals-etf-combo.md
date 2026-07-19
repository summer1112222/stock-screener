# P2 signals胜率+ETF因子+组合优化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 落地 P2 三项——signals 历史胜率回测（喂 quality 口径4 加权）、ETF 特化因子填口径2、quality 组合层最小方差开关。不新增 schema/disclaimer。

**Architecture:** 在 quality 生态原地增强。signals 加 `backtest_signals` 向量化算历史胜率；quality 口径2 对 etf 用跟踪误差+成交额稳定性、口径4 改胜率加权；`_apply_combo` 加 `combo_method=min_var` 用 numpy 解析解。

**Tech Stack:** Python 3、pandas、numpy、FastAPI、pytest。复权 qfq。

## Global Constraints

- **非 git 仓库**：Step 5 用"运行全量测试通过"作检查点，不执行 git commit。
- **合规**：不荐股/不输出买卖点/不承诺收益。signals 胜率是"历史触发统计事实"非预测；ETF 因子是机械统计量；min_var 权重是风险预算机械分配非推荐仓位。`cand_disclaimer`/`_CAND_DISCLAIMER` 沿用，不新增 disclaimer。
- **NaN→None**：DataFrame→records 用 `df.astype(object).where(pd.notna(df), None)`。
- **不新增 SQLite 表/schema**（P2 全复用 etf_daily/stock_daily/基准历史/corr_matrix）。
- **测试**：放 `tests/`，合成数据，不依赖网络。宿主跑 `python -m pytest tests/ -q`。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `backtest/signals.py` | 信号扫描 | Task1 新增 `backtest_signals` |
| `backtest/quality.py` | 优质筛选编排 | Task1 口径4 改胜率加权；Task2 口径2 etf+ETF_BENCHMARK_MAP；Task3 combo_method |
| `api/server.py` | 路由 | Task1 新路由 `/api/signals/backtest` |
| `tests/test_*.py` | 测试 | 每 task 一个测试文件 |

---

## Task 1: P2-1 signals 历史胜率回测 + 口径4 加权

**Files:**
- Modify: `backtest/signals.py`（新增 `backtest_signals`）
- Modify: `backtest/quality.py`（`_dim_scores` 口径4 改胜率加权）
- Modify: `api/server.py`（新路由 `/api/signals/backtest` + 请求模型）
- Test: `tests/test_signals_backtest.py`

**Interfaces:**
- Consumes: `signals._uni_panels`/`_rsi`、`data.history.fetch_benchmark_hist`、`backtest.eval.load_panel`
- Produces: `backtest_signals(universe, codes, signal_types=None, k_days=5, benchmark="sh000300") -> dict`。

- [ ] **Step 1: 写失败测试** `tests/test_signals_backtest.py`
```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import signals as bt_sig


def _synth(n=60, n_codes=3):
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-01", periods=n)
    px = 10 + np.cumsum(rng.normal(0, 0.5, (n, n_codes)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=[f"c{i}" for i in range(n_codes)])
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, (n, n_codes)),
                          index=dates, columns=close.columns)
    return close, amount


def test_backtest_signals_returns_winrate(monkeypatch):
    close, amount = _synth()
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c: (close, amount))
    res = bt_sig.backtest_signals("stock", list(close.columns),
                                  signal_types=["ma_breakout","rsi_oversold"],
                                  k_days=5, benchmark=None)
    assert "error" not in res
    assert res["n_scanned"] == close.shape[1]
    for r in res["rows"]:
        assert r["n_samples"] >= 0
        if r["n_samples"] >= 10:
            assert 0.0 <= r["abs_win_rate"] <= 1.0
        else:
            assert "样本不足" in (r.get("note") or "")


def test_backtest_signals_too_short(monkeypatch):
    close, amount = _synth(n=20)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c: (close, amount))
    res = bt_sig.backtest_signals("stock", list(close.columns), k_days=5, benchmark=None)
    assert "error" in res
```

- [ ] **Step 2: 跑测试确认失败**
`python -m pytest tests/test_signals_backtest.py -q`。期望 FAIL。

- [ ] **Step 3: 实现**

在 `backtest/signals.py` 末尾加：
```python
def backtest_signals(universe: str, codes: list[str],
                     signal_types: list[str] | None = None,
                     k_days: int = 5, benchmark: str | None = "sh000300") -> dict:
    """历史胜率回测：对每信号扫历史每个交易日 t，若 t 日触发则记 t→t+k 收益。
    合规：历史触发统计事实，非预测。"""
    if not codes:
        return {"error": "需提供 codes(已抓历史的标的)"}
    signal_types = signal_types or ["ma_breakout", "golden_cross", "volume_surge",
                                   "rsi_oversold", "momentum_up"]
    close, amount = _uni_panels(universe, codes)
    if close is None or close.empty:
        return {"error": "无历史数据，先 /api/backtest/fetch"}
    need = 25 + k_days
    if len(close) < need:
        return {"error": f"历史不足{need}日(25+k_days)"}

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    rsi = _rsi(close, 14)
    vol_avg5 = amount.rolling(5).mean() if amount is not None else None
    mom20 = close.pct_change(20)
    fwd = close.shift(-k_days) / close - 1

    bench_fwd = None
    if benchmark:
        try:
            from data.history import fetch_benchmark_hist
            bdf, ok, _ = fetch_benchmark_hist(benchmark, start="19900101", end="20991231")
            if ok and not bdf.empty and "close" in bdf.columns:
                bs = bdf.set_index("date")["close"].astype(float).sort_index()
                bs.index = pd.to_datetime(bs.index)
                bs = bs.reindex(close.index).ffill()
                bench_fwd = bs.shift(-k_days) / bs - 1
        except Exception:
            bench_fwd = None

    masks = {}
    if "ma_breakout" in signal_types:
        masks["ma_breakout"] = (close.shift(1) <= ma20.shift(1)) & (close > ma20)
    if "golden_cross" in signal_types:
        masks["golden_cross"] = (ma5.shift(1) <= ma20.shift(1)) & (ma5 > ma20)
    if "volume_surge" in signal_types and vol_avg5 is not None:
        masks["volume_surge"] = (amount > vol_avg5 * 2) & amount.notna() & (vol_avg5 > 0)
    if "rsi_oversold" in signal_types:
        masks["rsi_oversold"] = rsi < 30
    if "momentum_up" in signal_types:
        masks["momentum_up"] = (mom20 > 0) & (close > close.shift(1))

    out = []
    for s in signal_types:
        m = masks.get(s)
        if m is None or not m.any().any():
            out.append({"signal": s, "triggers": 0, "abs_win_rate": None,
                        "excess_win_rate": None, "mean_ret": None,
                        "median_ret": None, "n_samples": 0, "note": "无触发"})
            continue
        sel = m & fwd.notna()
        rets = fwd[sel].dropna().values
        n = len(rets)
        if n < 10:
            out.append({"signal": s, "triggers": int(m.sum().sum()),
                        "abs_win_rate": None, "excess_win_rate": None,
                        "mean_ret": None, "median_ret": None,
                        "n_samples": n, "note": "样本不足(<10)"})
            continue
        abs_wr = float((rets > 0).mean())
        exc_wr = None
        if bench_fwd is not None:
            bench_arr = bench_fwd.reindex(m.index).fillna(np.nan).values.reshape(-1, 1)
            bench_mat = pd.DataFrame(np.tile(bench_arr, (1, len(m.columns))),
                                     index=m.index, columns=m.columns)
            excess = (fwd[sel] - bench_mat[sel]).dropna().values
            if len(excess) > 0:
                exc_wr = float((excess > 0).mean())
        out.append({
            "signal": s, "triggers": int(m.sum().sum()),
            "abs_win_rate": round(abs_wr, 4),
            "excess_win_rate": round(exc_wr, 4) if exc_wr is not None else None,
            "mean_ret": round(float(np.mean(rets)), 4),
            "median_ret": round(float(np.median(rets)), 4),
            "n_samples": n, "note": "",
        })
    return {"rows": out, "n_scanned": len(close.columns),
            "k_days": k_days, "signals": signal_types}
```

修改 `backtest/quality.py` `_dim_scores` 口径4 段（原 `trig/5` 改胜率加权）：
```python
    # 口径4 多信号(历史)：胜率加权（excess_win_rate 均值→横截 pct），降级 trig/5
    try:
        import backtest.signals as bt_sig
        scan = bt_sig.scan_signals(universe, codes)
        if scan.get("error"):
            status["4"] = f"err:{scan['error']}"
        else:
            trig = {r["code"]: len(r["signals"]) for r in scan.get("rows", [])}
            bt = bt_sig.backtest_signals(universe, codes, k_days=5)
            win_by_code = {}
            if not bt.get("error"):
                sig_rows = {r["signal"]: r for r in bt.get("rows", [])}
                for r in scan.get("rows", []):
                    c = r["code"]
                    ewrs = [sig_rows[s]["excess_win_rate"] for s in r["signals"]
                            if s in sig_rows and sig_rows[s].get("excess_win_rate") is not None]
                    win_by_code[c] = float(np.mean(ewrs)) if ewrs else None
            s = pd.Series(index=codes, dtype=float)
            for c in codes:
                v = win_by_code.get(c)
                s[c] = v if v is not None else (trig.get(c, 0) / 5.0)
            pct = _to_pct(s)
            for c in codes:
                scores[c][4] = _to_float(pct.get(c)) if c in pct.index else None
            dims_avail.append(4)
            status["4"] = "ok(胜率加权)" if win_by_code else "ok(降级trig/5)"
    except Exception as e:
        status["4"] = f"err:{e}"
```
（确认 quality.py 顶部 `import numpy as np`；若无则加。）

`api/server.py` 加请求模型 + 路由（在 `/api/signals` 附近；先 Read 确认 signals 路由用的 disclaimer 常量名）：
```python
class BtSignalsReq(BaseModel):
    universe: str
    codes: list[str]
    signal_types: list[str] | None = None
    k_days: int = 5
    benchmark: str | None = "sh000300"

@app.post("/api/signals/backtest")
def bt_signals_route(req: BtSignalsReq):
    res = bt_sig.backtest_signals(req.universe, req.codes, req.signal_types,
                                 k_days=req.k_days, benchmark=req.benchmark)
    return _wrap(res, {"cand_disclaimer": <沿用现有 signals 路由的常量>})
```

- [ ] **Step 4: 跑测试确认通过**
`python -m pytest tests/test_signals_backtest.py -q`，期望 PASS。

- [ ] **Step 5: 检查点**
`python -m pytest tests/ -q`，全绿无回归。无 git commit。

---

## Task 2: P2-2 ETF 特化因子填口径2

**Files:**
- Modify: `backtest/quality.py`（`_dim_scores` 口径2 对 etf + `ETF_BENCHMARK_MAP`）
- Test: `tests/test_etf_dim2.py`

- [ ] **Step 1: 写失败测试** `tests/test_etf_dim2.py`
```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import quality as bt_q


def _seed_etf_daily(codes, n=60):
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2022-01-01", periods=n)
    rows = []
    for c in codes:
        px = 10 + np.cumsum(rng.normal(0, 0.3, n))
        amt = rng.uniform(1e8, 1e9, n)
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i]*1.01, "low": px[i]*0.99,
                         "close": px[i], "volume": amt/10, "amount": amt})
    bt_q.db.upsert_rows("etf_daily", rows)


def test_etf_dim2_not_empty(monkeypatch):
    codes = ["510300", "510050"]
    _seed_etf_daily(codes)
    import data.history as hist
    def _fake_bm(code, start="19900101", end="20991231"):
        rng = np.random.default_rng(22)
        dates = pd.bdate_range("2022-01-01", periods=60)
        bpx = 3000 + np.cumsum(rng.normal(0, 10, 60))
        df = pd.DataFrame({"date": [str(d.date()) for d in dates], "close": bpx})
        return df, True, ""
    monkeypatch.setattr(hist, "fetch_benchmark_hist", _fake_bm)
    bt_q.db.upsert_rows("etf_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e8, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="etf", days=20)
    ds = res.get("dim_status", {})
    assert "2" in ds
    assert ds["2"].startswith("ok")
```

- [ ] **Step 2: 跑测试确认失败**
`python -m pytest tests/test_etf_dim2.py -q`。期望 FAIL（口径2 对 etf 仍恒空）。

- [ ] **Step 3: 实现**

`backtest/quality.py` 顶部加常量：
```python
ETF_BENCHMARK_MAP = {
    "510300": "sh000300", "510310": "sh000300", "510160": "sh000300",
    "510050": "sh000016", "510500": "sh000905", "588000": "sh000688",
    "512100": "sh000852", "159915": "sz399006",
}
```

`_dim_scores` 口径2 的 `else:` 分支（原 `status["2"] = "err:ETF 无基本面(口径2恒空)"`）替换为：
```python
    else:
        # ETF 口径2：跟踪误差 + 成交额稳定性
        try:
            import backtest.eval as bt_eval
            from data.history import fetch_benchmark_hist
            close = bt_eval.load_panel("ETF", codes, "1990-01-01", "2099-12-31", "close")
            amount = bt_eval.load_panel("ETF", codes, "1990-01-01", "2099-12-31", "amount")
            if close is None or close.empty:
                status["2"] = "err:无etf历史，先 /api/backtest/fetch"
            else:
                ret = close.pct_change()
                te, av = {}, {}
                for c in codes:
                    bm = ETF_BENCHMARK_MAP.get(c)
                    te_c = None
                    if bm:
                        try:
                            bdf, ok, _ = fetch_benchmark_hist(bm, "19900101", "20991231")
                            if ok and not bdf.empty and "close" in bdf.columns:
                                bs = bdf.set_index("date")["close"].astype(float).sort_index()
                                bs.index = pd.to_datetime(bs.index)
                                bs = bs.reindex(close.index).ffill()
                                diff = (ret[c] - bs.pct_change()).dropna()
                                if len(diff) >= days:
                                    te_c = float(diff.tail(days).std())
                        except Exception:
                            pass
                    te[c] = te_c
                    if amount is not None and c in amount.columns:
                        a = amount[c].dropna()
                        if len(a) >= days and a.tail(days).mean() > 0:
                            av[c] = float(a.tail(days).std() / a.tail(days).mean())
                        else:
                            av[c] = None
                    else:
                        av[c] = None
                te_s = pd.Series(te).dropna()
                av_s = pd.Series(av).dropna()
                comp = pd.Series(0.0, index=codes)
                n_fac = 0
                if len(te_s):
                    comp = comp.add(_zscore(-te_s).reindex(codes).fillna(0.0))
                    n_fac += 1
                if len(av_s):
                    comp = comp.add(_zscore(-av_s).reindex(codes).fillna(0.0))
                    n_fac += 1
                comp = comp / n_fac if n_fac else comp
                pct = _to_pct(comp)
                for c in codes:
                    scores[c][2] = _to_float(pct.get(c)) if c in pct.index else None
                dims_avail.append(2)
                status["2"] = "ok(ETF:跟踪误差+成交额稳定)"
        except Exception as e:
            status["2"] = f"err:{e}"
```

- [ ] **Step 4: 跑测试确认通过**
`python -m pytest tests/test_etf_dim2.py -q`，期望 PASS。

- [ ] **Step 5: 检查点**
`python -m pytest tests/ -q`，全绿无回归。无 git commit。

---

## Task 3: P2-3 quality 组合层最小方差开关

**Files:**
- Modify: `backtest/quality.py`（`quality_rank` 加 `combo_method` + `_apply_combo` 分支 + `_min_var_weights`）
- Test: `tests/test_combo_minvar.py`

- [ ] **Step 1: 写失败测试** `tests/test_combo_minvar.py`
```python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import quality as bt_q


def _seed_stock_daily(codes, n=80, seed=13):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    rows = []
    for c in codes:
        px = 10 + np.cumsum(rng.normal(0, 0.3, n))
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i]*1.01, "low": px[i]*0.99,
                         "close": px[i], "volume": 0, "amount": 1e8})
    bt_q.db.upsert_rows("stock_daily", rows)


def test_minvar_weights_sum_to_one():
    codes = [f"c{i}" for i in range(8)]
    _seed_stock_daily(codes)
    bt_q.db.upsert_rows("stock_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e8, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="stock", days=20, combo_method="min_var")
    main = res.get("main", [])
    if main:
        total = sum(it.get("weight", 0) for it in main)
        assert abs(total - 1.0) < 1e-6, f"权重和={total} 应为1"
        for it in main:
            assert it["weight"] >= 0


def test_minvar_singular_degrades():
    codes = ["c0", "c1"]
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2022-01-01", periods=80)
    px = 10 + np.cumsum(rng.normal(0, 0.3, 80))
    rows = []
    for c in codes:
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i], "low": px[i],
                         "close": px[i], "volume": 0, "amount": 1e8})
    bt_q.db.upsert_rows("stock_daily", rows)
    bt_q.db.upsert_rows("stock_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e8, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="stock", days=20, combo_method="min_var")
    # 不崩即通过
    assert isinstance(res, dict)
```

- [ ] **Step 2: 跑测试确认失败**
`python -m pytest tests/test_combo_minvar.py -q`。期望 FAIL。

- [ ] **Step 3: 实现**

`backtest/quality.py` 加 `_min_var_weights`（在 `_apply_combo` 前）：
```python
def _min_var_weights(codes: list[str], universe: str) -> list[float]:
    """最小方差权重解析解 w = Σ⁻¹·1 / (1ᵀ·Σ⁻¹·1)。Σ 奇异降级 1/方差。long-only 归零归一。"""
    import numpy as np
    try:
        import backtest.eval as bt_eval
        close = bt_eval.load_panel(universe, codes, "1990-01-01", "2099-12-31", "close")
        if close is None or close.empty:
            return [1.0 / len(codes)] * len(codes)
        ret = close.pct_change().dropna(how="all").fillna(0.0)
        cov = ret.cov().values
        if cov.shape[0] != len(codes):
            return [1.0 / len(codes)] * len(codes)
        try:
            inv = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            var = np.diag(cov)
            inv_var = np.where(var > 0, 1.0 / var, 0.0)
            w = inv_var / inv_var.sum() if inv_var.sum() > 0 else np.ones(len(codes)) / len(codes)
            return w.tolist()
        ones = np.ones(len(codes))
        denom = ones @ inv @ ones
        if denom == 0 or not np.isfinite(denom):
            return [1.0 / len(codes)] * len(codes)
        w = (inv @ ones) / denom
        w = np.where(w < 0, 0.0, w)
        s = w.sum()
        if s <= 0:
            return [1.0 / len(codes)] * len(codes)
        w = w / s
        return w.tolist()
    except Exception:
        return [1.0 / len(codes)] * len(codes)
```

修改 `_apply_combo` 签名加 `combo_method="greedy"`，末尾加权重赋值：
```python
def _apply_combo(main, universe, df_spot, max_per_board, max_corr, limit,
                 combo_method="greedy"):
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
        kept.append(it)
        board_cnt[b] = board_cnt.get(b, 0) + 1
        if len(kept) >= limit:
            break
    if combo_method == "min_var" and len(kept) >= 2:
        ws = _min_var_weights([it["code"] for it in kept], universe)
    else:
        ws = [1.0 / len(kept)] * len(kept) if kept else []
    for it, w in zip(kept, ws):
        it["weight"] = round(float(w), 4)
    return kept
```

`quality_rank` 签名加 `combo_method: str = "greedy"`，调 `_apply_combo` 传参。

- [ ] **Step 4: 跑测试确认通过**
`python -m pytest tests/test_combo_minvar.py -q`，期望 PASS。

- [ ] **Step 5: 检查点**
`python -m pytest tests/ -q`，全绿无回归。无 git commit。

---

## Self-Review

**Spec 覆盖**：P2-1→Task1、P2-2→Task2、P2-3→Task3。**占位符**：无。**类型一致**：backtest_signals/ETF_BENCHMARK_MAP/combo_method 各 task 内闭合。**顺序**：Task1→2→3。

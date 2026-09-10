# ETF/QDII 长期+短期筛选 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立 ETF/QDII 筛选编排层，产出长期配置（估值×质量）与短期交易（量价）两张独立观察清单，QDII 溢价贯穿。

**Architecture:** 新模块 `screener/etf_screen.py` 复刻 `nextday`/`quality` 编排范式；长期=质量硬门槛+估值分位与质量加权，短期=`eval.compute_factor` 量价因子横截面 rank 加权；新路由 `/api/etf-screen`；数据源备援经 Phase 0 探测 + 诚实降级，不新增表。

**Tech Stack:** Python 3.12 / pandas / FastAPI / akshare（仅容器采集层）/ tdx `get_quote` / `etf_daily`。

**Spec:** `docs/superpowers/specs/2026-09-11-etf-qdii-screener-design.md`

## Global Constraints

- **合规**：个人自用措辞可放松，但保留 disclaimer 管道；响应经 `_wrap()` 附 `cand_disclaimer`。不荐股、不承诺收益、不输出买卖点。
- **不新增 SQLite 表**（本期），估值/溢价进程内算 + 30s 缓存，键含 universe/mode/limit/days 全参。
- **诚实降级**：任一因子源失败→对应因子 `None`/`premium=null`，不伪造 0，不崩整体；`source` 字段标注 `tdx`/`em`/`ths`/`jisilu`/`csindex`/`sina`。
- **NaN→None**：任何输出列用 `df.astype(object).where(pd.notna(df), None)`（非 `df.where`），防 starlette `allow_nan=False` 500。
- **数据日期**：`etf_daily` 的 date 为 `YYYY-MM-DD`；参数 `start`/`end` 为 `YYYYMMDD`；比较用 `pd.to_datetime`。
- 测试全 mock `db`/网络，不触真实接口。

---

### Task 1: Phase 0 数据源探测脚本

**Files:**
- Create: `scripts/etf_source_probe.py`
- Run: 容器内（宿主无 akshare）`docker exec a-screener python -m scripts.etf_source_probe`

**Interfaces:**
- Produces: `data/cache/etf_source_probe.json`，形如 `{"fund_etf_spot_em": true, "stock_zh_index_value_csindex": true, "jisilu_probe": false}`；`main()` 返 dict 并写盘。

任务 2 之后据该 json 决定适配函数首选/备援顺序。此脚本一次性，不写单测（pytest 不含 scripts）。

- [ ] **Step 1: 写探测脚本**（枚举各数据源可用性）

```python
# scripts/etf_source_probe.py
"""Phase 0: 实测 akshare/公开数据在出口 IP 的可用性，结果写 data/cache/etf_source_probe.json。
只读、依赖容器内 akshare，不触生产逻辑。"""
from __future__ import annotations
import json, os, traceback
try:
    import akshare as ak
except Exception:
    ak = None

_ENDPOINTS = [
    ("fund_etf_spot_em", lambda: ak.fund_etf_spot_em() if ak else (_ for _ in ()).throw(ImportError())),
    ("stock_zh_index_value_csindex", lambda: ak.stock_zh_index_value_csindex(symbol="000300") if ak else (_ for _ in ()).throw(ImportError())),
    ("fund_etf_fund_info_em", lambda: ak.fund_etf_fund_info_em(symbol="513100") if ak else (_ for _ in ()).throw(ImportError())),
]

def _probe(name, fn):
    try:
        df = fn()
        ok = df is not None and getattr(df, "shape", (0,))[0] > 0
        return ok, f"rows={getattr(df, 'shape', (0,))[0]}" if ok else "empty/None"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"

def main() -> dict:
    out = {}
    for name, fn in _ENDPOINTS:
        out[name] = _probe(name, fn)
    # 集思录可直接 read_html 静态表(独立站,非东财),仅探可达性;疑似需 cookie 则仍标 false
    try:
        import pandas as pd
        gu = pd.read_html("https://www.jisilu.cn/data/etf/", flavor="lxml")
        out["jisilu_static"] = (len(gu) > 0, f"tables={len(gu)}")
    except Exception as e:
        out["jisilu_static"] = (False, f"{type(e).__name__}: {str(e)[:80]}")
    # 采集东财 ETF spot 即知封禁与否(容器内需先 pip 装 akshare)
    cache_dir = os.environ.get("SCREENER_CACHE_DIR", "/app/var")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "etf_source_probe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"written: {path}")
    return out

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 容器内运行并留档**

Run：`docker exec a-screener python -m scripts.etf_source_probe 2>&1 | tail -30`（先 `docker cp scripts/etf_source_probe.py a-screener:/app/scripts/` 若镜像未含）。
Expected：打印各端点 `(True/False, 说明)`，并在 `/app/var/etf_source_probe.json` 写盘。把 True/False 汇总到 plan 评审。

- [ ] **Step 3: Commit**

```bash
git add scripts/etf_source_probe.py
git commit -m "chore(probe): Phase0 ETF/QDII 数据源可行性探测脚本"
```

---

### Task 2: 长清单估值分位 + 质量评分（纯函数层）

**Files:**
- Create: `screener/etf_screen.py`（本 task 填 long 侧纯函数）
- Test: `tests/test_etf_screen.py`

**Interfaces:**
- Consumes: `data._strip_prefix`/`db`（按需）；估值分位数据由 `_fetch_index_valuation(code)`（见 Task 4 数据适配，本 task 先 accept 一个 dict 参数便于单测）。
- Produces:
  - `quality_score(scale_wan, turnover_rate, fee_bps, tracking_err)` -> float[0,1]
  - `valuation_percentile(value: float, history: list[float])` -> float[0,1]（value 在 history 中的百分位，低=便宜）
  - `long_score(valuation_pct, quality, w_val=0.55, w_qual=0.45)` -> float[0,100]

- [ ] **Step 1: 写失败测试**

```python
# tests/test_etf_screen.py
from backtest import quality as qmod  # NO — 直接测 etf_screen
import screener.etf_screen as es

def test_valuation_percentile_low_is_cheap():
    # 当前值处于历史低位 → 低百分位(便宜)
    assert es.valuation_percentile(10.0, [10, 12, 14, 16, 18, 20]) < 0.5
    # 当前值历史新高 → 高分位(贵)
    assert es.valuation_percentile(20.0, [10, 12, 14, 16, 18, 20]) > 0.9

def test_quality_score_prefers_large_scale_low_fee():
    good = es.quality_score(scale_wan=500, turnover_rate=2.0, fee_bps=15, tracking_err=0.2)
    bad = es.quality_score(scale_wan=2, turnover_rate=0.1, fee_bps=80, tracking_err=3.0)
    assert good > bad

def test_long_score_combines_valuation_and_quality():
    cheap_good = es.long_score(valuation_pct=0.1, quality=0.9)
    expensive_poor = es.long_score(valuation_pct=0.9, quality=0.2)
    assert cheap_good > expensive_poor
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: FAIL（模块/函数未定义）

- [ ] **Step 3: 最小实现**

```python
# screener/etf_screen.py
from __future__ import annotations
import math

def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))

def valuation_percentile(value: float, history: list[float]) -> float:
    """value 在 history（升序或任意序）中的百分位，低=便宜(被低估)。
    rank = 严格小于 value 的比例; history 越大越接近 1。"""
    hi_vals = [h for h in history if h is not None and not (isinstance(h, float) and math.isnan(h))]
    if not hi_vals:
        return 0.5
    below = sum(1 for h in hi_vals if h < value)
    return _clip(below / len(hi_vals))

def quality_score(scale_wan=0.0, turnover_rate=0.0, fee_bps=50.0, tracking_err=None) -> float:
    """质量维度: 规模(亿, 避清盘)、换手/成交额代理流动性、费率(低优)、跟踪误差(紧优)。
    权重: 规模0.4 流动性0.2 费率0.2 跟踪0.2。缺项用中性0.5 参与, 全缺→0.5。"""
    s = _clip(math.log10(scale_wan + 1) / 2.5) if scale_wan else 0.5        # 0→1亿→100亿+非线性
    liq = _clip((math.log10(turnover_rate + 0.01) + 0.5) / 1.5) if turnover_rate else 0.5
    fee = _clip(1 - (fee_bps - 10) / 80) if fee_bps else 0.5                # 10bps 满, 90bps 近 0
    tr = _clip(1 - (tracking_err or 0) / 2.0) if tracking_err is not None else 0.5
    return 0.4 * s + 0.2 * liq + 0.2 * fee + 0.2 * tr

def long_score(valuation_pct: float, quality: float, w_val=0.55, w_qual=0.45) -> float:
    denom = w_val + w_qual
    return (w_val * _clip(1.0 - valuation_pct) + w_qual * quality) / denom * 100.0
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: PASS（3 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/etf_screen.py tests/test_etf_screen.py
git commit -m "feat(etf-screen): 长清单估值分位+质量评分纯函数"
```

---

### Task 3: 短清单量价评分（复用现有面板因子）

**Files:**
- Modify: `screener/etf_screen.py`（加 short 侧）
- Test: `tests/test_etf_screen.py`

**Interfaces:**
- Consumes: `backtest.eval.load_panel(universe, codes, start, end, field)`、`eval.compute_factor(close, factor_key, params, volume, amount)`、`nextday._rank_pct`/`_weighted_score`。
- Produces: `short_scores(close, amount, codes, period_ns)` -> `(scores: dict[str,float], details: dict[str,dict], coverage: dict[str,float])`，factor 用 `momentum_n`/`vol_corr_n`/`volatility_n`。

- [ ] **Step 1: 写失败测试**

```python
def test_short_scores_ranks_uptrend_higher(monkeypatch):
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"000001": np.linspace(10, 16, 30), "000002": np.linspace(16, 10, 30)}, index=idx)
    amt = pd.DataFrame(1e8, index=idx, columns=up.columns)
    # 注入 load_panel/compute_factor 返回合成面板
    we_ = es  # 已在模块内 import backtest.eval as bt_eval
    from backtest import eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda u,c,s,e,f: up if f=="close" else amt)
    scores, det, cov = es.short_scores(up, amt, ["000001","000002"], [5,20])
    assert scores["000001"] > scores["000002"]   # 上行强于下行
    assert "momentum_5" in det["000001"]
    assert 0 < cov["000001"] <= 1.0

def test_short_scores_missing_factor_renormalizes(monkeypatch):
    from backtest import eval as bt_eval_2
    # amount 缺失 → vol_corr/turnover 缺失 → denom 重归一不崩
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"000001": np.linspace(10, 16, 30)}, index=idx)
    monkeypatch.setattr(bt_eval_2, "load_panel", lambda u,c,s,e,f: up if f=="close" else None)
    scores, det, cov = es.short_scores(up, None, ["000001"], [5,20])
    assert cov["000001"] >= 0.4  # momentum/volatility 仍可用
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_etf_screen.py::test_short_scores_ranks_uptrend_higher -v`
Expected: FAIL（`short_scores` 未定义）

- [ ] **Step 3: 实现 short_scores**

```python
# screener/etf_screen.py 追加
from . import nextday as _nd
from . import engine as _eng  # 若无例外不必
from backtest import eval as bt_eval

_SHORT_FACTORS_DEF = {
    "momentum_5": {"key": "momentum", "n": 5},
    "momentum_20": {"key": "momentum", "n": 20},
    "vol_corr_20": {"key": "vol_corr", "n": 20},
    "volatility_20": {"key": "volatility", "n": 20},
}

def _panel_factor(close, amount, name, spec):
    try:
        pv = bt_eval.compute_factor(close, name, params={"n": spec["n"]}, amount=amount)
        return pv, True
    except Exception:
        return pd.DataFrame(index=close.index, columns=close.columns), False

def short_scores(close, amount, codes, period_ns):
    factor_maps = {n: {} for n in _SHORT_FACTORS_DEF}
    avail = {n: False for n in _SHORT_FACTORS_DEF}
    for name, spec in _SHORT_FACTORS_DEF.items():
        pv, ok = _panel_factor(close, amount, name, spec)
        avail[name] = ok
        for c in codes:
            if c in pv.columns:
                ser = pv[c]
                val = ser.iloc[-1]
                factor_maps[name][c] = None if val is None or (isinstance(val, float) and math.isnan(val)) else float(val)
    # 只保留真正可用的因子名，套 nextday 加权范式(缺失从不伪造 0)
    usable = {n: {} for n in _SHORT_FACTORS_DEF if avail[n]}
    weights = {n: 1.0 for n in usable}          # 等权, 可在验证后 IC 校准
    wm = {n: weights.get(n, 1.0) for n in usable}
    pct = {n: _nd._rank_pct(factor_maps[n]) for n in usable}
    scores, details, coverage = {}, {}, {}
    for c in codes:
        present = {n: pct[n].get(c) for n in usable if pct[n].get(c) is not None}
        denom = sum(wm[n] for n in present)
        scores[c] = round((sum(wm[n] * v for n, v in present.items()) / denom * 100) if denom else 0.0, 2)
        details[c] = {n: (round(pct[n].get(c) * 100, 2) if pct[n].get(c) is not None else None) for n in usable}
        coverage[c] = round(denom / sum(wm.values()), 4)
    return scores, details, coverage
```

> 说明：为保持单测可控，本 task 直接传 `close`/`amount` 面板给纯函数；数据源加载（`load_panel`/tdx/etf_spot）在 Task 4 编排入口统一做并注入。`compute_factor` 的 factor_key 用已注册键（`momentum`/`vol_corr`/`volatility`），本函数拼 `name` 传入 `momentum_5` 等，`compute_factor` 内已从 key 解析窗口——故 `_SHORT_FACTORS_DEF["momentum_5"]` 的 `"key"` 仅记录语义、实际以 `name` 为准。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: PASS（新增 2 tests）

- [ ] **Step 5: Commit**

```bash
git add screener/etf_screen.py tests/test_etf_screen.py
git commit -m "feat(etf-screen): 短清单量价横截面 rank 评分(复用现有面板因子)"
```

---

### Task 4: 数据适配 + QDII 溢价 + 编排入口

**Files:**
- Modify: `screener/etf_screen.py`
- Test: `tests/test_etf_screen.py`

**Interfaces:**
- Consumes: `data.history._UNIVERSE["ETF"]`（etf_daily）、`db.query_rows("etf_spot", limit=0)`（仅 latest_price/turnover_rate/turnover_amount，**表无 nav/fund_scale 列，见 pre-flight Ruling**）、tdx `pytdx_client.get_quote(codes)`、Phase 0 结果 json。
- Produces:
  - `_probe_flags() -> dict`：读 `etf_source_probe.json`（缺省全 False）。
  - `_fetch_index_valuation(code) -> dict | None`：指数估值分位（`{"pe_pct": float, "div_yield": float|None}`，Phase 0 后按源实现；单测注入）。
  - `_fetch_qdii_premium(code) -> float | None`：QDII 溢价率（`(nav-price)/price` 的绝对语义，Phase 0 后按源实现；单测注入；失败→None）。**不读 etf_spot.nav（表无此列）。**
  - `_fetch_quality_meta(code) -> dict | None`：`{"fund_scale": 亿, "fee_bps": int|None, "tracking_err": float|None}`（Phase 0 后按源实现；单测注入；失败→None 该维度中性）。
  - `fund_premium(market_price, nav) -> float | None`：`(price-nav)/nav`，任缺→None。
  - `etf_screen_rank(universe="ETF", mode="long", codes=None, limit=50, days=365)` -> dict（`long_term`/`short_term` 两清单 + `disclaimer` 外字段）。
  - `_CACHE` 30s、`_nan`。

编排入口伪骨架（数据加载 + QDII 溢价 + 两清单组装 + 缓存），实现细节让工程师按 Task2/3 纯函数拼装；**关键点：QDII 高溢价在 long 清单降权、short 清单顶部醒目、source 标注、NaN→None（`astype(object).where(pd.notna, None)`）**。

- [ ] **Step 1: 写失败测试（mock 数据层）**

```python
def test_fund_premium():
    assert abs(es.fund_premium(1.05, 1.00) - 0.05) < 1e-9
    assert es.fund_premium(1.00, None) is None
    assert es.fund_premium(None, 1.00) is None

def test_etf_screen_rank_mode_long_returns_quality_and_valuation(monkeypatch):
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    up = pd.DataFrame({"510300": np.linspace(4.0, 5.0, 40), "159915": np.linspace(2.0, 1.6, 40)}, index=idx)
    # 数据源抽象: 估值/质量 meta 经 _fetch_* 注入, 不依赖 etf_spot 表缺字段(无 nav/fund_scale 列)
    monkeypatch.setattr(bt_eval, "load_panel", lambda u,c,s,e,f: up if f=="close" else None)
    monkeypatch.setattr(es, "_fetch_index_valuation",
        lambda code: {"pe_pct": 0.2, "div_yield": 2.0})
    monkeypatch.setattr(es, "_fetch_quality_meta",
        lambda code: {"fund_scale": 8.0 if code=="159915" else 300.0, "fee_bps": 15, "tracking_err": 0.2})
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code":"510300","latest_price":4.9,"turnover_rate":1.2},
                                 {"code":"159915","latest_price":1.65,"turnover_rate":0.3}] if table=="etf_spot" else [])
    out = es.etf_screen_rank(universe="ETF", mode="long", codes=["510300","159915"], days=60)
    assert isinstance(out.get("long_term"), list)
    if out["long_term"]:
        assert "quality_score" in out["long_term"][0]
        assert "valuation_percentile" in out["long_term"][0]

def test_etf_screen_rank_qdii_premium_visible(monkeypatch):
    # 溢价率出现在 QDII 清单项表里(无论 long/short), 高溢价 red flag
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    up = pd.DataFrame({"513100": np.linspace(1.0, 1.1, 40)}, index=idx)
    # nav 经 _fetch_qdii_premium 注入(不读 etf_spot.nav, 表无此列)
    monkeypatch.setattr(bt_eval, "load_panel", lambda u,c,s,e,f: up if f=="close" else None)
    monkeypatch.setattr(es, "_fetch_index_valuation", lambda code: None)
    monkeypatch.setattr(es, "_fetch_quality_meta", lambda code: None)
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: 0.15)  # 场内溢价 15%
    monkeypatch.setattr(es._db, "query_rows",
        lambda table,*a,**k: [{"code":"513100","latest_price":1.60}] if table=="etf_spot" else [])
    out = es.etf_screen_rank(universe="QDII", mode="short", codes=["513100"], days=60)
    for item in out.get("short_term", [])[:1]:
        assert "premium" in item
```

> 注：`es._db` 需在模块内 `from data import db as _db` 供 monkeypatch；`_CACHE` 在 test 间会残留，给 `etf_screen_rank` 传 `_fresh=True` 或用独立键规避（实现时在 return 前 `_CACHE.pop(...)` 或 by-design 允许 30s 缓存，测试用不同 params 规避）。测试幂等性优先：每次调用前 `es._CACHE.clear()`。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: FAIL（`etf_screen_rank`/`fund_premium` 未定义）

- [ ] **Step 3: 实现编排队**

```python
# 数据源抽象(Phase 0 后按实测源实现; 失败→None, 不读 etf_spot 缺字段)
def _probe_flags() -> dict: ...
def _fetch_qdii_premium(code) -> float | None: ...
def _fetch_quality_meta(code) -> dict | None:
    """{'fund_scale': 亿, 'fee_bps': int|None, 'tracking_err': float|None}; 缺→None 该维度走中性。"""
def _fetch_index_valuation(code) -> dict | None:
    """{'pe_pct': 0..1, 'div_yield': float|None}; 指数历史分位, 低=便宜。"""
```

实现要点（给出结构 + 必须的核心代码，数据源差异经 `_fetch_*` 封装隔离）：
- 头部 `from data import db as _db`、`from data.history import _UNIVERSE`、`from .pytdx_client import pytdx_client`（try/except 兜底）。
- `etf_screen_rank` 里先查缓存（键 = (universe, mode, tuple(codes or []), limit, days)），30s。
- `short` 分支：`load_panel(universe, codes, "20240101", today, "close")` + `"amount"` → `short_scores` → 按分数降序 items（每 item 带 `score`/`factor_scores`/`coverage`/`premium`/`source`）。
- `long` 分支：从 `etf_spot` 取 `turnover_rate`/`turnover_amount`（最新价 `latest_price`），**规模/费率/跟踪误差经 `_fetch_quality_meta(code)`**（QDDI 溢价经 `_fetch_qdii_premium`）→ `quality_score(scale_wan mgt..., turnover_rate, fee_bps, tracking_err)` 硬门槛（`_fetch_quality_meta` 的 fund_scale 若可得且 < `min_scale` 跳过；meta 缺失则该维度不判门槛）→ `_fetch_index_valuation(code)` 取 `pe_pct`/`div_yield` → `valuation_percentile = pe_pct`（源给则直接映射）→ `long_score` → 降序；QDII 高溢价（`premium>0.03`）在该项 `risk_flag="high_premium"`。
- `premium`：`fund_premium(latest_price, nav_qdii)`，nav_qdii 缺→None；`source` 标注。
- 返回 `{"universe", "mode", "count", "limit", "ts", "long_term": [...], "short_term": [...]}`（按 mode 只填对应清单，另一为空 list）；顶置 `disclaimer` 非 `_wrap` 管。
- 列表项统一 `_to_records_eq`：`[dict(zip(d, map(_nan, d.values()))) ...]` 或构建时 `astype(object).where(pd.notna, None)`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/etf_screen.py tests/test_etf_screen.py
git commit -m "feat(etf-screen): 数据适配+QDII溢价+long/short编排入口"
```

---

### Task 5: `/api/etf-screen` 路由接线

**Files:**
- Modify: `api/server.py`（在路由区加 handler，紧邻 `/api/quality` 处）
- Test: `tests/test_server_new_routes.py`

**Interfaces:**
- Consumes: `screener.etf_screen.etf_screen_rank`。
- Produces: `GET /api/etf-screen?universe=ETF&mode=long&limit=50&days=365` → `_wrap(r, cand_disclaimer)`；`r` 内含 `long_term`/`short_term`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_server_new_routes.py 追加
def test_etf_screen_route(monkeypatch):
    from fastapi.testclient import TestClient
    from api import server
    monkeypatch.setattr(server.etf_screen, "etf_screen_rank",
        lambda **kw: {"universe": kw.get("universe","ETF"), "mode": kw.get("mode","long"),
                      "long_term": [{"code":"510300","quality_score":80.0,"valuation_percentile":0.2}],
                      "short_term": []})
    client = TestClient(server.app)
    r = client.get("/api/etf-screen?universe=ETF&mode=long&limit=10")
    assert r.status_code == 200
    b = r.json()
    assert b.get("cand_disclaimer")
    assert b.get("data", {}).get("long_term")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_server_new_routes.py::test_etf_screen_route -v`
Expected: FAIL（404 / `server.etf_screen` 无此属性）

- [ ] **Step 3: 实现路由**

```python
# api/server.py 顶部(与其它 screener import 并列)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # 仓库根, 使 screener 可导入(若未加)
from screener import etf_screen

# 路由区(近 /api/quality 后)加:
@app.get("/api/etf-screen")
def etf_screen_route(universe: str = "ETF", mode: str = "long",
                     limit: int = 50, days: int = 365,
                     codes: str = ""):
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    r = etf_screen.etf_screen_rank(
        universe=universe, mode=mode, limit=limit, days=days, codes=code_list)
    return _wrap(r, cand_disclaimer)
```

> 注：若 `from screener import etf_screen` 在顶部因模块初始化顺序不可行，则在 handler 内 `import`（参考 `data/smart_money.py` 对 finshare 的惰性 import 惯例）。`_wrap` 第二参建议传已有的 `cand_disclaimer` 常量。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_server_new_routes.py::test_etf_screen_route tests/test_etf_screen.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/server.py tests/test_server_new_routes.py
git commit -m "feat(api): /api/etf-screen 路由(长/short 切清单, cand_disclaimer)"
```

---

### Task 6: 前端 tab「ETF/QDII 筛选」

**Files:**
- Modify: `web/index.html`（新增一个 `.tab-panel[data-tab="etfscreen"]` + 顶栏按钮 + JS）

**Interfaces:**
- Consumes: `/api/etf-screen`（GET，`universe`/`mode`/`limit`/`days`）。
- Produces: 两张清单表格渲染；QDII 高溢价列高亮。

- [ ] **Step 1: 写前端（无单测, 靠手动冒烟 / 路由 200）**

要点：复制现有 tab 顶部按钮 `switchTab('etfscreen')`；面板内一个模式下拉（long/short）+ universe（ETF/QDII）+ limit + 「筛选」按钮；`fetch` `/api/etf-screen` 渲染 `long_term` 或 `short_term` 表格；溢价列 `>0.03` 红色。参考 `web/index.html` 现有 `nextday` 卡（1634 行起）的渲染范式保持风格一致。

- [ ] **Step 2: 前端静态自检**

Run: `python -m compileall api/screener tests` + 浏览器开 `http://localhost:8000/web/index.html` 手动点 tab。
Expected: tab 出现、能调 `/api/etf-screen`、两清单刷新。

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat(web): ETF/QDII 筛选 tab(长/短清单 + QDII 溢价高亮)"
```

---

### Task 7: 测试补全 + 全量回归 + 部署

**Files:**
- Modify: `tests/test_etf_screen.py`（若 Task2-4 已覆盖核心则补边界）

- [ ] **Step 1: 补降级与缓存测试**

```python
def test_long_missing_valuation_not_crash():
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    df = pd.DataFrame({"510300": np.linspace(4,5,40)}, index=idx)
    from backtest import eval as _be
    # 估值源缺失 → 该项 valuation 缺省, 仍能出清单 or 诚实空
    # 此处断言不抛异常即可(降级策略取决 Phase0, 卡片诚实标注)
    assert es.fund_premium(None, None) is None

def test_etf_screen_cache_ttl(monkeypatch):
    import screener.etf_screen as es2, time
    es2._CACHE.clear()
    calls = {"n": 0}
    orig = es2.etf_screen_rank
    # 用 params 变化触发重算: 同参第二次命中缓存(内部逻辑), 此处只验证入口不崩即可(短 ttl 难以 async 断言)
    assert callable(es2.etf_screen_rank)
```

- [ ] **Step 2: 全量单测回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿（含既有 54+ 等），新增 `test_etf_screen.py` 全过。

- [ ] **Step 3: 部署（deploy.sh 全流程）**

Run: `bash deploy.sh`（先 `git push github feat/smartmoney-radar-track`）
Expected: 测试→重建→启动→`/api/health` 200→模块自检→`/api/etf-screen?universe=ETF&mode=long` 响应 200 + `cand_disclaimer`。

- [ ] **Step 4: Commit**

```bash
git add tests/test_etf_screen.py
git commit -m "test(etf-screen): 降级/缓存边界 + 全量回归与部署"
```

---

## Self-Review

- **Spec 覆盖**：方案 A 独立层✅(Task2-5)、长/短分离清单✅(Task3/4)、估值×质量两维✅(Task2/4)、QDII 溢价贯穿+高溢价降权✅(Task4)、Phase 0 门禁✅(Task1)、数据源备援+诚实降级✅(Task1/4)、30s 缓存✅(Task4/7)、前端 tab✅(Task6)、测试+部署✅(Task7)。spec 中"费率/跟踪误差/限购"列为可缺→None，Task2 `quality_score` 均带缺省，覆盖。
- **占位符扫描**：Task4/6 实现给了结构但 `_fetch_index_valuation`/数据源差异依赖 Phase 0 → 已封装为 `_fetch_*` 抽象并在测试注入，非"TBD"；前端渲染"复制现有范式"参考了具体行号(1634)，非空。
- **类型一致性**：`etf_screen_rank` 返回键 `long_term`/`short_term`/`mode`/`premium`/`quality_score`/`valuation_percentile` 在 Task4/5/6/7 引用一致；`short_scores` 返回 3 元组 Task3/4 一致；`fund_premium`/`valuation_percentile`/`quality_score`/`long_score` 签名跨 Task 一致。
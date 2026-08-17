# 每日强势股筛选(daily-strong) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `screener/daily_strong.py` + `GET /api/daily-strong`，5步方法论(涨幅>5%入选→市值/PE/ST雷区剔除→多头排列/放量突破形态→量价验证→板块助攻)机械编排，混合范式(硬剔除+软打分)。

**Architecture:** 独立模块，与 `nextday.py` 并列不依赖。硬剔除层(step1/2/3/5 通过/不通过)+软打分层(step4 排序用 0-100)。零触网零新表，复用 `stock_spot`/`sector_fund_flow`/`stock_daily`；step3 复用 `backtest.signals._uni_panels` 取 close 面板(仅取数不复用触发语义)+本地算 MA；step5 复用 `board_money_link` 的 code→board 反查套路(内联，不调 board_money_link 避免多算 net_intensity)。粗筛 top K(≤200)+30s 进程缓存+codes 限定+NaN→None 守卫，全对齐 nextday。

**Tech Stack:** Python 3.12 + pandas 3.0 + FastAPI。无新依赖。

**Spec:** `docs/superpowers/specs/2026-08-17-daily-strong-design.md`

## Global Constraints

- Python 3.12.10 + pandas 3.0.3（见 requirements.txt），无新依赖。
- 单测在仓库根目录跑（无 conftest.py/pytest.ini）：`python -m pytest tests/test_daily_strong.py -q`。
- 测试 mock `db.query_rows`，不触网。mock 约定同 `tests/test_nextday.py`：`lambda table, **k: <data> if table=="stock_spot" else <...>`。
- NaN→None 守卫必须用 `_nan`（防 starlette `allow_nan=False` 500）。
- 合规（memory: personal-use-compliance-relax）：措辞可用"每日强势/板块助攻"，挂 `cand_disclaimer`："每日强势清单——多步机械漏斗+板块助攻排序观察清单，非荐股非买卖信号，盈亏自负"。
- pandas 3.0：`Series.reindex` 的 fill_value 必须用关键字。
- `stock_spot` 字段：`code/name/change_pct/turnover_rate/latest_price/circulating_market_cap/pe/st_type/volume_ratio/board`。
- `sector_fund_flow` 字段：`sector_type/indicator/name/main_net_inflow`。
- `stock_daily` 经 `backtest.signals._uni_panels(universe, codes)` 取 (close, amount) 面板，列=code 行=date 升序。

---

### Task 1: 工具函数 + step1/step2 硬剔除

**Files:**
- Create: `screener/daily_strong.py`
- Test: `tests/test_daily_strong.py`

**Interfaces:**
- Consumes: `data.db.query_rows("stock_spot", limit=0)` 取全表 spot
- Produces: `_nan/_to_f/_clip` 工具函数(本地定义，不 import nextday 保持独立)；`_step1_pass(s, p)->bool`；`_step2_pass(s, p)->bool`；`_SCAN_K=200`；`_CACHE`/`_CACHE_TTL=30`

- [ ] **Step 1: Write failing tests for step1/step2 + utils**

Create `tests/test_daily_strong.py`:

```python
# -*- coding: utf-8 -*-
"""daily-strong 每日强势5步漏斗单测。mock db.query_rows，不触网。"""
import screener.daily_strong as ds


def test_nan_none():
    assert ds._nan(float("nan")) is None
    assert ds._nan(float("inf")) is None
    assert ds._nan(3.5) == 3.5
    assert ds._nan(None) is None


def test_to_f():
    assert ds._to_f("abc") is None
    assert ds._to_f(3.5) == 3.5
    assert ds._to_f(float("nan")) is None


def test_clip():
    assert ds._clip(1.5) == 1.0
    assert ds._clip(-0.5) == 0.0
    assert ds._clip(0.5) == 0.5


def test_step1_pass():
    p = {"min_change_pct": 5.0, "min_turnover": 3.0, "max_price": 50.0}
    ok = {"change_pct": 6.0, "turnover_rate": 4.0, "latest_price": 20.0}
    assert ds._step1_pass(ok, p) is True
    # 涨幅不足
    assert ds._step1_pass({**ok, "change_pct": 4.0}, p) is False
    # 换手不足
    assert ds._step1_pass({**ok, "turnover_rate": 2.0}, p) is False
    # 价过高
    assert ds._step1_pass({**ok, "latest_price": 60.0}, p) is False


def test_step2_pass():
    p = {"min_mv": 10.0, "max_mv": 200.0, "max_pe": 150.0}
    ok = {"circulating_market_cap": 100.0, "pe": 30.0, "st_type": None}
    assert ds._step2_pass(ok, p) is True
    # 市值过小
    assert ds._step2_pass({**ok, "circulating_market_cap": 5.0}, p) is False
    # 市值过大
    assert ds._step2_pass({**ok, "circulating_market_cap": 300.0}, p) is False
    # PE 过高
    assert ds._step2_pass({**ok, "pe": 200.0}, p) is False
    # 亏损(pe 空)
    assert ds._step2_pass({**ok, "pe": None}, p) is False
    # ST
    assert ds._step2_pass({**ok, "st_type": "ST"}, p) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.daily_strong'`

- [ ] **Step 3: Write minimal implementation**

Create `screener/daily_strong.py`:

```python
# -*- coding: utf-8 -*-
"""每日强势股筛选(5步漏斗+板块助攻，混合:硬剔除+软打分)。

**不触网不新增表**: 复用 stock_spot/sector_fund_flow/stock_daily。
step3 复用 backtest.signals._uni_panels 取 close 面板(仅取数不复用触发语义)+本地算 MA。
step5 复用 board_money_link 的 code→board 反查套路(内联,不调 board_money_link 避免多算 net_intensity)。

5步(对齐用户方法论):
  step1 入选: 涨幅>5% + 换手>3% + 股价<50
  step2 雷区: 流通市值 10-200亿 + PE<=150且非亏损 + 非ST
  step3 形态: 多头排列(5/10/20 MA向上发散) 或 放量突破(站稳60日线+量翻倍)
  step4 软打分: 量比>2.5强度 + 涨幅<7%避追高 (分时项永久降级,0不崩)
  step5 板块助攻: 所属板块热度前5 + 板块内>=2涨停股

混合编排: step1/2/3/5 硬剔除(通过/不通过), step4 软打分(0-100排序)。
排序键: 硬通过数×10 + 软分 降序。30s 进程缓存。
合规(个人自用放松): 每日强势清单——机械漏斗+板块助攻排序观察清单。
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from data import db

_SCAN_K = 200       # 粗筛后精算上限(按涨幅降序)
_CACHE: dict[tuple, tuple] = {}
_CACHE_TTL = 30


def _nan(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _to_f(v):
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (TypeError, ValueError):
        return None


def _step1_pass(s, p) -> bool:
    """入选门槛: 涨幅>min_change_pct + 换手>min_turnover + 股价<max_price。"""
    chg = _to_f(s.get("change_pct"))
    tr = _to_f(s.get("turnover_rate"))
    px = _to_f(s.get("latest_price"))
    if chg is None or tr is None or px is None:
        return False
    return chg > p["min_change_pct"] and tr > p["min_turnover"] and px < p["max_price"]


def _step2_pass(s, p) -> bool:
    """雷区剔除: 市值[min_mv,max_mv] + PE<=max_pe且非亏损 + 非ST。"""
    mc = _to_f(s.get("circulating_market_cap"))
    pe = _to_f(s.get("pe"))
    st = s.get("st_type")
    if mc is None or mc < p["min_mv"] or mc > p["max_mv"]:
        return False
    # pe 为空/负=亏损→剔除; pe>max_pe→剔除
    if pe is None or pe <= 0 or pe > p["max_pe"]:
        return False
    if st:  # 非空=ST/*ST
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add screener/daily_strong.py tests/test_daily_strong.py
git commit -m "feat(daily-strong): 工具函数+step1/2硬剔除

5步方法论第1/2步: 涨幅>5%+换手>3%+价<50 入选; 市值10-200亿+PE<=150非亏损+非ST 雷区剔除。
_nan/_to_f/_clip 本地定义保持模块独立(不import nextday)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: step3 MA 形态过滤

**Files:**
- Modify: `screener/daily_strong.py` (追加 `_ma_arrange_batch` + `_step3_pass`)
- Test: `tests/test_daily_strong.py` (追加)

**Interfaces:**
- Consumes: `backtest.signals._uni_panels(universe, codes)` → (close_df, amount_df)，列=code 行=date 升序；pandas `Series.rolling(n).mean()`
- Produces: `_ma_arrange_batch(universe, codes) -> dict[code, ma_info]`，ma_info={ma5,ma10,ma20,ma60,bullish_align,volume_breakout,bearish,converged,need_history,last_vol,vol_avg20}；`_step3_pass(info) -> bool`

- [ ] **Step 1: Write failing tests for step3**

Append to `tests/test_daily_strong.py`:

```python
import pandas as pd


def _mk_close(codes_prices: dict, n=65):
    """造 close 面板: {code: [p0..p63]} 升序。n=65 够算60MA。"""
    data = {}
    for c, pxs in codes_prices.items():
        # 不足 n 日补前值
        full = list(pxs) + [pxs[-1]] * (n - len(pxs)) if len(pxs) < n else list(pxs)
        data[c] = full[:n]
    df = pd.DataFrame(data)
    df.index = pd.date_range("2026-06-01", periods=n, name="date")
    return df


def test_step3_bullish_align(monkeypatch):
    # A: 5/10/20 日均线严格上行发散(价递增)
    close = _mk_close({"A": list(range(60, 125))})  # 严格递增
    amount = _mk_close({"A": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    info = ds._ma_arrange_batch("stock", ["A"])
    assert info["A"]["bullish_align"] is True
    assert info["A"]["need_history"] is False
    assert ds._step3_pass(info["A"]) is True


def test_step3_volume_breakout(monkeypatch):
    # B: 站稳60日线 + 当日量翻倍(不严格多头排列但放量突破)
    px = [100] * 65  # 平盘,close>ma60 满足
    px[-1] = 101
    close = _mk_close({"B": px})
    # 最后一天量是前20日均量2倍
    amt = [100] * 65
    amt[-1] = 250
    amount = _mk_close({"B": amt})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    info = ds._ma_arrange_batch("stock", ["B"])
    assert info["B"]["volume_breakout"] is True
    assert ds._step3_pass(info["B"]) is True


def test_step3_bearish_reject(monkeypatch):
    # C: 空头排列(价递减)
    close = _mk_close({"C": list(range(125, 60, -1))})
    amount = _mk_close({"C": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    info = ds._ma_arrange_batch("stock", ["C"])
    assert info["C"]["bearish"] is True
    assert ds._step3_pass(info["C"]) is False


def test_step3_need_history(monkeypatch):
    # D: <60 日历史
    short = _mk_close({"D": [10, 11, 12]}, n=65)  # 只3个真实值
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (short, short))
    info = ds._ma_arrange_batch("stock", ["D"])
    assert info["D"]["need_history"] is True
    assert ds._step3_pass(info["D"]) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: FAIL with `AttributeError: module 'screener.daily_strong' has no attribute '_ma_arrange_batch'`

- [ ] **Step 3: Write minimal implementation**

Append to `screener/daily_strong.py` (after `_step2_pass`):

```python
from backtest import signals as _sig


def _ma_arrange_batch(universe: str, codes: list[str]) -> dict:
    """批量算 5/10/20/60 MA + 量。返 {code: ma_info}。
    ma_info: {ma5,ma10,ma20,ma60,bullish_align,volume_breakout,bearish,converged,need_history,last_vol,vol_avg20}
    无历史/<60日→need_history=True,该股 step3 跳过不崩。
    """
    out = {c: {"ma5": None, "ma10": None, "ma20": None, "ma60": None,
               "bullish_align": False, "volume_breakout": False,
               "bearish": False, "converged": False, "need_history": False,
               "last_vol": None, "vol_avg20": None} for c in codes}
    if not codes:
        return out
    try:
        close, amount = _sig._uni_panels(universe, codes)
    except Exception:
        return out
    if close is None or close.empty:
        return out
    for c in codes:
        if c not in close.columns:
            out[c]["need_history"] = True
            continue
        s = close[c].dropna()
        if len(s) < 60:
            out[c]["need_history"] = True
            continue
        ma5 = s.rolling(5).mean().iloc[-1]
        ma10 = s.rolling(10).mean().iloc[-1]
        ma20 = s.rolling(20).mean().iloc[-1]
        ma60 = s.rolling(60).mean().iloc[-1]
        ma20_prev = s.rolling(20).mean().iloc[-6] if len(s) >= 6 else ma20  # 5日前
        last_close = s.iloc[-1]
        out[c].update({"ma5": _nan(ma5), "ma10": _nan(ma10), "ma20": _nan(ma20),
                       "ma60": _nan(ma60)})
        # 多头排列: ma5>ma10>ma20 且 ma20 较5日前上行(向上发散)
        out[c]["bullish_align"] = bool(
            ma5 > ma10 > ma20 and ma20 > ma20_prev)
        # 空头排列: ma5<ma10<ma20
        out[c]["bearish"] = bool(ma5 < ma10 < ma20)
        # 均线粘合: (max-min)/min < 0.5%
        if min(ma5, ma10, ma20) > 0:
            spread = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / min(ma5, ma10, ma20)
            out[c]["converged"] = bool(spread < 0.005)
        # 放量突破: close>ma60 且 当日量>=2×过去20日均量
        if amount is not None and c in amount.columns:
            amt = amount[c].dropna()
            if len(amt) >= 20:
                last_vol = amt.iloc[-1]
                vol_avg20 = amt.iloc[-20:].mean()
                out[c]["last_vol"] = _nan(last_vol)
                out[c]["vol_avg20"] = _nan(vol_avg20)
                out[c]["volume_breakout"] = bool(
                    last_close > ma60 and last_vol >= 2 * vol_avg20)
    return out


def _step3_pass(info: dict) -> bool:
    """形态过滤: 多头排列 OR 放量突破 通过; 空头排列/均线粘合 剔除。"""
    if info.get("need_history"):
        return False
    if info.get("bearish") or info.get("converged"):
        return False
    return info.get("bullish_align") or info.get("volume_breakout")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add screener/daily_strong.py tests/test_daily_strong.py
git commit -m "feat(daily-strong): step3 MA形态过滤(多头排列/放量突破)

复用 signals._uni_panels 取 close 面板(仅取数)+本地算5/10/20/60MA。
多头排列(ma5>ma10>ma20且ma20较5日前上行)或放量突破(close>ma60+量>=2×20日均量)通过。
空头排列/均线粘合((max-min)/min<0.5%)剔除。<60日历史need_history不崩。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: step4 软打分 + step5 板块助攻

**Files:**
- Modify: `screener/daily_strong.py` (追加 `_step4_score` + `_step5_pass`)
- Test: `tests/test_daily_strong.py` (追加)

**Interfaces:**
- Consumes: `stock_spot.volume_ratio`/`change_pct`(step4)；`stock_spot.board` + `sector_fund_flow`(step5)
- Produces: `_step4_score(s) -> float`(0-100)；`_step5_pass(code, spots, sff) -> tuple[bool, dict]`，dict={board,board_rank,board_zt_count}

- [ ] **Step 1: Write failing tests for step4/step5**

Append to `tests/test_daily_strong.py`:

```python
def test_step4_score():
    # 量比>2.5 满分 + 涨幅<7% 满分
    s = {"volume_ratio": 3.0, "change_pct": 5.5}
    sc = ds._step4_score(s)
    assert 80 < sc <= 100
    # 涨幅>7% 扣分
    s2 = {"volume_ratio": 3.0, "change_pct": 8.0}
    assert ds._step4_score(s2) < sc
    # 量比低
    s3 = {"volume_ratio": 1.0, "change_pct": 5.5}
    assert ds._step4_score(s3) < sc


def test_step5_pass():
    spots = [
        {"code": "A", "board": "电池", "change_pct": 10.0},
        {"code": "B", "board": "电池", "change_pct": 10.0},
        {"code": "C", "board": "电池", "change_pct": 2.0},
    ]
    # 电池板块净流入排名第3(前5) + 2涨停
    sff = [{"name": "半导体", "main_net_inflow": 100},
           {"name": "军工", "main_net_inflow": 80},
           {"name": "电池", "main_net_inflow": 60}]
    ok, d = ds._step5_pass("A", spots, sff)
    assert ok is True
    assert d["board"] == "电池"
    assert d["board_rank"] == 3
    assert d["board_zt_count"] == 2


def test_step5_fail_rank():
    spots = [{"code": "A", "board": "电池", "change_pct": 10.0}]
    # 电池排名第10(>5)
    sff = [{"name": f"板块{i}", "main_net_inflow": 100 - i} for i in range(10)]
    sff.append({"name": "电池", "main_net_inflow": 1})
    ok, d = ds._step5_pass("A", spots, sff)
    assert ok is False
    assert d["board_rank"] == 11


def test_step5_no_board():
    spots = [{"code": "A", "board": None}]
    ok, d = ds._step5_pass("A", spots, [])
    assert ok is False
    assert d["board"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: FAIL with `AttributeError: ... has no attribute '_step4_score'`

- [ ] **Step 3: Write minimal implementation**

Append to `screener/daily_strong.py`:

```python
def _step4_score(s) -> float:
    """软打分(0-100,排序用): 量比>2.5强度(0.5权重) + 涨幅<7%避追高(0.5权重)。
    分时项永久降级(无数据源),0不崩。"""
    vr = _to_f(s.get("volume_ratio"))
    chg = _to_f(s.get("change_pct"))
    # 量比强度: >=2.5满分, 线性缩放到[0,1]
    a = _clip((vr - 1.0) / 1.5) if vr is not None else 0.0
    # 涨幅温和: <7%满分, 7-9.8%线性扣至0, >=9.8%为0
    if chg is None:
        b = 0.0
    elif chg < 7:
        b = 1.0
    elif chg < 9.8:
        b = (9.8 - chg) / 2.8
    else:
        b = 0.0
    return round(_clip(0.5 * a + 0.5 * b) * 100, 2)


def _step5_pass(code, spots, sff) -> tuple[bool, dict]:
    """板块助攻(行业口径): 板块净流入排名前5 + 板块内>=2涨停股。
    返 (pass, {board, board_rank, board_zt_count})。无board→pass=False。"""
    code = str(code)
    base = {"board": None, "board_rank": None, "board_zt_count": None}
    board = None
    for s in spots:
        if str(s.get("code")) == code:
            board = s.get("board")
            break
    if not board:
        return False, base
    base["board"] = board
    # 板块净流入排名(降序)
    if sff:
        ranked = sorted(sff, key=lambda x: _to_f(x.get("main_net_inflow")) or -1e18,
                        reverse=True)
        names = [r.get("name") for r in ranked]
        if board in names:
            idx = names.index(board)
            base["board_rank"] = idx + 1
    # 板块内涨停股(change_pct>=9.8%)
    intra = [s for s in spots if s.get("board") == board]
    zt = 0
    for s in intra:
        chg = _to_f(s.get("change_pct"))
        if chg is not None and chg >= 9.8:
            zt += 1
    base["board_zt_count"] = zt
    ok = base["board_rank"] is not None and base["board_rank"] <= 5 and zt >= 2
    return ok, base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add screener/daily_strong.py tests/test_daily_strong.py
git commit -m "feat(daily-strong): step4软打分+step5板块助攻

step4: 量比>2.5强度+涨幅<7%避追高 软打分(0-100排序),分时项永久降级。
step5: 行业口径,板块净流入前5+板块内>=2涨停股(复用board_money_link反查套路,内联)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 编排主函数 daily_strong_rank

**Files:**
- Modify: `screener/daily_strong.py` (追加 `daily_strong_rank`)
- Test: `tests/test_daily_strong.py` (追加)

**Interfaces:**
- Consumes: Task1/2/3 的 `_step1_pass/_step2_pass/_ma_arrange_batch/_step3_pass/_step4_score/_step5_pass`；`db.query_rows("stock_spot", limit=0)` + `db.query_rows("sector_fund_flow", where=..., params=...)`
- Produces: `daily_strong_rank(universe, codes, limit, days, min_change_pct, min_turnover, max_price, min_mv, max_mv, max_pe) -> dict`，返 {universe,count,items,ts,filters,market_median_chg,note?}

- [ ] **Step 1: Write failing tests for orchestration**

Append to `tests/test_daily_strong.py`:

```python
_SPOT = [
    {"code": "A", "name": "甲", "change_pct": 6.0, "volume_ratio": 3.0,
     "turnover_rate": 5.0, "latest_price": 20.0,
     "circulating_market_cap": 100.0, "pe": 30.0, "st_type": None,
     "board": "电池"},
    {"code": "B", "name": "乙", "change_pct": 8.0, "volume_ratio": 1.0,
     "turnover_rate": 4.0, "latest_price": 30.0,
     "circulating_market_cap": 50.0, "pe": 40.0, "st_type": None,
     "board": "电池"},
    {"code": "C", "name": "丙", "change_pct": 2.0, "volume_ratio": 0.5,
     "turnover_rate": 1.0, "latest_price": 10.0,
     "circulating_market_cap": 5.0, "pe": 10.0, "st_type": "ST",
     "board": "军工"},
]
_SFF = [{"name": "电池", "main_net_inflow": 100},
        {"name": "军工", "main_net_inflow": 50}]


def _mock_orch(monkeypatch, close_map=None):
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: (_SPOT if table == "stock_spot"
                            else _SFF if table == "sector_fund_flow" else []))
    # step3: A/B 多头排列通过, C 需历史
    close = close_map or _mk_close(
        {"A": list(range(60, 125)), "B": list(range(60, 125))})
    amount = _mk_close({"A": [100] * 65, "B": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels",
                        lambda u, codes: (close, amount))
    # 涨停股: A 涨停(6.0 改 10.0 模拟) — 用 spots 内 A change_pct=6 不涨停
    # 为测 step5, 单测里直接造板块内2涨停
    ds._CACHE.clear()


def test_rank_basic(monkeypatch):
    _mock_orch(monkeypatch)
    # A: step1✓ step2✓ step3✓ step5(电池排名1但只A涨停1只<2→✗)
    # B: step1✓ step2✓ step3✓ step5(电池,1只<2→✗)
    r = ds.daily_strong_rank(limit=10, min_change_pct=5.0)
    assert r["count"] == 2  # A,B 通过粗筛
    by = {i["code"]: i for i in r["items"]}
    assert by["A"]["step1_pass"] is True
    assert by["A"]["step2_pass"] is True
    assert by["A"]["step3_pass"] is True
    assert by["A"]["step5_pass"] is False  # 涨停股不足
    # A 通过 step1/2/3 = 3 步硬剔除
    assert by["A"]["hard_pass"] >= 3


def test_rank_order_hard_pass(monkeypatch):
    _mock_orch(monkeypatch)
    r = ds.daily_strong_rank(limit=10, min_change_pct=1.0)
    # A 硬通过3步(step1/2/3) > C 硬通过0步 → A 排前
    items = r["items"]
    a_idx = next(i for i, x in enumerate(items) if x["code"] == "A")
    c_idx = next(i for i, x in enumerate(items) if x["code"] == "C")
    assert a_idx < c_idx


def test_rank_codes_limit(monkeypatch):
    _mock_orch(monkeypatch)
    r = ds.daily_strong_rank(codes=["A"], limit=10, min_change_pct=0.0)
    assert r["count"] == 1
    assert r["items"][0]["code"] == "A"


def test_rank_empty_spot(monkeypatch):
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    ds._CACHE.clear()
    r = ds.daily_strong_rank(limit=10)
    assert r["count"] == 0
    assert "先 /api/refresh" in r.get("note", "")


def test_rank_cache(monkeypatch):
    _mock_orch(monkeypatch)
    r1 = ds.daily_strong_rank(limit=10, min_change_pct=5.0)
    # 改 spot 后再调(应命中缓存,不变)
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: [])
    r2 = ds.daily_strong_rank(limit=10, min_change_pct=5.0)
    assert r2["count"] == r1["count"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: FAIL with `AttributeError: ... has no attribute 'daily_strong_rank'`

- [ ] **Step 3: Write minimal implementation**

Append to `screener/daily_strong.py`:

```python
def daily_strong_rank(universe: str = "stock",
                      codes: list[str] | None = None,
                      limit: int = 50, days: int = 30,
                      min_change_pct: float = 5.0,
                      min_turnover: float = 3.0,
                      max_price: float = 50.0,
                      min_mv: float = 10.0, max_mv: float = 200.0,
                      max_pe: float = 150.0) -> dict:
    """每日强势5步混合编排。返 {universe,count,items,ts,filters,market_median_chg,note?}。

    step1/2/3/5 硬剔除, step4 软打分。排序键: 硬通过数×10 + 软分 降序。
    粗筛: codes 非空不粗筛; 否则 change_pct>min_change_pct 按涨幅降序截 _SCAN_K。
    30s 进程缓存。无历史/无board→对应步降级不崩。
    """
    p = {"min_change_pct": min_change_pct, "min_turnover": min_turnover,
         "max_price": max_price, "min_mv": min_mv, "max_mv": max_mv,
         "max_pe": max_pe}
    key = (universe, tuple(codes or []), limit, days, min_change_pct,
           min_turnover, max_price, min_mv, max_mv, max_pe)
    now = datetime.now()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]).total_seconds() < _CACHE_TTL:
        return hit[1]

    base = {"universe": universe, "count": 0, "items": [], "limit": limit,
            "days": days, "filters": p,
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S")}

    spot_all = db.query_rows("stock_spot", limit=0)
    if codes:
        cset = {str(c) for c in codes}
        spot_all = [s for s in spot_all if str(s.get("code")) in cset]

    if not spot_all:
        base["note"] = "stock_spot 为空，先 /api/refresh 采集"
        base["market_median_chg"] = None
        _CACHE[key] = (now, base)
        return base

    median_chg = _median([_to_f(s.get("change_pct")) for s in spot_all])
    base["market_median_chg"] = _nan(median_chg)

    # 粗筛(codes 限定时不粗筛)
    if not codes:
        cand = [s for s in spot_all
                if (_to_f(s.get("change_pct")) or -99) > min_change_pct]
        cand.sort(key=lambda s: _to_f(s.get("change_pct")) or -99, reverse=True)
        cand = cand[:_SCAN_K]
    else:
        cand = spot_all

    codes_k = [str(s.get("code")) for s in cand]

    # step3 批量 MA
    ma_info = _ma_arrange_batch(universe, codes_k)

    # step5 板块助攻: 单次取 sector_fund_flow(行业,今日)
    try:
        sff = db.query_rows("sector_fund_flow",
                            where="sector_type = ? AND indicator = ?",
                            params=("行业", "今日"), limit=0)
    except Exception:
        sff = []

    items = []
    for s in cand:
        code = str(s.get("code"))
        name = s.get("name") or code
        s1 = _step1_pass(s, p)
        s2 = _step2_pass(s, p)
        mi = ma_info.get(code, {})
        s3 = _step3_pass(mi)
        s4 = _step4_score(s)
        s5, bd = _step5_pass(code, spot_all, sff)
        hard = sum([s1, s2, s3, s5])
        items.append({
            "code": code, "name": name,
            "change_pct": _nan(_to_f(s.get("change_pct"))),
            "turnover_rate": _nan(_to_f(s.get("turnover_rate"))),
            "latest_price": _nan(_to_f(s.get("latest_price"))),
            "circulating_market_cap": _nan(_to_f(s.get("circulating_market_cap"))),
            "pe": _nan(_to_f(s.get("pe"))),
            "st_type": s.get("st_type"),
            "volume_ratio": _nan(_to_f(s.get("volume_ratio"))),
            "board": bd["board"], "board_rank": bd["board_rank"],
            "board_zt_count": bd["board_zt_count"],
            "step1_pass": s1, "step2_pass": s2, "step3_pass": s3,
            "step4_score": s4, "step5_pass": s5,
            "hard_pass": hard,
            "need_history": mi.get("need_history", False),
            "score": hard * 10 + s4,
        })

    items.sort(key=lambda x: (x["hard_pass"], x["step4_score"]), reverse=True)
    items = items[:max(0, limit)]
    for i, it in enumerate(items):
        it["rank"] = i + 1

    base["count"] = len(items)
    base["items"] = items
    _CACHE[key] = (now, base)
    return base


def _median(values):
    xs = [v for v in values if v is not None]
    if not xs:
        return None
    return float(np.median(xs))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_strong.py -q`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add screener/daily_strong.py tests/test_daily_strong.py
git commit -m "feat(daily-strong): 编排主函数 daily_strong_rank

5步混合编排: step1/2/3/5硬剔除+step4软打分,排序键=硬通过数×10+软分。
粗筛topK(≤200)+30s缓存+codes限定。无历史/无board降级不崩。NaN→None守卫。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: API 路由 + 前端入口

**Files:**
- Modify: `api/server.py` (在 `/api/nextday-strong` 路由后追加 `/api/daily-strong`)
- Modify: `web/index.html` (加"每日强势"入口)
- Test: `tests/test_daily_strong.py` (追加路由测试)

**Interfaces:**
- Consumes: `screener.daily_strong.daily_strong_rank`；`_wrap(res, {"cand_disclaimer": ...})`
- Produces: `GET /api/daily-strong` 路由

- [ ] **Step 1: Write failing test for route**

Append to `tests/test_daily_strong.py`:

```python
def test_route_daily_strong(monkeypatch):
    from fastapi.testclient import TestClient
    import api.server as srv
    monkeypatch.setattr(srv, "_DS", None)  # 清模块缓存引用(如有)
    monkeypatch.setattr(ds.db, "query_rows",
                        lambda table, **k: (_SPOT if table == "stock_spot"
                            else _SFF if table == "sector_fund_flow" else []))
    close = _mk_close({"A": list(range(60, 125)), "B": list(range(60, 125))})
    amount = _mk_close({"A": [100] * 65, "B": [100] * 65})
    import backtest.signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    ds._CACHE.clear()
    client = TestClient(srv.app)
    r = client.get("/api/daily-strong?limit=10&min_change_pct=5.0")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "cand_disclaimer" in body
    assert "每日强势" in body["cand_disclaimer"]
    assert body["data"]["count"] >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daily_strong.py::test_route_daily_strong -q`
Expected: FAIL with 404 (route not registered)

- [ ] **Step 3: Add route to api/server.py**

Insert after the `/api/nextday-strong` route (around the `@app.get("/api/buffett")` line):

```python
@app.get("/api/daily-strong")
def daily_strong(universe: str = Query("stock"),
                 limit: int = Query(50, ge=1, le=200),
                 days: int = Query(30, ge=5, le=120),
                 min_change_pct: float = Query(5.0, ge=0.0, le=20.0),
                 min_turnover: float = Query(3.0, ge=0.0, le=50.0),
                 max_price: float = Query(50.0, ge=1.0, le=500.0),
                 min_mv: float = Query(10.0, ge=0.0, le=1000.0),
                 max_mv: float = Query(200.0, ge=10.0, le=10000.0),
                 max_pe: float = Query(150.0, ge=0.0, le=1000.0),
                 codes: str = Query("")):
    """每日强势股5步漏斗+板块助攻(混合:硬剔除+软打分)。
    step1入选/step2雷区/step3形态/step4软打分/step5板块助攻。
    复用 stock_spot/sector_fund_flow/stock_daily，不触网不新增表。
    依赖 stock_daily(step3/MA)，无历史该步降级。挂 cand_disclaimer。"""
    from screener import daily_strong as ds
    cl = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    res = ds.daily_strong_rank(universe=universe, codes=cl, limit=limit, days=days,
                               min_change_pct=min_change_pct,
                               min_turnover=min_turnover, max_price=max_price,
                               min_mv=min_mv, max_mv=max_mv, max_pe=max_pe)
    return _wrap(res, {"cand_disclaimer":
                       "每日强势清单——多步机械漏斗+板块助攻排序观察清单，非荐股非买卖信号，盈亏自负。"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_daily_strong.py::test_route_daily_strong -q`
Expected: PASS

- [ ] **Step 5: Add frontend entry**

In `web/index.html`, find the "次日强势"按钮区(由 nextday 落地时加入, 搜索 `nextday-strong` 或 `次日强势` 定位), 在其旁加"每日强势"按钮,点击调 `fetch('/api/daily-strong?limit=50').then(r=>r.json()).then(render)`。渲染表格列: 代码/名称/涨幅/step1-5通过/score/板块/板块涨停数。具体 DOM 结构对齐现有 nextday 渲染函数风格(复制其 render 函数改字段名)。

若 nextday 无独立按钮(走优质筛选 tab),则在优质筛选 tab 加并列"每日强势"卡,复用 `qsLoad` 风格的 fetch+渲染。

- [ ] **Step 6: Manual smoke test**

```bash
uvicorn api.server:app --reload --port 8000
# 浏览器开 http://localhost:8000/web/index.html, 点"每日强势"按钮, 看是否有数据
```

- [ ] **Step 7: Commit**

```bash
git add api/server.py web/index.html tests/test_daily_strong.py
git commit -m "feat(api): /api/daily-strong 路由+前端入口

GET /api/daily-strong 挂 cand_disclaimer。前端加每日强势入口,复用 nextday 渲染风格。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: CLAUDE.md 同步

**Files:**
- Modify: `CLAUDE.md` (路由速查加 `/api/daily-strong`；改动检查清单加 daily-strong 条目)

**Interfaces:**
- Consumes: 无
- Produces: CLAUDE.md 与代码对齐

- [ ] **Step 1: Add route to 路由速查**

In `CLAUDE.md` 路由速查段,在 `/api/nextday-strong` 之后加:

```
→ `/api/daily-strong`（每日强势5步漏斗+板块助攻：step1入选[涨幅>5%/换手>3%/价<50]+step2雷区[市值10-200亿/PE≤150非亏损/非ST]+step3形态[多头排列或放量突破60日线]+step4软打分[量比/涨幅温和]+step5板块助攻[行业热度前5+≥2涨停]；混合硬剔除+软打分，复用 stock_spot/sector_fund_flow/stock_daily，step3 复用 signals._uni_panels 取数本地算MA，无历史该步降级，30s缓存，附 `cand_disclaimer`："每日强势清单——多步机械漏斗+板块助攻排序观察清单，非荐股非买卖信号，盈亏自负"）
```

- [ ] **Step 2: Add checklist entry**

In `CLAUDE.md` 改动检查清单段,在 nextday 条目后加:

```
- 新增 daily-strong 每日强势编排层 → `screener/daily_strong.py` `daily_strong_rank(universe,codes,limit,days,...)` 5步混合编排(step1入选/step2雷区/step3 MA形态/step4软打分/step5板块助攻,**硬剔除+软打分**,与 nextday 打分式范式不同)→排序键=硬通过数×10+软分降序;**不触网不新增表**复用 stock_spot/sector_fund_flow/stock_daily;step3 复用 `signals._uni_panels` 取 close 面板(仅取数不复用触发语义)+本地算5/10/20/60MA判多头排列/放量突破/空头/粘合;step5 复用 `board_money_link` code→board 反查套路(内联,不调 board_money_link 避免多算 net_intensity);粗筛 top K(≤200)+30s缓存+codes限定;无历史/无board降级不崩;NaN→None守卫(`_nan`)。依赖 stock_daily(step3,需 /api/backtest/fetch)+sector_fund_flow(需 /api/refresh)。同步 tests/test_daily_strong.py。措辞"每日强势/板块助攻",挂 cand_disclaimer。
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 同步 daily-strong(路由速查+改动清单)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

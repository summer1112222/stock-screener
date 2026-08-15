# 主力动向判断力深化 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给主力动向模块补四项操盘手实战能力：主力阶段判定、筹码迁移趋势、板块-个股资金联动、席位胜率。

**Architecture:** 方案 A——查询层按需算、零新表、席位胜率靠一次性 finshare 回填脚本。全部挂在 `screener/smart_money.py`（复用现有 `_streak`/`_nan`/`_attach_intensity`/`chip_distribution`/`behavior_series`/`_uni_panels`），新路由经 `_wrap`。

**Tech Stack:** Python 3.12 / pandas 3.0 / FastAPI / SQLite / finshare（龙虎榜回填）/ pytdx（已接入）

**Spec:** `docs/superpowers/specs/2026-08-15-sm-phase-judgment-design.md`

## Global Constraints

- NaN→None 序列化（防 `allow_nan=False` 500）：所有数值出口用 `_nan()` 包裹。
- 不触网除非按需补历史（finshare 回填脚本显式触网，标 `source=finshare`）；查询层纯 `db.query_rows`。
- `pd.read_html` 必须显式 `flavor='lxml'`（本计划不新增 read_html，但沿用约束）。
- 单测 mock `db.query_rows`/`_uni_panels`/模块内函数，不连网。
- 个人自用：措辞直接用"吸筹/洗盘/拉升/出货/观望"，`confidence` 作信号强度，disclaimer 管道保留不拆。
- 进程缓存键含函数参数，TTL 30s（phase/chip_trend 共用）。

## File Structure

- **Modify** `screener/smart_money.py`：+`main_force_phase` / +`board_money_link` / +`seat_winrate` / `chip_distribution` 加 `trend` / 复用现有 `_streak`/`_nan`/`_attach_intensity`。模块级 `_PHASE_CACHE`、`_CHIP_CACHE`。
- **Create** `scripts/backfill_lhb_history.py`：一次性龙虎榜历史回填（finshare）。
- **Modify** `api/server.py`：+3 路由（`/api/smart-money/phase`、`/seat-winrate`、`/board-link`），`/api/chip` 透传 `trend`。
- **Modify** `web/index.html`：phase 色块 + board-link 展开 + 筹码迁移标。
- **Create** `tests/test_main_force_phase.py` / `test_chip_trend.py` / `test_board_money_link.py` / `test_seat_winrate.py` / `test_backfill_lhb.py`。
- **Modify** `CLAUDE.md` + `README.md`：同步能力与口径5已实现。

---

### Task 1: 主力阶段判定 `main_force_phase`

**Files:**
- Modify: `screener/smart_money.py`（末尾追加）
- Test: `tests/test_main_force_phase.py`

**Interfaces:**
- Consumes: `behavior_series(code, days)`（本模块已有，返 `streak_inflow`/`streak_outflow`/`cum_inflow`/`margin_accel`）；`chip_distribution(code, window)`（返 `avg_cost`/`profit_ratio`/`chip_concentration`/`spot`）；`db.query_rows("stock_spot", where="code=?", params=(code,))`；`backtest.signals._uni_panels("stock",[code])` 返 `(close, amount)`。
- Produces: `main_force_phase(code, days=30) -> dict`，含 `phase`/`confidence`/`triggers`/`indicators`/`ts`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_main_force_phase.py
import screener.smart_money as sm

def _mk(streak_in=0, streak_out=0, cum=0.0, accel=0.0, profit=0.5,
        conc=0.2, avg=10.0, spot=10.0, chg=1.0, tnv=1e8, hi60=0.4, tnv5=1e8):
    return dict(streak_in=streak_in, streak_out=streak_out, cum=cum,
                accel=accel, profit=profit, conc=conc, avg=avg, spot=spot,
                chg=chg, tnv=tnv, hi60=hi60, tnv5=tnv5)

def _patch(monkeypatch, m):
    monkeypatch.setattr(sm, "behavior_series", lambda c, days=30: {
        "streak_inflow": m["streak_in"], "streak_outflow": m["streak_out"],
        "cum_inflow": m["cum"], "margin_accel": m["accel"]})
    monkeypatch.setattr(sm, "chip_distribution", lambda c, window=60: {
        "avg_cost": m["avg"], "profit_ratio": m["profit"],
        "chip_concentration": m["conc"], "spot": m["spot"], "need_history": False})
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0:
        [{"change_pct": m["chg"], "turnover_amount": m["tnv"],
          "latest_price": m["spot"], "name": "X"}] if table == "stock_spot" else [])
    monkeypatch.setattr(sm, "_high60_pct", lambda code: m["hi60"])
    monkeypatch.setattr(sm, "_turnover5_avg", lambda code: m["tnv5"])

def test_phase_chuhuo(monkeypatch):
    _patch(monkeypatch, _mk(streak_out=3, profit=0.9, accel=-1, hi60=0.9))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "出货"
    assert r["confidence"] == 1.0

def test_phase_lasheng(monkeypatch):
    _patch(monkeypatch, _mk(streak_in=3, chg=5, tnv=2e8, tnv5=1e8, spot=11, avg=10))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "拉升"
    assert r["confidence"] == 1.0

def test_phase_xichou(monkeypatch):
    _patch(monkeypatch, _mk(streak_in=4, cum=1e8, chg=1, profit=0.6))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "吸筹"
    assert r["confidence"] == 1.0

def test_phase_xipan(monkeypatch):
    _patch(monkeypatch, _mk(cum=1e8, accel=-1, chg=-3))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "洗盘"
    assert r["confidence"] == 1.0

def test_phase_guanwang_low_conf(monkeypatch):
    _patch(monkeypatch, _mk(streak_in=1, chg=0, profit=0.5))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "观望"

def test_phase_tiebreak_risk_priority(monkeypatch):
    # 出货与拉升各命中2/4并列 -> 风险优先取出货
    _patch(monkeypatch, _mk(streak_out=3, profit=0.9, streak_in=3, chg=5, tnv=2e8, tnv5=1e8, spot=11, avg=10, accel=-1, hi60=0.9))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "出货"

def test_phase_data_insufficient(monkeypatch):
    monkeypatch.setattr(sm, "behavior_series", lambda c, days=30: {"streak_inflow": None, "streak_outflow": None, "cum_inflow": None, "margin_accel": None})
    monkeypatch.setattr(sm, "chip_distribution", lambda c, window=60: {"avg_cost": None, "profit_ratio": None, "chip_concentration": None, "spot": None, "need_history": True})
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    monkeypatch.setattr(sm, "_high60_pct", lambda code: None)
    monkeypatch.setattr(sm, "_turnover5_avg", lambda code: None)
    r = sm.main_force_phase("000001")
    assert r["phase"] == "观望"
    assert r["confidence"] == 0
    assert "数据不足" in r.get("note", "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_main_force_phase.py -v`
Expected: FAIL（`main_force_phase`/`_high60_pct`/`_turnover5_avg` 不存在）

- [ ] **Step 3: 实现**

```python
# screener/smart_money.py 末尾追加
import time as _time_mod
_PHASE_CACHE: dict = {}
_PHASE_TTL = 30.0

def _high60_pct(code: str) -> float | None:
    """latest_price 在近60日 close 的 [min,max] 区间分位(0-1)。无历史→None。"""
    try:
        from backtest.signals import _uni_panels
        close, _ = _uni_panels("stock", [code])
        if close is None or close.empty or code not in close.columns:
            return None
        s = close[code].dropna().iloc[-60:]
        if len(s) < 5:
            return None
        lo, hi = float(s.min()), float(s.max())
        sp = db.query_rows("stock_spot", where="code = ?", params=(code,), limit=1)
        lp = None
        if sp:
            lp = _nan(sp[0].get("latest_price"))
        if lp is None:
            lp = float(s.iloc[-1])
        if hi == lo:
            return 0.5
        return round(min(max((lp - lo) / (hi - lo), 0.0), 1.0), 4)
    except Exception:
        return None

def _turnover5_avg(code: str) -> float | None:
    """近5日成交额均值(元)。无历史→None。"""
    try:
        from backtest.signals import _uni_panels
        _, amount = _uni_panels("stock", [code])
        if amount is None or amount.empty or code not in amount.columns:
            return None
        s = amount[code].dropna().iloc[-5:]
        if s.empty:
            return None
        return float(s.mean())
    except Exception:
        return None

_PHASE_COND = {
    "出货": lambda i: [
        (i["streak_outflow"] or 0) >= 2,
        (i["profit_ratio"] is not None and i["profit_ratio"] > 0.8),
        (i["margin_accel"] is not None and i["margin_accel"] < 0),
        (i["high60_pct"] is not None and i["high60_pct"] > 0.8),
    ],
    "拉升": lambda i: [
        (i["streak_inflow"] or 0) >= 2,
        (i["change_pct"] is not None and i["change_pct"] > 3),
        i["_vol_surge"],
        (i["latest_price"] is not None and i["avg_cost"] is not None and i["latest_price"] > i["avg_cost"]),
    ],
    "吸筹": lambda i: [
        (i["streak_inflow"] or 0) >= 3,
        (i["cum_inflow"] is not None and i["cum_inflow"] > 0),
        (i["change_pct"] is not None and i["change_pct"] < 3),
        (i["profit_ratio"] is not None and i["profit_ratio"] < 0.85),
    ],
    "洗盘": lambda i: [
        (i["cum_inflow"] is not None and i["cum_inflow"] > 0),
        (i["margin_accel"] is not None and i["margin_accel"] < 0),
        (i["change_pct"] is not None and -5 < i["change_pct"] < -1),
    ],
}
_PHASE_RISK_ORDER = ["出货", "拉升", "洗盘", "吸筹"]  # 并列时保守优先

def main_force_phase(code: str, days: int = 30) -> dict:
    """主力阶段判定(计分制): 出货/拉升/吸筹/洗盘/观望。
    confidence=命中条件数/该阶段总条件数, 并列按风险优先级取保守。
    复用 behavior_series + chip_distribution + spot, 零新数据源。
    进程缓存30s(依赖日级数据, 避免高频重算 chip)。"""
    key = (str(code), days)
    now = _time_mod.time()
    hit = _PHASE_CACHE.get(key)
    if hit and now - hit[0] < _PHASE_TTL:
        return hit[1]
    bs = behavior_series(code, days)
    chip = chip_distribution(code, window=60)
    spot = db.query_rows("stock_spot", where="code = ?", params=(code,), limit=1)
    srow = spot[0] if spot else {}
    tnv5 = _turnover5_avg(code)
    tnv = _nan(srow.get("turnover_amount"))
    ind = {
        "streak_inflow": bs.get("streak_inflow"),
        "streak_outflow": bs.get("streak_outflow"),
        "cum_inflow": bs.get("cum_inflow"),
        "margin_accel": bs.get("margin_accel"),
        "profit_ratio": chip.get("profit_ratio"),
        "chip_concentration": chip.get("chip_concentration"),
        "avg_cost": chip.get("avg_cost"),
        "spot": chip.get("spot"),
        "change_pct": _nan(srow.get("change_pct")),
        "turnover_amount": tnv,
        "latest_price": _nan(srow.get("latest_price")),
        "high60_pct": _high60_pct(code),
        "tnv5_avg": tnv5,
        "_vol_surge": (tnv is not None and tnv5 is not None and tnv5 > 0 and tnv > tnv5 * 1.5),
    }
    best_phase, best_conf, best_trig = "观望", 0.0, []
    for ph in _PHASE_RISK_ORDER:  # 出货>拉升>洗盘>吸筹,并列时先迭代者胜=保守优先
        flags = _PHASE_COND[ph](ind)
        hits = sum(1 for f in flags if f)
        conf = hits / len(flags)
        trig = [f"条件{i+1}={'命中' if f else '未'}" for i, f in enumerate(flags)]
        if conf > best_conf:   # 严格大于:并列(==)保留先迭代者(出货),实现风险优先级
            best_conf, best_phase, best_trig = conf, ph, trig
    if best_conf < 0.5:
        best_phase, best_conf, best_trig = "观望", 0.0, []
    name = srow.get("name") or code
    out = {"code": str(code), "name": name, "phase": best_phase,
           "confidence": round(best_conf, 4), "triggers": best_trig,
           "indicators": {k: _nan(v) for k, v in ind.items() if not k.startswith("_")},
           "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    if best_phase == "观望" and best_conf == 0 and not any(ind.values()):
        out["note"] = "数据不足"
    _PHASE_CACHE[key] = (now, out)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_main_force_phase.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add screener/smart_money.py tests/test_main_force_phase.py
git commit -m "feat(sm): 主力阶段判定 main_force_phase(计分制4阶段+观望)"
```

---

### Task 2: 筹码迁移 `chip_distribution` 加 `trend`

**Files:**
- Modify: `screener/smart_money.py` `chip_distribution`（line 234-309 区块）
- Test: `tests/test_chip_trend.py`

**Interfaces:**
- Consumes: `backtest.signals._uni_panels("stock",[code])` 返 `(close, amount)`；现有 `chip_distribution` 内部已用的 `_weighted_percentile`/`_histogram`/`_nan`。
- Produces: `chip_distribution` 返回多一个 `trend` 字段（`profit_ratio_delta`/`chip_concentration_delta`/`avg_cost_delta`/`profit_ratio_5d`/`profit_ratio_20d`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chip_trend.py
import pandas as pd, numpy as np
import screener.smart_money as sm

def _mock_panels(monkeypatch, closes, amounts):
    # closes/amounts: 列表(每日值), 近→远 或 远→近均按升序整理
    idx = pd.date_range("2026-06-01", periods=len(closes), freq="D")
    close = pd.DataFrame({"000001": closes}, index=idx)
    amount = pd.DataFrame({"000001": amounts}, index=idx)
    monkeypatch.setattr("backtest.signals._uni_panels",
                        lambda u, codes: (close, amount))
    # 跳过 spot DB（chip_distribution 内部查 stock_spot）
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])

def test_chip_trend_delta_sign(monkeypatch):
    # 价格上行 -> profit_ratio 近5 > 近20 -> delta>0
    # 需 >window+5 日(90>60)使 _chip_series 产足够每日记录取[-5:]/[-20:]
    n = 90
    closes = list(np.linspace(8, 12, n))   # 递增
    amounts = [1e8]*n
    _mock_panels(monkeypatch, closes, amounts)
    r = sm.chip_distribution("000001", window=60, spot_price=12.0)
    assert "trend" in r
    assert r["trend"]["profit_ratio_delta"] >= 0
    assert "profit_ratio_5d" in r["trend"]

def test_chip_trend_no_history(monkeypatch):
    monkeypatch.setattr("backtest.signals._uni_panels", lambda u, codes: (None, None))
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    r = sm.chip_distribution("000001", window=60, spot_price=10.0)
    assert r["need_history"] is True
    assert r.get("trend") is None or r["trend"] == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_chip_trend.py -v`
Expected: FAIL（无 `trend` 字段）

- [ ] **Step 3: 实现**

在 `chip_distribution` 末尾 return 前（计算完 `avg_cost`/`profit_ratio` 之后、`return {**base, ...}` 处），加趋势计算。**先把 `"trend": {},` 加入 `base` 字典**（line 249 的 `"spot_source": None}` 前），使早返回路径（need_history）也带空 trend 键；末尾 return 处再覆盖为算出的 trend。新增辅助函数 `_chip_series`，返回近 N 日每日的 `(avg_cost, profit_ratio, chip_concentration)`。

```python
# screener/smart_money.py —— 新增辅助函数(放在 chip_distribution 之前)
def _chip_series(close_s, amount_s, window: int, spot: float):
    """滚动算近 len(close_s)-window 日每日的 (avg_cost, profit_ratio, chip_concentration)。
    返回 dict 列表按日期升序。close_s/amount_s 已对齐升序 Series。"""
    out = []
    arr_c = close_s.values.astype(float)
    arr_w = amount_s.reindex(close_s.index).values.astype(float) if amount_s is not None else None
    n = len(arr_c)
    for end in range(window, n + 1):
        c = arr_c[end - window:end]
        w = (arr_w[end - window:end] if arr_w is not None else np.ones(window))
        w = np.where(np.isnan(w) | (w < 0), 0.0, w)
        tot = float(w.sum())
        if tot <= 0:
            w = np.ones(window); tot = float(window)
        avg = float((c * w).sum() / tot)
        pr = float(w[c < spot].sum() / tot) if spot else None
        var = float((w * (c - avg) ** 2).sum() / tot)
        conc = (math.sqrt(var) / avg) if avg else None
        out.append({"avg_cost": avg, "profit_ratio": pr, "chip_concentration": conc})
    return out
```

在 `chip_distribution` 的 `return {**base, "avg_cost":..., ...}` 之前插入：

```python
    # 筹码迁移趋势: 近20日每日 chip 指标序列
    trend = {}
    try:
        full_c = close[code].dropna()
        full_a = amount[code].dropna() if (amount is not None and code in amount.columns) else None
        common = full_c.index
        if full_a is not None:
            common = full_c.index.intersection(full_a.index)
            full_c = full_c.loc[common]; full_a = full_a.loc[common]
        if len(full_c) > window + 5:
            ser = _chip_series(full_c, full_a, window, spot)
            if len(ser) >= 5:
                pr5 = [x["profit_ratio"] for x in ser[-5:] if x["profit_ratio"] is not None]
                pr20 = [x["profit_ratio"] for x in ser[-20:] if x["profit_ratio"] is not None]
                co5 = [x["chip_concentration"] for x in ser[-5:] if x["chip_concentration"] is not None]
                co20 = [x["chip_concentration"] for x in ser[-20:] if x["chip_concentration"] is not None]
                ac5 = [x["avg_cost"] for x in ser[-5:]]
                ac20 = [x["avg_cost"] for x in ser[-20:]]
                trend = {
                    "profit_ratio_5d": [round(v, 4) for v in pr5],
                    "profit_ratio_20d": [round(v, 4) for v in pr20],
                    "profit_ratio_delta": round(float(np.mean(pr5) - np.mean(pr20)), 4) if pr5 and pr20 else None,
                    "chip_concentration_delta": round(float(np.mean(co5) - np.mean(co20)), 4) if co5 and co20 else None,
                    "avg_cost_delta": round(float(np.mean(ac5) - np.mean(ac20)), 4) if ac5 and ac20 else None,
                }
    except Exception:
        pass
```

并把最后 return 改为 `... , "distribution": dist, "trend": trend, "spot": ...}`（在 `distribution` 后加 `"trend": trend`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_chip_trend.py tests/test_chip_behavior.py -v`
Expected: PASS（新测试 + 旧 chip 测试不回归）

- [ ] **Step 5: 提交**

```bash
git add screener/smart_money.py tests/test_chip_trend.py
git commit -m "feat(sm): chip_distribution 加筹码迁移 trend(近5vs20日delta)"
```

---

### Task 3: 板块-个股资金联动 `board_money_link`

**Files:**
- Modify: `screener/smart_money.py`（追加）
- Test: `tests/test_board_money_link.py`

**Interfaces:**
- Consumes: `db.query_rows("stock_spot")`（含 `board`/`main_net_inflow`/`turnover_amount`/`code`）；`db.query_rows("sector_fund_flow", where="sector_type=? AND indicator=?", ...)`。net_intensity 内联算（`main_net_inflow/turnover_amount`），**不复用 `_attach_intensity`**（后者读 `amount` 字段、是给 `smart_money_action` 行设计的）。
- Produces: `board_money_link(code) -> dict`（`board`/`board_main_net_inflow`/`board_rank`/`board_pct`/`intra_board_rank`/`intra_board_pct`/`board_5d_trend`/`note`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_board_money_link.py
import screener.smart_money as sm

def test_board_link_basic(monkeypatch):
    spots = [
        {"code":"000001","name":"A","board":"银行","main_net_inflow":1e8,"turnover_amount":5e8},
        {"code":"000002","name":"B","board":"银行","main_net_inflow":2e8,"turnover_amount":4e8},
        {"code":"600000","name":"C","board":"地产","main_net_inflow":-1e8,"turnover_amount":3e8},
    ]
    sff = [
        {"sector_type":"行业","indicator":"今日","name":"银行","main_net_inflow":3e8},
        {"sector_type":"行业","indicator":"今日","name":"地产","main_net_inflow":-1e8},
        {"sector_type":"行业","indicator":"5日","name":"银行","main_net_inflow":1e9},
    ]
    calls = {"stock_spot": spots, "sector_fund_flow": sff}
    def fake_query(table, where="", params=(), order_by="", limit=0):
        return calls.get(table, [])
    monkeypatch.setattr(sm.db, "query_rows", fake_query)
    # board_money_link 内联算 net_intensity(main_net_inflow/turnover_amount),不调 _attach_intensity
    r = sm.board_money_link("000001")
    assert r["board"] == "银行"
    assert r["board_rank"] == 1   # 银行净流入3e8 第一
    assert r["board_pct"] >= 0.5
    # 板块内 net_intensity: A=1e8/5e8=0.2, B=2e8/4e8=0.5 -> B第1 A第2
    assert r["intra_board_rank"] == 2

def test_board_link_empty_sector(monkeypatch):
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    r = sm.board_money_link("000001")
    assert r["board"] in ("未知", None) or r.get("note")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_board_money_link.py -v`
Expected: FAIL（`board_money_link` 不存在）

- [ ] **Step 3: 实现**

```python
# screener/smart_money.py 追加
def board_money_link(code: str) -> dict:
    """板块-个股资金联动: 该股所属板块在 sector_fund_flow 的净流入排名,
    及该股在板块内 net_intensity(主力净额/成交额) 的横截位置。
    只读 stock_spot/sector_fund_flow, 不触网。"""
    code = str(code)
    base = {"code": code, "board": None, "board_main_net_inflow": None,
            "board_rank": None, "board_pct": None,
            "intra_board_rank": None, "intra_board_pct": None,
            "board_5d_trend": [], "note": ""}
    spots = db.query_rows("stock_spot", limit=0)
    if not spots:
        base["note"] = "stock_spot 为空,先 /api/refresh"
        return base
    board = None
    for s in spots:
        if str(s.get("code")) == code:
            board = s.get("board")
            break
    if not board:
        base["note"] = "该股无 board 字段"
        return base
    base["board"] = board
    # 板块净流入排名
    sff = db.query_rows("sector_fund_flow",
                        where="sector_type = ? AND indicator = ?",
                        params=("行业", "今日"), limit=0)
    if sff:
        ranked = sorted(sff, key=lambda x: _nan(x.get("main_net_inflow")) or 0, reverse=True)
        names = [r.get("name") for r in ranked]
        if board in names:
            idx = names.index(board)
            base["board_rank"] = idx + 1
            base["board_pct"] = round(1 - idx / max(len(names), 1), 4)
            base["board_main_net_inflow"] = _nan(ranked[idx].get("main_net_inflow"))
    # 板块内个股 net_intensity 横截(主力净额/成交额占比)
    # 注意:stock_spot 行的净额字段是 main_net_inflow,与 smart_money_action 的 amount 不同,
    # 故不复用 _attach_intensity(它读 amount 且会再查 stock_spot),直接内联算。
    intra = [s for s in spots if s.get("board") == board]
    def _ni(s):
        m = _nan(s.get("main_net_inflow")); t = _nan(s.get("turnover_amount"))
        return round(m / t, 4) if (m is not None and t and t != 0) else None
    intra_sorted = sorted(intra, key=lambda x: (_ni(x) if _ni(x) is not None else -1e18), reverse=True)
    codes_in = [str(x.get("code")) for x in intra_sorted]
    if code in codes_in:
        ix = codes_in.index(code)
        base["intra_board_rank"] = ix + 1
        base["intra_board_pct"] = round(1 - ix / max(len(codes_in), 1), 4)
    # 板块5日趋势
    sff5 = db.query_rows("sector_fund_flow",
                         where="sector_type = ? AND indicator = ? AND name = ?",
                         params=("行业", "5日", board), limit=0)
    base["board_5d_trend"] = [_nan(r.get("main_net_inflow")) for r in sff5]
    return base
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_board_money_link.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add screener/smart_money.py tests/test_board_money_link.py
git commit -m "feat(sm): 板块-个股资金联动 board_money_link"
```

---

### Task 4: 席位胜率 `seat_winrate` + 回填脚本

**Files:**
- Create: `scripts/backfill_lhb_history.py`
- Modify: `screener/smart_money.py`（追加 `seat_winrate`）
- Test: `tests/test_seat_winrate.py` / `tests/test_backfill_lhb.py`

**Interfaces:**
- Consumes: `db.query_rows("smart_money_action", where="channel=? AND actor LIKE ? AND date>=?", ...)`；`backtest.signals._uni_panels("stock",[code])` 返 `close` 算前视收益；finshare `get_lhb(start_date,end_date)`（回填脚本，标 source=finshare）；`sm_data._rec`/`db.upsert_rows`。
- Produces: `seat_winrate(actor, k=5, days=180) -> dict`（`actor`/`samples`/`by_k`/`recent`）。

- [ ] **Step 1: 写 seat_winrate 失败测试**

```python
# tests/test_seat_winrate.py
import pandas as pd
import screener.smart_money as sm

def test_seat_winrate(monkeypatch):
    rows = [
        {"code":"000001","name":"A","actor":"游资A","date":"2026-07-01","channel":"龙虎榜","amount":1e8},
        {"code":"000001","name":"A","actor":"游资A","date":"2026-07-10","channel":"龙虎榜","amount":2e8},
    ]
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: rows if table=="smart_money_action" else [])
    # close 需 ≥15 行(_fwd_ret 用 iloc[pos+k] 位置索引,k=5 需 entry 后≥5 行);
    # 7/01(pos0)=10→pos5=11 ret10%; 7/10(pos9)=10→pos14=12 ret20%
    idx = pd.date_range("2026-07-01", periods=20, freq="D")
    vals = [10.0]*20
    vals[5] = 11.0   # 7/01 entry k=5 -> +10%
    vals[14] = 12.0  # 7/10 entry k=5 -> +20%
    close = pd.DataFrame({"000001": vals}, index=idx)
    monkeypatch.setattr("backtest.signals._uni_panels", lambda u, codes: (close, None))
    r = sm.seat_winrate("游资A", k=5, days=180)
    assert r["actor"] == "游资A"
    assert r["samples"] == 2
    assert r["by_k"]["5"]["win_rate"] == 1.0
    assert 0.09 <= r["by_k"]["5"]["median_ret"] <= 0.21

def test_seat_winrate_national_team(monkeypatch):
    monkeypatch.setattr(sm, "_expand_national_team", lambda: ["中央汇金"])
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    monkeypatch.setattr("backtest.signals._uni_panels", lambda u, codes: (None, None))
    r = sm.seat_winrate("国家队", k=5, days=180)
    assert r["samples"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_seat_winrate.py -v`
Expected: FAIL（`seat_winrate` 不存在）

- [ ] **Step 3: 实现 seat_winrate**

```python
# screener/smart_money.py 追加
def _fwd_ret(close_s, date_str: str, k: int) -> float | None:
    """date_str 当日 close -> k 日后 close 收益率。不足→None。close_s 升序 Series。"""
    try:
        idx = pd.to_datetime(close_s.index)
        target = pd.to_datetime(date_str)
        pos = idx.get_loc(target) if target in idx else None
        if pos is None:
            # 最近一天
            mask = idx <= target
            if not mask.any():
                return None
            pos = int(mask.sum()) - 1
        if pos + k >= len(close_s):
            return None
        c0 = float(close_s.iloc[pos]); c1 = float(close_s.iloc[pos + k])
        return round(c1 / c0 - 1, 4) if c0 else None
    except Exception:
        return None

def seat_winrate(actor: str, k: int = 5, days: int = 180) -> dict:
    """席位胜率: 该 actor(或'国家队'展开) 近 days 日龙虎榜上榜个股,
    前视 k 日收益的中位数+胜率(正占比)。多 k(5/10/20)。
    依赖 smart_money_action 历史龙虎榜(需先跑 scripts/backfill_lhb_history.py)
    + stock_daily 前视(需先 /api/backtest/fetch)。历史统计事实非预测。"""
    actor = actor or ""
    out = {"actor": actor, "samples": 0, "by_k": {}, "recent": [], "note": ""}
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if actor == _NATIONAL_TEAM_KEYWORD:
        keys = _expand_national_team() or [actor]
    else:
        keys = [actor]
    rows = []
    for key in keys:
        rows += db.query_rows("smart_money_action",
                              where="channel = ? AND actor LIKE ? AND date >= ?",
                              params=("龙虎榜", f"%{key}%", since),
                              order_by="date DESC", limit=0)
    # 去重 (code,date)
    seen = set(); pairs = []
    for r in rows:
        key = (str(r.get("code")), str(r.get("date")))
        if key in seen or not r.get("code"):
            continue
        seen.add(key)
        pairs.append({"code": str(r["code"]), "date": str(r.get("date")),
                      "name": r.get("name"), "amount": _nan(r.get("amount"))})
    if not pairs:
        out["note"] = "无龙虎榜历史(先跑 scripts/backfill_lhb_history.py)"
        return out
    from backtest.signals import _uni_panels
    close, _ = _uni_panels("stock", list({p["code"] for p in pairs}))
    for kx in (5, 10, 20):
        rets = []
        for p in pairs:
            if close is None or p["code"] not in close.columns:
                continue
            r = _fwd_ret(close[p["code"]], p["date"], kx)
            if r is not None:
                rets.append(r)
        if rets:
            out["by_k"][str(kx)] = {
                "median_ret": round(float(np.median(rets)), 4),
                "win_rate": round(sum(1 for x in rets if x > 0) / len(rets), 4),
                "n": len(rets)}
    out["samples"] = max((v.get("n", 0) for v in out["by_k"].values()), default=0)
    # 近5次明细(k=5)
    for p in pairs[:5]:
        if close is not None and p["code"] in close.columns:
            r = _fwd_ret(close[p["code"]], p["date"], 5)
            out["recent"].append({**p, "ret_k5": r})
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_seat_winrate.py -v`
Expected: 2 passed

- [ ] **Step 5: 写回填脚本测试**

```python
# tests/test_backfill_lhb.py
import scripts.backfill_lhb_history as bf

def test_backfill_idempotent(monkeypatch):
    # finshare 返回两日榜单, upsert 应被调用, 失败不崩
    calls = []
    class _FS:
        @staticmethod
        def get_lhb(start_date, end_date):
            import pandas as pd
            return pd.DataFrame([
                {"code":"000001","trade_date":"20260701","name":"A","net_buy":1e8},
                {"code":"000002","trade_date":"20260702","name":"B","net_buy":2e8},
            ])
    monkeypatch.setattr(bf, "_get_finshare", lambda: _FS())
    monkeypatch.setattr(bf.sm_data, "_rec",
                        lambda *a, **k: {"date":"2026-07-01","code":"000001","channel":"龙虎榜"})
    monkeypatch.setattr(bf.db, "upsert_rows", lambda table, recs: calls.append(len(recs)))
    bf.backfill(months=1, batch_days=30)
    assert calls  # upsert 至少调用一次

def test_backfill_finshare_missing(monkeypatch):
    monkeypatch.setattr(bf, "_get_finshare", lambda: None)
    monkeypatch.setattr(bf.db, "upsert_rows", lambda table, recs: None)
    bf.backfill(months=1, batch_days=30)  # 不抛异常
```

- [ ] **Step 6: 实现回填脚本**

```python
# scripts/backfill_lhb_history.py  (新建)
"""一次性龙虎榜历史回填: finshare get_lhb 拉过去N月落 smart_money_action。
幂等(UNIQUE upsert 覆盖),可重复跑。失败不崩,标 source=finshare。
用法: python -m scripts.backfill_lhb_history  [months=6] [batch_days=30]
不进 refresh(一次性手动;以后每日 refresh_today 自然累积)。"""
from __future__ import annotations
import sys, datetime as _dt
from data import db, smart_money as sm_data

def _get_finshare():
    try:
        import finshare as fs
        return fs
    except Exception:
        return None

def _norm_date(s):
    return sm_data._norm_date(s) if s else None

def backfill(months: int = 6, batch_days: int = 30) -> int:
    fs = _get_finshare()
    if fs is None:
        print("finshare 未安装/不可用,跳过回填")
        return 0
    end = _dt.date.today()
    start = end - _dt.timedelta(days=months * 30)
    total = 0
    cur = start
    while cur < end:
        nxt = min(cur + _dt.timedelta(days=batch_days), end)
        try:
            df = fs.get_lhb(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d"))
        except Exception as e:
            print(f"  {cur}~{nxt} 失败:{e}")
            cur = nxt
            continue
        if df is None or getattr(df, "empty", True):
            cur = nxt
            continue
        recs = []
        for _, row in df.iterrows():
            d = _norm_date(row.get("trade_date") or row.get("日期"))
            code = str(row.get("code") or row.get("股票代码") or "").zfill(6)
            if not d or not code:
                continue
            amt = row.get("net_buy") or row.get("净买额") or row.get("净额")
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                amt = None
            recs.append(sm_data._rec(
                d, code, row.get("name") or row.get("股票简称"), "股票",
                "龙虎榜", "游资", "净买入", amt,
                raw={"source": "finshare"}))
        if recs:
            try:
                db.upsert_rows("smart_money_action", recs)
                total += len(recs)
            except Exception as e:
                print(f"  upsert {cur}~{nxt} 失败:{e}")
        cur = nxt
    print(f"回填完成,写入 {total} 条(标 source=finshare)")
    return total

if __name__ == "__main__":
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    bd = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    backfill(months=months, batch_days=bd)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python -m pytest tests/test_seat_winrate.py tests/test_backfill_lhb.py -v`
Expected: 4 passed

- [ ] **Step 8: 提交**

```bash
git add screener/smart_money.py scripts/backfill_lhb_history.py tests/test_seat_winrate.py tests/test_backfill_lhb.py
git commit -m "feat(sm): 席位胜率 seat_winrate + 龙虎榜历史回填脚本"
```

---

### Task 5: 路由接入

**Files:**
- Modify: `api/server.py`

**Interfaces:**
- Consumes: `screener.smart_money.main_force_phase`/`board_money_link`/`seat_winrate`；现有 `chip_distribution`（已带 trend）。
- Produces: 3 新路由 + `/api/chip` 透传 trend（chip_distribution 已自带，路由无需改，确认即可）。

- [ ] **Step 1: 写路由冒烟测试**

```python
# tests/test_server_new_routes.py 追加
# 注意:client 是该模块顶部的 module-level TestClient(app),不是 pytest fixture——
# 测试函数只取 monkeypatch,直接用全局 client(参考同文件 test_management_route)。
def test_smart_money_phase_route(monkeypatch):
    import screener.smart_money as sm
    monkeypatch.setattr(sm, "main_force_phase",
        lambda c, days=30: {"code":c,"phase":"吸筹","confidence":0.75,"triggers":[],"indicators":{}})
    r = client.get("/api/smart-money/phase?code=000001")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["phase"] == "吸筹"   # _wrap 把数据放 data 键
    assert "cand_disclaimer" in body

def test_smart_money_seat_winrate_route(monkeypatch):
    import screener.smart_money as sm
    monkeypatch.setattr(sm, "seat_winrate", lambda a, k=5, days=180: {"actor":a,"samples":0,"by_k":{}})
    r = client.get("/api/smart-money/seat-winrate?actor=testseat&k=5")  # ascii 避编码问题
    assert r.status_code == 200
    assert "cand_disclaimer" in r.json()

def test_smart_money_board_link_route(monkeypatch):
    import screener.smart_money as sm
    monkeypatch.setattr(sm, "board_money_link", lambda c: {"code":c,"board":"银行","board_rank":1})
    r = client.get("/api/smart-money/board-link?code=000001")
    assert r.status_code == 200
    assert r.json()["data"]["board"] == "银行"
```

> 注：`_wrap(data, extra)` 返回 `{"data":..., "update_time":..., "disclaimer":...}` 并把 extra 合并进顶层——所以 `cand_disclaimer` 与 `data` 都是顶层键。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_server_new_routes.py -k "phase or seat_winrate or board_link" -v`
Expected: FAIL（路由不存在,404）

- [ ] **Step 3: 实现路由**

在 `api/server.py` 现有 smart-money 路由块附近追加：

```python
# api/server.py —— 现有 smart-money 路由块附近追加
# _wrap(data, extra_dict): extra 合并进顶层,故 cand_disclaimer 传 dict 不是 kwarg。
@app.get("/api/smart-money/phase")
def smart_money_phase(code: str = Query(..., description="股票代码")):
    from screener.smart_money import main_force_phase
    return _wrap(main_force_phase(code),
                 {"cand_disclaimer": "主力阶段机械判定(计分制),研究观察非买卖信号,盈亏自负。"})

@app.get("/api/smart-money/seat-winrate")
def smart_money_seat_winrate(actor: str = Query(..., description="席位/游资名"),
                             k: int = Query(5), days: int = Query(180)):
    from screener.smart_money import seat_winrate
    return _wrap(seat_winrate(actor, k=k, days=days),
                 {"cand_disclaimer": "席位历史胜率统计事实,非预测,盈亏自负。"})

@app.get("/api/smart-money/board-link")
def smart_money_board_link(code: str = Query(..., description="股票代码")):
    from screener.smart_money import board_money_link
    return _wrap(board_money_link(code),
                 {"cand_disclaimer": "板块-个股资金联动机械统计,非买卖信号,盈亏自负。"})
```

> `Query` 已在 server.py 顶部导入(现有路由均用)。`/api/chip` 无需改——`chip_distribution` 已自带 `trend`，`_wrap` 透传 `data` 即可。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_server_new_routes.py -v`
Expected: PASS（含新路由 + 旧路由不回归）

- [ ] **Step 5: 提交**

```bash
git add api/server.py tests/test_server_new_routes.py
git commit -m "feat(api): 主力阶段/席位胜率/板块联动3路由接入"
```

---

### Task 6: 前端展示

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `/api/smart-money/phase`、`/api/smart-money/board-link`、`/api/chip`（trend）。
- Produces: 个股分析卡"主力阶段"色块 + board-link 行 + 筹码迁移标。

- [ ] **Step 1: 个股分析卡加 phase 色块**

在 `stockAnalysis()` 渲染逻辑（找到现有主力动向 sparkline 渲染处）后追加 phase 块。phase 取色：

```javascript
// web/index.html —— 个股分析卡内
const PHASE_COLOR = {吸筹:"#dc2626", 洗盘:"#ca8a04", 拉升:"#ea580c", 出货:"#16a34a", 观望:"#6b7280"};
async function fetchPhase(code){
  const r = await fetch(`/api/smart-money/phase?code=${encodeURIComponent(code)}`);
  const j = await r.json();
  const p = j.data;   // _wrap 把数据放 data 键(不是 rows/顶层)
  if(!p || !p.phase) return;
  const el = document.getElementById('saPhase') || (()=>{
    const d=document.createElement('div'); d.id='saPhase';
    document.querySelector('#analysisCard .sm-block')?.appendChild(d); return d;})();
  el.innerHTML = `<span style="background:${PHASE_COLOR[p.phase]||'#6b7280'};color:#fff;padding:2px 10px;border-radius:8px;font-size:13px">${p.phase} ${(p.confidence*100|0)}%</span>`
    + `<div style="font-size:12px;color:var(--muted);margin-top:4px">${(p.triggers||[]).join(' · ')}</div>`;
}
```

并在 `stockAnalysis(code)` 末尾调 `fetchPhase(code)`。

- [ ] **Step 2: 筹码迁移标**

在 `_renderChip(chip)`（chip 分布渲染函数，参数 `chip` 已是 `_wrap` 解包后的 `data` dict，含 `chip.trend`）的现有 `<div>` 块（line 1462 集中度那行下方）追加 trend 小标：

```javascript
// 在 _renderChip 内,集中度 _kv 行之后加:
${chip.trend && chip.trend.profit_ratio_delta!=null ? (()=>{
  const up = chip.trend.profit_ratio_delta>=0;
  return `<div style="font-size:11px;margin-top:2px">迁移: 获利盘5/20日Δ <span style="color:${up?'#dc2626':'#16a34a'}">${(chip.trend.profit_ratio_delta*100).toFixed(1)}%</span> · 集中度Δ ${chip.trend.chip_concentration_delta!=null?(chip.trend.chip_concentration_delta*100).toFixed(1)+'%':'—'}</div>`;
})():''}
```

> `_renderChip` 由 `stockAnalysis` 里 `_renderChip(chipR.data)` 调用（line 1568），`chipR.data` 即 chip dict（含 trend）。trend 为空（无历史）时 `chip.trend` 为 `{}`，`profit_ratio_delta` 为 undefined → 不渲染。

- [ ] **Step 3: 手测**

`docker compose up -d` 后访问 `http://localhost:8000/web/index.html`，个股分析 tab 输入代码，确认 phase 色块与筹码迁移标显示。无数据时降级"观望/数据不足"。

- [ ] **Step 4: 提交**

```bash
git add web/index.html
git commit -m "feat(web): 个股分析卡主力阶段色块+筹码迁移标"
```

---

### Task 7: 文档同步

**Files:**
- Modify: `CLAUDE.md`、`README.md`

- [ ] **Step 1: CLAUDE.md 更新**

`backtest/quality.py` 段：将"Phase4 口径5景气/IC 透明度报告待续"改为"口径5景气已实现(研报覆盖/评级/目标价)；IC 透明度报告待续"。
`screener/smart_money.py` 段：补 `main_force_phase`(计分制4阶段+观望,复用 behavior+chip+spot,30s缓存) / `board_money_link`(板块净流入排名+板块内net_intensity位置) / `seat_winrate`(finshare回填6月龙虎榜+前视收益) / `chip_distribution` 加 trend。
路由速查补 `/api/smart-money/phase` `/seat-winrate` `/board-link`。

- [ ] **Step 2: README.md 更新**

功能段"主力动向"补"主力阶段判定(吸筹/洗盘/拉升/出货计分制)"；路由表补 3 路由。

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md README.md
git commit -m "docs: 同步主力动向4项能力+口径5已实现"
```

---

### Task 8: 全量回归 + 部署

- [ ] **Step 1: 全量单测**

Run: `python -m pytest tests/ -q`
Expected: 全绿（原有 282 + 新增 ~15）

- [ ] **Step 2: 部署**

Run: `bash deploy.sh`
Expected: 测试通过 → 重建 → 健康检查 200 → 模块自检 → 路由冒烟 200

- [ ] **Step 3: 合并 main**

```bash
git checkout main && git merge --ff-only feat/sm-phase-judgment
git push github main
```

## Self-Review（已执行）

1. **Spec 覆盖**：阶段判定(T1)/筹码迁移(T2)/板块联动(T3)/席位胜率+回填(T4)/路由(T5)/前端(T6)/文档(T7) 全覆盖。
2. **Placeholder 扫描**：无 TBD/TODO；所有 code step 含真实代码。
3. **类型一致**：`main_force_phase`/`board_money_link`/`seat_winrate` 签名在 T1/T3/T4 定义，T5 路由调用一致；`_high60_pct`/`_turnover5_avg`/`_fwd_ret`/`_chip_series` 在定义 task 中出现后再被使用。

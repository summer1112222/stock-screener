# quality 两阶段盘口精排 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 quality 四口径共振粗筛后插入 pytdx 实时盘口精排阶段，用 A 流动性深度重排 top50 小名单（B/C 仅 raw 展示不进排序），盘后自动降级。

**Architecture:** `backtest/quality.py` 编排层内新增 `_is_in_session()` + `_refine_by_quote()` 两个函数，`quality_rank` 加 `refine`/`refine_pool` 参数并在过 hits 门槛的 `main` 上调用精排，精排后顺序交给现有 `_apply_combo` 组合层。不新增表/采集源，调 `data/pytdx_client.get_quote`。

**Tech Stack:** Python 3.12 + pandas 3.0 + pytest（mock `pytdx_client.get_quote` 不真连网）

**Spec:** `docs/superpowers/specs/2026-08-13-quality-quote-refine-design.md`

## Global Constraints

- 合规硬约束（最高优先级）：不荐股/不输出买卖信号。B/C（挂单不对称、内外盘比）方向**不进排序综合分**，仅 raw 字段展示。字段命名中性（`bid_ask_ratio`/`liquidity_pct`/`inner_outer_ratio`），不叫"看多度/优质度"。
- 不新增表、不新增采集源、不进 refresh。pytdx 按需小名单调用（≤80/批一次 TCP），非全市场 5200 次。
- NaN→None：盘口字段经 `_to_float`→None，防 `JSONResponse allow_nan=False` 500。
- 权重 0.6/0.4、`refine_pool=50`、盘中缓存 30s 均为经验先验，未经回归校准。
- 单测放 `tests/`，合成数据 mock `db.query_rows` + `pytdx_client.get_quote`，不依赖网络。仓库根目录跑 `python -m pytest tests/ -q`。

---

### Task 1: `_is_in_session()` 盘中判定

**Files:**
- Modify: `backtest/quality.py`（顶部加 `import datetime as _dt`，新增函数）
- Test: `tests/test_quality_refine.py`（新建）

**Interfaces:**
- Produces: `_is_in_session(now: datetime | None = None) -> bool`，`now=None` 时取当前本地时间。周一至周五 9:30-11:30 / 13:00-15:00 返 True，否则 False。注入 `now` 供测试。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_quality_refine.py`：
```python
# -*- coding: utf-8 -*-
"""quality 盘口精排测试。mock pytdx_client.get_quote，不真连网。"""
import datetime as dt
from unittest.mock import patch

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import quality


def test_is_in_session_morning():
    # 周一 10:00 = 盘中
    mon = dt.datetime(2026, 8, 10, 10, 0)  # 2026-08-10 周一
    assert quality._is_in_session(mon) is True


def test_is_in_session_afternoon():
    tue = dt.datetime(2026, 8, 11, 14, 30)  # 周二 14:30
    assert quality._is_in_session(tue) is True


def test_is_in_session_lunch():
    mon = dt.datetime(2026, 8, 10, 12, 0)  # 午休 12:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_preopen():
    mon = dt.datetime(2026, 8, 10, 9, 0)  # 盘前 9:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_afterclose():
    mon = dt.datetime(2026, 8, 10, 16, 0)  # 盘后 16:00
    assert quality._is_in_session(mon) is False


def test_is_in_session_weekend():
    sat = dt.datetime(2026, 8, 15, 10, 0)  # 周六
    assert quality._is_in_session(sat) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality_refine.py -q`
Expected: FAIL with `AttributeError: module 'backtest.quality' has no attribute '_is_in_session'`

- [ ] **Step 3: Write minimal implementation**

在 `backtest/quality.py` 顶部 `import numpy as np` 之后加：
```python
import datetime as _dt
```

在 `_to_float` 函数之前新增：
```python
def _is_in_session(now: "_dt.datetime | None" = None) -> bool:
    """best-effort 判 A 股交易时段：周一至周五 9:30-11:30 / 13:00-15:00。
    节假日无历：误判盘中时 get_quote 返回收盘盘口，上层降级为盘后语义，不崩。
    now=None 取当前本地时间；测试可注入 mock datetime。"""
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:  # 周六5/周日6
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_quality_refine.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_refine.py
git commit -m "feat(quality): _is_in_session 盘中判定(注入now可测)"
```

---

### Task 2: `_refine_by_quote()` 盘中场景（A/B/C + 综合分重排）

**Files:**
- Modify: `backtest/quality.py`（新增 `_refine_by_quote`）
- Test: `tests/test_quality_refine.py`（追加）

**Interfaces:**
- Consumes: `pytdx_client.get_quote(codes: list[str]) -> list[dict]`（每行含 `code/price/bid_vol1-5/ask_vol1-5/b_vol/s_vol`）；`_to_pct`/`_to_float`（已存在于 quality.py）
- Produces: `_refine_by_quote(pool: list[dict], df_spot: pd.DataFrame, in_session: bool) -> tuple[list[dict], str, dict]`，返回 `(pool, refine_status, quote_by_code)`。pool 每行被原地附加 `quote` 字段；盘中按 `_refine_score` 重排（`_refine_score` 为内部键，最终清理时删除）。`quote_by_code` 供 `quality_rank` 给非 pool 行补空 quote。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_quality_refine.py`：
```python
def _mock_quote(code):
    """构造一只 mock 五档行情：bid 略厚、主动买略多。"""
    return {
        "code": code, "price": 10.0, "last_close": 9.8, "open": 9.9,
        "high": 10.2, "low": 9.8, "vol": 10000.0, "amount": 1e7,
        "b_vol": 5500.0, "s_vol": 4500.0,
        "bid1": 9.99, "ask1": 10.01,
        "bid2": 9.98, "ask2": 10.02, "bid3": 9.97, "ask3": 10.03,
        "bid4": 9.96, "ask4": 10.04, "bid5": 9.95, "ask5": 10.05,
        "bid_vol1": 600.0, "ask_vol1": 400.0,
        "bid_vol2": 500.0, "ask_vol2": 300.0,
        "bid_vol3": 400.0, "ask_vol3": 200.0,
        "bid_vol4": 300.0, "ask_vol4": 100.0,
        "bid_vol5": 200.0, "ask_vol5": 50.0,
    }


def test_refine_intraday_factors_and_resort():
    # 两只候选：A 流动性深度高、B 低，验综合分重排后 A 排前
    pool = [
        {"code": "000001", "name": "平A", "resonance": 20.5, "hits": 2, "dim_scores": {}},
        {"code": "000002", "name": "万B", "resonance": 20.4, "hits": 2, "dim_scores": {}},
    ]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}, {"code": "000002"}])

    def fake_get_quote(codes):
        out = []
        for c in codes:
            q = _mock_quote(c)
            # 000001 盘口更厚
            if c == "000001":
                for i in range(1, 6):
                    q[f"bid_vol{i}"] *= 5
                    q[f"ask_vol{i}"] *= 5
            out.append(q)
        return out

    with patch("data.pytdx_client.get_quote", side_effect=fake_get_quote):
        out_pool, status, qmap = quality._refine_by_quote(list(pool), df, in_session=True)

    assert status == "ok(盘中)"
    # 000001 流动性深度更高 → 综合分更高 → 排前
    assert out_pool[0]["code"] == "000001"
    # quote 字段齐
    q0 = out_pool[0]["quote"]
    assert q0["liquidity_depth"] is not None
    assert q0["bid_ask_ratio"] is not None
    assert q0["inner_outer_ratio"] is not None
    assert q0["liquidity_pct"] is not None
    assert q0["in_session"] is True
    # _refine_score 为内部键，最终 quality_rank 清理；此处精排阶段仍存在
    assert out_pool[0]["_refine_score"] >= out_pool[1]["_refine_score"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality_refine.py::test_refine_intraday_factors_and_resort -q`
Expected: FAIL with `AttributeError: module 'backtest.quality' has no attribute '_refine_by_quote'`

- [ ] **Step 3: Write minimal implementation**

在 `_is_in_session` 之后新增：
```python
def _refine_by_quote(pool: list, df_spot, in_session: bool):
    """对小名单 get_quote 取盘口，算 A 流动性深度(+综合分重排) + B/C raw 展示。
    B/C 方向不进排序（合规：方向=择时信号非质量）。
    返回 (pool, refine_status, quote_by_code)。pool 原地附加 quote 字段。
    盘中：按 _refine_score=0.6*resonance_pct+0.4*liquidity_pct 重排。
    盘后：A/B=None + note，仅 C 全天内外盘，不重排。
    get_quote 失败：refine_status=err，pool 不变。"""
    import math
    from data import pytdx_client
    codes = [str(it.get("code")) for it in pool]
    quotes = {str(q.get("code")): q for q in pytdx_client.get_quote(codes)}
    if not quotes:
        return pool, "err:通达信不可用,跳过精排", {}

    depths, brs, iors = {}, {}, {}
    for c in codes:
        q = quotes.get(c) or {}
        bv = [q.get(f"bid_vol{i}") for i in range(1, 6)]
        av = [q.get(f"ask_vol{i}") for i in range(1, 6)]
        bid_sum = sum((x or 0) for x in bv)
        ask_sum = sum((x or 0) for x in av)
        tot = bid_sum + ask_sum
        depths[c] = math.log(tot) if tot > 0 else None
        brs[c] = (bid_sum / tot) if tot > 0 else None
        b_vol = q.get("b_vol")
        s_vol = q.get("s_vol")
        iors[c] = (b_vol / s_vol) if (b_vol and s_vol and s_vol != 0) else None

    def _quote_dict(c):
        lp = lpct.get(c) if in_session else None
        return {
            "liquidity_depth": _to_float(depths.get(c)),
            "bid_ask_ratio": _to_float(brs.get(c)) if in_session else None,
            "inner_outer_ratio": _to_float(iors.get(c)),
            "liquidity_pct": _to_float(lp) if in_session else None,
            "in_session": in_session,
            **({} if in_session else {"note": "收盘挂单,A/B失效"}),
        }

    lpct = {}
    if in_session:
        ds = pd.Series({c: depths[c] for c in codes if depths.get(c) is not None})
        lpct = _to_pct(ds).to_dict() if not ds.empty else {}
        rs = pd.Series({str(it.get("code")): it.get("resonance") or 0 for it in pool})
        rpct = _to_pct(rs).to_dict() if not rs.empty else {}
        for it in pool:
            c = str(it.get("code"))
            lp = lpct.get(c, 0.0)
            rp = rpct.get(c, 0.0)
            it["_refine_score"] = 0.6 * _to_float(rp) + 0.4 * _to_float(lp)
            it["quote"] = _quote_dict(c)
        pool.sort(key=lambda x: x.get("_refine_score") or 0.0, reverse=True)
    else:
        for it in pool:
            it["quote"] = _quote_dict(str(it.get("code")))

    status = "ok(盘中)" if in_session else "ok(盘后,仅C展示)"
    return pool, status, {c: _quote_dict(c) for c in codes}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_quality_refine.py::test_refine_intraday_factors_and_resort -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_refine.py
git commit -m "feat(quality): _refine_by_quote 盘中场景(A流动性深度重排+B/C raw)"
```

---

### Task 3: 盘后降级 + get_quote 失败降级

**Files:**
- Modify: `backtest/quality.py`（无新代码，Task 2 已覆盖分支；仅测试验证）
- Test: `tests/test_quality_refine.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `_refine_by_quote`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_quality_refine.py`：
```python
def test_refine_afterhours_degrades_to_c_only():
    pool = [{"code": "000001", "name": "x", "resonance": 20.0, "hits": 2, "dim_scores": {}}]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}])
    with patch("data.pytdx_client.get_quote", side_effect=lambda cs: [_mock_quote(c) for c in cs]):
        out, status, qmap = quality._refine_by_quote(list(pool), df, in_session=False)
    assert status == "ok(盘后,仅C展示)"
    q = out[0]["quote"]
    assert q["liquidity_depth"] is None
    assert q["bid_ask_ratio"] is None
    assert q["inner_outer_ratio"] is not None  # 全天内外盘仍有效
    assert q["in_session"] is False
    assert q.get("note")  # 失效标注
    # 盘后不重排：pool 顺序不变
    assert out[0]["code"] == "000001"


def test_refine_quote_failure_no_crash():
    pool = [{"code": "000001", "name": "x", "resonance": 20.0, "hits": 2, "dim_scores": {}}]
    import pandas as pd
    df = pd.DataFrame([{"code": "000001"}])
    with patch("data.pytdx_client.get_quote", side_effect=lambda cs: []):
        out, status, qmap = quality._refine_by_quote(list(pool), df, in_session=True)
    assert status == "err:通达信不可用,跳过精排"
    assert out == pool  # 原样返回，不崩
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality_refine.py::test_refine_afterhours_degrades_to_c_only tests/test_quality_refine.py::test_refine_quote_failure_no_crash -q`
Expected: PASS（Task 2 实现已覆盖两分支；若 FAIL 说明分支有 bug，修 `_refine_by_quote`）

- [ ] **Step 3: (仅当 FAIL) 修正 `_refine_by_quote` 分支**

检查盘后分支与空 quotes 分支，确保 `quote` 字段结构与测试一致。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_quality_refine.py -q`
Expected: 全部 passed（含 Task 1+2+3）

- [ ] **Step 5: Commit**

```bash
git add tests/test_quality_refine.py
git commit -m "test(quality): 盘后降级(仅C)+get_quote失败降级"
```

---

### Task 4: `quality_rank` 接入精排（参数 + quote 字段 + 组合层交互）

**Files:**
- Modify: `backtest/quality.py`：`quality_rank` 签名 + 缓存 key + 精排调用 + `_clean_item` 透传 quote + `_RESULT_CACHE` 盘中 TTL
- Test: `tests/test_quality_refine.py`（追加）

**Interfaces:**
- Consumes: `_is_in_session`、`_refine_by_quote`、`_apply_combo`、`db.query_rows`
- Produces: `quality_rank(..., refine=True, refine_pool=50)`，返回 `main` 每行含 `quote` 字段，顶层含 `refine_status`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_quality_refine.py`：
```python
def _seed_spot_rows():
    """3 只合成 spot 行，供 quality_rank 走通。"""
    return [
        {"code": "000001", "name": "平A", "latest_price": 10.0, "turnover_amount": 1e8,
         "change_pct": 2.0, "main_net_inflow": 1e7, "turnover_rate": 3.0,
         "pe": 15.0, "pb": 1.5, "amplitude": 3.0, "board": "银行"},
        {"code": "000002", "name": "万B", "latest_price": 20.0, "turnover_amount": 1.2e8,
         "change_pct": 3.0, "main_net_inflow": 2e7, "turnover_rate": 4.0,
         "pe": 18.0, "pb": 2.0, "amplitude": 4.0, "board": "地产"},
        {"code": "600519", "name": "贵C", "latest_price": 1500.0, "turnover_amount": 2e8,
         "change_pct": 1.0, "main_net_inflow": 3e7, "turnover_rate": 2.0,
         "pe": 30.0, "pb": 8.0, "amplitude": 2.0, "board": "白酒"},
    ]


def test_quality_rank_intraday_attaches_quote_and_resort():
    # 盘中：mock 全部 DB 表 + get_quote + buffett/signals 依赖
    import pandas as pd
    rows = _seed_spot_rows()
    qr = {"stock_spot": rows, "industry_board": []}

    def fake_query(table, **kw):
        return qr.get(table, [])

    def fake_get_quote(codes):
        return [_mock_quote(c) for c in codes]

    # 屏蔽口径1/4 对历史的依赖（返空历史）+ 口径2 buffett + 口径3 smart_money
    with patch("data.db.query_rows", side_effect=fake_query), \
         patch("data.pytdx_client.get_quote", side_effect=fake_get_quote), \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.buffett._AK_OK", False), \
         patch("screener.smart_money.top_by_amount", return_value={"rows": []}), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}):
        res = quality.quality_rank(universe="stock", refine=True, refine_pool=3,
                                   min_turnover=0, dim_thresh=0.0, min_dims=1)

    assert res["refine_status"] == "ok(盘中)"
    assert len(res["main"]) >= 1
    # 每行有 quote 字段
    assert "quote" in res["main"][0]
    assert res["main"][0]["quote"]["in_session"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality_refine.py::test_quality_rank_intraday_attaches_quote_and_resort -q`
Expected: FAIL（`quality_rank` 不接受 `refine`/`refine_pool`，无 `refine_status`）

- [ ] **Step 3: Write minimal implementation**

修改 `quality_rank` 签名（加参数）：
```python
def quality_rank(universe="stock", days=20, weights=None, min_dims=2,
                 dim_thresh=0.6, min_turnover=5e7, max_per_board=3,
                 max_corr=0.85, limit=20, min_signals=2, limit_pct=9.9,
                 combo_method: str = "greedy",
                 refine: bool = True, refine_pool: int = 50) -> dict:
```

缓存 key 加 refine/refine_pool，并把 TTL 改为盘中分档。定位 `import time as _time` 那段，替换为：
```python
    import time as _time
    in_session = _is_in_session()
    _key = (universe, days, min_dims, dim_thresh, min_turnover, max_per_board,
            max_corr, limit, min_signals, limit_pct, combo_method, refine, refine_pool)
    _now = _time.time()
    _ttl = 30.0 if in_session else _RESULT_TTL
    _hit = _RESULT_CACHE.get(_key)
    if _hit and _now - _hit[0] < _ttl:
        return _hit[1]
```

在 `_apply_combo` 调用之前、`main = [it for it in enriched if it["hits"] >= eff_min_dims]` 与 `main.sort(...)` 之后插入精排：
```python
    main = [it for it in enriched if it["hits"] >= eff_min_dims]
    main.sort(key=lambda x: x["resonance"] or 0, reverse=True)

    # 盘口精排阶段（仅个股 + refine）
    refine_status = "skip(refine=False)"
    quote_by_code = {}
    if universe == "stock" and refine and main:
        pool = main[:refine_pool]
        pool, refine_status, quote_by_code = _refine_by_quote(
            pool, df, in_session=in_session)
        main = pool  # 精排重排后的 top refine_pool 直接作为组合层输入
    elif universe != "stock":
        refine_status = "skip(ETF不精排)"

    main = _apply_combo(main, universe, df, max_per_board, max_corr, limit,
                        combo_method=combo_method, close=close, board_map=board_map)
```

`_clean_item` 透传 quote 字段（在 `_clean_item` 内补一行，避免被 `dim_scores` 清理误删）：
```python
    def _clean_item(it):
        it["reasons"] = _build_reasons(it)
        it["dim_scores"] = {d: _to_float(v) for d, v in it.get("dim_scores", {}).items()}
        it["resonance"] = _to_float(it.get("resonance"))
        it.pop("_refine_score", None)  # 内部键清理
        return it
```

result 顶层加 `refine_status`：
```python
    result = {"main": main, "by_dim": by_dim, "dims_available": dims_avail,
              "dim_status": dim_status, "min_dims": eff_min_dims,
              "refine_status": refine_status,
              "source_health": {str(d): dim_status.get(str(d), "") for d in (1, 2, 3, 4)},
              "cand_disclaimer": _CAND_DISCLAIMER, "error": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_quality_refine.py::test_quality_rank_intraday_attaches_quote_and_resort -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_quality_refine.py
git commit -m "feat(quality): quality_rank 接入盘口精排(quote字段+refine_status+盘中30s缓存)"
```

---

### Task 5: ETF 跳过 + `refine=False` 跳过 + 组合层约束仍生效

**Files:**
- Modify: `backtest/quality.py`（Task 4 已加分支；本任务测试验证）
- Test: `tests/test_quality_refine.py`（追加）

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_quality_refine.py`：
```python
def test_quality_rank_etf_skips_refine():
    import pandas as pd
    qr = {"etf_spot": [{"code": "510300", "name": "沪深300ETF", "latest_price": 4.0,
                        "turnover_amount": 1e8, "change_pct": 1.0,
                        "main_net_inflow": 1e6, "turnover_rate": 5.0,
                        "pe": None, "pb": None, "amplitude": 2.0}],
          "industry_board": []}
    with patch("data.db.query_rows", side_effect=lambda t, **k: qr.get(t, [])), \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}):
        res = quality.quality_rank(universe="etf", min_turnover=0,
                                   dim_thresh=0.0, min_dims=1)
    assert res["refine_status"] == "skip(ETF不精排)"


def test_quality_rank_refine_false_skips():
    import pandas as pd
    qr = {"stock_spot": _seed_spot_rows(), "industry_board": []}
    with patch("data.db.query_rows", side_effect=lambda t, **k: qr.get(t, [])), \
         patch("data.pytdx_client.get_quote", side_effect=lambda cs: [_mock_quote(c) for c in cs]) as gq, \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.buffett._AK_OK", False), \
         patch("screener.smart_money.top_by_amount", return_value={"rows": []}), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}):
        res = quality.quality_rank(universe="stock", refine=False,
                                   min_turnover=0, dim_thresh=0.0, min_dims=1)
    assert res["refine_status"] == "skip(refine=False)"
    gq.assert_not_called()  # refine=False 不触网


def test_quality_rank_combo_constraint_holds():
    # 5 只同行业候选，max_per_board=2 → main 最多 2 只该行业
    import pandas as pd
    rows = []
    for i in range(5):
        rows.append({"code": f"00000{i}", "name": f"N{i}", "latest_price": 10.0,
                     "turnover_amount": 1e8, "change_pct": 2.0,
                     "main_net_inflow": 1e7, "turnover_rate": 3.0,
                     "pe": 15.0, "pb": 1.5, "amplitude": 3.0, "board": "同行业"})
    qr = {"stock_spot": rows, "industry_board": []}
    with patch("data.db.query_rows", side_effect=lambda t, **k: qr.get(t, [])), \
         patch("data.pytdx_client.get_quote", side_effect=lambda cs: [_mock_quote(c) for c in cs]), \
         patch("backtest.eval.load_panel", return_value=pd.DataFrame()), \
         patch("backtest.buffett._AK_OK", False), \
         patch("screener.smart_money.top_by_amount", return_value={"rows": []}), \
         patch("backtest.signals.scan_signals", return_value={"rows": [], "error": "无历史"}), \
         patch("backtest.signals.backtest_signals", return_value={"error": "无历史"}):
        res = quality.quality_rank(universe="stock", refine=True, refine_pool=5,
                                   max_per_board=2, min_turnover=0,
                                   dim_thresh=0.0, min_dims=1, limit=10)
    boards = [it.get("constraints", {}).get("board") for it in res["main"]]
    assert boards.count("同行业") <= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality_refine.py -q -k "etf_skips or refine_false_skips or combo_constraint"`
Expected: ETF/refine=False 的 PASS（Task 4 已覆盖分支）；combo_constraint 可能 PASS 或暴露 `_apply_combo` 接收 pool 后约束问题。

- [ ] **Step 3: (仅当 FAIL) 修 `_apply_combo` 调用**

确认 `main = pool` 后 `_apply_combo(main, ...)` 贪心在精排后顺序上工作，`constraints.board` 字段仍被设置。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_quality_refine.py -q`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_quality_refine.py
git commit -m "test(quality): ETF跳过+refine=False跳过+组合层约束仍生效"
```

---

### Task 6: `/api/quality` 路由透传 + disclaimer 措辞 + CLAUDE.md 同步

**Files:**
- Modify: `api/server.py`：`/api/quality` 加 `refine`/`refine_pool` Query 参数
- Modify: `backtest/quality.py`：`_CAND_DISCLAIMER` 追加盘口措辞
- Modify: `CLAUDE.md`：架构段 quality.py + 改动检查清单

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_quality_refine.py`：
```python
def test_api_quality_passes_refine_params():
    from fastapi.testclient import TestClient
    from api import server
    import pandas as pd
    qr = {"stock_spot": _seed_spot_rows(), "industry_board": []}
    captured = {}

    def fake_quality_rank(**kw):
        captured.update(kw)
        return {"main": [], "by_dim": {}, "dims_available": [], "dim_status": {},
                "min_dims": 1, "refine_status": "skip(refine=False)",
                "cand_disclaimer": "x", "error": None}

    with patch("backtest.quality.quality_rank", side_effect=fake_quality_rank):
        client = TestClient(server.app)
        r = client.get("/api/quality?universe=stock&refine=false&refine_pool=30")
    assert r.status_code == 200
    assert captured.get("refine") is False
    assert captured.get("refine_pool") == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_quality_refine.py::test_api_quality_passes_refine_params -q`
Expected: FAIL（路由不接受 `refine`/`refine_pool`，`captured` 无这两个 key）

- [ ] **Step 3: Write minimal implementation**

`api/server.py` 的 `/api/quality` 路由加参数：
```python
@app.get("/api/quality")
def quality_screen(universe: str = Query("stock"), days: int = Query(20),
                   min_dims: int = Query(2), min_turnover: float = Query(5e7),
                   max_per_board: int = Query(3), max_corr: float = Query(0.85),
                   limit: int = Query(20), combo_method: str = Query("greedy"),
                   dim_thresh: float = Query(0.6, ge=0.0, le=1.0),
                   refine: bool = Query(True), refine_pool: int = Query(50)):
    from backtest import quality
    res = quality.quality_rank(
        universe=universe, days=days, min_dims=min_dims,
        min_turnover=min_turnover, max_per_board=max_per_board,
        max_corr=max_corr, limit=limit, combo_method=combo_method,
        dim_thresh=dim_thresh, refine=refine, refine_pool=refine_pool)
    return _wrap(res, {"cand_disclaimer": res.get("cand_disclaimer",
                       "多口径共振机械排序观察清单，非荐股非买卖信号，盈亏自负。")})
```

`backtest/quality.py` 的 `_CAND_DISCLAIMER` 追加盘口措辞：
```python
_CAND_DISCLAIMER = ("多口径共振机械排序观察清单，非荐股非买卖信号，"
                    "不构成投资建议、不承诺收益。盘口微结构为实时供求机械观察，"
                    "非买卖信号；A/B 收盘后失效。市场有风险，盈亏自负。")
```

- [ ] **Step 4: Run test to verify it passes + 全量回归**

Run: `python -m pytest tests/test_quality_refine.py -q && python -m pytest tests/ -q`
Expected: 全部 passed（新 13 测试 + 既有测试不回归）

- [ ] **Step 5: 同步 CLAUDE.md**

`CLAUDE.md` 架构段 `quality.py` 描述补「盘口精排」：在 `quality.py（多口径共振编排层...）` 末尾追加 `；盘口精排阶段(2026-08-13)：四口径共振粗筛 top50 → pytdx get_quote 取实时盘口 → A 流动性深度重排(0.6共振+0.4流动性横截分位) + B 挂单不对称/C 内外盘比 仅 raw 展示不进排序(合规:方向=择时非质量),盘后自动降级(A/B失效仅留C全天内外盘),refine/refine_pool 参数,盘中缓存30s;ETF 与 refine=False 跳过。措辞"盘口供求机械观察",disclaimer 追加 A/B 收盘失效。`

改动检查清单「新增 quality 编排层」条目末尾补一句：`盘口精排阶段(pytdx get_quote)守同约束:仅 universe=stock+refine+小名单(top refine_pool=50)触网,非全市场;A 流动性深度进综合分(0.6共振+0.4流动性),B/C 方向不进排序仅 raw(合规);盘后 in_session=False 自动降级 A/B=None 仅 C;_is_in_session 注入 now 可测;改权重/阈值同步 tests/test_quality_refine.py(13 测试)。`

- [ ] **Step 6: Commit**

```bash
git add api/server.py backtest/quality.py CLAUDE.md tests/test_quality_refine.py
git commit -m "feat(quality): /api/quality 透传 refine/refine_pool + disclaimer + CLAUDE.md 同步"
```

---

## Self-Review 已完成

**1. Spec coverage**：§3 数据流→Task 4；§4 因子→Task 2；§5 盘中判定→Task 1+3；§6 缓存 TTL→Task 4；§7 接口→Task 4+6；§8 ETF→Task 5；§10 合规措辞→Task 6；§12 测试 1-8→Task 1-5（8 场景全覆盖）；§13 不在范围→未引入。✅

**2. Placeholder scan**：无 TBD/TODO，所有代码步骤含实际代码。✅

**3. Type consistency**：`_refine_by_quote` 返回 `(pool, refine_status, quote_by_code)` 在 Task 2 定义、Task 4 消费，一致；`quote` 字段结构（liquidity_depth/bid_ask_ratio/inner_outer_ratio/liquidity_pct/in_session）在 Task 2/4 一致；`refine_status` 取值集合在 Task 4 定义、Task 5 验证，一致。✅

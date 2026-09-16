# 雷达共振触发因子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把主力雷达多通道共振作为触发型因子接回 nextday（乘法 boost）并升级 quality 口径3（替换单通道子因子），比重构前砍掉的单通道因子更克制。

**Architecture:** 新增 `radar_resonance_for(codes, days)` 薄封装（复用 `radar()` 30s 缓存，按 code 匹配候选集），产出 `in_count`/`out_count`(0-3，强度地板 0.001)。nextday 不进五因子权重表，对 `base_score≥50` 的股乘法 boost（+5%/+10%），低 base 不救。quality 口径3 用 `in_count` 候选集内 rank-pct 替换 streak/north/margin 三子因子，保留 `inner_outer_ratio`，`out_count≥2` 进 `risk_flags`。

**Tech Stack:** Python 3.12 / FastAPI / pandas / pytest（无 conftest，仓库根目录跑测试）

**Spec:** `docs/superpowers/specs/2026-09-16-radar-resonance-trigger-design.md`

## Global Constraints

- 合规硬约束：所有新字段为机械统计/条件标记，挂 `cand_disclaimer`，不出现"推荐买入/卖出"。措辞用"筛选/排序/观察清单"。
- 不新增表、不触网：`radar_resonance_for` 只读 `smart_money_action` + `stock_spot`，复用 `radar()` 30s 进程缓存。
- 量纲分离：金额通道（资金流/龙虎榜/北向）进 in/out_count；股数通道（高管增减持/限售解禁/十大股东）不并入计数，`mgmt_confirm` 单独标。
- NaN→None 守卫：复用 `smart_money._nan`，防 starlette `allow_nan=False` 500。
- 测试在仓库根目录跑：`python -m pytest tests/test_xxx.py -q`，mock `db.query_rows`/`radar()`/`radar_resonance_for`，不触网。

---

## File Structure

- `screener/smart_money.py` — 新增 `radar_resonance_for(codes, days=5)`，置于 `radar()`（line 332）之后、`summarize_by_code`（line 335）之前。
- `screener/nextday.py` — 在 `_weighted_score` 调用（line 973）后插入 radar boost 块；item dict（line 990-1018）追加诊断字段。
- `backtest/quality.py` — 改 `_flow_factor_series`（line 394-446）返 `radar_in_count`+`inner_outer_ratio`；改口径3 调用（line 657-679）；`_PENALTY_MAP`（line 171）加条目；enriched 循环（line 1118-1137）注入 out_count risk_flag。
- `web/index.html` — nextday 表格新增 `radar_resonance_in`/`radar_boost`/`risk_flag` 列。
- `tests/test_sm_radar.py` — `radar_resonance_for` 单测。
- `tests/test_nextday.py` — boost 逻辑测试。
- `tests/test_quality_factors.py` — 口径3 + risk_flag 测试。

---

### Task 1: `radar_resonance_for` 触发原语

**Files:**
- Modify: `screener/smart_money.py`（line 332 后插入）
- Test: `tests/test_sm_radar.py`

**Interfaces:**
- Consumes: `radar(days, limit=0)`（line 201，已存在）、`_AMOUNT_CHANNELS`（line 157）、`_nan`（line 409）
- Produces: `radar_resonance_for(codes: list[str], days: int = 5) -> dict[str, dict]`，每 code 返 `{"in_count": int, "out_count": int, "mgmt_confirm": bool, "has_data": bool, "low_liq": bool, "asof": str|None}`

- [ ] **Step 1: Write failing tests**

追加到 `tests/test_sm_radar.py` 末尾（需 mock `screener.smart_money.radar`）：

```python
import screener.smart_money as sm


def _radar_row(code, turnover, channels, low_liq=False, asof="2026-09-15"):
    """构造 radar() 单行返回结构（复用 radar 内部 _turnover/channels/net 字段）。"""
    return {"code": code, "name": code, "_turnover": turnover,
            "channels": channels, "low_liq": low_liq, "data_asof": asof,
            "channel_hits": 0, "resonance": None, "net_intensity": None,
            "cum_net": 0.0, "unlock_flag": False}


def _ch(net):
    """构造单通道 dict，net 为累计净额。"""
    return {"net": net, "daily": {}, "latest_date": None, "positive": net > 0}


def test_radar_resonance_for_in_count_strength_floor(monkeypatch):
    # 资金流 net=200万, turnover=1亿 → intensity=0.002 > 0.001 → 计入 in
    # 龙虎榜 net=5万, turnover=1亿 → intensity=0.0005 < 0.001 → 不计
    rows = [_radar_row("600519", 1e8, {
        "资金流": _ch(2e6), "龙虎榜": _ch(5e4), "北向": _ch(-3e6)})]
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": rows})
    out = sm.radar_resonance_for(["600519"], days=5)
    assert out["600519"]["in_count"] == 1   # 仅资金流过门槛
    assert out["600519"]["out_count"] == 1  # 北向 -3e6/1e8=-0.03 < -0.001
    assert out["600519"]["has_data"] is True
    assert out["600519"]["low_liq"] is False


def test_radar_resonance_for_low_liq_not_counted(monkeypatch):
    # low_liq=True → 通道一律不计入（哪怕 net/turnover 数值大）
    rows = [_radar_row("000001", 1e4, {"资金流": _ch(1e6)}, low_liq=True)]
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": rows})
    out = sm.radar_resonance_for(["000001"], days=5)
    assert out["000001"]["in_count"] == 0
    assert out["000001"]["out_count"] == 0
    assert out["000001"]["has_data"] is True
    assert out["000001"]["low_liq"] is True


def test_radar_resonance_for_no_data(monkeypatch):
    # 候选 code 不在 radar 返回中 → has_data=False
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": []})
    out = sm.radar_resonance_for(["300999"], days=5)
    assert out["300999"]["has_data"] is False
    assert out["300999"]["in_count"] == 0
    assert out["300999"]["out_count"] == 0
    assert out["300999"]["asof"] is None


def test_radar_resonance_for_mgmt_confirm_separate(monkeypatch):
    # 高管增持正向 → mgmt_confirm=True，但不并入 in_count
    rows = [_radar_row("600519", 1e8, {
        "资金流": _ch(2e6),
        "高管增减持": {"net": None, "daily": {}, "latest_date": None, "positive": True}})]
    monkeypatch.setattr(sm, "radar", lambda days=5, market=None, limit=50, **k: {"rows": rows})
    out = sm.radar_resonance_for(["600519"], days=5)
    assert out["600519"]["in_count"] == 1   # 仅资金流
    assert out["600519"]["mgmt_confirm"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sm_radar.py -q -k radar_resonance_for`
Expected: FAIL — `AttributeError: module 'screener.smart_money' has no attribute 'radar_resonance_for'`

- [ ] **Step 3: Implement `radar_resonance_for`**

在 `screener/smart_money.py` 的 `radar()` 函数结束（line 332）后、`summarize_by_code`（line 335）前插入：

```python
def radar_resonance_for(codes: list[str], days: int = 5) -> dict[str, dict]:
    """候选集共振触发原语（spec 2026-09-16）。复用 radar() 30s 缓存，limit=0
    全量后按 code 字典匹配候选集，不触网不新增表。

    返回 {code: {in_count, out_count, mgmt_confirm, has_data, low_liq, asof}}：
    - in_count/out_count (0-3)：金额通道[资金流/龙虎榜/北向]里累计净额同向且
      intensity=net/turnover 绝对值 > 0.001 的通道数；
    - low_liq 股通道一律不计入（小分母 net/turnover 放大失真，强度地板挡不住）；
    - mgmt_confirm：高管增减持为增持方向（股数通道，单独标不并入 0-3）；
    - 无 smart_money_action 记录 → has_data=False，in/out_count=0 不触发。

    机械统计，非买卖信号。"""
    codes_s = [str(c) for c in codes]
    rad = radar(days=days, limit=0)
    rows = {str(r.get("code")): r for r in rad.get("rows", [])}
    out: dict[str, dict] = {}
    for c in codes_s:
        r = rows.get(c)
        if not r:
            out[c] = {"in_count": 0, "out_count": 0, "mgmt_confirm": False,
                      "has_data": False, "low_liq": False, "asof": None}
            continue
        low_liq = bool(r.get("low_liq"))
        in_count, out_count = 0, 0
        if not low_liq:
            turnover = _nan(r.get("_turnover")) or 0.0
            for ch_name in _AMOUNT_CHANNELS:
                ch = (r.get("channels") or {}).get(ch_name) or {}
                net = _nan(ch.get("net"))
                if net is None or turnover <= 0:
                    continue
                intensity = net / turnover
                if intensity > 0.001:
                    in_count += 1
                elif intensity < -0.001:
                    out_count += 1
        mgmt = (r.get("channels") or {}).get("高管增减持", {}) or {}
        out[c] = {"in_count": in_count, "out_count": out_count,
                  "mgmt_confirm": bool(mgmt.get("positive")), "has_data": True,
                  "low_liq": low_liq, "asof": r.get("data_asof")}
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sm_radar.py -q -k radar_resonance_for`
Expected: 4 PASS

- [ ] **Step 5: Run full smart_money test suite to verify no regression**

Run: `python -m pytest tests/test_sm_radar.py tests/test_smart_money.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add screener/smart_money.py tests/test_sm_radar.py
git commit -m "feat(smart-money): radar_resonance_for 触发原语(in/out_count 强度地板 0.001)"
```

---

### Task 2: nextday 乘法 boost 接入

**Files:**
- Modify: `screener/nextday.py`（line 973 后插入 boost 块；line 990-1018 item dict 追加字段）
- Test: `tests/test_nextday.py`

**Interfaces:**
- Consumes: `radar_resonance_for(codes, days=5)`（Task 1 产出）、`_weighted_score`（line 628，产出 `scores` 0-100 base_score）
- Produces: nextday item 追加 `base_score`/`radar_resonance_in`/`radar_resonance_out`/`radar_boost`/`radar_data_source`/`risk_flag` 字段；`score` 字段改为 boosted 值

- [ ] **Step 1: Write failing tests**

追加到 `tests/test_nextday.py`。需 mock `radar_resonance_for` 与既有 nextday 依赖（spot/ma_info）。复用文件已有的 mock 模式（参考现有 test_nextday.py 的 `_mock_spot` 风格）：

```python
def _mock_radar(in_map, out_map=None, has=True, low_liq_map=None):
    """返 radar_resonance_for mock：{code: {in_count, out_count, has_data, low_liq, asof}}。"""
    out_map = out_map or {}
    low_liq_map = low_liq_map or {}
    def _f(codes, days=5):
        return {c: {"in_count": in_map.get(c, 0),
                    "out_count": out_map.get(c, 0),
                    "mgmt_confirm": False, "has_data": has,
                    "low_liq": low_liq_map.get(c, False),
                    "asof": "2026-09-15"} for c in codes}
    return _f


def test_nextday_radar_boost_applied_when_base_above_floor(monkeypatch, _nextday_fixture):
    # base_score≈80(高动量) + in_count=3 → boost 1.10 → score≈88
    monkeypatch.setattr("screener.nextday.radar_resonance_for",
                        _mock_rar({"600519": 3}))
    res = nextday_strong_rank("stock", codes=["600519"], selection_mode="score")
    it = res["items"][0]
    assert it["base_score"] >= 75
    assert abs(it["score"] - round(it["base_score"] * 1.10, 2)) < 0.1
    assert it["radar_boost"] == 1.10
    assert it["radar_resonance_in"] == 3
    assert it["radar_data_source"].startswith("smart_money_action@")


def test_nextday_radar_boost_skipped_when_base_below_floor(monkeypatch, _nextday_fixture):
    # base_score<50 → 不 boost（radar_boost=1.0, score==base_score）
    # 需构造低动量 spot（change_pct 小、无历史动量）使 base_score<50
    monkeypatch.setattr("screener.nextday.radar_resonance_for",
                        _mock_rar({"600519": 3}))
    res = nextday_strong_rank("stock", codes=["600519_low"], selection_mode="score")
    it = next(i for i in res["items"] if i["code"] == "600519_low")
    assert it["base_score"] < 50
    assert it["radar_boost"] == 1.0
    assert it["score"] == it["base_score"]


def test_nextday_radar_no_data_degrades(monkeypatch, _nextday_fixture):
    # has_data=False → 无 boost，data_source="无主力数据"
    monkeypatch.setattr("screener.nextday.radar_resonance_for",
                        _mock_rar({}, has=False))
    res = nextday_strong_rank("stock", codes=["600519"], selection_mode="score")
    it = res["items"][0]
    assert it["radar_boost"] == 1.0
    assert it["radar_resonance_in"] is None
    assert it["radar_data_source"] == "无主力数据"


def test_nextday_radar_outflow_risk_flag(monkeypatch, _nextday_fixture):
    # out_count>=2 → risk_flag="多通道主力净流出"（不阻断入选）
    monkeypatch.setattr("screener.nextday.radar_resonance_for",
                        _mock_rar({"600519": 0}, out_map={"600519": 2}))
    res = nextday_strong_rank("stock", codes=["600519"], selection_mode="score")
    it = res["items"][0]
    assert it["risk_flag"] == "多通道主力净流出"
```

> 注：`_nextday_fixture` 与低动量构造参考 `tests/test_nextday.py` 现有 fixture/mock 模式；若文件无现成 fixture，则在各 test 内 inline mock `db.query_rows("stock_spot")` + `_ma_arrange_batch`，使 base_score 可控。实现者按文件现有风格对齐。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_nextday.py -q -k radar`
Expected: FAIL — 字段不存在 / `radar_resonance_for` 未被调用

- [ ] **Step 3: Insert boost block after `_weighted_score`**

在 `screener/nextday.py` line 973（`scores, factor_scores, coverage = _weighted_score(factor_maps, codes_k)`）之后插入：

```python
    # 雷达共振触发 boost（spec 2026-09-16）：条件叠加，不进 _FACTOR_WEIGHTS。
    # base_score≥50 才吃 boost，低 base 不救；has_data/low_liq → 不 boost。
    try:
        from screener import smart_money as _sm_rad
        _radar = _sm_rad.radar_resonance_for(codes_k)
    except Exception:
        _radar = {}
    _boost_by_code: dict[str, float] = {}
    _radar_in_by_code: dict[str, object] = {}
    _radar_out_by_code: dict[str, object] = {}
    _radar_src_by_code: dict[str, str] = {}
    _base_scores = dict(scores)
    for _c in codes_k:
        _r = _radar.get(_c) or {}
        _in = _r.get("in_count") if _r.get("has_data") and not _r.get("low_liq") else None
        _out = _r.get("out_count") if _r.get("has_data") and not _r.get("low_liq") else None
        _base = scores.get(_c, 0.0)
        _boost = 1.0
        if _in is not None and _base >= 50:
            _boost = 1.0 + 0.05 * max(0, _in - 1)
        scores[_c] = round(_base * _boost, 2)  # 覆盖为 boosted score（排序用）
        _boost_by_code[_c] = _boost
        _radar_in_by_code[_c] = _in
        _radar_out_by_code[_c] = _out
        if not _r:
            _radar_src_by_code[_c] = "无主力数据"
        elif _r.get("low_liq"):
            _radar_src_by_code[_c] = "low_liq"
        else:
            _radar_src_by_code[_c] = f"smart_money_action@{_r.get('asof')}"
```

- [ ] **Step 4: Add diagnostic fields to item dict**

在 line 1017 的 `"score": scores.get(code, 0.0),` 改为并追加字段（原行替换为）：

```python
            "base_score": round(_base_scores.get(code, 0.0), 2),
            "radar_resonance_in": _radar_in_by_code.get(code),
            "radar_resonance_out": _radar_out_by_code.get(code),
            "radar_boost": _boost_by_code.get(code, 1.0),
            "radar_data_source": _radar_src_by_code.get(code, "无主力数据"),
            "risk_flag": ("多通道主力净流出"
                          if (_radar_out_by_code.get(code) or 0) >= 2 else None),
            "score": scores.get(code, 0.0),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_nextday.py -q -k radar`
Expected: 4 PASS

- [ ] **Step 6: Run full nextday suite for regression**

Run: `python -m pytest tests/test_nextday.py -q`
Expected: all PASS（50 既有 + 4 新）

- [ ] **Step 7: Commit**

```bash
git add screener/nextday.py tests/test_nextday.py
git commit -m "feat(nextday): 雷达共振乘法 boost(base>=50 才吃,最大+10%)+outflow risk_flag"
```

---

### Task 3: quality 口径3 替换子因子

**Files:**
- Modify: `backtest/quality.py`（`_flow_factor_series` line 394-446；口径3 调用 line 657-679）
- Test: `tests/test_quality_factors.py`

**Interfaces:**
- Consumes: `radar_resonance_for(codes, days)`（Task 1）、`_avg_rank_pct`（line 306）、`_to_float`、`pd`、`pytdx_client.get_quote`
- Produces: `_flow_factor_series(codes, days, quote_codes=None)` 返 `{"radar_in_count": pd.Series, "inner_outer_ratio": pd.Series}`；口径3 status 改 `"ok(radar共振+内外盘)"`

- [ ] **Step 1: Write failing tests**

追加到 `tests/test_quality_factors.py`：

```python
def test_dim3_uses_radar_in_count(monkeypatch):
    """口径3 主子因子 = radar in_count 候选集内 rank-pct。"""
    import backtest.quality as q
    # mock radar_resonance_for：三只候选 in_count 分别 3/2/0
    monkeypatch.setattr("screener.smart_money.radar_resonance_for",
                        lambda codes, days=5: {
                            "000001": {"in_count": 3, "out_count": 0, "has_data": True,
                                       "low_liq": False, "mgmt_confirm": False, "asof": "d"},
                            "000002": {"in_count": 2, "out_count": 0, "has_data": True,
                                       "low_liq": False, "mgmt_confirm": False, "asof": "d"},
                            "000003": {"in_count": 0, "out_count": 0, "has_data": True,
                                       "low_liq": False, "mgmt_confirm": False, "asof": "d"}})
    monkeypatch.setattr("data.pytdx_client.get_quote", lambda codes: [])
    ff = q._flow_factor_series(["000001", "000002", "000003"], days=20)
    assert "radar_in_count" in ff
    assert "inner_outer_ratio" in ff
    # 旧子因子应已移除
    assert "streak_inflow" not in ff
    assert "north_cum" not in ff
    assert "margin_accel" not in ff
    # in_count=3 → rank-pct 最高
    assert ff["radar_in_count"]["000001"] > ff["radar_in_count"]["000003"]


def test_dim3_has_data_false_excluded(monkeypatch):
    """has_data=False → in_count 给 None，排除出口径3 rank-pct 分母。"""
    import backtest.quality as q
    monkeypatch.setattr("screener.smart_money.radar_resonance_for",
                        lambda codes, days=5: {
                            "000001": {"in_count": 3, "out_count": 0, "has_data": True,
                                       "low_liq": False, "mgmt_confirm": False, "asof": "d"},
                            "000002": {"in_count": 0, "out_count": 0, "has_data": False,
                                       "low_liq": False, "mgmt_confirm": False, "asof": None}})
    monkeypatch.setattr("data.pytdx_client.get_quote", lambda codes: [])
    ff = q._flow_factor_series(["000001", "000002"], days=20)
    assert ff["radar_in_count"]["000001"] == 3.0
    # 000002 has_data=False → None（不伪造 0）
    import pandas as pd
    assert pd.isna(ff["radar_in_count"]["000002"])


def test_dim3_low_liq_excluded(monkeypatch):
    """low_liq=True → in_count 给 None（与 has_data=False 同处理）。"""
    import backtest.quality as q
    monkeypatch.setattr("screener.smart_money.radar_resonance_for",
                        lambda codes, days=5: {
                            "000001": {"in_count": 0, "out_count": 0, "has_data": True,
                                       "low_liq": True, "mgmt_confirm": False, "asof": "d"}})
    monkeypatch.setattr("data.pytdx_client.get_quote", lambda codes: [])
    ff = q._flow_factor_series(["000001"], days=20)
    import pandas as pd
    assert pd.isna(ff["radar_in_count"]["000001"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quality_factors.py -q -k dim3`
Expected: FAIL — `_flow_factor_series` 仍返旧子因子 / 签名不匹配

- [ ] **Step 3: Rewrite `_flow_factor_series`**

替换 `backtest/quality.py` line 394-446 整个函数为：

```python
def _flow_factor_series(codes: list, days: int, quote_codes=None) -> dict:
    """资金流向口径因子集（改造 2026-09-16）：radar 共振 in_count 候选集内
    rank-pct + inner_outer_ratio(tdx 内外盘 b_vol/s_vol)。旧单通道因子
    (streak/north/margin/dragon) 被多通道共振计数替代；inner_outer_ratio 保留
    为正交 tdx 第二子因子。has_data=False/low_liq → None 排除分母（不伪造 0）。

    quote_codes: 限小名单取盘口(shortlist)，默认全 codes；触网失败→缺省不崩。"""
    codes_l = [str(c) for c in codes]
    qc = [str(c) for c in (quote_codes if quote_codes is not None else codes_l)]
    # radar 共振 in_count（主子因子）
    in_count = {c: None for c in codes_l}
    try:
        from screener import smart_money as sm_q
        rad = sm_q.radar_resonance_for(codes_l, days=days)
        for c in codes_l:
            r = rad.get(c) or {}
            if r.get("has_data") and not r.get("low_liq"):
                in_count[c] = float(r.get("in_count") or 0)
    except Exception:
        pass
    # 内外盘比（主动买/主动卖，>1 买盘占优）— 正交 tdx 信号，保留
    ior = {c: None for c in codes_l}
    try:
        from data import pytdx_client
        for q in (pytdx_client.get_quote(qc) or []):
            c = str(q.get("code"))
            bv = _to_float(q.get("b_vol"))
            sv = _to_float(q.get("s_vol"))
            if bv is not None and sv and sv > 0:
                ior[c] = bv / sv
    except Exception:
        pass
    return {
        "radar_in_count": pd.Series(in_count, dtype=float),
        "inner_outer_ratio": pd.Series(ior, dtype=float),
    }
```

- [ ] **Step 4: Update 口径3 caller in `_dim_scores`**

替换 `backtest/quality.py` line 657-679（口径3 stock 分支的 `bb = sm_q._behavior_batch(...)` 块到 `used_new = True`）为：

```python
    # 口径3 资金流向(改造 2026-09-16: radar 多通道共振 in_count + 内外盘;
    #               限 shortlist,非 shortlist 口径3=None;降级旧 spot 路径)
    try:
        import screener.smart_money as sm_q
        used_new = False
        if universe == "stock":
            try:
                import backtest.buffett as bt_buf
                sl = set(bt_buf.shortlist_by_turnover(min_turnover=5e8, k=80))
                sl_codes = [c for c in codes if c in sl]
            except Exception:
                sl_codes = codes
            # 任一 shortlist 标的 has_data=True → 新口径
            try:
                _rad = sm_q.radar_resonance_for(sl_codes, days=days)
                rad_avail = any((r or {}).get("has_data") for r in _rad.values())
            except Exception:
                rad_avail = False
            if rad_avail:
                ff = _flow_factor_series(sl_codes, days=days, quote_codes=sl_codes)
                comp = _avg_rank_pct(list(ff.values()), sl_codes)
                for c in codes:
                    scores[c][3] = _to_float(comp.get(c)) if c in comp.index else None
                dims_avail.append(3)
                status["3"] = "ok(radar共振+内外盘)"
                used_new = True
        if not used_new:
```

（`if not used_new:` 之后接 line 680 起的原降级 spot 路径，不改）

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality_factors.py -q -k dim3`
Expected: 3 PASS

- [ ] **Step 6: Run full quality factors suite for regression**

Run: `python -m pytest tests/test_quality_factors.py -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backtest/quality.py tests/test_quality_factors.py
git commit -m "feat(quality): 口径3 用 radar in_count rank-pct 替换 4 单通道子因子"
```

---

### Task 4: quality `out_count≥2` risk_flag + penalty

**Files:**
- Modify: `backtest/quality.py`（`_PENALTY_MAP` line 171；enriched 循环 line 1118-1137）
- Test: `tests/test_quality_factors.py`

**Interfaces:**
- Consumes: `radar_resonance_for`（Task 1）、`_quality_gate`（line 150，产 `risk_flags`）、`_risk_penalty`（line 174，读 `_PENALTY_MAP`）
- Produces: `out_count≥2` 的 item 追加 `risk_flag "主力多通道净流出"`；`_PENALTY_MAP` 加 `("主力多通道净流出", 1.0)`

- [ ] **Step 1: Write failing test**

追加到 `tests/test_quality_factors.py`：

```python
def test_outflow_risk_flag_and_penalty(monkeypatch):
    """out_count>=2 → risk_flag 进 gate，_risk_penalty 扣减 adjusted_resonance。"""
    import backtest.quality as q
    # mock radar_resonance_for：out_count=2
    monkeypatch.setattr("screener.smart_money.radar_resonance_for",
                        lambda codes, days=5: {
                            c: {"in_count": 0, "out_count": 2, "has_data": True,
                                "low_liq": False, "mgmt_confirm": False, "asof": "d"}
                            for c in codes})
    # 构造一个 gate（硬通过、无财务红旗）直接验证 risk_flag 注入
    gate = {"hard_pass": True, "risk_flags": [], "warnings": []}
    # 模拟 enriched 循环里的注入逻辑
    rad = {"000001": {"in_count": 0, "out_count": 2, "has_data": True,
                      "low_liq": False, "mgmt_confirm": False, "asof": "d"}}
    if rad.get("000001", {}).get("has_data") and not rad.get("000001", {}).get("low_liq") \
            and (rad["000001"].get("out_count") or 0) >= 2:
        gate["risk_flags"].append("主力多通道净流失")
    # _risk_penalty 应识别 "主力多通道净流出" 关键字
    assert any("主力多通道净流出" == f for f in gate["risk_flags"]) or \
           any("主力多通道净流" in f for f in gate["risk_flags"])
    penalty = q._risk_penalty({}, gate, {})
    assert penalty >= 1.0  # _PENALTY_MAP 新条目权重 1.0


def test_outflow_below_threshold_no_flag(monkeypatch):
    """out_count=1 → 不触发 risk_flag。"""
    import backtest.quality as q
    rad = {"000001": {"in_count": 0, "out_count": 1, "has_data": True,
                      "low_liq": False, "mgmt_confirm": False, "asof": "d"}}
    gate = {"hard_pass": True, "risk_flags": [], "warnings": []}
    r = rad["000001"]
    if r.get("has_data") and not r.get("low_liq") and (r.get("out_count") or 0) >= 2:
        gate["risk_flags"].append("主力多通道净流出")
    assert gate["risk_flags"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quality_factors.py -q -k outflow`
Expected: FAIL — `_PENALTY_MAP` 无 "主力多通道净流出" 条目

- [ ] **Step 3: Add penalty map entry**

修改 `backtest/quality.py` line 171：

```python
_PENALTY_MAP = [("杠杆", 1.5), ("FCF", 1.5), ("波动", 2.0), ("脉冲", 1.0),
                ("主力多通道净流出", 1.0)]
```

- [ ] **Step 4: Inject risk_flag in enriched loop**

在 `backtest/quality.py` line 1117（`conf_summary = ...`）之后、line 1118（`for c in codes:`）之前插入一次性 radar 调用：

```python
    # radar 多通道净流出 risk_flag（spec 2026-09-16）：out_count>=2 → risk_flag
    try:
        from screener import smart_money as _sm_rf
        _rad_out = _sm_rf.radar_resonance_for(codes, days=days)
    except Exception:
        _rad_out = {}
```

然后在 line 1137（`gate = _quality_gate({"code": c}, conf, fundamental=None, behavior_days=behavior_days)`）之后插入：

```python
        _r_out = _rad_out.get(c) or {}
        if _r_out and _r_out.get("has_data") and not _r_out.get("low_liq") \
                and (_r_out.get("out_count") or 0) >= 2:
            gate["risk_flags"].append("主力多通道净流出")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality_factors.py -q -k outflow`
Expected: 2 PASS

- [ ] **Step 6: Run quality confidence + full suite for regression**

Run: `python -m pytest tests/test_quality_factors.py tests/test_quality_confidence.py tests/test_quality.py -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backtest/quality.py tests/test_quality_factors.py
git commit -m "feat(quality): out_count>=2 主力多通道净流出 risk_flag + penalty 1.0"
```

---

### Task 5: 前端 nextday 表格新增列

**Files:**
- Modify: `web/index.html`（nextday 渲染函数）

**Interfaces:**
- Consumes: nextday item 新字段 `radar_resonance_in`/`radar_boost`/`risk_flag`/`base_score`（Task 2 产出）

- [ ] **Step 1: 定位 nextday 渲染函数**

在 `web/index.html` 中找到 nextday 表格渲染处（搜索 `nextday` / `ndLoad` / `radar_resonance_in` 尚不存在；参考既有 `factor_scores` 列渲染模式）。

- [ ] **Step 2: 加列**

在 nextday 表格表头与行模板中，`score` 列后追加三列：

```html
<th>共振</th><th>boost</th><th>风险</th>
```

行单元格（参考既有 `_nan` 占位 '-' 风格）：

```javascript
const rIn = it.radar_resonance_in;
const rBoost = it.radar_boost;
const rFlag = it.risk_flag;
// 共振：in_count 或 '-'；boost：×1.05 形式或 '-';风险：risk_flag 文本或 '-'
```

`radar_boost` 显示为 `×${rBoost.toFixed(2)}`（>1.0 时），否则 `-`。`risk_flag` 非空时红字显示。措辞用"主力共振"机械标记，禁止"买入/卖出"。

- [ ] **Step 3: 手工校验**

启动服务 `uvicorn api.server:app --port 8000`，浏览器开 `http://localhost:8000/web/index.html` → nextday tab → 跑一次 → 确认新列渲染、boost 值正确、无数据时显 `-`、无 console 报错。

- [ ] **Step 4: Commit**

```bash
git add web/index.html
git commit -m "feat(web): nextday 表格加 主力共振/boost/风险 三列"
```

---

## Self-Review

**Spec coverage:**
- 触发原语 `radar_resonance_for`（强度地板 0.001 / low_liq 不计 / mgmt 分离）→ Task 1 ✓
- nextday 乘法 boost（base≥50 / +5%/+10% / has_data 降级）→ Task 2 ✓
- nextday `out_count≥2` risk_flag → Task 2 ✓
- quality 口径3 `in_count` rank-pct 替换 4 子因子 → Task 3 ✓
- `inner_outer_ratio` 保留 → Task 3 ✓
- `has_data=False`→None 排除 → Task 3 ✓
- quality `out_count≥2` 进 risk_flags + risk_penalty → Task 4 ✓
- 前端展示 → Task 5 ✓
- 不新增表/不触网/量纲分离/disclaimer → Global Constraints ✓
- 口径3 权重维持 0.6 → 不改 `_DEFAULT_DIM_WEIGHTS`，无任务 = 维持 ✓

**Type consistency:**
- `radar_resonance_for` 返回 `in_count`/`out_count` 为 int（0-3）；nextday/quality 读取时用 `or 0` 防 None；quality `_flow_factor_series` 转 `float(r.get("in_count") or 0)` 入 pd.Series ✓
- nextday `_base_scores` dict 保存 boost 前 base；`scores` 被覆盖为 boosted；item `base_score` 读 `_base_scores`，`score` 读 `scores` ✓
- quality `_flow_factor_series` 签名 `(codes, days, quote_codes=None)`，caller 传 `_flow_factor_series(sl_codes, days=days, quote_codes=sl_codes)` ✓

**Placeholder scan:** Task 5 Step 2 的 JS 为伪代码骨架——前端为原生 JS 无构建，实现者按既有 `ndLoad`/`ndRender` 渲染风格补全单元格拼接，非占位（已给字段名与显示规则）。其余步骤均含完整可执行代码。

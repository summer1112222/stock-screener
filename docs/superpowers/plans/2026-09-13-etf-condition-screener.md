# ETF/QDII 条件筛选器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 etfscreen tab 升级为完全通用条件筛选器，新增 csindex 估值分位接线，复用现有长/短清单模式。

**Architecture:** 在 `screener/etf_screen.py` 内新增 `etf_screen_filter(conditions, sort, asc, limit, universe, days, codes)`，复用现有 `_fetch_*`（规模/溢价/估值）+ 量价因子 + `_CACHE` 缓存 + `_display_name`/`_to_record`/`_nan` 数据诚实抽象；复用 `engine._apply_conditions`/`_sort_df` 做 AND 过滤与 None-末排序。接线 `_fetch_index_valuation`（csindex 按 code→指数映射 + 名称兜底 + 按指数去重）。前端 etfscreen tab 加"条件筛选"模式。

**Tech Stack:** Python 3.12, FastAPI, pandas, akshare（容器采集层）, 原生 JS 前端（无构建）。

**Spec:** `docs/superpowers/specs/2026-09-13-etf-condition-screener-design.md`

## Global Constraints

- 本项目是**数据筛选/回测研究工具，非投资咨询**：不荐股、不出实时买卖点、不承诺收益、不自动下单。
- `etf_spot` 表**不新增列**——规模/溢价/估值分位经 `_fetch_*` 抽象按需取，绝不读不存在的 `nav`/`fund_scale` 列（实测 `etf_spot` 仅 code/name/latest_price/change_pct/turnover_amount/turnover_rate）。
- 每个 `_fetch_*` 失败/无数据 → 诚实 `None`（该维度中性/不判门槛），不崩不伪造。`_nan`/`_to_record` 防 starlette `allow_nan=False` 500。
- 措辞全程"条件机械筛选观察清单/机械排序/非荐股非买卖信号"，挂 `cand_disclaimer`。
- 条件参数格式复用 `/api/screen`：`[{"field": key, "op": gt/gte/lt/lte/eq/ne/between/topn/topn_asc, "value": x}, …]`，AND 过滤。
- `_CACHE` 30s 进程缓存；键必须含全部参数（含 conditions/sort/asc/codes）。
- 测试全 mock 不触网；单测放 `tests/test_etf_screen.py`。
- 改动时同步 `CLAUDE.md`（架构/路由/改动检查清单）。

---

### Task 1: csindex 指数估值分位接线

**Files:**
- Modify: `screener/etf_screen.py`（`_fetch_index_valuation` 实现替换 + 新增 `_INDEX_KEYWORDS`/`_INDEX_MAP`/`_index_code`/`_INDEX_VAL_CACHE`）
- Test: `tests/test_etf_screen.py`（新增测试）

**Interfaces:**
- Produces: `_index_code(code: str, name: str|None) -> str|None`（ETF code/名称 → 指数 csindex code）；`_fetch_index_valuation(code) -> dict|None`，返回 `{'pe_pct': float 0..1, 'div_yield': float|None}`；模块级 `_INDEX_MAP: dict[str,str]`（ETF code→指数 code，手动精确映射）。
- Consumes: `_etf_spot_em_df()`（取名称）、`valuation_percentile`、`_to_f`/`_nan`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_etf_screen.py` 末尾追加：

```python
def test_index_code_map_and_fuzzy():
    # 手动映射命中
    assert es._index_code("510300", "华泰柏瑞沪深300ETF") == "000300"
    # 名称模糊兜底命中(未在映射表)
    assert es._index_code("99xxxx", "易方达中证500ETF") == "000905"
    # 无映射无兜底 → None
    assert es._index_code("999999", "某LOF基金") is None

def test_fetch_index_valuation_from_csindex(monkeypatch):
    import pandas as pd
    es._INDEX_VAL_CACHE = {"ts": 0.0, "data": {}}
    # 名称查全市场快照
    snap = pd.DataFrame({"代码": ["510300"], "名称": ["华泰柏瑞沪深300ETF"]})
    monkeypatch.setattr(es, "_etf_spot_em_df", lambda: snap)
    # csindex 历史分位(降序: 新→旧; 含 PE 列)
    hist = pd.DataFrame({
        "日期": ["2026-09-10", "2026-09-09", "2026-09-08"],
        "市盈率": [13.0, 12.0, 11.0],
    })
    monkeypatch.setattr(es._ak_mod, "stock_zh_index_value_csindex",
                        lambda symbol: hist if symbol == "000300" else pd.DataFrame())
    v = es._fetch_index_valuation("510300")
    assert v is not None and "pe_pct" in v
    # 当前 PE=13 是历史最高 → 分位近 1(close-interval: 3/3=1.0)
    assert abs(v["pe_pct"] - 1.0) < 1e-9

def test_fetch_index_valuation_no_map_returns_none(monkeypatch):
    es._INDEX_VAL_CACHE = {"ts": 0.0, "data": {}}
    monkeypatch.setattr(es, "_etf_spot_em_df",
                        lambda: pd.DataFrame({"代码": ["999999"], "名称": ["某LOF"]}))
    assert es._fetch_index_valuation("999999") is None
```

> 注意：csindex 历史 `市盈率` 列名实测可能不同，用 `_pick_first` 风格列候选（`市盈率`/`PE`/`市盈率(倍)`）；测试里 mock 返回 `市盈率` 列。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_etf_screen.py::test_index_code_map_and_fuzzy tests/test_etf_screen.py::test_fetch_index_valuation_from_csindex tests/test_etf_screen.py::test_fetch_index_valuation_no_map_returns_none -v`
Expected: FAIL（`AttributeError: module has no attribute '_index_code'` / `_INDEX_VAL_CACHE`）

- [ ] **Step 3: 写实现**

在 `screener/etf_screen.py` 的 `_fetch_index_valuation` 上方插入：

```python
# csindex 指数估值分位接线：ETF → 跟踪指数 csindex code。
# 层1 手动精确映射(主流宽基/行业/QDII)，层2 名称关键词兜底，层3 无 → None(诚实)。
_INDEX_MAP = {
    "510300": "000300", "159919": "000300", "510310": "000300", "510330": "000300",  # 沪深300
    "510050": "000016",                                                              # 上证50
    "510500": "000905", "159922": "000905",                                          # 中证500
    "512100": "000852",                                                              # 中证1000
    "510880": "000015",                                                              # 红利
    "159915": "399006",                                                              # 创业板指
    "588000": "000688",                                                              # 科创50
}
_INDEX_KEYWORDS = {
    "沪深300": "000300", "上证50": "000016", "中证500": "000905", "中证1000": "000852",
    "中证A500": "000510", "红利": "000015", "创业板": "399006", "科创50": "000688",
    "科创100": "000698", "恒生": "HSI", "纳斯达克": "NDX", "标普": "SPX",
    "日经": "N225", "德国": "GDAXI",
}
# 按指数 code 去重缓存(同一指数多只 ETF 只拉一次)；300s TTL。
_INDEX_VAL_CACHE: dict = {"ts": 0.0, "data": {}}


def _index_code(code: str, name=None) -> str | None:
    """ETF code/名称 → 跟踪指数 csindex code；无映射/兜底 → None(诚实)。"""
    key = str(code)
    if key in _INDEX_MAP:
        return _INDEX_MAP[key]
    if name:
        for kw, idx in _INDEX_KEYWORDS.items():
            if kw in str(name):
                return idx
    return None


def _pe_col(df) -> str | None:
    """csindex 宽表 PE 列名候选。"""
    for c in ("市盈率", "PE", "市盈率(倍)", "市盈率TTM"):
        if c in df.columns:
            return c
    return None
```

替换 `_fetch_index_valuation` 为：

```python
def _fetch_index_valuation(code) -> dict | None:
    """{'pe_pct': 0..1, 'div_yield': float|None}；指数历史估值分位, 低=便宜。
    经 fund_etf_spot_em 名称 → _index_code 映射 csindex symbol, 按指数去重缓存。
    映射失败/源失败/无 PE 列 → None(该维度中性 0.5)。单测注入 mock。"""
    if not _AK_OK:
        return None
    try:
        snap = _etf_spot_em_df()
        name = None
        if snap is not None and not snap.empty:
            cc = next((c for c in ("代码", "code") if c in snap.columns), None)
            nc = next((c for c in ("名称", "基金简称", "name") if c in snap.columns), None)
            if cc and nc:
                m = snap[snap[cc].astype(str).str.zfill(6) == str(code).zfill(6)]
                if not m.empty:
                    name = m.iloc[0].get(nc)
        idx = _index_code(code, name)
        if not idx:
            return None
        now = time.time()
        if _INDEX_VAL_CACHE["ts"] and now - _INDEX_VAL_CACHE["ts"] < 300:
            if idx in _INDEX_VAL_CACHE["data"]:
                return _INDEX_VAL_CACHE["data"][idx]
        else:
            _INDEX_VAL_CACHE["ts"] = now
            _INDEX_VAL_CACHE["data"] = {}
        df = _ak_mod.stock_zh_index_value_csindex(symbol=idx)
        pe_col = _pe_col(df) if df is not None else None
        if df is None or pe_col is None or df.empty:
            return None
        pe_hist = [h for h in df[pe_col].tolist() if h is not None]
        if not pe_hist:
            return None
        cur = _to_f(pe_hist[-1]) if pe_hist else None
        vp = valuation_percentile(cur, pe_hist) if cur is not None else 0.5
        out = {"pe_pct": _nan(vp), "div_yield": None}
        _INDEX_VAL_CACHE["data"][idx] = out
        return out
    except Exception:
        return None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: PASS（新增 3 + 原有全绿）

- [ ] **Step 5: 提交**

```bash
git add screener/etf_screen.py tests/test_etf_screen.py
git commit -m "feat(etf-screen): csindex 指数估值分位接线(映射+名称兜底+按指数去重)"
```

---

### Task 2: 条件引擎 `etf_screen_filter`

**Files:**
- Modify: `screener/etf_screen.py`（新增 `ETF_FILTER_FIELDS` + `etf_screen_filter`；顶部加 `from . import engine as _engine`）
- Test: `tests/test_etf_screen.py`（新增）

**Interfaces:**
- Consumes: `_fetch_quality_meta`/`_fetch_qdii_premium`/`_fetch_index_valuation`（Task 1 已接线）、`short_scores`、`_load_history_panel`、`_display_name`、`_to_record`/`_nan`/`_to_f`、`_CACHE`、`_db.query_rows("etf_spot")`、`engine._apply_conditions`/`engine._sort_df`。
- Produces: `ETF_FILTER_FIELDS: list[dict]`（供 `/api/fields` 动态渲染）；`etf_screen_filter(conditions, sort=None, asc=False, limit=50, universe="ETF", days=365, codes=None) -> dict`，返回 `{"items":[{code,name,<字段>…}], "total", "skipped", "universe", "mode":"filter"}`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_etf_screen.py` 末尾追加：

```python
def _filter_harness(monkeypatch):
    import pandas as pd, numpy as np
    from backtest import eval as bt_eval
    es._CACHE.clear()
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"510300": np.linspace(4, 5, 30), "159915": np.linspace(2, 2.6, 30),
                       "513100": np.linspace(1, 1.1, 30)}, index=idx)
    monkeypatch.setattr(bt_eval, "load_panel",
        lambda u, c, s, e, f: up if f == "close" else pd.DataFrame(1e8, index=idx, columns=up.columns))
    monkeypatch.setattr(es, "_fetch_index_valuation",
        lambda code: {"pe_pct": 0.2, "div_yield": 2.0} if code == "510300" else {"pe_pct": 0.8, "div_yield": 1.0})
    monkeypatch.setattr(es, "_fetch_quality_meta",
        lambda code: {"fund_scale": 500.0 if code == "510300" else 30.0, "fee_bps": None, "tracking_err": None})
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: None if code == "513100" else 0.01)
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [
            {"code": "510300", "latest_price": 4.9, "turnover_rate": 1.2, "turnover_amount": 5e8, "change_pct": 1.5},
            {"code": "159915", "latest_price": 2.3, "turnover_rate": 0.4, "turnover_amount": 1e8, "change_pct": 2.0},
            {"code": "513100", "latest_price": 1.1, "turnover_rate": 0.1, "turnover_amount": 5e7, "change_pct": -0.5},
        ] if table == "etf_spot" else [])
    return up


def test_etf_screen_filter_and_conditions(monkeypatch):
    _filter_harness(monkeypatch)
    r = es.etf_screen_filter(
        [{"field": "fund_scale", "op": "gte", "value": 50.0},
         {"field": "change_pct", "op": "gt", "value": 1.0}])
    codes = [i["code"] for i in r["items"]]
    assert "510300" in codes and "159915" not in codes and "513100" not in codes
    assert r["total"] == 3


def test_etf_screen_filter_topn_and_sort_none_last(monkeypatch):
    _filter_harness(monkeypatch)
    # topn 按 change_pct 降序取前 2
    r = es.etf_screen_filter([{"field": "change_pct", "op": "topn", "value": 2}])
    assert [i["code"] for i in r["items"]] == ["159915", "510300"]
    # 排序: fund_scale asc, None 排最后 —— 这里全部有值, 验证升序
    r2 = es.etf_screen_filter([], sort="fund_scale", asc=True)
    assert r2["items"][0]["code"] == "159915"  # 30亿最小


def test_etf_screen_filter_missing_source_skips_condition(monkeypatch):
    # 某字段全部候选无数据(源失败) → 该条件跳过, 不误筛(所有 code 保留)
    import pandas as pd
    from backtest import eval as bt_eval
    es._CACHE.clear()
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: pd.DataFrame({}, index=[]))
    monkeypatch.setattr(es, "_fetch_index_valuation", lambda code: None)   # 估值源全缺
    monkeypatch.setattr(es, "_fetch_quality_meta", lambda code: {"fund_scale": 100.0})
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: None)
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code": "510300", "latest_price": 4.9}] if table == "etf_spot" else [])
    r = es.etf_screen_filter([{"field": "valuation_percentile", "op": "gt", "value": 0.5}])
    assert r["items"], "估值源缺失该条件应跳过, code 不被误筛"
    assert any("valuation_percentile" in s for s in r["skipped"])


def test_etf_screen_filter_cache(monkeypatch):
    from backtest import eval as bt_eval
    _filter_harness(monkeypatch)
    calls = {"n": 0}
    orig = es._fetch_quality_meta
    def spy(code):
        calls["n"] += 1
        return orig(code)
    monkeypatch.setattr(es, "_fetch_quality_meta", spy)
    es.etf_screen_filter([{"field": "fund_scale", "op": "gt", "value": 1.0}])
    first = calls["n"]
    es.etf_screen_filter([{"field": "fund_scale", "op": "gt", "value": 1.0}])  # 缓存命中
    assert calls["n"] == first
    es._CACHE.clear()
    es.etf_screen_filter([{"field": "fund_scale", "op": "gt", "value": 1.0}])
    assert calls["n"] > first
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: FAIL（`AttributeError: module has no attribute 'etf_screen_filter'` / `ETF_FILTER_FIELDS`）

- [ ] **Step 3: 写实现**

在 `etf_screen.py` 顶部 import 区加 `from . import engine as _engine`。在 `_rank_short` 上方（或文件尾部）插入：

```python
# 条件筛选器字段目录(供 /api/fields 动态渲染 + etf_screen_filter 语义)。
# 来源标注: spot=etf_spot 表 / em=fund_etf_spot_em 一次全市场 / csindex=估值接线 / hist=etf_daily 历史量价因子
ETF_FILTER_FIELDS = [
    {"key": "change_pct", "label": "涨跌幅(%)", "ops": ["gt", "gte", "lt", "lte", "eq", "ne", "between"]},
    {"key": "turnover_amount", "label": "成交额(元)", "ops": ["gt", "lt", "eq", "ne", "between", "topn", "topn_asc"]},
    {"key": "turnover_rate", "label": "换手率(%)", "ops": ["gt", "lt", "between"]},
    {"key": "latest_price", "label": "最新价(元)", "ops": ["gt", "lt", "between"]},
    {"key": "fund_scale", "label": "规模(亿)", "ops": ["gt", "gte", "lt", "lte", "between", "topn", "topn_asc"]},
    {"key": "premium", "label": "QDII溢价率", "ops": ["gt", "gte", "lt", "lte", "between", "topn", "topn_asc"]},
    {"key": "valuation_percentile", "label": "估值分位", "ops": ["gt", "gte", "lt", "lte", "between", "topn", "topn_asc"]},
    {"key": "momentum_5", "label": "动量(5日)", "ops": ["gt", "lt", "between", "topn", "topn_asc"]},
    {"key": "momentum_20", "label": "动量(20日)", "ops": ["gt", "lt", "between", "topn", "topn_asc"]},
    {"key": "volatility_20", "label": "波动率(20日)", "ops": ["gt", "lt", "between", "topn", "topn_asc"]},
    {"key": "vol_corr_20", "label": "量价相关(20日)", "ops": ["gt", "lt", "between", "topn", "topn_asc"]},
]


def etf_screen_filter(conditions, sort=None, asc=False, limit=50,
                      universe="ETF", days=365, codes=None) -> dict:
    """通用条件筛选(字段+运算符+值 AND 过滤 + 排序)。数据诚实: 字段整体无数据源 → 该条件跳过不误筛。
    复用 engine._apply_conditions/_sort_df(缺失 NaN 不满足数值比较 → 逐 code 缺失被过滤, 排序 None 末)。"""
    conds = conditions or []
    rows = _db.query_rows("etf_spot", limit=0) or []
    spot_by = {str(r.get("code")): r for r in rows if r.get("code") is not None}
    use_codes = codes or list(spot_by)

    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=max(days - 1, 1))).strftime("%Y%m%d")

    # 量价历史因子(可选用; etf_daily 无数据 → factor 全 None)
    close = _load_history_panel(universe, use_codes, start, today, "close")
    amount = _load_history_panel(universe, use_codes, start, today, "amount")
    if close is not None and len(close.columns):
        _, det, _ = short_scores(close, amount, list(close.columns), [5, 20])
    else:
        det = {}

    records = []
    for c in use_codes:
        spot = spot_by.get(str(c)) or {}
        meta = _fetch_quality_meta(c) or {}
        val = _fetch_index_valuation(c) or {}
        rec = {
            "code": c,
            "name": _display_name(c, spot),
            "change_pct": _to_f(spot.get("change_pct")),
            "turnover_amount": _to_f(spot.get("turnover_amount")),
            "turnover_rate": _to_f(spot.get("turnover_rate")),
            "latest_price": _to_f(spot.get("latest_price")),
            "fund_scale": (meta or {}).get("fund_scale"),
            "premium": _nan(_fetch_qdii_premium(c)),
            "valuation_percentile": _to_f(val.get("pe_pct")),
            "div_yield": _nan(val.get("div_yield")),
        }
        for k in ("momentum_5", "momentum_20", "volatility_20", "vol_corr_20"):
            rec[k] = _nan((det.get(c) or {}).get(k))
        records.append(rec)

    # 字段整体可用性: 所有候选全 None → 该字段条件跳过(数据诚实, 不误筛掉无数据源字段的 code)
    field_avail = {f["key"]: any(r.get(f["key"]) is not None for r in records)
                   for f in ETF_FILTER_FIELDS}
    active, skipped = [], []
    for c in conds:
        f = c.get("field")
        if f not in field_avail:
            skipped.append(f"字段不存在: {f}")
            continue
        if not field_avail[f]:
            skipped.append(f"字段 {f} 无数据源, 该条件跳过")
            continue
        active.append(c)

    df = pd.DataFrame(records).set_index("code")
    df, apply_skipped = _engine._apply_conditions(df, active)
    skipped.extend(apply_skipped)
    df = _engine._sort_df(df, sort, asc)
    if limit:
        df = df.head(limit)
    items = [_to_record({**{k: v for k, v in row.items() if k != "code"}, "code": idx})
             for idx, row in df.iterrows()]
    return {"items": items, "total": len(records), "skipped": skipped,
            "universe": universe, "mode": "filter"}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add screener/etf_screen.py tests/test_etf_screen.py
git commit -m "feat(etf-screen): 通用条件筛选引擎 etf_screen_filter(复用 engine 过滤/排序, 字段目录)"
```

---

### Task 3: 后端路由 + 字段目录

**Files:**
- Modify: `api/server.py`（`/api/etf-screen` 加 `mode=filter`/`conditions`/`sort`/`asc`；`/api/fields` 加 `etf_filter` 分类）
- Test: `tests/test_etf_screen.py`（新增路由测试，复用现有 TestClient 模式——若 test 文件已有 TestClient 则沿用，否则加）

**Interfaces:**
- Consumes: `etf_screen_filter`、`etf_screen_rank`、`ETF_FILTER_FIELDS`（Task 2）、`json.loads`（server.py 已 import）。
- Produces: `/api/etf-screen` 支持 `mode=filter`（返回 items/total/skipped，挂 cand_disclaimer）；`/api/fields` 返回 `etf_filter` 字段目录。

- [ ] **Step 1: 写失败测试**

在 `tests/test_etf_screen.py` 末尾追加（若文件无 TestClient 导入，先加 `from fastapi.testclient import TestClient` 与 `from api.server import app`；并给 `/api/etf-screen` 的 filter 分支 mock `etf_screen_filter`）：

```python
def test_route_mode_filter_dispatches(monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from api import server as sv
    called = {}
    def fake_filter(conditions, sort=None, asc=False, limit=50, universe="ETF", days=365, codes=None):
        called["conditions"] = conditions
        return {"items": [{"code": "510300", "name": "沪深300ETF"}], "total": 1,
                "skipped": [], "universe": universe, "mode": "filter"}
    monkeypatch.setattr(sv.etf_screen, "etf_screen_filter", fake_filter)
    with TestClient(sv.app) as client:
        r = client.get("/api/etf-screen", params={
            "mode": "filter", "universe": "ETF", "limit": 20, "days": 180,
            "conditions": json.dumps([{"field": "fund_scale", "op": "gte", "value": 50.0}]),
        })
    assert r.status_code == 200
    body = r.json()
    assert called["conditions"] == [{"field": "fund_scale", "op": "gte", "value": 50.0}]
    assert body["data"]["mode"] == "filter"
    assert "cand_disclaimer" in body


def test_route_fields_has_etf_filter(monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from api import server as sv
    with TestClient(sv.app) as client:
        r = client.get("/api/fields")
    body = r.json()["data"]
    assert "etf_filter" in body and isinstance(body["etf_filter"], list) and body["etf_filter"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_etf_screen.py -q`
Expected: FAIL（`/api/etf-screen` 不接受 `conditions`；`/api/fields` 无 `etf_filter`）

- [ ] **Step 3: 写实现**

`api/server.py` 中 `/api/etf-screen`（约 982 行）替换为：

```python
@app.get("/api/etf-screen")
def etf_screen_route(universe: str = Query("ETF"), mode: str = Query("long"),
                     limit: int = Query(50), days: int = Query(365),
                     codes: str = Query(""),
                     conditions: str | None = Query(None),
                     sort: str | None = Query(None), asc: bool = Query(False)):
    """ETF/QDII 筛选路由。mode=long/short 走长短清单，mode=filter 走通用条件筛选。

    机械排序/条件筛选观察清单，非荐股非买卖信号。
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    if mode == "filter":
        try:
            conds = json.loads(conditions) if conditions else []
        except (json.JSONDecodeError, TypeError):
            return _wrap({"items": [], "total": 0, "skipped": [f"conditions 非法 JSON: {conditions!r}"],
                          "universe": universe, "mode": "filter"},
                         {"cand_disclaimer": "ETF/QDII 条件机械筛选观察清单，非荐股非买卖信号，盈亏自负。"})
        if not isinstance(conds, list):
            conds = [conds]
        r = etf_screen.etf_screen_filter(conditions=conds, sort=sort, asc=asc,
                                         limit=limit, universe=universe,
                                         days=days, codes=code_list)
        r["mode"] = "filter"
        return _wrap(r, {"cand_disclaimer":
            "ETF/QDII 条件机械筛选观察清单，非荐股非买卖信号，盈亏自负。"})
    r = etf_screen.etf_screen_rank(universe=universe, mode=mode, limit=limit,
                                   days=days, codes=code_list)
    return _wrap(r, {"cand_disclaimer":
        "ETF/QDII 长短清单机械排序观察清单，非荐股非买卖信号，盈亏自负。"})
```

`api/server.py` 的 `/api/fields`（约 220 行）返回体加一行：

```python
        "etf_filter": etf_screen.ETF_FILTER_FIELDS,
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_etf_screen.py tests/test_engine.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add api/server.py tests/test_etf_screen.py
git commit -m "feat(etf-screen): /api/etf-screen 加 mode=filter + /api/fields 加 etf_filter 字段目录"
```

---

### Task 4: 前端 etfscreen tab「条件筛选」模式

**Files:**
- Modify: `web/index.html`（etfscreen tab 控件 + `etfScreenLoad`/`etfScreenRender` 扩展）

**Interfaces:**
- Consumes: 扩展后的 `/api/etf-screen`（`mode=filter` 返回 `items`/`skipped`）、`/api/fields` 的 `etf_filter`（字段目录）+ `ops`。
- Produces: etfscreen tab 新增"条件筛选"模式交互（多条件行 + 排序 + 结果渲染）。

- [ ] **Step 1: 前端 HTML 加「条件筛选」模式控件**

在 `web/index.html` etfscreen tab（约 350 行 `.card` 内）的 `efsMode` 下拉加一个 `<option value="filter">条件筛选(字段×运算符)</option>`，并在 `efsMode` 下拉与 `efsRun` 按钮之间插入一段**条件筛选专用控件**（默认隐藏，`mode=filter` 时显示）：

```html
<div id="efsFilterPane" style="display:none;flex:1;min-width:280px;margin-top:6px">
  <div class="row" style="align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:6px">
    <div class="f"><label>排序字段</label><select id="efsSort" class="btn ghost"></select></div>
    <div class="f"><label>方向</label><select id="efsAsc" class="btn ghost"><option value="false" selected>降序</option><option value="true">升序</option></select></div>
    <button class="btn ghost" id="efsAddCond" type="button">＋条件</button>
  </div>
  <div id="efsConds"></div>
</div>
```

- [ ] **Step 2: 写 JS——条件行渲染 + 提交参数**

在 `web/index.html` 的 `etfScreenLoad` 前插入条件行管理逻辑，并改造 `etfScreenLoad`/`etfScreenRender`。要点：
- 一个 `efsState.condRows` 数组（每项 `{field, op, value}`），初始一条空行。
- `renderCondRows()`：用 `/api/fields` 拉 `etf_filter` + `ops`（缓存到 `efsState.fields`），为每行渲染三个下拉/输入；字段 onchange 时按其 ops 重建运算符下拉；"删除"按钮移行；`efsAddCond` 加行。
- `etfScreenLoad`：`mode==='filter'` 时，收集 `efsConds` 中非空行成 `conditions` JSON，拼 `&mode=filter&conditions=...&sort=...&asc=...` 调 `/api/etf-screen`；否则维持原有 long/short 调用。
- `etfScreenRender`：`mode==='filter'` 时渲染 `items`（复用现有行模板字段：code/name/规模/溢价/估值分位/量价因子列），skipped 显示到 `efsNote`。

```js
// 条件筛选模式: 从 /api/fields 动态渲染字段/运算符下拉(与实时筛选 tab 同模式)
let efsFieldsLoaded=false;
async function efsLoadFields(){
  if(efsFieldsLoaded) return;
  const r=await fetch(`${API}/api/fields`); const j=await r.json();
  const d=j.data||{};
  efsState.fields=d.etf_filter||[]; efsState.ops=d.ops||{};
  // 填排序下拉
  const sel=document.getElementById('efsSort');
  sel.innerHTML=(d.etf_filter||[]).map(f=>`<option value="${f.key}">${f.label}</option>`).join('');
  efsFieldsLoaded=true;
}
function efsRenderCondRows(){
  const box=document.getElementById('efsConds');
  if(!efsState.condRows.length) efsState.condRows=[{field:'',op:'',value:''}];
  box.innerHTML=efsState.condRows.map((row,ri)=>{
    const fSel=efsState.fields.map(f=>`<option value="${f.key}"${row.field===f.key?' selected':''}>${f.label}</option>`).join('');
    const fd=efsState.fields.find(f=>f.key===row.field);
    const ops=(fd?fd.ops:[]).map(o=>`<option value="${o}"${row.op===o?' selected':''}>${(efsState.ops[o]||o)}</option>`).join('');
    const val=`<input class="btn ghost" style="width:110px" value="${row.value??''}" placeholder="值/区间" oninput="efsState.condRows[${ri}].value=this.value">`;
    return `<div class="row" style="align-items:center;gap:6px;margin-bottom:4px">
      <select class="btn ghost" style="min-width:130px" onchange="efsState.condRows[${ri}].field=this.value;efsState.condRows[${ri}].op='';efsRenderCondRows()">${fSel}</select>
      <select class="btn ghost" style="min-width:90px" onchange="efsState.condRows[${ri}].op=this.value">${ops}</select>
      ${val}
      <button class="btn ghost" onclick="efsState.condRows.splice(${ri},1);efsRenderCondRows()">删</button>
    </div>`;
  }).join('');
}
function efsCollectConds(){
  return efsState.condRows.filter(r=>r.field&&r.op&&r.value!==''&&r.value!=null)
    .map(r=>({field:r.field,op:r.op,value:r.op==='between'?r.value.split(',').map(Number):r.value}));
}
```

改造 `etfScreenLoad`：把 `const mode=...` 后加 `const isFilter=mode==='filter';`，并在 fetch URL 拼接时按 isFilter 分支：

```js
let url=`${API}/api/etf-screen?universe=${uni}&mode=${mode}&limit=${limit}&days=${days}`;
if(isFilter){
  document.getElementById('efsFilterPane').style.display='flex';
  await efsLoadFields(); efsRenderCondRows();
  const conds=efsCollectConds();
  url+=`&sort=${document.getElementById('efsSort').value}&asc=${document.getElementById('efsAsc').value}`;
  if(conds.length) url+=`&conditions=${encodeURIComponent(JSON.stringify(conds))}`;
}else{ document.getElementById('efsFilterPane').style.display='none'; }
```

改造 `etfScreenRender`：开头加 `if(mode==='filter'){ const d=(efsState.raw?.data)||{}; const rows=d.items||[]; /* 复用 isLong=false 的行模板但列=code/name/规模/溢价/估值/量价 */ ... return; }`。条件筛选结果列简化为：代码/名称/最新价/换手%/规模(亿)/溢价率/估值分位/动量(5日)/来源。skipped 塞 `efsNote`。

`efsMode.onchange` 已有（1686 行），改为切换后也刷新 `efsFilterPane` 显隐并 `etfScreenLoad()`；在 JS 末尾追加 `document.getElementById('efsAddCond').onclick=()=>{efsState.condRows.push({field:'',op:'',value:''});efsRenderCondRows();};`

- [ ] **Step 3: 手动/浏览器验证**

Run: 启动后端（或 docker 容器已含前端需重建），浏览器开 `http://localhost:8000/web/index.html` → ETF/QDII 筛选 tab → 清单下拉选"条件筛选" → 出现排序+条件面板 → 加条件 `规模≥50` → 筛选 → 结果表渲染且 `efsNote` 显示跳过说明。
Expected: 面板显隐正确、字段/运算符下拉来自 `/api/fields`、结果表正常、无 JS 报错。

- [ ] **Step 4: 提交**

```bash
git add web/index.html
git commit -m "feat(etf-screen): 前端 etfscreen tab 加条件筛选模式(动态字段/运算符下拉+排序)"
```

---

### Task 5: CLAUDE.md 同步

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新架构 `screener/etf_screen.py` 描述**

在 CLAUDE.md 第 65 行 `etf_screen.py` 描述末尾补一句：条件筛选器 `etf_screen_filter(conditions,sort,asc,...)`（通用字段+运算符 AND 过滤，复用 `engine._apply_conditions/_sort_df`，字段整体无数据源→条件跳过不误筛；`ETF_FILTER_FIELDS` 目录，估值接线 `_INDEX_MAP`/`_INDEX_KEYWORDS`+csindex 按指数去重；`/api/fields` 加 `etf_filter`；30s 缓存键含 conditions/sort/asc）。

- [ ] **Step 2: 更新路由速查**

`/api/etf-screen` 后追加：`mode=filter`（conditions JSON/sort/asc，通用条件机械筛选观察清单，挂 cand_disclaimer"条件机械筛选观察清单，非荐股非买卖信号"）。

- [ ] **Step 3: 更新改动检查清单**

补一条：新增 ETF 条件筛选字段 → `ETF_FILTER_FIELDS` 与 `/api/fields` 的 `etf_filter` 需同步；估值分位源为 csindex，字段整体缺失→条件跳过不误筛；`etf_spot` 不新增列。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md
git commit -m "docs(etf-screen): CLAUDE.md 同步条件筛选器/估值接线/路由"
```

---

## Self-Review

**Spec 覆盖核对：**
- csindex 估值接线（映射+名称兜底+按指数去重）→ Task 1 ✅
- `etf_screen_filter` AND 过滤 + topn + 排序 None 末 + 缺失诚实（字段整体无源→条件跳过不误筛）→ Task 2 ✅
- `ETF_FILTER_FIELDS` + `/api/fields` 加 `etf_filter` → Task 2 产出 + Task 3 接线 ✅
- `/api/etf-screen` 加 `mode=filter`/`conditions`/`sort`/`asc`，向后兼容 → Task 3 ✅
- 前端条件筛选模式 + 动态字段下拉 + 排序 → Task 4 ✅
- 测试全 mock、合规措辞、`etf_spot` 不新增列 → 各任务 Global Constraints + 测试 ✅
- CLAUDE.md 同步 → Task 5 ✅

**Placeholder 扫描：** 无 TBD/TODO；每步含实际代码。Task 4 的渲染模板有一处"复用 isLong=false 的行模板"精简表述，但已在步骤内给了字段列清单，可落地。

**Type 一致性：** `etf_screen_filter(conditions, sort, asc, limit, universe, days, codes)` 在 Task 2 定义、Task 3 路由调用、Task 4 前端调用（经 URL 参数）签名一致；`ETF_FILTER_FIELDS` 在 Task 2 产出、Task 3 `/api/fields` 引用、Task 4 `efsState.fields` 消费一致；`_index_code`/`_fetch_index_valuation` 在 Task 1 定义、Task 2 消费一致。

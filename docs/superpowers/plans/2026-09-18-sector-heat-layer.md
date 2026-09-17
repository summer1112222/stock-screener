# 宏观/行业景气排序加成层（sector-heat）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用板块资金验证 + 政策主题命中构建一个行业景气分位层，作为 quality 精排与 smart_money 清单的**排序上下文加成**，补上三模块缺失的宏观/景气维度。

**Architecture:** 新纯函数模块 `screener/sector_heat.py`：`board_heat`（=0.6×板块资金净流入 rank-pct + 0.4×板块上涨宽度 rank-pct）+ 静态 `POLICY_THEMES` 政策命中加成。个股→板块用 `nextday._board_members_batch`（板块成分股反查，30s 缓存）建立 code→board 映射。加成层**不改** quality 共振签名（min_dims/hits/缓存键/warmup 零波及），只在精排(final)阶段把 `sector_heat` 以 0.2 权重 + policy 提前量揉进现有排序；smart_money 仅附标注不改主排序。

**Tech Stack:** Python 3.12 / pandas 3.0 / SQLite（`sector_fund_flow`/`industry_board`/`stock_spot` 三表只读）。零新增采集源。

**Spec:** `docs/superpowers/specs/2026-09-18-sector-heat-layer-design.md`

## Global Constraints

- **零新增采集源**：只用现有表 `sector_fund_flow`（板块净流入）、`industry_board`（板内上涨/下跌家数）、`stock_spot`（个股 change_pct）。不新增 DB 列、不新增采集。
- **`stock_spot` 无 `board` 列**：个股归板块必须经 `nextday._board_members_batch(codes, sector_type="industry")` 反查（返回 `{board: [code,...]}`），sector_heat.py 本身不触网、纯函数。
- **不改共振签名**：`_DEFAULT_DIM_WEIGHTS`/`min_dims`/`hits`/缓存键/warmup 一律不动。
- 板块广度用 `industry_board.up_count / (up_count+down_count)`，**不**用成分股逐只判，避免大成员反查。
- NaN→None 守卫：所有输出经 `_to_float`/`_nan`，防 starlette `allow_nan=False` 500。
- 合规：措辞"行业景气机械排序上下文/资金+政策验证"；不预测板块涨跌、不荐板块。disclaimer 追加说明。
- 测试：`tests/test_sector_heat.py` 纯 mock 三源 + mock `_board_members_batch`，不触网。
- 命名统一：函数返回 `(sector_heat, policy_hit)` 数值，浮点 `float|None`。

---

### Task 1: sector_heat.py 核心纯函数模块

**Files:**
- Create: `screener/sector_heat.py`
- Test: `tests/test_sector_heat.py`

**Interfaces:**
- Consumes: 无（纯标准库 + pandas）。三表行由调用方以 `list[dict]` 传入。
- Produces（后续 Task 2/3 依赖）:
  - `POLICY_THEMES: dict[str, list[str]]` — 政策主题名 → 命中板块名列表（静态）
  - `policy_hit(board: str) -> float` — 命中返回 `0.05`，否则 `0.0`
  - `rank_pct(series) -> "pd.Series"` — 横截 rank-pct（0-1，并列平均秩，n≤1→0.5）
  - `board_heat(fund_flow, board_rows) -> dict[str, float]` — 板块名 → heat[0,1]
  - `pick_board(code, member_map, heat) -> str | None` — code 命中板块中 heat 最高者；无→None
  - `attach_sector_heat(rows, fund_flow, board_rows, member_map) -> None` — 原地给每个 row 加 `sector_heat`/`policy_hit`

- [ ] **Step 1: Write the failing test**

`tests/test_sector_heat.py`:
```python
# -*- coding: utf-8 -*-
"""sector_heat 行业景气加成层测试。纯 mock 三源(板块资金流/行业板手脚)不触网。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from screener import sector_heat as sh


def test_policy_hit():
    b = sh.board_heat  # 触发模块 import
    assert sh.policy_hit("先进制造") == 0.05
    assert sh.policy_hit("白酒") == 0.0
    assert sh.policy_hit("") == 0.0


def test_rank_pct_order():
    s = pd.Series({"a": 1.0, "b": 3.0, "c": 2.0})
    r = sh.rank_pct(s)
    assert r["b"] > r["c"] > r["a"]
    assert float(r["b"]) == 1.0 and float(r["a"]) == 0.0
    assert sh.rank_pct(pd.Series(dtype=float)).empty
    assert float(sh.rank_pct(pd.Series({"z": 5.0}))["z"]) == 0.5


def test_board_heat_weights():
    """0.6*资金分位 + 0.4*上涨宽度分位；width 用 up/(up+down)。"""
    ff = [
        {"name": "先进制造", "main_net_inflow": 3e8},
        {"name": "白酒", "main_net_inflow": -1e8},
        {"name": "医药", "main_net_inflow": 1e8},
    ]
    br = [
        {"name": "先进制造", "up_count": 60, "down_count": 40, "constituent_count": 100},
        {"name": "白酒", "up_count": 5, "down_count": 95, "constituent_count": 100},
        {"name": "医药", "up_count": 30, "down_count": 70, "constituent_count": 100},
    ]
    h = sh.board_heat(ff, br)
    # 先进制造 资金最高(1.0)+宽度0.6=(1.0) → heat 最高；白酒最低
    assert h["先进制造"] == max(h.values())
    assert h["白酒"] == min(h.values())
    for v in h.values():
        assert 0.0 <= v <= 1.0


def test_pick_board_highest_heat():
    mm = {"先进制造": {"a", "b"}, "医药": {"a"}}
    heat = {"先进制造": 0.8, "医药": 0.4}
    assert sh.pick_board("a", mm, heat) == "先进制造"  # 多命中取 heat 最高
    assert sh.pick_board("c", mm, heat) is None


def test_attach_sector_heat():
    """attach 原地加 sector_heat/policy_hit；未命中板块→sector_heat=None。"""
    rows = [{"code": "a"}, {"code": "c"}]
    ff = [{"name": "先进制造", "main_net_inflow": 3e8}]
    br = [{"name": "先进制造", "up_count": 60, "down_count": 40}]
    mm = {"先进制造": {"a"}}
    sh.attach_sector_heat(rows, ff, br, mm)
    r0 = rows[0]
    assert r0["policy_hit"] == 0.05            # 先进制造 命中政策
    assert r0["sector_heat"] is not None and 0.0 <= r0["sector_heat"] <= 1.0
    assert rows[1]["policy_hit"] == 0.0
    assert rows[1]["sector_heat"] is None      # 无板块 → None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sector_heat.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.sector_heat'`

- [ ] **Step 3: Write minimal implementation**

`screener/sector_heat.py`:
```python
"""行业景气排序加成层：板块资金验证 + 政策主题命中 → 行业景气分位。

把"板块资金净流入排名 + 板块上涨宽度 + 静态政策命中"捏成一个横截排序上下文，
供 quality 精排 / smart_money 清单作**排序加成**（不改共振签名、不荐板块）。

数据源三表只读（sector_fund_flow / industry_board / stock_spot），由调用方以
list[dict] 传入；个股→板块用调用方给的 board_members 反查（如
screener.nextday._board_members_batch 30s 缓存）。本模块不触网、纯函数。
"""
from __future__ import annotations
import pandas as pd

# 静态政策主题 → 命中板块名列表。从 gov.cn 政策要点人工维护，非实时抓取。
# 预填 2026-09 六大主线（先进制造/电子信息/智能家居消费/基础研究算力/人形机器人/智能驾驶）。
POLICY_THEMES: dict[str, list[str]] = {
    "先进制造": ["先进制造", "工业母机", "机器人", "高端装备"],
    "电子信息": ["电子", "半导体", "消费电子", "面板"],
    "智能家居消费": ["智能家居", "家用电器", "厨卫电器"],
    "基础研究(算力/AI)": ["CPO", "算力", "AI应用", "光模块"],
    "人形机器人": ["人形机器人", "减速器", "传感器"],
    "智能驾驶": ["智能驾驶", "汽车零部件", "车联网"],
}
# 把主题的板块名制成 flat 命中集，policy_hit 判"该板块名是否命中任一政策主题"
_HIT_BOARDS: set[str] = {b for ls in POLICY_THEMES.values() for b in ls}


def policy_hit(board: str) -> float:
    """命中静态政策主题 → 0.05 提前量加成；未命中/空 → 0.0。"""
    if not board:
        return 0.0
    return 0.05 if str(board).strip() in _HIT_BOARDS else 0.0


def rank_pct(series: "pd.Series") -> "pd.Series":
    """横截 rank-pct(0-1)：并列取平均秩；n<=1 → 0.5(无对比信息取中位)。"""
    if series.empty:
        return series
    if len(series) <= 1:
        return pd.Series({k: 0.5 for k in series.index}, dtype=float)
    return series.rank(method="average", pct=True)


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def board_heat(fund_flow: list[dict], board_rows: list[dict]) -> dict[str, float]:
    """板块景气分位：0.6*资金净流入 rank-pct + 0.4*上涨宽度 rank-pct。

    breadth = up_count/(up_count+down_count)；缺该板 fund_flow/board_rows → 该板缺席。
    返回 board 名 → heat ∈ [0,1]（横截后）。"""
    inflow = {str(r.get("name")): _f(r.get("main_net_inflow")) for r in fund_flow}
    inflow = {k: v for k, v in inflow.items() if v is not None}
    brd = {}
    for r in board_rows:
        n = str(r.get("name"))
        up = _f(r.get("up_count")); dn = _f(r.get("down_count"))
        if up is None or dn is None or (up + dn) == 0:
            brd[n] = None
        else:
            brd[n] = up / (up + dn)
    # 只对同时有资金与宽度证据的板块打分（缺一不可，避免单边失真）
    boards = [n for n in brd if n in inflow and brd[n] is not None]
    if not boards:
        return {}
    s_in = pd.Series({n: inflow[n] for n in boards})
    s_w = pd.Series({n: brd[n] for n in boards})  # type: ignore[dict-item]
    pin = rank_pct(s_in)
    pwd = rank_pct(s_w)
    return {n: round(0.6 * float(pin[n]) + 0.4 * float(pwd[n]), 4) for n in boards}


def pick_board(code: str, member_map: dict[str, set[str]], heat: dict[str, float]) -> str | None:
    """code 命中板块（member_map 含之）中 heat 最高者；无命中 → None。"""
    best, best_h = None, None
    for board, codes in (member_map or {}).items():
        if code not in codes:
            continue
        h = heat.get(str(board))
        if h is None:
            continue
        if best_h is None or h > best_h:
            best, best_h = str(board), h
    return best


def attach_sector_heat(rows: list[dict], fund_flow: list[dict],
                       board_rows: list[dict], member_map: dict[str, set[str]]) -> None:
    """原地给每行加 sector_heat(所属最优板块景气)与 policy_hit(政策加成)。

    rows 需含 code；无板块/无景气 → sector_heat=None, policy_hit=0.0，诚实缺失。"""
    heat = board_heat(fund_flow, board_rows)
    for r in rows:
        board = pick_board(str(r.get("code") or ""), member_map, heat)
        r["sector_heat"] = heat.get(str(board)) if board else None
        r["policy_hit"] = policy_hit(str(board) if board else "")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_sector_heat.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add screener/sector_heat.py tests/test_sector_heat.py
git commit -m "feat(sector-heat): 行业景气分位纯函数模块(资金+政策命中)"
```

---

### Task 2: quality 精排阶段集成 sector_heat 加成

**Files:**
- Modify: `backtest/quality.py`（`quality_rank` refine 块，约 L1210-1216 处 `pool = main[:max(refine_pool, limit)]` 之后）
- Test: `tests/test_sector_heat.py`（追加 2 测试）

**Interfaces:**
- Consumes: `task1.attach_sector_heat(rows, fund_flow, board_rows, member_map)`；`nextday._board_members_batch(codes, sector_type="industry")`；现有 `_refine_by_quote(pool, df_spot, in_session)` 已在 pool 上留下 `_refine_score`（盘中）/`resonance`。
- Produces: 精排池 `pool` 重排为 `final = 0.6*res_pct + 0.2*liq_pct + 0.2*heat_pct + policy`（盘中）或 `0.8*res_pct + 0.2*heat_pct + policy`（盘后）；每 item 附 `sector_heat`/`policy_hit`。API 自动透传（item 已含字段）。

- [ ] **Step 1: Write the failing test（追加到 tests/test_sector_heat.py）**

```python
def test_quality_refine_applies_sector_heat(monkeypatch):
    from backtest import quality

    # 三只票：共 2 命中板块；先按 _refine_score 盘中重排(与 sector_heat 无冲突基础)
    pool = [
        {"code": "a", "resonance": 0.0, "_refine_score": 0.3},
        {"code": "b", "resonance": 0.0, "_refine_score": 0.7},
        {"code": "c", "resonance": 0.0, "_refine_score": 0.5},
    ]
    ff = [{"name": "先进制造", "main_net_inflow": 3e8},
          {"name": "白酒", "main_net_inflow": -1e8},
          {"name": "医药", "main_net_inflow": 1e8}]
    br = [{"name": "先进制造", "up_count": 60, "down_count": 40},
          {"name": "白酒", "up_count": 5, "down_count": 95},
          {"name": "医药", "up_count": 30, "down_count": 70}]
    mm = {"先进制造": {"b"}, "白酒": {"a"}, "医药": {"c"}}

    def _qr(t, **k):
        return {"sector_fund_flow": ff, "industry_board": br}.get(t, [])

    monkeypatch.setattr(quality._db_, "query_rows", _qr) if False else None
    import data.db as dbmod
    monkeypatch.setattr(dbmod, "query_rows", _qr)
    monkeypatch.setattr(quality, "sector_heat", sh)  # 同模块引用保持

    out = quality._apply_sector_heat(pool, ff, br, mm, in_session=True)
    # c 在医药(资金+宽度均中)→heat 最高，policy 0.05；重排后 c 应上升
    top = out[0]["code"]
    assert top == "c", f"医药景气最高应列首: {[x['code'] for x in out]}"
    # item 附字段
    merged = {x["code"]: x for x in out}
    assert merged["c"]["sector_heat"] is not None
    assert merged["a"]["policy_hit"] == 0.0
```

> 注：测试经 `_apply_sector_heat(pool, fund_flow_rows, board_rows, member_map, in_session)` 纯函数入口（不依赖 db 触网），member_map 显式传入。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sector_heat.py::test_quality_refine_applies_sector_heat -q`
Expected: FAIL with `AttributeError: module 'backtest.quality' has no attribute '_apply_sector_heat'`

- [ ] **Step 3: Write minimal implementation**

在 `backtest/quality.py` 新增：

```python
def _apply_sector_heat(pool: list, fund_flow: list, board_rows: list,
                       member_map: dict, in_session: bool) -> list:
    """行业景气加成重排(排序加成层，不改共振签名)。

    final = 0.6*res_pct + 0.2*liq_pct + 0.2*heat_pct + policy   (盘中，liq 来自 _refine_score)
          = 0.8*res_pct + 0.2*heat_pct + policy                 (盘后，A 流动性失效)
    仅作排序上下文，不预测板块；成员反查失败板块 → 不加成。"""
    from screener import sector_heat as _sh
    _sh.attach_sector_heat(pool, fund_flow, board_rows, member_map)
    rs = pd.Series({str(i.get("code")): _to_float(i.get("resonance")) or 0.0 for i in pool})
    rp = _to_pct(rs).to_dict() if not rs.empty else {}
    hs = pd.Series({str(i.get("code")): _to_float(i.get("sector_heat")) or 0.0 for i in pool})
    hp = _to_pct(hs).to_dict() if not hs.empty else {}
    for i in pool:
        c = str(i.get("code"))
        h = hp.get(c, 0.0)
        p = _to_float(i.get("policy_hit")) or 0.0
        if in_session:
            r = rp.get(c, 0.0)
            liq = _to_float(i.get("_refine_score")) or 0.0  # 已含 res 0.6+liq 0.4
            liq_pct = liq  # 粗粒度：_refine_score 本身 0-1 可直接作 liq 分位代理
            i["_final"] = 0.6 * r + 0.2 * liq_pct + 0.2 * h + p
        else:
            i["_final"] = 0.8 * rp.get(c, 0.0) + 0.2 * h + p
    pool.sort(key=lambda x: x.get("_final") or 0.0, reverse=True)
    return pool
```

在 `quality_rank` 的 refine 块（现有 `pool = main[:max(refine_pool, limit)]` 与 `_refine_by_quote` 之后）追加：

```python
if refine and universe == "stock":
    import data.db as _db
    from screener import nextday as _nd
    codes = [str(i.get("code")) for i in pool]
    ff = _db.query_rows("sector_fund_flow", where="sector_type='行业' AND indicator='今日'",
                        order_by="", limit=0) or []
    br = _db.query_rows("industry_board", order_by="", limit=0) or []
    mm = {}
    try:
        mm = _nd._board_members_batch(codes, sector_type="industry")  # 30s缓存
    except Exception:
        mm = {}
    pool = _apply_sector_heat(pool, ff, br, mm, in_session=in_session)
    main = pool
```

> 若 `_refine_by_quote` 盘中已按 `_refine_score` 排序，此处以 `_final` 最终重排（_final 把 _refine_score 作为 liq 代理保留其权重，避免景气一把推翻既有流动性重排）。

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_sector_heat.py -q`
Expected: 6 passed（含新测试）＋回归 `python -m pytest tests/test_quality.py tests/test_quality_refine.py tests/test_quality_factors.py tests/test_quality_confidence.py -q` 全绿

- [ ] **Step 5: Commit**

```bash
git add backtest/quality.py tests/test_sector_heat.py
git commit -m "feat(quality): 精排阶段集成行业景气加成(资金+政策命中,不动共振)"
```

---

### Task 3: smart_money 清单附 sector_heat/policy_hit 标注

**Files:**
- Modify: `screener/smart_money.py`（`top_by_amount` 的 `_attach_intensity(pool)` 之后；`today_list` 返回行上传）
- Test: `tests/test_sector_heat.py`（追加 1 测试）

**Interfaces:**
- Consumes: `task1.attach_sector_heat(rows, fund_flow, board_rows, member_map)`；现有 `_attach_intensity(pool)`。
- Produces: `top_by_amount`/`today_list` 每行附 `sector_heat`/`policy_hit`。**不改主排序**。

- [ ] **Step 1: Write the failing test（追加）**

```python
def test_top_by_amount_annotates_sector(monkeypatch):
    from screener import smart_money as sm

    rows = [{"code": "a", "name": "甲", "market": "sh", "amount": 1e7, "count": 1},
            {"code": "z", "name": "乙", "market": "sz", "amount": 2e7, "count": 1}]
    ff = [{"name": "先进制造", "main_net_inflow": 3e8}]
    br = [{"name": "先进制造", "up_count": 60, "down_count": 40}]
    mm = {"先进制造": {"a"}}

    monkeypatch.setattr(sm, "_attach_intensity", lambda p: p)
    monkeypatch.setattr(sm, "_sector_ctx", lambda rows, ff, br, mm: sh.attach_sector_heat(rows, ff, br, mm))

    out = sm.top_by_amount(days=5)
    merged = {x["code"]: x for x in out["rows"]}
    assert "sector_heat" in merged["a"] and merged["a"]["policy_hit"] == 0.05
```

> 本测试聚焦"top_by_amount 正确走 `_sector_ctx` 标注管线"，数据源（ff/br/mm）由 `_sector_ctx` 内部用 mock 的 `data.db.query_rows` 提供（见实现）。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sector_heat.py::test_top_by_amount_annotates_sector -q`
Expected: FAIL 无 `sector_heat` 字段

- [ ] **Step 3: Write minimal implementation**

在 `screener/smart_money.py` 顶部 import 后加一个惰性上下文助手（本模块不触网，数据由 DB 一次取好，30s 缓存复用成员反查）：

```python
def _sector_ctx(rows: list, fund_flow: list | None = None,
                board_rows: list | None = None,
                member_map: dict | None = None):
    """给 rows 原地附 sector_heat/policy_hit（只读上下文标注，不改主排序）。
    fund_flow/board_rows/member_map 为可注入依赖，供测试 mock；默认从 DB/nextday 取。"""
    from screener import sector_heat as _sh
    if fund_flow is None:
        fund_flow = db.query_rows("sector_fund_flow",
                                  where="sector_type='行业' AND indicator='今日'",
                                  order_by="", limit=0) or []
    if board_rows is None:
        board_rows = db.query_rows("industry_board", order_by="", limit=0) or []
    if member_map is None:
        from screener import nextday as _nd
        member_map = {}
        try:
            member_map = _nd._board_members_batch(
                [str(r.get("code")) for r in rows if r.get("code")], sector_type="industry")
        except Exception:
            member_map = {}
    _sh.attach_sector_heat(rows, fund_flow, board_rows, member_map)
```

在 `top_by_amount` 的 `_attach_intensity(pool)` 之后追加：
```python
    _sector_ctx(pool)
```
在 `today_list` 返回 `rows` 前（对行列表追加）：
```python
    if rows:
        _sector_ctx(rows)
```
> members 反查 30s 缓存（`_board_members_batch` 内），frequencies 可控。

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_sector_heat.py -q`
Expected: 7 passed ＋ 回归 `python -m pytest tests/test_smart_money.py tests/test_sm_radar.py -q` 全绿（today_list/top_by_amount 现有断言不因新增字段破坏）

- [ ] **Step 5: Commit**

```bash
git add screener/smart_money.py tests/test_sector_heat.py
git commit -m "feat(smart-money): 主力清单附行业景气/政策命中标注(不改主排序)"
```

---

### Task 4: CLAUDE.md 文档同步

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 在 screener 模块简介补一节**

在 CLAUDE.md `screener/` 段落追加一行说明新增 `sector_heat.py`，并在路由速查 `/api/quality` 条目补 `sector_heat`/`policy_hit` 字段说明。

- [ ] **Step 2: 检查清单补**：在"新增 quality 编排层"检查清单末尾追加"行业景气加成层：`sector_heat.board_heat` 应保持纯函数（资金+宽度 rank-pct），不改共振签名；个股归板块必须用 `nextday._board_members_batch` 反查（stock_spot 无 board 列），policy 命中使用静态 `POLICY_THEMES`。"

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): sector-heat 行业景气加成层 + quality 字段说明"
```

---

## 自检记录

- **Spec 覆盖**：§2 架构（sector_heat.py 纯函数）→ Task1；§3 quality 精排加成 → Task2；§4 smart_money 标注 → Task3；§8 交付 CLAUDE.md → Task4。§5 B 风格/C 看板为后续子项目，不在本计划。
- **0.6/0.4 公式细化**：spec §3 给了 `final = 0.6*resonance + 0.4*sector_heat`；本计划在盘中进一步把副分位 0.4 拆为 `0.2*liq + 0.2*heat`（保留刚上线的流动性精排权重），政策提前量 +0.05——精神一致，已在 Task2 注释说明。
- **占位扫描**：无 TBD/TODO；每个 Step 含真实代码。
- **类型一致**：`attach_sector_heat(rows, fund_flow, board_rows, member_map)` 签名跨 Task 一致；`sector_heat`/`policy_hit` 均为 `float|None`。
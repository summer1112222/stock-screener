# 三清单排序因子诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 `list_track` 前视收益给 smart_money/quality/nextday 三清单做排序有效性诊断，产出报告驱动第二阶段排序改造。

**Architecture:** 阶段一 = A) 给 smart_money 补 `list_track` 追踪（today_list 头部 top20）→ B) nextday 收益诊断（头部 vs 整体、五因子分档、穿透分层，复用 `tracker.summary` + `backtest/eval.py`）→ C) quality 样本枯竭结构诊断。诊断脚本只读 `list_track`（`ret_k1/k3/k5` + `meta_json`），不改排序签名/权重/缓存键。

**Tech Stack:** Python 3.12 / FastAPI / SQLite（`list_track` 表）/ pytest（单测 mock `db.query_rows`，不触网）。

**Spec:** `docs/superpowers/specs/2026-09-19-sorting-diagnosis-design.md`

## Global Constraints

- **合规**：只输出历史收益统计事实；措辞"机械历史统计，非预测、非荐股"；不产出买卖点；沿用 cand_disclaimer 管道。
- **追踪记录**入口必须 `is_default_params` 判定，仅默认参数组合落库，防任意参数污染统计。
- **诊断零副作用**：不改排序签名/权重/缓存键；`_insert_ignore` 幂等，重跑不覆盖非空收益字段。
- **不触网**：单测全 mock `db.query_rows`；diagnose 只读 `list_track`，不拉 `stock_daily`。
- 仓库根目录跑 `python -m pytest tests/ -q`；无 conftest/pytest.ini，必须在根目录跑。

---

### Task 1: tracker 扩展支持 smart_money 追踪

**Files:**
- Modify: `backtest/tracker.py:25-38`（DEFAULT_PARAMS）、`:60-74`（_meta_payload）、`:97-100`（record_list 白名单+score）
- Test: `tests/test_tracker.py`

**Interfaces:**
- Consumes: 现有 `record_list(module, mode, date, items) -> int`、`is_default_params(module, params)`、`_meta_payload`、`_to_f`
- Produces: `DEFAULT_PARAMS["smart_money"]`（默认参数表）；`record_list("smart_money", ...)` 不再被白名单拒绝；smart_money 行 `score=净额`、`meta_json` 存 `quality_pct`

- [ ] **Step 1: 写失败测试** —— 验证 smart_money 能被 record 且 score/meta 正确

```python
# tests/test_tracker.py 追加
def test_record_smart_money():
    """B3 追踪：smart_money 应能 record，score=净额、meta_json 含 quality_pct。"""
    tracker.DEFAULT_PARAMS["smart_money"] = {
        "date": None, "channel": None, "market": None, "days": 7, "limit": 1000,
    }
    items = [{"code": "600001", "name": "甲", "score": 1.2e8, "quality_pct": 0.9}]
    n = tracker.record_list("smart_money", "today", "2026-09-18", items)
    assert n >= 1
    rows = db.query_rows("list_track",
                         where="module='smart_money' AND date='2026-09-18'")
    assert rows and rows[0]["score"] == 1.2e8
    assert json.loads(rows[0]["meta_json"]).get("quality_pct") == 0.9


def test_smart_money_default_params():
    """smart_money 默认参数判定：默认组合命中，改 limit 不命中。"""
    tracker.DEFAULT_PARAMS["smart_money"] = {
        "date": None, "channel": None, "market": None, "days": 7, "limit": 1000,
    }
    assert tracker.is_default_params(
        "smart_money", {"date": None, "channel": None, "market": None,
                        "days": 7, "limit": 1000})
    assert not tracker.is_default_params(
        "smart_money", {"date": None, "channel": None, "market": None,
                        "days": 7, "limit": 50})
```

- [ ] **Step 2: 跑测试确认失败** —— `_insert_ignore` 需真实 `db.get_conn()`；按 test_tracker 现有 mock 方式（查该文件顶部 fixture）仿照。Expected: FAIL（record_list 白名单 return 0 → n==0）

- [ ] **Step 3: 实现 tracker 三处改动**

```python
# 1) DEFAULT_PARAMS 追加(在 "nextday" 后):
    "smart_money": {
        "date": None, "channel": None, "market": None, "days": 7, "limit": 1000,
    },

# 2) _meta_payload 在 if quality 与 else 之间插 smart_money 分支:
    elif module == "smart_money":
        for k in ("quality_pct",):
            if k in item:
                keep[k] = item.get(k)

# 3) record_list 白名单加 smart_money; score 分支:
    if module not in {"quality", "nextday", "smart_money"}:
        return 0
    ...
            "score": _to_f(it.get("score")) if module in ("nextday", "smart_money")
                     else _to_f(it.get("adjusted_resonance")),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tracker.py -q` Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backtest/tracker.py tests/test_tracker.py
git commit -m "feat(tracker): smart_money 补 list_track 追踪(today 头部, score=净额, meta=quality_pct)"
```

---

### Task 2: server `/api/smart-money/today` 接入追踪记录

**Files:**
- Modify: `api/server.py:810-820`（sm_today 路由）
- Test: `tests/test_tracker_api.py`

**Interfaces:**
- Consumes: `tracker.is_default_params` / `tracker.record_list`（Task 1）、`_track_date()`（server 已存在）、`sm_query.today_list`
- Produces: `/api/smart-money/today` 命中默认参数时，对头部前 20 只落库 `module="smart_money", mode="today"`

- [ ] **Step 1: 写失败测试** —— 默认参数调用触发 record，头部只记前 20

```python
# tests/test_tracker_api.py 追加
def test_sm_today_records_head_when_default(monkeypatch):
    """默认参数调 /api/smart-money/today 应记录头部前20;改 limit 不记录。"""
    rows = [{"code": f"600{i:03d}", "name": f"股{i}", "amount": 1e8 - i,
             "quality_pct": 0.9 - i / 100} for i in range(30)]
    calls = {}
    def fake_today_list(date, channel, market, days, limit):
        calls["limit"] = limit
        return {"rows": rows, "total": len(rows), "date": "2026-09-18"}
    monkeypatch.setattr("api.server.sm_query.today_list", fake_today_list)
    rec = []
    def fake_record(module, mode, date, items):
        rec.append((module, mode, date, items))
        return len(items)
    monkeypatch.setattr("api.server.bt_tracker.record_list", fake_record)
    monkeypatch.setattr("api.server.bt_tracker.is_default_params",
                        lambda m, p: m == "smart_money" and p.get("limit") == 1000)

    cli = TestClient(app)
    r = cli.get("/api/smart-money/today")
    assert r.status_code == 200
    assert rec and rec[0][0] == "smart_money" and rec[0][1] == "today"
    assert len(rec[0][3]) == 20          # 只记头部前20
    assert rec[0][3][0]["score"] == rows[0]["amount"]
```

- [ ] **Step 2: 跑测试确认失败** Expected: FAIL（未接入 record，rec 为空）

- [ ] **Step 3: 实现** —— 在 `sm_today` 返回前插 record 块

```python
    try:
        _sm_params = {"date": date, "channel": channel, "market": market,
                      "days": days, "limit": limit}
        if bt_tracker.is_default_params("smart_money", _sm_params) and res.get("rows"):
            _heads = [{"code": r.get("code"), "name": r.get("name"),
                       "score": r.get("amount"), "quality_pct": r.get("quality_pct")}
                      for r in res["rows"][:20]]
            bt_tracker.record_list("smart_money", "today", _track_date(), _heads)
    except Exception:
        pass
    return _wrap(res["rows"], {...})   # 原 return 不变
```

- [ ] **Step 4: 跑测试确认通过** Run: `python -m pytest tests/test_tracker_api.py -q` Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add api/server.py tests/test_tracker_api.py
git commit -m "feat(api): /api/smart-money/today 命中默认参数记录头部 top20 追踪"
```

---

### Task 3: `scripts/diagnose_ranking.py` 三清单收益诊断

**Files:**
- Create: `scripts/diagnose_ranking.py`
- Test: `tests/test_diagnose_ranking.py`

**Interfaces:**
- Consumes: `db.query_rows("list_track", ...)`、`backtest.tracker.summary`、`backtest.eval` 分层工具（可选）
- Produces:
  - `head_vs_all(rows, heads=(10, 20)) -> dict`：每日 topN vs 整体 ret_k1/k3/k5 中位数/胜率
  - `factor_deciles(rows, factors) -> dict`：解 `meta_json.factor_scores`，各因子 5 档收益单调性
  - `penetration_layers(rows, keys) -> dict`：`meta_json` 的 `sector_heat`/`mf_phase`/`quality_pct` 分档 vs ret
  - `quality_structure(rows) -> dict`：每日记录数、通过率分布
  - `main(module=None)`：聚合各函数打印报告

- [ ] **Step 1: 写失败测试** —— 纯函数对合成 list_track 行算统计

```python
# tests/test_diagnose_ranking.py
# -*- coding: utf-8 -*-
"""排序因子诊断单测：合成 list_track 行，不触网。
合规：只算历史收益统计事实，不预测不荐股。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
import pytest
from scripts import diagnose_ranking as dr

def _row(rank, score, ret_k1, ret_k3, factor=None, quality_pct=None, sector_heat=None):
    meta = {"factor_scores": factor or {}}
    if quality_pct is not None: meta["quality_pct"] = quality_pct
    if sector_heat is not None: meta["sector_heat"] = sector_heat
    return {"code": f"6{r:05d}", "date": "2026-09-18", "rank": rank, "score": score,
            "ret_k1": ret_k1, "ret_k3": ret_k3, "meta_json": json.dumps(meta)}

ROWS = [
    _row(1, 0.9, 0.05, 0.12, {"mom_5_1": 0.9, "rel_strength": 0.8}, quality_pct=0.8, sector_heat=0.7),
    _row(2, 0.7, 0.03, 0.08, {"mom_5_1": 0.7, "rel_strength": 0.6}, quality_pct=0.6, sector_heat=0.5),
    _row(3, 0.5, 0.01, 0.04, {"mom_5_1": 0.5, "rel_strength": 0.4}, quality_pct=0.4, sector_heat=0.3),
    _row(4, 0.3, -0.01, -0.02, {"mom_5_1": 0.3, "rel_strength": 0.2}, quality_pct=0.2, sector_heat=0.1),
    _row(5, 0.1, -0.03, -0.08, {"mom_5_1": 0.1, "rel_strength": 0.0}, quality_pct=0.0, sector_heat=0.0),
]


def test_head_vs_all():
    """头部 top2 的 ret_k1/k3 中位数应高于整体(合成数据头部更强)。"""
    out = dr.head_vs_all(ROWS, heads=(2,))
    assert out["top2"]["ret_k1"]["median"] > out["all"]["ret_k1"]["median"]


def test_factor_deciles_monotonic():
    """mom_5_1 高分档 ret_k3 中位数应单调递增(合成数据)。"""
    out = dr.factor_deciles(ROWS, ("mom_5_1",))
    d = out["mom_5_1"]["deciles"]
    vals = [v["ret_k3"]["median"] for v in sorted(d.items(), key=lambda kv: kv[0])]
    assert vals == sorted(vals) and vals[0] < vals[-1]


def test_penetration_layers():
    """quality_pct/sector_heat 分档: 高分组 ret_k3 中位数高于低分组。"""
    out = dr.penetration_layers(ROWS, ("quality_pct", "sector_heat"))
    for k in ("quality_pct", "sector_heat"):
        hi = out[k]["high"]["ret_k3"]["median"]
        lo = out[k]["low"]["ret_k3"]["median"]
        assert hi > lo


def test_quality_structure_counts_daily():
    """按 date 统计每日记录数(通过率代理)。"""
    out = dr.quality_structure(ROWS)
    assert out["2026-09-18"]["n"] == 5
```

- [ ] **Step 2: 跑测试确认失败** Run: `python -m pytest tests/test_diagnose_ranking.py -q` Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现** `scripts/diagnose_ranking.py`

```python
# -*- coding: utf-8 -*-
"""排序因子诊断脚本：读 list_track 前视收益，评估三清单排序有效性。
合规：只输出历史收益统计事实，非预测、非荐股、非买卖点。
用法: python -m scripts.diagnose_ranking [module]
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import db


def _load(module: str) -> list[dict]:
    return db.query_rows("list_track", where="module=?", params=(module,),
                         order_by="date ASC, rank ASC", limit=0)


def _median(vals: list) -> float | None:
    s = sorted(v for v in vals if v is not None)
    return s[len(s) // 2] if s else None


def _win_rate(vals: list) -> float | None:
    s = [v for v in vals if v is not None]
    return round(sum(1 for v in s if v > 0) / len(s), 3) if s else None


def _stat(vals: list) -> dict:
    return {"n": sum(1 for v in vals if v is not None),
            "median": _median(vals), "win_rate": _win_rate(vals)}


def _by_date(rows):
    d: dict[str, list] = {}
    for r in rows:
        d.setdefault(r.get("date"), []).append(r)
    return d


def head_vs_all(rows: list[dict], heads=(10, 20)) -> dict:
    """每日按 rank 取 topN 与全体，比 ret_k1/k3/k5 中位数/胜率。"""
    by = _by_date(rows)
    all_v = {k: [r.get(k) for r in rows] for k in ("ret_k1", "ret_k3", "ret_k5")}
    out = {"all": {k: _stat(v) for k, v in all_v.items()}}
    for n in heads:
        hv = {k: [] for k in ("ret_k1", "ret_k3", "ret_k5")}
        for d, rs in by.items():
            top = sorted(rs, key=lambda r: r.get("rank") or 999)[:n]
            for r in top:
                for k in hv:
                    hv[k].append(r.get(k))
        out[f"top{n}"] = {k: _stat(v) for k, v in hv.items()}
    return out


def factor_deciles(rows: list[dict], factors=("mom_5_1", "rel_strength", "sr_10",
                                              "close_vol_corr", "liq_turnover")) -> dict:
    """解 meta_json.factor_scores, 各因子按值 5 档看 ret_k3 单调性。"""
    out = {}
    for f in factors:
        pairs = []
        for r in rows:
            try:
                fs = json.loads(r.get("meta_json") or "{}").get("factor_scores") or {}
            except (ValueError, TypeError):
                fs = {}
            v = fs.get(f)
            if v is not None and r.get("ret_k3") is not None:
                pairs.append((v, r["ret_k3"]))
        if not pairs:
            out[f] = {"deciles": {}}
            continue
        mx = max(p[0] for p in pairs)
        mn = min(p[0] for p in pairs)
        span = (mx - mn) or 1.0
        buckets = {i: [] for i in range(5)}
        for v, ret in pairs:
            idx = min(int((v - mn) / span * 5), 4)
            buckets[idx].append(ret)
        out[f] = {"deciles": {i: _stat(v) for i, v in buckets.items()}}
    return out


def penetration_layers(rows: list[dict], keys=("quality_pct", "sector_heat")) -> dict:
    """meta_json 数值键按高/低两半比 ret_k3 中位数(穿透信号区分度)。"""
    out = {}
    for k in keys:
        pairs = []
        for r in rows:
            try:
                meta = json.loads(r.get("meta_json") or "{}")
            except (ValueError, TypeError):
                meta = {}
            v = meta.get(k)
            if v is not None and r.get("ret_k3") is not None:
                pairs.append((v, r["ret_k3"]))
        if not pairs:
            out[k] = {}
            continue
        pivot = _median([p[0] for p in pairs])
        hi = [p[1] for p in pairs if p[0] > pivot]
        lo = [p[1] for p in pairs if p[0] <= pivot]
        out[k] = {"high": _stat(hi), "low": _stat(lo)}
    return out


def quality_structure(rows: list[dict]) -> dict:
    """按 date 统计每日记录数 + 总数(样本枯竭/通过率代理)。"""
    by = _by_date(rows)
    return {d: {"n": len(rs)} for d, rs in by.items()}


def main(module: str | None = None):
    for m in (module or ("nextday", "quality", "smart_money")):
        rows = _load(m)
        if not rows:
            print(f"\n== {m}: 无追踪样本 ==")
            continue
        print(f"\n== {m} ({len(rows)} 行) ==")
        print("头部 vs 整体:", json.dumps(head_vs_all(rows), ensure_ascii=False))
        if m == "nextday":
            print("五因子分档:", json.dumps(factor_deciles(rows), ensure_ascii=False))
            print("穿透分层:", json.dumps(penetration_layers(rows), ensure_ascii=False))
        elif m == "quality":
            print("通过率结构:", json.dumps(quality_structure(rows), ensure_ascii=False))
        elif m == "smart_money":
            print("穿透分层:", json.dumps(penetration_layers(rows), ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
```

- [ ] **Step 4: 跑测试确认通过** Run: `python -m pytest tests/test_diagnose_ranking.py -q` Expected: PASS

- [ ] **Step 5: 真库冒烟（只读，不触网）**

Run: `python -m scripts.diagnose_ranking` Expected: 打印三清单报告（nextday 有数据，quality 有 14 行，smart_money 暂无/新积累）

- [ ] **Step 6: 提交**

```bash
git add scripts/diagnose_ranking.py tests/test_diagnose_ranking.py
git commit -m "feat(scripts): diagnose_ranking 三清单收益诊断(头部vs整体/因子分档/穿透分层)"
```

---

### Task 4: 文档 + 全量回归

**Files:**
- Modify: `CLAUDE.md`（smart_money 追踪说明 + 检查清单 + scripts 说明）

- [ ] **Step 1: 更新 CLAUDE.md**

在 `screener/smart_money.py` 段补一句：`today_list` 命中默认参数时经 tracker 落 `list_track`(mode=today, 头部 top20, score=净额, meta=quality_pct)；新增 `scripts/diagnose_ranking.py`（只读 `list_track` 前视收益做三清单排序诊断，输出头部 vs 整体/因子分档/穿透分层，不触网不改排序）；改动检查清单补"smart_money 追踪/诊断"条目（沿用 `is_default_params` 只记默认参数、诊断零排序副作用）。

- [ ] **Step 2: 全量回归**

Run: `python -m pytest tests/ -q` Expected: 全绿（含新增 test_tracker/test_tracker_api/test_diagnose_ranking）
Run: `python -m compileall -q backtest/tracker.py api/server.py scripts/diagnose_ranking.py`

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): smart_money 追踪 + diagnose_ranking 诊断脚本说明"
```

---

## Self-Review

- **Spec 覆盖**：A(smart_money 追踪)→Task1+2；B(nextday 收益诊断)→Task3；C(quality 结构诊断)→Task3 `quality_structure`；阶段二（积累后统一诊断）→Task3 脚本已建，待积累后人工重跑，不在本 plan。✓
- **类型一致**：`record_list` 白名单/score/`_meta_payload` 三处在 Task1 同改；`is_default_params` 参数名 `_sm_params` 与 `DEFAULT_PARAMS["smart_money"]` 键对齐；`diagnose_ranking` 各函数名/返回在测试与实现一致。✓
- **占位扫描**：无 TBD/TODO；每步含具体代码。✓

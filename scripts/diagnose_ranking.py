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
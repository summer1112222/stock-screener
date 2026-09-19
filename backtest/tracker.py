# -*- coding: utf-8 -*-
"""清单历史追踪层：把 quality / nextday 每日机械清单落库(list_track)，
幂等回填 T+1 open 买入、T+1+k close 卖出的前视收益，并按市场温度/行业强弱汇总。

合规:历史清单机械追踪统计,非预测,不构成投资建议,盈亏自负。
依赖 list_track 表(见 data/models.SCHEMA_SQL) 与 stock_daily 历史。
"""
from __future__ import annotations

import json
import math
from datetime import datetime

from data import db

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

# 回填容忍：同一行重跑不覆盖非空收益之外字段，filled_ts 幂等标记
_LIST_TABLE = "list_track"

# 模块级：quality 记录 selection_mode；nextday 记录 passed(strict) 与 all(score)
DEFAULT_PARAMS: dict[str, dict] = {
    "quality": {
        "universe": "stock", "days": 20, "min_dims": 2, "min_turnover": 5e7,
        "max_per_board": 3, "max_corr": 0.85, "limit": 10,
        "combo_method": "greedy", "resonance_mode": "greedy", "dim_thresh": 0.7,
        "refine": True, "refine_pool": 50, "strict_quality": True,
        "min_confidence": 0.50, "risk_penalty": True,
    },
    "nextday": {
        "limit": 50, "days": 30, "min_change_pct": 5.0, "min_turnover": 3.0,
        "max_price": 50.0, "min_mv": 10.0, "max_mv": 200.0, "max_pe": 150.0,
        "exclude_st": True,
    },
    "smart_money": {
        "date": None, "channel": None, "market": None, "days": 7, "limit": 1000,
    },
}


def is_default_params(module: str, params: dict) -> bool:
    """仅默认参数组合才记录追踪样本，避免任意参数污染统计。未知模块=不追踪。"""
    base = DEFAULT_PARAMS.get(module)
    if not base:
        return False
    for k, v in base.items():
        got = params.get(k)
        if isinstance(v, float):
            if got is None or abs(float(got) - v) > 1e-9:
                return False
        elif got != v:
            return False
    return True


def _norm_code(code) -> str:
    return str(code)


def _meta_payload(module: str, item: dict) -> str:
    """把 item 里可做摘要的字段压成 JSON 落 meta_json（能源紧凑查询）。"""
    keep: dict = {}
    if module == "quality":
        for k in ("hits", "dim_scores", "confidence_level", "data_confidence"):
            if k in item:
                keep[k] = item.get(k)
    elif module == "smart_money":
        for k in ("quality_pct",):
            if k in item:
                keep[k] = item.get(k)
    else:
        # nextday: 五因子分 + 穿透标注(供 diagnose_ranking 穿透分层用 sector_heat/policy_hit/mf_phase)
        for k in ("factor_scores", "step_status", "hard_pass", "score_coverage",
                  "sector_heat", "policy_hit", "mf_phase", "streak_inflow"):
            if k in item:
                keep[k] = item.get(k)
    try:
        return json.dumps(keep, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


# list_track 规范列(与 SCHEMA_SQL 对齐)。record 用 INSERT OR IGNORE 幂等:
# 同日同 mode 同 code 已存在则跳过,不回填已回填过的收益字段(避免被 record 覆盖清空)。
_LIST_COLS = ("module", "mode", "date", "code", "name", "rank", "score",
              "meta_json", "ret_k1", "ret_k3", "ret_k5", "filled_ts", "ts")


def _insert_ignore(row: dict) -> int:
    """按 UNIQUE(module,mode,date,code) INSERT OR IGNORE 单行;已存在返回 0 不覆盖。"""
    try:
        with db.get_conn() as conn:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {_LIST_TABLE} ({','.join(_LIST_COLS)}) "
                f"VALUES ({','.join('?' * len(_LIST_COLS))})",
                tuple(row.get(c) for c in _LIST_COLS))
            conn.commit()
            return cur.rowcount
    except Exception:
        return 0


def record_list(module: str, mode: str, date: str, items: list[dict]) -> int:
    """记录一批清单条目(同日同 mode 同 code 幂等)。返回写入行数。"""
    if module not in {"quality", "nextday", "smart_money"}:
        return 0
    if not date or not items:
        return 0
    rows = []
    data = (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "")
    for rank, it in enumerate(items, start=1):
        code = _norm_code(it.get("code"))
        if not code:
            continue
        rows.append({
            "module": module, "mode": mode, "date": date, "code": code,
            "name": it.get("name"), "rank": rank,
            "score": _to_f(it.get("score")) if module in ("nextday", "smart_money")
                     else _to_f(it.get("adjusted_resonance")),
            "meta_json": _meta_payload(module, it),
            "ret_k1": None, "ret_k3": None, "ret_k5": None,
            "filled_ts": None, "ts": data[0],
        })
    if not rows:
        return 0
    insert_count = 0
    for r in rows:
        insert_count += _insert_ignore(r)
    return insert_count or len(rows)


def _to_f(v):
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _load_panels(codes: list[str]):
    """返回 (close, open) 两个 DataFrame，列=code，索引=date；缺数据返 None。"""
    if not codes:
        return None, None
    from backtest.signals import _uni_panels
    import pandas as pd
    close, _ = _uni_panels("stock", codes)
    if close is None:
        open_df = None
    else:
        try:
            rows = db.query_rows("stock_daily", limit=0)
            if rows:
                odf = pd.DataFrame(rows)
                odf = odf[odf["symbol"].isin(codes)]
                open_df = odf.pivot_table(index="date", columns="symbol", values="open", aggfunc="first")
            else:
                open_df = None
        except Exception:
            open_df = None
    return close, open_df


def fill_returns(limit: int = 0) -> dict:
    """对 filled_ts IS NULL 的行回填 T+1 open 买入、T+1+k close 卖出收益。
    k=1/3/5；无 T+1 open 时降级 T 日 close 买入并在 meta_json.entry 标注 t_close。
    不自动拉数据(依赖 stock_daily，不足跳过)。返回统计。"""
    rows = db.query_rows(_LIST_TABLE,
                         where='filled_ts IS NULL',
                         order_by="date ASC", limit=limit)
    if not rows:
        return {"scanned": 0, "filled": 0, "skipped": 0, "note": "无待回填行"}
    codes = sorted({str(r.get("code")) for r in rows})
    close, open_df = _load_panels(codes)
    if close is None or close.empty:
        return {"scanned": len(rows), "filled": 0, "skipped": len(rows),
                "note": "stock_daily 无历史，先 /api/backtest/fetch 拉对应 code"}
    filled = 0
    skipped = 0
    for r in rows:
        code = _norm_code(r.get("code"))
        date = str(r.get("date") or "")
        if code not in close.columns:
            skipped += 1
            continue
        entry_ts = None
        entry_open = None
        t_close = None
        try:
            idx = [str(d) for d in close.index]
            if date in idx:
                pos = idx.index(date)
            else:
                past = [i for i, d in enumerate(idx) if d <= date]
                if not past:
                    skipped += 1
                    continue
                pos = past[-1]
            t_close = _to_f(close.iloc[pos][code])
            if open_df is not None and code in open_df.columns:
                open_dates = [str(d) for d in open_df.index]
                if pos + 1 < len(close):
                    entry_ts = idx[pos + 1]
                    if entry_ts in open_dates:
                        entry_open = _to_f(open_df.loc[entry_ts, code])
        except Exception:
            skipped += 1
            continue
        if t_close is None:
            skipped += 1
            continue
        use_open = entry_open is not None and entry_open > 0
        entry = entry_open if use_open else t_close
        if entry is None or entry <= 0:
            skipped += 1
            continue
        meta = {}
        try:
            meta = json.loads(r.get("meta_json") or "{}")
        except Exception:
            meta = {}
        meta["entry"] = ("t+1_open" if use_open else "t_close")
        meta["entry_ts"] = entry_ts if use_open else date
        rets = {}
        for k in (1, 3, 5):
            if pos + k < len(close):
                ck = _to_f(close.iloc[pos + k][code])
                rets[f"ret_k{k}"] = round(ck / entry - 1, 4) if ck else None
            else:
                rets[f"ret_k{k}"] = None
        if rets.get("ret_k1") is None and rets.get("ret_k3") is None and rets.get("ret_k5") is None:
            skipped += 1
            continue
        try:
            db.upsert_rows(_LIST_TABLE, [{
                **{kk: r.get(kk) for kk in ("module", "mode", "date", "code", "name", "rank", "score")},
                "meta_json": json.dumps(meta, ensure_ascii=False),
                "ret_k1": rets.get("ret_k1"), "ret_k3": rets.get("ret_k3"),
                "ret_k5": rets.get("ret_k5"),
                "filled_ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }])
            filled += 1
        except Exception:
            skipped += 1
    return {"scanned": len(rows), "filled": filled, "skipped": skipped,
            "note": "" if filled else "无足够历史回填(先 fetch)"}


def _stat(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    s = sorted(vals)
    mid = len(s) // 2
    med = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
    return {"n": len(s), "mean": round(sum(s) / len(s), 4),
            "median": round(med, 4),
            "win_rate": round(sum(1 for v in s if v > 0) / len(s), 4)}


def _expand_temperature():
    """简化的市场温度代理：涨停 vs 跌停家数、及近期涨跌家数比。
    读 market_daily(仅当日)，列缺失时退化为 None。"""
    try:
        md = db.query_rows("market_daily", where="date=(SELECT MAX(date) FROM market_daily)",
                           limit=1)
        if not md:
            return None
        r = md[0]
        zt = _to_f(r.get("zt_count"))
        dt = _to_f(r.get("dt_count"))
        up = _to_f(r.get("up_count"))
        down = _to_f(r.get("down_count"))
        if zt is None or dt is None:
            return None
        ratio = zt / dt if dt else (None if zt is None else (999.0 if zt else None))
        temp = "强" if zt and (not dt or zt >= dt) else ("弱" if dt and (not zt or dt > zt) else "中性")
        return {"zt": zt, "dt": dt, "ratio": ratio if ratio is not None else (999.0 if zt else None),
                "label": temp}
    except Exception:
        return None


def summary(module: str | None = None, mode: str | None = None) -> dict:
    """聚合 list_track 各 k 收益统计，并按模块/mode/市场温度/行业强弱分层。"""
    where = []
    params = []
    if module:
        where.append("module = ?")
        params.append(module)
    if mode:
        where.append("mode = ?")
        params.append(mode)
    w = " AND ".join(where) if where else ""
    rows = db.query_rows(_LIST_TABLE, where=w, params=tuple(params),
                         order_by="date ASC", limit=0)
    filled = [r for r in rows if r.get("ret_k1") is not None]
    overall = {f"k{k}": _stat([_to_f(r.get(f"ret_k{k}")) for r in filled])
               for k in (1, 3, 5)}
    by_temp: dict[str, dict] = {}
    temp = _expand_temperature()
    label = temp.get("label") if temp else "unknown"
    for it in filled:
        bucket = by_temp.setdefault(label, {"n": 0, "rows": []})
        bucket["n"] += 1
        bucket["rows"].append(it)
    by_temp_out = {}
    for b, v in by_temp.items():
        by_temp_out[b] = {f"k{k}": _stat([_to_f(x.get(f"ret_k{k}")) for x in v["rows"]])
                          for k in (1, 3, 5)}
    # 行业分层只读 stock_spot.board；没有板块字段时诚实归入 unknown，不触网补数据。
    by_industry: dict[str, list[dict]] = {}
    try:
        spot_rows = db.query_rows("stock_spot", limit=0) or []
        board_map = {str(x.get("code")): str(x.get("board") or "unknown")
                     for x in spot_rows}
    except Exception:
        board_map = {}
    for it in filled:
        board = board_map.get(str(it.get("code")), "unknown")
        by_industry.setdefault(board, []).append(it)
    by_industry_out = {
        board: {f"k{k}": _stat([_to_f(x.get(f"ret_k{k}")) for x in items])
                for k in (1, 3, 5)}
        for board, items in by_industry.items()
    }
    return {
        "module": module, "mode": mode, "total": len(rows), "filled": len(filled),
        "track_days": len({str(r.get("date")) for r in rows}),
        "k": overall, "by_temperature": by_temp_out, "by_industry": by_industry_out,
        "temperature_asof": temp,
        "note": "" if filled else "暂无已回填样本(先 /api/track/fill)",
    }
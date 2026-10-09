# -*- coding: utf-8 -*-
"""筛选引擎：从 SQLite 读数据 → 板块合并资金流 → 条件 AND 过滤 → 排序 → 截断。

合规：只做阈值/排名过滤，不输出买卖信号、不评级、不喊买卖点。输出仅为候选列表，
调用方必须自行附免责声明。
"""
from __future__ import annotations

import pandas as pd

from data import db
from .conditions import VALID_OPS


# 派生因子(纯筛选维度，不打分不荐股)：查询时实时计算，不落库。
#   activity   = 换手率 × |涨跌幅|            活跃度
#   momentum   = 涨跌幅 × 换手率               动量(带符号)
#   strength   = 涨跌幅 ÷ 振幅                 强弱比(振幅为0时缺失)
#   liquidity  = 成交额 ÷ 流通市值 × 100        资金比(%)
#   inflow_pct = 主力净流入 ÷ 成交额 × 100      主力净流入占比(%)
DERIVED_FIELDS = {
    "activity": ("turnover_rate", "change_pct"),
    "momentum": ("turnover_rate", "change_pct"),
    "strength": ("change_pct", "amplitude"),
    "liquidity": ("turnover_amount", "circulating_market_cap"),
    "inflow_pct": ("main_net_inflow", "turnover_amount"),
}


def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """补算派生因子列，使派生字段可被过滤/排序。缺依赖列则该因子缺失。"""
    if df is None or df.empty:
        return df
    df = df.copy()
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df.columns else None
    tr, cp = num("turnover_rate"), num("change_pct")
    if tr is not None and cp is not None:
        df["activity"] = tr * cp.abs()
        df["momentum"] = tr * cp
    am = num("amplitude")
    if cp is not None and am is not None:
        df["strength"] = cp / am.where(am > 0)
    amt, mc = num("turnover_amount"), num("circulating_market_cap")
    if amt is not None and mc is not None:
        df["liquidity"] = amt / mc.where(mc > 0) * 100
    fl = num("main_net_inflow")
    if fl is not None and amt is not None:
        df["inflow_pct"] = fl / amt.where(amt > 0) * 100
    return df


def _apply_conditions(df: pd.DataFrame,
                      conditions: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    """对 df 依次应用非 topn 条件(AND)；返回 (过滤后 df, 被跳过的条件原因)。"""
    skipped: list[str] = []
    if df.empty:
        return df, skipped

    topn_conds = []
    for c in conditions:
        field = c.get("field")
        op = c.get("op")
        value = c.get("value")
        if op not in VALID_OPS:
            skipped.append(f"未知op: {op}")
            continue
        if field not in df.columns:
            skipped.append(f"字段不存在(可能数据源失败): {field}")
            continue
        if op in ("topn", "topn_asc"):
            topn_conds.append(c)
            continue
        col = pd.to_numeric(df[field], errors="coerce")
        if op == "gt":
            df = df[col > value]
        elif op == "gte":
            df = df[col >= value]
        elif op == "lt":
            df = df[col < value]
        elif op == "lte":
            df = df[col <= value]
        elif op == "eq":
            df = df[col == value]
        elif op == "ne":
            df = df[col != value]
        elif op == "between":
            if not (isinstance(value, (list, tuple)) and len(value) == 2):
                skipped.append(f"between 需 [lo,hi] 二元组，收到: {value!r}")
                continue
            lo, hi = value
            df = df[(col >= lo) & (col <= hi)]

    # topn/topn_asc 在其它过滤之后应用：按该字段降序/升序取前 N
    for c in topn_conds:
        field = c["field"]
        n = int(c["value"])
        ascending = (c.get("op") == "topn_asc")
        df = df.sort_values(field, ascending=ascending,
                            na_position="last").head(n)

    return df, skipped


def _sort_df(df: pd.DataFrame, sort: str | None,
             asc: bool = False) -> pd.DataFrame:
    if not sort or df.empty or sort not in df.columns:
        return df
    return df.sort_values(sort, ascending=asc, na_position="last")


def filter_boards(category: str = "行业",
                  conditions: list | None = None,
                  sort: str | None = "main_net_inflow",
                  asc: bool = False,
                  limit: int = 50,
                  indicator: str = "今日") -> dict:
    """筛选板块(行业/概念)：合并板块表 + 资金流表，按条件过滤。
    返回 {rows: [...], total: int, skipped: [...], category, indicator}。"""
    conditions = conditions or []
    table = "industry_board" if category == "行业" else "concept_board"

    boards = db.query_rows(table)
    flow = db.query_rows(
        "sector_fund_flow",
        where="sector_type=? AND indicator=?",
        params=(category, indicator),
    )

    if not boards:
        return {"rows": [], "total": 0, "skipped": ["板块数据为空，先 /api/refresh"],
                "category": category, "indicator": indicator}

    df = pd.DataFrame(boards)
    if flow:
        fdf = pd.DataFrame(flow)
        # 资金流表与板块表都有 name，合并
        fdf = fdf[["name", "main_net_inflow", "super_large_net",
                   "large_net", "medium_net", "small_net"]
                  ].drop_duplicates("name")
        df = df.merge(fdf, on="name", how="left")

    df = _add_derived(df)
    df, skipped = _apply_conditions(df, conditions)
    df = _sort_df(df, sort, asc)
    if limit:
        df = df.head(int(limit))

    rows = df.astype(object).where(pd.notna(df), None).to_dict("records")
    return {"rows": rows, "total": len(rows),
            "skipped": skipped, "category": category, "indicator": indicator}


def filter_etfs(conditions: list | None = None,
                sort: str | None = "turnover_amount",
                asc: bool = False,
                limit: int = 50) -> dict:
    """筛选 ETF：从 etf_spot 表按条件过滤。"""
    conditions = conditions or []
    etfs = db.query_rows("etf_spot")
    if not etfs:
        return {"rows": [], "total": 0, "skipped": ["ETF数据为空，先 /api/refresh"]}
    df = pd.DataFrame(etfs)
    df = _add_derived(df)
    df, skipped = _apply_conditions(df, conditions)
    df = _sort_df(df, sort, asc)
    if limit:
        df = df.head(int(limit))
    rows = df.astype(object).where(pd.notna(df), None).to_dict("records")
    return {"rows": rows, "total": len(rows), "skipped": skipped}


def _tradable_stocks(df: pd.DataFrame, min_turnover: float,
                     limit_pct: float) -> pd.DataFrame:
    """个股可交易预筛：排除 ST/停牌/涨停/低成交额。limit_pct=9.9 对科创/创业/北交误杀，已知。"""
    if df is None or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if "name" in df.columns:
        mask &= ~df["name"].astype(str).str.contains("ST", case=False, na=False)
    if "latest_price" in df.columns:
        lp = pd.to_numeric(df["latest_price"], errors="coerce")
        mask &= lp.notna() & (lp > 0)
    if "turnover_amount" in df.columns:
        mask &= pd.to_numeric(df["turnover_amount"], errors="coerce").fillna(0) >= min_turnover
    if "change_pct" in df.columns:
        mask &= pd.to_numeric(df["change_pct"], errors="coerce").fillna(-99) < limit_pct
    return df[mask]


#: live 增强只覆盖截断后头部（get_quote 批量 80/批，全市场 5000+ 逐个取太慢）。
LIVE_ENRICH_K = 100


def _live_quote_map(codes: list[str]) -> dict:
    """批量取 tdx 实时盘口，返 {纯6位code: quote}；失败返 {} 诚实降级。"""
    pure = list(dict.fromkeys(str(c or "")[-6:] for c in codes if c))
    pure = [c for c in pure if len(c) == 6 and c.isdigit()]
    if not pure:
        return {}
    try:
        from data import pytdx_client
        out = {}
        for q in pytdx_client.get_quote(pure) or []:
            code = str(q.get("code") or "")[-6:]
            if code:
                out[code] = q
        return out
    except Exception:
        return {}


def _bid_ask_imbalance(q: dict) -> float | None:
    """五档失衡 = (买1-5总量-卖1-5总量)/(买+卖总量)，∈[-1,1]；缺值返 None。"""
    try:
        bv = sum(float(q.get(f"bid_vol{i}") or 0) for i in range(1, 6))
        av = sum(float(q.get(f"ask_vol{i}") or 0) for i in range(1, 6))
        tot = bv + av
        if tot <= 0:
            return None
        return round((bv - av) / tot, 4)
    except (TypeError, ValueError):
        return None


def _enrich_live(rows: list[dict]) -> tuple[list[dict], str]:
    """对截断后行批量盘口增强：新增 live_price/live_change_pct/live_imbalance/live_source。

    盘后/tdx 失败 → 行保持快照值 + live_source=snapshot 诚实标注。返回 (rows, live_source)。
    """
    if not rows:
        return rows, "snapshot"
    qmap = _live_quote_map([r.get("code") for r in rows])
    if not qmap:
        for r in rows:
            r["live_source"] = "snapshot"
        return rows, "snapshot"
    n_hit = 0
    for r in rows:
        code = str(r.get("code") or "")[-6:]
        q = qmap.get(code)
        if not q:
            r["live_source"] = "snapshot"
            continue
        n_hit += 1
        r["live_source"] = "live"
        try:
            price = q.get("price")
            if price is not None:
                r["live_price"] = float(price)
            lc = q.get("last_close")
            if price is not None and lc:
                r["live_change_pct"] = round((float(price) / float(lc) - 1.0) * 100.0, 2)
        except (TypeError, ValueError):
            pass
        imb = _bid_ask_imbalance(q)
        if imb is not None:
            r["live_imbalance"] = imb
    return rows, ("live" if n_hit else "snapshot")


def filter_stocks(conditions: list | None = None,
                  sort: str | None = "turnover_amount",
                  asc: bool = False,
                  limit: int = 50,
                  min_turnover: float = 5e7,
                  limit_pct: float = 9.9,
                  live: bool = False) -> dict:
    """筛选个股：stock_spot → 可交易预筛 → 条件 AND → 排序 → 截断 → 可选盘口实时增强。

    live=True 时对截断后头部批量 get_quote，新增 live_price/live_change_pct/
    live_imbalance + live_source 行字段（缺值 None 不伪造）；失败自动降级快照。
    返回结构与 filter_etfs 一致：{rows, total, skipped, category, live_source}。"""
    conditions = conditions or []
    rows = db.query_rows("stock_spot")
    if not rows:
        return {"rows": [], "total": 0, "skipped": ["个股数据为空，先 /api/refresh"],
                "category": "个股", "live_source": "snapshot"}
    df = pd.DataFrame(rows)
    df = _tradable_stocks(df, min_turnover, limit_pct)
    df = _add_derived(df)
    df, skipped = _apply_conditions(df, conditions)
    df = _sort_df(df, sort, asc)
    if limit:
        df = df.head(int(limit))
    out = df.astype(object).where(pd.notna(df), None).to_dict("records")
    live_source = "snapshot"
    if live and out:
        head = out[:LIVE_ENRICH_K]
        _, live_source = _enrich_live(head)
        for r in out[len(head):]:
            r["live_source"] = "snapshot"
    return {"rows": out, "total": len(out), "skipped": skipped,
            "category": "个股", "live_source": live_source}


def list_boards(category: str = "行业",
                sort: str | None = "change_pct",
                asc: bool = False,
                limit: int = 20) -> list[dict]:
    """无条件下列出板块排名(供 /api/boards 简单查看)。"""
    return filter_boards(category=category, conditions=[], sort=sort,
                         asc=asc, limit=limit)["rows"]


def list_etfs(sort: str | None = "turnover_amount",
              asc: bool = False,
              limit: int = 30) -> list[dict]:
    return filter_etfs(conditions=[], sort=sort, asc=asc, limit=limit)["rows"]

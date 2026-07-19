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
#   activity = 换手率 × |涨跌幅|   活跃度
#   momentum = 涨跌幅 × 换手率      动量(带符号)
DERIVED_FIELDS = {
    "activity": ("turnover_rate", "change_pct", lambda tr, cp: tr * cp.abs()),
    "momentum": ("turnover_rate", "change_pct", lambda tr, cp: tr * cp),
}


def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """补算派生因子列，使派生字段可被过滤/排序。缺依赖列则置 NaN。"""
    if df is None or df.empty:
        return df
    tr = pd.to_numeric(df.get("turnover_rate"), errors="coerce") \
        if "turnover_rate" in df.columns else None
    cp = pd.to_numeric(df.get("change_pct"), errors="coerce") \
        if "change_pct" in df.columns else None
    if tr is None or cp is None:
        return df
    df = df.copy()
    for name, (_, _, fn) in DERIVED_FIELDS.items():
        df[name] = fn(tr, cp)
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


def filter_stocks(conditions: list | None = None,
                  sort: str | None = "turnover_amount",
                  asc: bool = False,
                  limit: int = 50,
                  min_turnover: float = 5e7,
                  limit_pct: float = 9.9) -> dict:
    """筛选个股：stock_spot → 可交易预筛 → 条件 AND → 排序 → 截断。
    返回结构与 filter_etfs 一致：{rows, total, skipped, category}。"""
    conditions = conditions or []
    rows = db.query_rows("stock_spot")
    if not rows:
        return {"rows": [], "total": 0, "skipped": ["个股数据为空，先 /api/refresh"],
                "category": "个股"}
    df = pd.DataFrame(rows)
    df = _tradable_stocks(df, min_turnover, limit_pct)
    df = _add_derived(df)
    df, skipped = _apply_conditions(df, conditions)
    df = _sort_df(df, sort, asc)
    if limit:
        df = df.head(int(limit))
    out = df.astype(object).where(pd.notna(df), None).to_dict("records")
    return {"rows": out, "total": len(out), "skipped": skipped, "category": "个股"}


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

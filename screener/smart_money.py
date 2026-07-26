# -*- coding: utf-8 -*-
"""主力动向查询/聚合层（不触网，纯 db.query_rows）。

合规：输出"主力动向观察清单/机械归类"，不荐股、不输出买卖点、不承诺收益。
依赖 smart_money_action 表（由 data/smart_money.refresh_today 写入）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from data import db, smart_money as sm_data

_NATIONAL_TEAM_KEYWORD = "国家队"


def _expand_national_team() -> list[str]:
    return list(sm_data.NATIONAL_TEAM)


def today_list(date: str | None = None, channel: str | None = None,
               market: str | None = None) -> dict:
    """用法 A：某日主力动向清单，按 amount 降序。
    date 省略时默认取表内最新日期，避免返回全表（数万行）拖慢加载。"""
    where, params = [], []
    if not date:
        try:
            latest = db.query_rows("smart_money_action",
                                   order_by="date DESC", limit=1)
            if latest:
                date = latest[0].get("date")
        except Exception:
            pass
    if date:
        where.append("date = ?"); params.append(date)
    if channel:
        where.append("channel = ?"); params.append(channel)
    if market:
        where.append("market = ?"); params.append(market)
    w = " AND ".join(where) if where else ""
    rows = db.query_rows("smart_money_action", where=w, params=tuple(params),
                         order_by="amount DESC", limit=0)
    return {"rows": rows, "total": len(rows), "date": date}


def by_actor(actor: str, days: int = 30) -> dict:
    """用法 B：某席位/股东 N 日内动向记录 + 汇总。
    actor 传席位名/股东名子串，或保留词"国家队"（展开为 LIKE 多名匹配）。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.query_rows("smart_money_action",
                         where="date >= ?", params=(since,),
                         order_by="date DESC", limit=0)
    if actor == _NATIONAL_TEAM_KEYWORD:
        keys = _expand_national_team()
        filtered = [r for r in rows if r.get("actor")
                    and any(k in r["actor"] for k in keys)]
    else:
        filtered = [r for r in rows if r.get("actor") and actor in r["actor"]]
    amt = 0.0
    for r in filtered:
        a = r.get("amount")
        if a is not None:
            amt += a
    return {"rows": filtered,
            "summary": {"出现次数": len(filtered), "累计净额": amt}}


def top_by_amount(days: int = 5, market: str | None = None,
                  channel: str | None = None, limit: int = 30) -> dict:
    """用法 C：按 N 日累计主力净额排序的观察池（group by code 降序）。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where, params = ["date >= ?"], [since]
    if market:
        where.append("market = ?"); params.append(market)
    if channel:
        where.append("channel = ?"); params.append(channel)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="", limit=0)
    agg: dict[str, dict] = {}
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        cur = agg.setdefault(code, {"code": code, "name": r.get("name"),
                                    "market": r.get("market"),
                                    "amount": 0.0, "count": 0})
        a = r.get("amount")
        if a is not None:
            cur["amount"] += a
        cur["count"] += 1
    pool = sorted(agg.values(), key=lambda x: x["amount"], reverse=True)[:limit]
    return {"rows": pool, "total": len(pool)}


def unlock_by_month(month: str | None = None, code: str | None = None) -> dict:
    """限售解禁按 as_of 月份查(channel=限售解禁)。
    month 形如 2026-07;不传取当月。code 非空则再按 code 过滤。as_of 升序。

    合规:主力动向观察清单,机械归类,非荐股非买卖信号。"""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    where, params = ["channel = ?", "as_of LIKE ?"], ["限售解禁", f"{month}%"]
    if code:
        where.append("code = ?")
        params.append(code)
    rows = db.query_rows("smart_money_action", where=" AND ".join(where),
                         params=tuple(params), order_by="as_of ASC", limit=0)
    total_amt = 0.0
    for r in rows:
        a = r.get("amount")
        if a is not None:
            total_amt += a
    return {"rows": rows, "total": len(rows),
            "month": month, "total_amount": total_amt}

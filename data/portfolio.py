# -*- coding: utf-8 -*-
"""持仓跟踪：本地记录买入，按 spot 最新价算浮盈。

个人本地用。表 portfolio(id, code, name, buy_date, buy_price, shares, note, ts)。
"""
from __future__ import annotations

from datetime import datetime

from . import db


def add_position(code: str, name: str, buy_date: str, buy_price: float,
                 shares: float, note: str = "") -> dict:
    pos = {"code": code, "name": name, "buy_date": buy_date,
           "buy_price": float(buy_price), "shares": float(shares),
           "note": note, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO portfolio(code,name,buy_date,buy_price,shares,note,ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (pos["code"], pos["name"], pos["buy_date"], pos["buy_price"],
             pos["shares"], pos["note"], pos["ts"]),
        )
        conn.commit()
    return pos


def list_positions() -> list[dict]:
    """列持仓 + 按 stock_spot 最新价算浮盈/收益率。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id,code,name,buy_date,buy_price,shares,note,ts FROM portfolio "
            "ORDER BY buy_date DESC"
        ).fetchall()
    if not rows:
        return []
    codes = list({r["code"] for r in rows})
    spot = {}
    if codes:
        ph = ",".join("?" * len(codes))
        with db.get_conn() as conn:
            # 个股与ETF spot 都查，取最新价
            for tbl in ("stock_spot", "etf_spot"):
                try:
                    sr = conn.execute(
                        f"SELECT code, latest_price FROM {tbl} WHERE code IN ({ph})",
                        codes).fetchall()
                except Exception:
                    sr = []
                for x in sr:
                    if x["latest_price"] is not None and x["code"] not in spot:
                        spot[x["code"]] = x["latest_price"]
    out = []
    for r in rows:
        lp = spot.get(r["code"])
        cost = r["buy_price"] or 0
        shares = r["shares"] or 0
        pnl = (lp - cost) * shares if lp else None
        pct = (lp / cost - 1) if (lp and cost) else None
        out.append({
            "id": r["id"], "code": r["code"], "name": r["name"],
            "buy_date": r["buy_date"], "buy_price": cost, "shares": shares,
            "note": r["note"], "ts": r["ts"],
            "latest_price": lp, "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pct, 4) if pct is not None else None,
        })
    return out


def close_position(pid: int) -> bool:
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM portfolio WHERE id=?", (pid,))
        conn.commit()
        return cur.rowcount > 0

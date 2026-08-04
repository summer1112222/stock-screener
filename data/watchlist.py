# -*- coding: utf-8 -*-
"""自选股(观察清单)：本地记录跟踪标的，按 spot 最新价显示现价。

个人本地用。区别于 portfolio(已买入持仓)：watchlist 是未买入、仅观察。
表 watchlist(id, code, name, note, added_ts)。
机械观察清单，非荐股非买卖信号。
"""
from __future__ import annotations

from datetime import datetime

from . import db


def add(code: str, name: str = "", note: str = "") -> dict:
    """加入自选；同 code 已存在则更新 name/note(不重复插入)。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        cur = conn.execute("SELECT id FROM watchlist WHERE code=?", (code,)).fetchone()
        if cur:
            conn.execute(
                "UPDATE watchlist SET name=COALESCE(NULLIF(?, ''), name), "
                "note=COALESCE(NULLIF(?, ''), note), added_ts=? WHERE id=?",
                (name, note, ts, cur["id"]))
            conn.commit()
            return {"id": cur["id"], "code": code, "name": name, "note": note, "added_ts": ts}
        cur = conn.execute(
            "INSERT INTO watchlist(code,name,note,added_ts) VALUES(?,?,?,?)",
            (code, name, note, ts))
        conn.commit()
        return {"id": cur.lastrowid, "code": code, "name": name, "note": note, "added_ts": ts}


def list_items() -> list[dict]:
    """列自选 + 按 stock_spot/etf_spot 最新价显示现价。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id,code,name,note,added_ts FROM watchlist ORDER BY id DESC"
        ).fetchall()
    if not rows:
        return []
    codes = list({r["code"] for r in rows})
    spot = {}
    if codes:
        ph = ",".join("?" * len(codes))
        with db.get_conn() as conn:
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
    return [{
        "id": r["id"], "code": r["code"], "name": r["name"],
        "note": r["note"], "added_ts": r["added_ts"],
        "latest_price": spot.get(r["code"]),
    } for r in rows]


def remove(wid: int) -> bool:
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))
        conn.commit()
        return cur.rowcount > 0

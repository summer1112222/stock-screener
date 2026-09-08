"""因子快照读写。

因子计算结果落库为 (factor_name, factor_version, code, date, value)，支持按
唯一键覆盖（避免同口径重复堆积），预留 params_json 保留计算参数便于追溯。
本模块只记录机械计算结果，不输出投资建议、买卖信号或收益承诺。
"""

from __future__ import annotations

from datetime import datetime
import json

from data import db

_CHUNK = 500
#: 单个 IN 子句可承载的变量数上限(低于 SQLite ~999 变量上限)，超大 code 集分批。
_IN_BATCH = 900


def _row_to_snap(row: dict) -> dict:
    snap = dict(row)
    if snap.get("params_json"):
        try:
            snap["params"] = json.loads(snap["params_json"])
        except (ValueError, TypeError):
            snap["params"] = None  # 单条损坏参数不阻断整批读
    return snap


def write_snapshots(
    factor_name: str,
    factor_version: str,
    items: list[dict],
) -> int:
    """批量写快照。每项 code/date/value 必填，params 可选（存为 params_json）。
    相同 factor_name+factor_version+code+date 覆盖。"""
    rows = []
    now = datetime.now().isoformat(timespec="seconds")
    for it in items:
        rows.append({
            "factor_name": factor_name,
            "factor_version": factor_version,
            "code": it["code"],
            "date": it["date"],
            "value": it.get("value"),
            "params_json": json.dumps(it.get("params"), ensure_ascii=False) if it.get("params") is not None else None,
            "created_ts": now,
        })
    written = 0
    for i in range(0, len(rows), _CHUNK):
        written += db.upsert_rows("factor_snapshot", rows[i:i + _CHUNK])
    return written


def read_snapshots(
    factor_name: str,
    factor_version: str,
    codes: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """读快照，默认按 code, date 排序；可选按代码集合与日期区间过滤。"""
    where = "factor_name=? AND factor_version=?"
    params: list = [factor_name, factor_version]
    if codes:
        ph = ",".join("?" * len(codes))
        where += f" AND code IN ({ph})"
        params.extend(codes)
    if start:
        where += " AND date>=?"
        params.append(start)
    if end:
        where += " AND date<=?"
        params.append(end)
    if codes and len(codes) > _IN_BATCH:
        # 超大 code 集分批查询合并，避免超 SQLite 变量上限整体报错
        rows: list[dict] = []
        for i in range(0, len(codes), _IN_BATCH):
            chunk = codes[i:i + _IN_BATCH]
            ph = ",".join("?" * len(chunk))
            w = f"factor_name=? AND factor_version=? AND code IN ({ph})"
            p: list = [factor_name, factor_version, *chunk]
            if start:
                w += " AND date>=?"
                p.append(start)
            if end:
                w += " AND date<=?"
                p.append(end)
            rows.extend(db.query_rows("factor_snapshot", where=w, params=tuple(p)))
        rows.sort(key=lambda r: (str(r.get("code")), str(r.get("date"))))
    else:
        rows = db.query_rows(
            "factor_snapshot",
            where=where,
            params=tuple(params),
            order_by="code ASC, date ASC",
        )
    return [_row_to_snap(r) for r in rows]
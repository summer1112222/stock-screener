"""因子定义注册表。

定义与计算结果分离：定义保存口径、版本和来源元数据，快照层只保存具体
code×date 数值，便于研究和线上排序复用同一因子口径。
本模块仅服务历史研究与机械排序，不构成投资建议、买卖信号或收益承诺。
"""

from __future__ import annotations

from datetime import datetime
import json

from data import db


def register_factor(
    name: str,
    version: str,
    description: str = "",
    frequency: str = "1d",
    source_fields: list[str] | None = None,
    formula: str = "",
) -> dict:
    """注册或更新因子定义，按 factor_name 幂等。"""
    row = {
        "factor_name": name,
        "version": version,
        "description": description,
        "frequency": frequency,
        "source_fields": json.dumps(source_fields or [], ensure_ascii=False),
        "formula": formula,
        "created_ts": datetime.now().isoformat(timespec="seconds"),
    }
    db.upsert_rows("factor_definition", [row])
    return row


def get_factor(name: str) -> dict | None:
    """读取一个因子当前注册定义。"""
    rows = db.query_rows("factor_definition", where="factor_name=?", params=(name,), limit=1)
    return rows[0] if rows else None


def list_factors() -> list[dict]:
    """列出全部已注册因子。"""
    return db.query_rows("factor_definition", order_by="factor_name ASC")

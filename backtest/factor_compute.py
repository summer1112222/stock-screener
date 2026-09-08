"""因子计算与快照桥接。

提供把历史面板(close 等) 转成因子快照行的纯计算入口，供研究(IC/分层)与
快照写入共用同一口径。计算函数只做 pandas 纯计算，不触库；写快照由
factor_snapshot 负责。
本模块仅用于历史研究与机械因子计算，不构成投资建议、买卖信号或收益承诺。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def series_to_rows(panel: pd.DataFrame, factor_name: str, version: str) -> list[dict]:
    """把以 code 为列、date 为索引的因子面板转成快照长表行。

    空值(NaN)行被跳过，不写入快照，避免污染覆盖语义。
    """
    rows: list[dict] = []
    for code in panel.columns:
        series = panel[code]
        for date_val, value in series.items():
            if pd.isna(value):
                continue
            rows.append({
                "code": str(code),
                "date": str(date_val),
                "value": float(value),
                "params": {"window": 5},
            })
    return rows


def compute_mom_5_1(close: pd.DataFrame) -> pd.DataFrame:
    """mom_5_1：5 日动量，close / close.shift(5) - 1。

    与 nextday 线上口径(最新收盘相对 5 日前收盘比价)单调一致，rank 顺序等价。
    返回与输入同形状的 DataFrame，前 5 行为 NaN。
    """
    return close / close.shift(5) - 1

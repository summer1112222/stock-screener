# -*- coding: utf-8 -*-
"""统一的次日开盘成交模拟器。

仅用于历史研究，不构成实时交易或投资建议。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_bps: float = 8.0
    commission_bps: float = 3.0
    stamp_tax_bps: float = 5.0
    transfer_fee_bps: float = 0.0


def _base_result(signal_date: Any, execution_date: Any) -> dict:
    return {
        "signal_date": str(signal_date),
        "execution_date": str(execution_date),
        "filled": False,
        "fill_price": None,
        "quantity": 0,
        "cost": 0.0,
        "reason": None,
        "blocked_reason": None,
    }


def _open(bar: dict) -> float | None:
    value = bar.get("open")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _limit_state(bar: dict, limit_pct: float) -> str | None:
    prev = bar.get("prev_close")
    opening = _open(bar)
    try:
        prev, opening = float(prev), float(opening)
    except (TypeError, ValueError):
        return None
    if prev <= 0 or opening <= 0:
        return None
    if opening >= prev * (1 + limit_pct - 1e-8):
        return "up"
    if opening <= prev * (1 - limit_pct + 1e-8):
        return "down"
    return None


def execute_entry(signal_date: Any, execution_date: Any, bar: dict,
                  config: ExecutionConfig | None = None,
                  quantity: int = 1, limit_pct: float = 0.10) -> dict:
    cfg = config or ExecutionConfig()
    result = _base_result(signal_date, execution_date)
    opening = _open(bar)
    if opening is None:
        result["blocked_reason"] = "missing_open"
        return result
    if _limit_state(bar, limit_pct) == "up":
        result["blocked_reason"] = "limit_up_not_filled"
        return result
    qty = max(0, int(quantity))
    if qty == 0:
        result["blocked_reason"] = "zero_quantity"
        return result
    fill_price = round(opening * (1 + max(0.0, cfg.slippage_bps) / 10000), 6)
    result.update({"filled": True, "fill_price": fill_price,
                   "quantity": qty, "reason": "next_open"})
    result["cost"] = round(fill_price * qty * max(0.0, cfg.commission_bps + cfg.transfer_fee_bps) / 10000, 6)
    return result


def execute_exit(signal_date: Any, execution_date: Any, bar: dict,
                 config: ExecutionConfig | None = None,
                 quantity: int = 1, limit_pct: float = 0.10) -> dict:
    cfg = config or ExecutionConfig()
    result = _base_result(signal_date, execution_date)
    opening = _open(bar)
    if opening is None:
        result["blocked_reason"] = "missing_open"
        return result
    if _limit_state(bar, limit_pct) == "down":
        result["blocked_reason"] = "limit_down_exit_blocked"
        return result
    qty = max(0, int(quantity))
    if qty == 0:
        result["blocked_reason"] = "zero_quantity"
        return result
    fill_price = round(opening * (1 - max(0.0, cfg.slippage_bps) / 10000), 6)
    result.update({"filled": True, "fill_price": fill_price,
                   "quantity": qty, "reason": "next_open"})
    rate = max(0.0, cfg.commission_bps + cfg.stamp_tax_bps + cfg.transfer_fee_bps)
    result["cost"] = round(fill_price * qty * rate / 10000, 6)
    return result

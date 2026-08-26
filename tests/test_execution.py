# -*- coding: utf-8 -*-
import pandas as pd

from backtest.execution import ExecutionConfig, execute_entry, execute_exit


def test_entry_fills_at_next_open_with_buy_slippage():
    bar = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
    fill = execute_entry("2026-08-21", "2026-08-22", bar, ExecutionConfig(slippage_bps=10))
    assert fill["filled"] is True
    assert fill["fill_price"] == 10.01
    assert fill["reason"] == "next_open"


def test_entry_rejects_limit_up_open():
    bar = {"open": 11.0, "high": 11.0, "low": 10.0, "close": 11.0, "prev_close": 10.0}
    fill = execute_entry("2026-08-21", "2026-08-22", bar, ExecutionConfig(), limit_pct=0.10)
    assert fill["filled"] is False
    assert fill["blocked_reason"] == "limit_up_not_filled"


def test_exit_applies_sell_costs_and_slippage():
    bar = {"open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1}
    cfg = ExecutionConfig(slippage_bps=10, commission_bps=3, stamp_tax_bps=5)
    fill = execute_exit("2026-08-21", "2026-08-22", bar, cfg, quantity=100)
    assert fill["filled"] is True
    assert fill["fill_price"] == 11.988
    assert round(fill["cost"], 4) == round(11.988 * 100 * 0.0008, 4)


def test_exit_marks_limit_down_as_blocked():
    bar = {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "prev_close": 10.0}
    fill = execute_exit("2026-08-21", "2026-08-22", bar, ExecutionConfig(), limit_pct=0.10)
    assert fill["filled"] is False
    assert fill["blocked_reason"] == "limit_down_exit_blocked"


def test_entry_zero_quantity_returns_blocked():
    bar = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
    fill = execute_entry("2026-08-21", "2026-08-22", bar, quantity=0)
    assert fill["filled"] is False
    assert fill["fill_price"] is None
    assert fill["blocked_reason"] == "zero_quantity"


def test_entry_missing_open_inputs():
    bar = {"high": 10.5, "low": 9.8, "close": 10.2}
    fill = execute_entry("2026-08-21", "2026-08-22", bar)
    assert fill["filled"] is False
    assert fill["blocked_reason"] == "missing_open"


def test_entry_negative_open_treated_as_missing():
    bar = {"open": -1.0, "high": 10.5, "low": 9.8, "close": 10.2}
    fill = execute_entry("2026-08-21", "2026-08-22", bar)
    assert fill["filled"] is False
    assert fill["blocked_reason"] == "missing_open"


def test_exit_zero_quantity_returns_blocked():
    bar = {"open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1}
    fill = execute_exit("2026-08-21", "2026-08-22", bar, quantity=0)
    assert fill["filled"] is False
    assert fill["fill_price"] is None
    assert fill["blocked_reason"] == "zero_quantity"


def test_entry_applies_buy_commission():
    bar = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
    cfg = ExecutionConfig(commission_bps=3, stamp_tax_bps=0, slippage_bps=0)
    fill = execute_entry("2026-08-21", "2026-08-22", bar, cfg, quantity=1000)
    assert fill["filled"] is True
    assert fill["fill_price"] == 10.0
    assert fill["cost"] == 3.0


def test_exit_applies_commission_and_stamp_tax():
    bar = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
    cfg = ExecutionConfig(commission_bps=3, stamp_tax_bps=5, slippage_bps=0)
    fill = execute_exit("2026-08-21", "2026-08-22", bar, cfg, quantity=1000)
    assert fill["filled"] is True
    assert fill["fill_price"] == 10.0
    assert fill["cost"] == 8.0


def test_entry_not_at_limit_up():
    """开盘价未触及涨停时正常成交。"""
    bar = {"open": 10.5, "high": 11.0, "low": 10.3, "close": 10.8, "prev_close": 10.0}
    fill = execute_entry("2026-08-21", "2026-08-22", bar, limit_pct=0.10)
    assert fill["filled"] is True
    assert fill["blocked_reason"] is None


def test_exit_not_at_limit_down():
    """开盘价未触及跌停时正常卖出。"""
    bar = {"open": 9.5, "high": 9.8, "low": 9.2, "close": 9.6, "prev_close": 10.0}
    fill = execute_exit("2026-08-21", "2026-08-22", bar, limit_pct=0.10)
    assert fill["filled"] is True
    assert fill["blocked_reason"] is None
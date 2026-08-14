# -*- coding: utf-8 -*-
"""持仓到价提醒单测：mock db.get_conn 验 alert_triggered 读时计算 + set_alert 只更提醒列。

合规：提醒是用户自设价位规则触发的通知，非 AI 买卖点。测试只验比较逻辑。
运行: pytest tests/test_portfolio_alert.py  (在 stock-screener 目录下)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from data import portfolio


class _Result:
    def __init__(self, val): self._val = val; self.rowcount = 1
    def fetchone(self): return self._val
    def fetchall(self): return self._val if isinstance(self._val, list) else []


class _Conn:
    """按 sql 子串匹配预置结果。portfolio 行 / spot 价。"""
    def __init__(self, pf_rows, spot=None):
        self._pf = pf_rows; self._spot = spot or {}
    def execute(self, sql, *params):
        if "FROM portfolio" in sql:
            return _Result(self._pf)
        if "latest_price" in sql and "IN" in sql:
            # 返回 spot 行 [{code, latest_price},...]
            return _Result([{"code": c, "latest_price": p}
                            for c, p in self._spot.items()])
        return _Result([])
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _row(code, buy_price, alert_hi=None, alert_lo=None, lp=10.0):
    return {"id": 1, "code": code, "name": "T", "buy_date": "2026-01-01",
            "buy_price": buy_price, "shares": 100, "note": "",
            "alert_hi": alert_hi, "alert_lo": alert_lo, "ts": "x"}


def test_alert_triggered_hi(monkeypatch):
    """latest_price >= alert_hi → triggered='hi'。"""
    r = _row("600519", 100.0, alert_hi=10.5, lp=11.0)
    monkeypatch.setattr(portfolio.db, "get_conn", lambda: _Conn([r], {"600519": 11.0}))
    out = portfolio.list_positions()
    assert out[0]["alert_triggered"] == "hi"


def test_alert_triggered_lo(monkeypatch):
    """latest_price <= alert_lo → triggered='lo'。"""
    r = _row("600519", 100.0, alert_lo=9.5)
    monkeypatch.setattr(portfolio.db, "get_conn", lambda: _Conn([r], {"600519": 9.0}))
    out = portfolio.list_positions()
    assert out[0]["alert_triggered"] == "lo"


def test_alert_none_when_no_rule(monkeypatch):
    r = _row("600519", 100.0)
    monkeypatch.setattr(portfolio.db, "get_conn", lambda: _Conn([r], {"600519": 12.0}))
    out = portfolio.list_positions()
    assert out[0]["alert_triggered"] is None


def test_alert_none_when_no_spot(monkeypatch):
    """无最新价(spot 缺 + pytdx 兜底也无)时不触发。"""
    r = _row("600519", 100.0, alert_hi=9.0)
    monkeypatch.setattr(portfolio.db, "get_conn", lambda: _Conn([r], {}))
    # pytdx 兜底也返空,模拟行情服务器全不可用
    monkeypatch.setattr(portfolio.pytdx_client, "get_quote", lambda codes: [])
    out = portfolio.list_positions()
    assert out[0]["alert_triggered"] is None


def test_set_alert_only_updates_alert_cols(monkeypatch):
    """set_alert 只更 alert_hi/alert_lo，不动 buy_price。"""
    captured = {}
    class _C:
        def execute(self, sql, params=None):
            captured["sql"] = sql; captured["params"] = params
            return _Result(None)
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(portfolio.db, "get_conn", lambda: _C())
    portfolio.set_alert(7, alert_hi=12.5, alert_lo=9.0)
    assert "alert_hi" in captured["sql"] and "alert_lo" in captured["sql"]
    assert 12.5 in captured["params"] and 9.0 in captured["params"] and 7 in captured["params"]

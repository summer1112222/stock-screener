# -*- coding: utf-8 -*-
"""市场温度采集/查询单测：mock akshare + db.get_conn，不触网。

合规：测试只验涨跌家数比 SQL 与采集降级逻辑，不涉及买卖点/择时判断。
运行: pytest tests/test_market.py  (在 stock-screener 目录下)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from data import market


# ---------- 伪连接：按 sql 子串匹配预置结果 ----------
class _Result:
    """fetchone 返回 dict(或 None)；fetchall 返回 list[dict]。"""
    def __init__(self, val): self._val = val
    def fetchone(self):
        if isinstance(self._val, list):
            return self._val[0] if self._val else None
        return self._val  # dict 或 None
    def fetchall(self):
        return self._val if isinstance(self._val, list) else []


class _Conn:
    def __init__(self, mapping):
        self._m = mapping  # {sql_substring: dict | list | None}
    def execute(self, sql, *params):
        for key, val in self._m.items():
            if key in sql:
                return _Result(val)
        return _Result(None)
    def commit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _patch(monkeypatch, mapping):
    monkeypatch.setattr(market.db, "get_conn", lambda: _Conn(mapping))


# ---------- 涨跌家数比：纯 DB，零采集 ----------
def test_updown_from_spot(monkeypatch):
    _patch(monkeypatch, {"change_pct>0": {"n": 120}, "change_pct<0": {"n": 80}})
    r = market._updown_from_spot()
    assert r["up_count"] == 120 and r["down_count"] == 80
    assert r["_db_ok"] is True


def test_updown_db_failure_degrades(monkeypatch):
    class _Boom:
        def execute(self, *a): raise RuntimeError("spot boom")
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(market.db, "get_conn", lambda: _Boom())
    r = market._updown_from_spot()
    assert r["_db_ok"] is False
    assert r["up_count"] is None and r["down_count"] is None


# ---------- collect_temperature：legu 不可用时降级，仍出 up/down ----------
def test_collect_temperature_legu_disabled(monkeypatch):
    _patch(monkeypatch, {"change_pct>0": {"n": 200}, "change_pct<0": {"n": 100}})
    monkeypatch.setattr(market, "_fetch_legu", lambda: (None, False, "akshare 不可用"))
    rec = market.collect_temperature()
    assert rec["ok"] is True                       # DB 路成功即整体 ok
    assert rec["up_count"] == 200 and rec["down_count"] == 100
    assert rec["zt_count"] is None                # legu 路失败该指标为 None
    assert "akshare" in rec["err"]


def test_collect_temperature_all_fail_ok_false(monkeypatch):
    class _Boom:
        def execute(self, *a): raise RuntimeError("db boom")
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(market.db, "get_conn", lambda: _Boom())
    monkeypatch.setattr(market, "_fetch_legu", lambda: (None, False, "boom"))
    rec = market.collect_temperature()
    assert rec["ok"] is False


# ---------- latest / trend：只读查询 ----------
def test_latest_none_when_empty(monkeypatch):
    _patch(monkeypatch, {"ORDER BY date DESC LIMIT 1": None})
    assert market.latest() is None


def test_trend_returns_rows(monkeypatch):
    # DB ORDER BY date DESC 返回降序(新→旧)，trend() 再 reverse 成升序(旧→新)供 sparkline
    rows = [{"date": "2026-08-07", "up_count": 110, "zt_count": 25},
            {"date": "2026-08-06", "up_count": 100, "zt_count": 30}]
    class _C:
        def execute(self, sql, *p):
            return _Result(rows)
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(market.db, "get_conn", lambda: _C())
    tr = market.trend(30)
    assert len(tr) == 2 and tr[-1]["date"] == "2026-08-07"

# -*- coding: utf-8 -*-
import pandas as pd
from screener import engine as e


def test_condition_value_coercion_and_between_swap():
    df = pd.DataFrame([{"a": 1}, {"a": 2}, {"a": 3}])
    r, s = e._apply_conditions(df, [{"field": "a", "op": "gt", "value": "2"}])
    assert len(r) == 1 and not s
    r, s = e._apply_conditions(df, [{"field": "a", "op": "gt", "value": "x"}])
    assert len(r) == 3 and len(s) == 1
    r, s = e._apply_conditions(df, [{"field": "a", "op": "between", "value": [3, 1]}])
    assert len(r) == 3 and len(s) == 1


def test_topn_intersection():
    df = pd.DataFrame([
        {"x": 10, "y": 1}, {"x": 9, "y": 100}, {"x": 8, "y": 90},
        {"x": 1, "y": 2},
    ])
    r, _ = e._apply_conditions(df, [
        {"field": "x", "op": "topn", "value": 2},
        {"field": "y", "op": "topn", "value": 2},
    ])
    assert sorted(r["x"].tolist()) == [9]


def test_st_prefix_and_board_limit():
    df = pd.DataFrame([
        {"code": "300001", "name": "CYB", "latest_price": 10.0,
         "turnover_amount": 2e8, "change_pct": 12.0, "board": "CYB"},
        {"code": "600001", "name": "MAIN", "latest_price": 10.0,
         "turnover_amount": 2e8, "change_pct": 10.0, "board": "MAIN"},
        {"code": "600002", "name": "MID-ST-X", "latest_price": 10.0,
         "turnover_amount": 2e8, "change_pct": 1.0, "board": "MAIN"},
        {"code": "600003", "name": "*ST-AAA", "latest_price": 10.0,
         "turnover_amount": 2e8, "change_pct": 1.0, "board": "MAIN"},
    ])
    assert e._is_st_name("*ST-AAA") is True
    assert e._is_st_name("MID-ST-X") is False
    out = e._tradable_stocks(df, 5e7, 9.9)
    codes = set(out["code"].tolist())
    assert "600001" not in codes
    assert "600002" in codes and "600003" not in codes


def test_strength_floor_and_inflow_clip():
    df = pd.DataFrame([
        {"turnover_rate": 1.0, "change_pct": 10.0, "amplitude": 0.01,
         "turnover_amount": 2e6, "circulating_market_cap": 1e10,
         "main_net_inflow": 1e9},
    ])
    out = e._add_derived(df)
    assert out["strength"].iloc[0] == 10.0 / 0.5
    assert abs(out["inflow_pct"].iloc[0]) <= 500


def test_radar_memo_reuses_cache(monkeypatch):
    import backtest.quality as q
    q._RADAR_MEMO.clear()
    calls = []

    def fake(codes, days=5):
        calls.append(1)
        return {c: {"has_data": True, "in_count": 1} for c in codes}

    monkeypatch.setattr("screener.smart_money.radar_resonance_for", fake)
    q._radar_for(["b", "a"], days=5)
    q._radar_for(["a", "b"], days=5)
    assert len(calls) == 1


def test_shortlist_memo(monkeypatch):
    import backtest.quality as q
    q._SHORTLIST_MEMO.clear()
    monkeypatch.setattr("backtest.buffett.shortlist_by_turnover",
                        lambda min_turnover=5e8, k=80: ["a"])
    assert q._shortlist_codes(["a", "b"]) == ["a"]

    def boom(min_turnover=5e8, k=80):
        raise RuntimeError("down")

    monkeypatch.setattr("backtest.buffett.shortlist_by_turnover", boom)
    assert q._shortlist_codes(["a", "b"]) == ["a"]

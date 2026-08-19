# -*- coding: utf-8 -*-
"""nextday 次日强势概率排序单测(7 因子统一评分版：含趋势形态+板块助攻)。
mock smart_money._behavior_batch/chip_distribution/db，不触网。"""
import screener.nextday as nd

# _behavior_batch 返 {streak_inflow, streak_outflow, margin_accel, north_cum}
_BATCH = {
    "A": {"streak_inflow": 5, "streak_outflow": 0, "margin_accel": 1e7, "north_cum": 2e8},
    "B": {"streak_inflow": 2, "streak_outflow": 0, "margin_accel": 0.0, "north_cum": 0.0},
    "C": {"streak_inflow": 0, "streak_outflow": 4, "margin_accel": -1e7, "north_cum": -1e8},
}
_CHIP = {
    "A": {"trend": {"chip_concentration_delta": -0.05}},
    "B": {"trend": {"chip_concentration_delta": 0.0}},
    "C": {"trend": {"chip_concentration_delta": 0.02}},
}
# A: 涨幅3.5(3-5%最佳)+量比2.0+换手7%(5-10%)+市值100亿(50-200)
# B: 涨幅1.5(1-3%次之)+量比1.0+换手4%(3-5%次之)+市值30亿(20-50次之)
# C: 涨幅2.0+量比0.8+换手20%(>15%仅0.2)+市值1000亿(>500仅0.2)
_SPOT = [
    {"code": "A", "name": "甲股", "change_pct": 3.5, "volume_ratio": 2.0,
     "turnover_rate": 7.0, "circulating_market_cap": 100.0},
    {"code": "B", "name": "乙股", "change_pct": 1.5, "volume_ratio": 1.0,
     "turnover_rate": 4.0, "circulating_market_cap": 30.0},
    {"code": "C", "name": "丙股", "change_pct": 2.0, "volume_ratio": 0.8,
     "turnover_rate": 20.0, "circulating_market_cap": 1000.0},
]


def _mock(monkeypatch):
    monkeypatch.setattr(nd.smart_money, "_behavior_batch",
                        lambda codes, days=30: {c: _BATCH.get(c, {}) for c in codes})
    monkeypatch.setattr(nd.smart_money, "chip_distribution",
                        lambda code, window=60: _CHIP.get(code, {}))
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: _SPOT if table == "stock_spot" else [])
    nd._CACHE.clear()


# ---------- 基础排序 ----------

def test_nextday_basic_rank(monkeypatch):
    _mock(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 3
    assert r["items"][0]["code"] == "A"
    assert r["items"][-1]["code"] == "C"
    a, b, c = r["items"][0], r["items"][1], r["items"][2]
    assert a["score"] > b["score"] >= c["score"]
    assert a["rank"] == 1 and c["rank"] == 3


def test_nextday_score_range(monkeypatch):
    _mock(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    for it in r["items"]:
        assert 0 <= it["score"] <= 100
    c = [i for i in r["items"] if i["code"] == "C"][0]
    assert c["score"] <= 10  # 出货 -1.0 拉低总分,但其他因子>0 故不 strict 0
    assert c["phase"] == "出货"


def test_nextday_phase_mapping(monkeypatch):
    _mock(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    by = {i["code"]: i for i in r["items"]}
    assert by["A"]["phase"] == "吸筹"
    assert by["C"]["phase"] == "出货"
    assert by["B"]["phase"] == "流入"


# ---------- 新增因子：趋势形态 ----------

import pandas as pd


def _mk_close(codes_prices, n=65):
    data = {}
    for c, pxs in codes_prices.items():
        if len(pxs) < n:
            full = list(pxs) + [float("nan")] * (n - len(pxs))
        else:
            full = list(pxs)
        data[c] = full[:n]
    df = pd.DataFrame(data)
    df.index = pd.date_range("2026-06-01", periods=n, name="date")
    return df


def test_trend_factor_bullish_align(monkeypatch):
    """多头排列 → trend 因子约 1.0。"""
    close = _mk_close({"A": list(range(60, 125))})
    from backtest import signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, None))
    d = nd._f_趋势形态_batch(["A"], close, None)
    s = d["A"]["strength"]
    assert s >= 0.9, f"多头排列趋势强度应高:{s}"


def test_trend_factor_bearish(monkeypatch):
    """空头排列 → trend 因子约 -1.0。"""
    close = _mk_close({"A": list(range(124, 59, -1))})  # 严格递减
    from backtest import signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, None))
    d = nd._f_趋势形态_batch(["A"], close, None)
    s = d["A"]["strength"]
    assert s <= -0.9, f"空头排列趋势强度应低:{s}"


def test_trend_factor_volume_breakout(monkeypatch):
    """放量突破60日线 → trend 约 0.8。"""
    px = [100] * 64 + [101]
    close = _mk_close({"A": px})
    amt = [100] * 64 + [250]
    amount = _mk_close({"A": amt})
    from backtest import signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, amount))
    d = nd._f_趋势形态_batch(["A"], close, amount)
    s = d["A"]["strength"]
    assert s >= 0.7, f"放量突破趋势强度应中高:{s}"


def test_trend_factor_no_history(monkeypatch):
    """无历史 → trend 0.0 不崩。"""
    close = pd.DataFrame()
    from backtest import signals as sig
    monkeypatch.setattr(sig, "_uni_panels", lambda u, codes: (close, None))
    d = nd._f_趋势形态_batch(["A"], close, None)
    assert d["A"]["strength"] == 0.0


# ---------- 新增因子：板块助攻 ----------

def test_board_factor_ranked(monkeypatch):
    """板块热度前5 + 涨停≥2 → strength 高。"""
    # sector_fund_flow 按主净流入排名
    _sff = [
        {"name": "电池", "main_net_inflow": 100.0},
        {"name": "军工", "main_net_inflow": 50.0},
    ]
    def _mock_qr(table, **k):
        if table == "industry_board":
            return [{"name": "电池", "members": ["A", "B"]},
                    {"name": "军工", "members": ["C"]}]
        if table == "sector_fund_flow":
            return _sff
        return _SPOT
    monkeypatch.setattr(nd.db, "query_rows", _mock_qr)
    nd._CACHE.clear()
    # 造 2 涨停(A,B change_pct=10)
    spots = [{"code": "A", "name": "甲", "change_pct": 10.0, "volume_ratio": 2.0},  # 涨停
             {"code": "B", "name": "乙", "change_pct": 10.0, "volume_ratio": 1.5},  # 涨停
             {"code": "C", "name": "丙", "change_pct": 2.0, "volume_ratio": 0.5}]  # 非涨停
    d = nd._f_板块助攻_batch(["A", "B", "C"], spots)
    # A: 电池(排名1前5) + 2涨停 → strength 1.0
    assert d["A"]["strength"] >= 0.9, "板块助攻A应高"
    assert d["A"]["board"] == "电池"
    assert d["A"]["board_rank"] == 1
    assert d["A"]["sector_zt"] == 2
    # C: 军工(排名2前5)但0涨停 → strength 0.7
    assert d["C"]["strength"] >= 0.6, "板块助攻C应中"
    assert d["C"]["sector_zt"] == 0


def test_board_factor_no_board(monkeypatch):
    """无 industry_board 数据 → 中性 0.5 降级不崩。"""
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: [] if table == "industry_board" else _SPOT)
    nd._CACHE.clear()
    d = nd._f_板块助攻_batch(["A"], [_SPOT[0]])
    assert d["A"]["strength"] == 0.5


# ---------- 权重体系 ----------

def test_weights_include_new_factors():
    """WEIGHTS 含 7 因子，合计=1.0。"""
    w = nd.WEIGHTS
    assert "趋势形态" in w
    assert "板块助攻" in w
    assert len(w) == 7
    assert abs(sum(w.values()) - 1.0) < 0.001


def test_legacy_weights_unchanged():
    """weights='legacy' 用旧 5 因子权重(合计=1.0)。"""
    w = nd.WEIGHTS_LEGACY
    assert len(w) == 5
    assert abs(sum(w.values()) - 1.0) < 0.001


# ---------- 兼容性 ----------

def test_legacy_mode_uses_5_factors(monkeypatch):
    """mode='legacy' 跳过趋势形态和板块助攻，只算 5 因子。"""
    _mock(monkeypatch)
    # B 涨幅1.5非最佳但在 legacy 下仍正常排序
    r = nd.nextday_strong_rank(limit=10, mode="legacy")
    assert r["count"] == 3
    # 无趋势因子输出
    for it in r["items"]:
        assert "趋势形态" not in it.get("factors", {})


def test_route_returns_new_factors(monkeypatch):
    """路由返回含 7 因子。"""
    _mock(monkeypatch)
    nd._CACHE.clear()
    # 模拟路由侧调用
    r = nd.nextday_strong_rank(limit=10)
    assert r["items"][0]["factors"].get("趋势形态") is not None
    assert r["items"][0]["factors"].get("板块助攻") is not None


# ---------- 原有测试 ----------

def test_nextday_empty_spot(monkeypatch):
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    nd._CACHE.clear()
    r = nd.nextday_strong_rank()
    assert r["count"] == 0
    assert "先" in r.get("note", "")


def test_nextday_degrade_on_exception(monkeypatch):
    monkeypatch.setattr(nd.smart_money, "_behavior_batch",
                        lambda codes, days=30: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(nd.smart_money, "chip_distribution",
                        lambda code, window=60: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: _SPOT if table == "stock_spot" else [])
    nd._CACHE.clear()
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] >= 1
    for it in r["items"]:
        assert 0 <= it["score"] <= 100


def test_nextday_codes_filter(monkeypatch):
    _mock(monkeypatch)
    r = nd.nextday_strong_rank(codes=["A", "B"], limit=10)
    assert r["count"] == 2
    assert {i["code"] for i in r["items"]} == {"A", "B"}


def test_nextday_coarse_filter_excludes(monkeypatch):
    spot = _SPOT + [{"code": "D", "name": "丁", "change_pct": 0.2,
                     "volume_ratio": 2.0, "turnover_rate": 7,
                     "circulating_market_cap": 100}]
    monkeypatch.setattr(nd.smart_money, "_behavior_batch",
                        lambda codes, days=30: {c: _BATCH.get(c, {}) for c in codes})
    monkeypatch.setattr(nd.smart_money, "chip_distribution",
                        lambda code, window=60: _CHIP.get(code, {}))
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: spot if table == "stock_spot" else [])
    nd._CACHE.clear()
    r = nd.nextday_strong_rank(limit=10)
    codes = {i["code"] for i in r["items"]}
    assert "D" not in codes


def test_nextday_cache_hit(monkeypatch):
    _mock(monkeypatch)
    nd.nextday_strong_rank(limit=10)
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 3
# tests/test_etf_screen.py
import screener.etf_screen as es

def test_valuation_percentile_low_is_cheap():
    # 当前值处于历史低位 → 低百分位(便宜)
    assert es.valuation_percentile(10.0, [10, 12, 14, 16, 18, 20]) < 0.5
    # 当前值历史新高 → 高分位(贵)
    assert es.valuation_percentile(20.0, [10, 12, 14, 16, 18, 20]) > 0.9

def test_quality_score_prefers_large_scale_low_fee():
    good = es.quality_score(scale_wan=500, turnover_rate=2.0, fee_bps=15, tracking_err=0.2)
    bad = es.quality_score(scale_wan=2, turnover_rate=0.1, fee_bps=80, tracking_err=3.0)
    assert good > bad

def test_long_score_combines_valuation_and_quality():
    cheap_good = es.long_score(valuation_pct=0.1, quality=0.9)
    expensive_poor = es.long_score(valuation_pct=0.9, quality=0.2)
    assert cheap_good > expensive_poor


def test_short_scores_ranks_uptrend_higher(monkeypatch):
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"000001": np.linspace(10, 16, 30), "000002": np.linspace(16, 10, 30)}, index=idx)
    amt = pd.DataFrame(1e8, index=idx, columns=up.columns)
    from backtest import eval as bt_eval
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: up if f == "close" else amt)
    scores, det, cov = es.short_scores(up, amt, ["000001", "000002"], [5, 20])
    assert scores["000001"] > scores["000002"]   # 上行强于下行
    assert "momentum_5" in det["000001"]
    assert 0 < cov["000001"] <= 1.0


def test_short_scores_missing_factor_renormalizes(monkeypatch):
    from backtest import eval as bt_eval_2
    import pandas as pd, numpy as np
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"000001": np.linspace(10, 16, 30)}, index=idx)
    monkeypatch.setattr(bt_eval_2, "load_panel", lambda u, c, s, e, f: up if f == "close" else None)
    scores, det, cov = es.short_scores(up, None, ["000001"], [5, 20])
    assert cov["000001"] >= 0.4  # momentum/volatility 仍可用


def test_fund_premium():
    assert abs(es.fund_premium(1.05, 1.00) - 0.05) < 1e-9
    assert es.fund_premium(1.00, None) is None
    assert es.fund_premium(None, 1.00) is None


def test_etf_screen_rank_mode_long_returns_quality_and_valuation(monkeypatch):
    import pandas as pd, numpy as np
    from backtest import eval as bt_eval
    es._CACHE.clear()
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    up = pd.DataFrame({"510300": np.linspace(4.0, 5.0, 40), "159915": np.linspace(2.0, 1.6, 40)}, index=idx)
    # 数据源抽象: 估值/质量 meta 经 _fetch_* 注入, 不依赖 etf_spot 表缺字段(无 nav/fund_scale 列)
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: up if f == "close" else None)
    monkeypatch.setattr(es, "_fetch_index_valuation",
        lambda code: {"pe_pct": 0.2, "div_yield": 2.0})
    monkeypatch.setattr(es, "_fetch_quality_meta",
        lambda code: {"fund_scale": 8.0 if code == "159915" else 300.0, "fee_bps": 15, "tracking_err": 0.2})
    # 编排测试必须完全隔离网络；QDII 溢价源显式 mock。
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: None)
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code": "510300", "latest_price": 4.9, "turnover_rate": 1.2},
                                 {"code": "159915", "latest_price": 1.65, "turnover_rate": 0.3}] if table == "etf_spot" else [])
    out = es.etf_screen_rank(universe="ETF", mode="long", codes=["510300", "159915"], days=60)
    assert isinstance(out.get("long_term"), list)
    # 159915 fund_scale=8亿 < _MIN_SCALE(10) 被硬门槛剔除, 只剩 510300
    codes_in = [it.get("code") for it in out["long_term"]]
    assert "159915" not in codes_in
    if out["long_term"]:
        assert "quality_score" in out["long_term"][0]
        assert "valuation_percentile" in out["long_term"][0]
        assert out["long_term"][0]["premium"] is None          # 全 mock 无网络, 溢价诚实 None
        assert "source" in out["long_term"][0]                 # 来源标注保留
        # code / source 字符串必须原样保留, 不被 _nan 转成 float
        assert isinstance(out["long_term"][0]["code"], str)
        assert out["long_term"][0]["code"] == "510300"
        assert isinstance(out["long_term"][0]["source"], str)


def test_etf_screen_rank_qdii_premium_visible(monkeypatch):
    # 溢价率出现在 QDII 清单项表里(无论 long/short), 高溢价 red flag
    import pandas as pd, numpy as np
    from backtest import eval as bt_eval
    es._CACHE.clear()
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    up = pd.DataFrame({"513100": np.linspace(1.0, 1.1, 40)}, index=idx)
    # nav 经 _fetch_qdii_premium 注入(不读 etf_spot.nav, 表无此列)
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: up if f == "close" else None)
    monkeypatch.setattr(es, "_fetch_index_valuation", lambda code: None)
    monkeypatch.setattr(es, "_fetch_quality_meta", lambda code: None)
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: 0.15)  # 场内溢价 15%
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code": "513100", "latest_price": 1.60}] if table == "etf_spot" else [])
    out = es.etf_screen_rank(universe="QDII", mode="short", codes=["513100"], days=60)
    for item in out.get("short_term", [])[:1]:
        assert "premium" in item
        assert item["code"] == "513100"
        assert isinstance(item["code"], str)


def test_long_missing_valuation_not_crash(monkeypatch):
    # 估值分位源缺失(返 None) → 长清单不崩, 估值维度走中性 0.5, 清单仍成形
    import pandas as pd, numpy as np
    from backtest import eval as bt_eval
    es._CACHE.clear()
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: pd.DataFrame({}, index=[]))
    monkeypatch.setattr(es, "_fetch_index_valuation", lambda code: None)   # 缺估值源
    monkeypatch.setattr(es, "_fetch_quality_meta", lambda code: None)      # 缺质量 meta → 规模门槛跳过
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: None)
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code": "510300", "latest_price": 4.9, "turnover_rate": 1.0}]
        if table == "etf_spot" else [])
    out = es.etf_screen_rank(mode="long", codes=["510300"], days=60)
    assert out["long_term"], "缺估值源长清单仍应成形"
    assert out["long_term"][0]["valuation_percentile"] == 0.5   # 中性 0.5
    assert out["long_term"][0]["premium"] is None
    assert isinstance(out["long_term"][0]["code"], str)


def test_etf_screen_cache_ttl(monkeypatch):
    # 30s 进程缓存: 同参二次命中不重算源; 清缓存后重算
    import pandas as pd
    from backtest import eval as bt_eval
    es._CACHE.clear()
    calls = {"n": 0}
    def _val(code):
        calls["n"] += 1
        return {"pe_pct": 0.3}
    monkeypatch.setattr(bt_eval, "load_panel", lambda u, c, s, e, f: pd.DataFrame({}, index=[]))
    monkeypatch.setattr(es, "_fetch_index_valuation", _val)
    monkeypatch.setattr(es, "_fetch_quality_meta", lambda code: None)
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: None)
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code": "510300"}] if table == "etf_spot" else [])
    es.etf_screen_rank(mode="long", codes=["510300"], days=60)
    first = calls["n"]
    assert first > 0
    es.etf_screen_rank(mode="long", codes=["510300"], days=60)   # 缓存命中, 不重算
    assert calls["n"] == first, "缓存命中不应重复调用源"
    es._CACHE.clear()
    es.etf_screen_rank(mode="long", codes=["510300"], days=60)   # 清缓存后重算
    assert calls["n"] > first


def test_item_carries_name_field(monkeypatch):
    # long/short item 带 name 字段; 本地无真实名称时诚实回退占位(不因缺失崩)
    import pandas as pd, numpy as np
    from backtest import eval as bt_eval
    es._CACHE.clear()
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    up = pd.DataFrame({"510300": np.linspace(4, 5, 30)}, index=idx)
    amt = pd.DataFrame(1e8, index=idx, columns=up.columns)
    monkeypatch.setattr(bt_eval, "load_panel",
        lambda u, c, s, e, f: up if f == "close" else amt)
    monkeypatch.setattr(es, "_fetch_index_valuation", lambda code: None)
    monkeypatch.setattr(es, "_fetch_quality_meta", lambda code: None)
    monkeypatch.setattr(es, "_fetch_qdii_premium", lambda code: None)
    # 占位名称(等于 code) → _display_name 无全市场源时诚实返回占位, 前端显"待刷新"
    monkeypatch.setattr(es._db, "query_rows",
        lambda table, *a, **k: [{"code": "510300", "name": "510300", "latest_price": 4.9}]
        if table == "etf_spot" else [])
    out = es.etf_screen_rank(mode="long", codes=["510300"], days=60)
    assert "name" in out["long_term"][0]
    out2 = es.etf_screen_rank(mode="short", codes=["510300"], days=60)
    assert "name" in out2["short_term"][0]
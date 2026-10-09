# -*- coding: utf-8 -*-
from screener import engine
from data import db


def _seed():
    db.init_db()
    with db.get_conn() as c:
        c.execute('DELETE FROM stock_spot')
        c.commit()
    rows = [
        {"code":"000001","name":"平安银行","latest_price":10.0,"change_pct":2.0,
         "turnover_amount":2e8,"turnover_rate":1.5,"pe":8,"pb":0.9,"total_market_cap":2e10},
        {"code":"000002","name":"万科A","latest_price":9.0,"change_pct":-1.0,
         "turnover_amount":1.5e8,"turnover_rate":1.0,"pe":7,"pb":0.8,"total_market_cap":1.5e10},
        {"code":"000003","name":"*ST某某","latest_price":5.0,"change_pct":5.0,
         "turnover_amount":3e8,"turnover_rate":3.0,"pe":None,"pb":None,"total_market_cap":5e9},
        {"code":"000004","name":"涨停股","latest_price":11.0,"change_pct":10.0,
         "turnover_amount":5e8,"turnover_rate":5.0,"pe":20,"pb":2.0,"total_market_cap":1e10},
        {"code":"000005","name":"低成交额股","latest_price":8.0,"change_pct":1.0,
         "turnover_amount":1e7,"turnover_rate":0.1,"pe":9,"pb":1.0,"total_market_cap":5e9},
    ]
    db.upsert_rows("stock_spot", rows)


def test_filter_stocks_excludes_st_and_limit():
    _seed()
    res = engine.filter_stocks(conditions=[], sort="turnover_amount", asc=False, limit=10)
    names = [r["name"] for r in res["rows"]]
    assert "*ST某某" not in names
    assert "涨停股" not in names
    assert "低成交额股" not in names
    assert "平安银行" in names


def test_filter_stocks_between():
    _seed()
    res = engine.filter_stocks(conditions=[
        {"field":"pe","op":"between","value":[7.5, 9]},
    ], sort="pe", asc=False, limit=10)
    codes = [r["code"] for r in res["rows"]]
    assert "000001" in codes
    assert "000002" not in codes


def test_filter_stocks_live_enrich_mock(monkeypatch):
    """live=True 时批量 get_quote 填充实时字段；失败降级 snapshot。"""
    _seed()
    codes = ["000001", "000002"]
    fake_q = [{"code": c, "price": 10.0, "last_close": 9.5,
               "bid_vol1": 100, "bid_vol2": 100, "bid_vol3": 100,
               "bid_vol4": 100, "bid_vol5": 100,
               "ask_vol1": 50, "ask_vol2": 50, "ask_vol3": 50,
               "ask_vol4": 50, "ask_vol5": 50} for c in codes]
    qmap = {q["code"]: q for q in fake_q}
    monkeypatch.setattr("data.pytdx_client.get_quote", lambda cs: [qmap[c] for c in cs if c in qmap])
    res = engine.filter_stocks(conditions=[], limit=50, live=True)
    assert res["live_source"] == "live"
    hit = [r for r in res["rows"] if r.get("code") in qmap]
    assert hit, "seeded rows missing from results (DB shared across tests)"
    assert all(r.get("live_price") == 10.0 for r in hit)
    assert all(r.get("live_imbalance") is not None for r in hit)
    # tdx 失败 → snapshot 降级
    def _boom(cs):
        raise Exception("down")
    monkeypatch.setattr("data.pytdx_client.get_quote", _boom)
    res2 = engine.filter_stocks(conditions=[], limit=50, live=True)
    assert res2["live_source"] == "snapshot"


def test_bid_ask_imbalance_math():
    q = {f"bid_vol{i}": 100 for i in range(1, 6)}
    q.update({f"ask_vol{i}": 50 for i in range(1, 6)})
    assert engine._bid_ask_imbalance(q) == round((500 - 250) / 750, 4)
    assert engine._bid_ask_imbalance({}) is None

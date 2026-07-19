# -*- coding: utf-8 -*-
from screener import engine
from data import db


def _seed():
    db.init_db()
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

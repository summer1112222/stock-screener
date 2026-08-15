import screener.smart_money as sm

def test_board_link_basic(monkeypatch):
    spots = [
        {"code":"000001","name":"A","board":"银行","main_net_inflow":1e8,"turnover_amount":5e8},
        {"code":"000002","name":"B","board":"银行","main_net_inflow":2e8,"turnover_amount":4e8},
        {"code":"600000","name":"C","board":"地产","main_net_inflow":-1e8,"turnover_amount":3e8},
    ]
    sff = [
        {"sector_type":"行业","indicator":"今日","name":"银行","main_net_inflow":3e8},
        {"sector_type":"行业","indicator":"今日","name":"地产","main_net_inflow":-1e8},
        {"sector_type":"行业","indicator":"5日","name":"银行","main_net_inflow":1e9},
    ]
    calls = {"stock_spot": spots, "sector_fund_flow": sff}
    def fake_query(table, where="", params=(), order_by="", limit=0):
        return calls.get(table, [])
    monkeypatch.setattr(sm.db, "query_rows", fake_query)
    # board_money_link 内联算 net_intensity(main_net_inflow/turnover_amount),不调 _attach_intensity
    r = sm.board_money_link("000001")
    assert r["board"] == "银行"
    assert r["board_rank"] == 1   # 银行净流入3e8 第一
    assert r["board_pct"] >= 0.5
    # 板块内 net_intensity: A=1e8/5e8=0.2, B=2e8/4e8=0.5 -> B第1 A第2
    assert r["intra_board_rank"] == 2

def test_board_link_empty_sector(monkeypatch):
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    r = sm.board_money_link("000001")
    assert r["board"] in ("未知", None) or r.get("note")

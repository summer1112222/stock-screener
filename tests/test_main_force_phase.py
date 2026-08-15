import screener.smart_money as sm

def _mk(streak_in=0, streak_out=0, cum=0.0, accel=0.0, profit=0.5,
        conc=0.2, avg=10.0, spot=10.0, chg=1.0, tnv=1e8, hi60=0.4, tnv5=1e8):
    return dict(streak_in=streak_in, streak_out=streak_out, cum=cum,
                accel=accel, profit=profit, conc=conc, avg=avg, spot=spot,
                chg=chg, tnv=tnv, hi60=hi60, tnv5=tnv5)

def _patch(monkeypatch, m):
    sm._PHASE_CACHE.clear()
    monkeypatch.setattr(sm, "behavior_series", lambda c, days=30: {
        "streak_inflow": m["streak_in"], "streak_outflow": m["streak_out"],
        "cum_inflow": m["cum"], "margin_accel": m["accel"]})
    monkeypatch.setattr(sm, "chip_distribution", lambda c, window=60: {
        "avg_cost": m["avg"], "profit_ratio": m["profit"],
        "chip_concentration": m["conc"], "spot": m["spot"], "need_history": False})
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0:
        [{"change_pct": m["chg"], "turnover_amount": m["tnv"],
          "latest_price": m["spot"], "name": "X"}] if table == "stock_spot" else [])
    monkeypatch.setattr(sm, "_high60_pct", lambda code: m["hi60"])
    monkeypatch.setattr(sm, "_turnover5_avg", lambda code: m["tnv5"])

def test_phase_chuhuo(monkeypatch):
    _patch(monkeypatch, _mk(streak_out=3, profit=0.9, accel=-1, hi60=0.9))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "出货"
    assert r["confidence"] == 1.0

def test_phase_lasheng(monkeypatch):
    _patch(monkeypatch, _mk(streak_in=3, chg=5, tnv=2e8, tnv5=1e8, spot=11, avg=10))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "拉升"
    assert r["confidence"] == 1.0

def test_phase_xichou(monkeypatch):
    _patch(monkeypatch, _mk(streak_in=4, cum=1e8, chg=1, profit=0.6))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "吸筹"
    assert r["confidence"] == 1.0

def test_phase_xipan(monkeypatch):
    _patch(monkeypatch, _mk(cum=1e8, accel=-1, chg=-3))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "洗盘"
    assert r["confidence"] == 1.0

def test_phase_guanwang_low_conf(monkeypatch):
    _patch(monkeypatch, _mk(streak_in=1, chg=0, profit=0.5))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "观望"

def test_phase_tiebreak_risk_priority(monkeypatch):
    # 出货与拉升各命中2/4并列 -> 风险优先取出货
    _patch(monkeypatch, _mk(streak_out=3, profit=0.9, streak_in=3, chg=5, tnv=2e8, tnv5=1e8, spot=11, avg=10, accel=-1, hi60=0.9))
    r = sm.main_force_phase("000001")
    assert r["phase"] == "出货"

def test_phase_data_insufficient(monkeypatch):
    sm._PHASE_CACHE.clear()
    monkeypatch.setattr(sm, "behavior_series", lambda c, days=30: {"streak_inflow": None, "streak_outflow": None, "cum_inflow": None, "margin_accel": None})
    monkeypatch.setattr(sm, "chip_distribution", lambda c, window=60: {"avg_cost": None, "profit_ratio": None, "chip_concentration": None, "spot": None, "need_history": True})
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    monkeypatch.setattr(sm, "_high60_pct", lambda code: None)
    monkeypatch.setattr(sm, "_turnover5_avg", lambda code: None)
    r = sm.main_force_phase("000001")
    assert r["phase"] == "观望"
    assert r["confidence"] == 0
    assert "数据不足" in r.get("note", "")

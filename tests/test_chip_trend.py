import pandas as pd, numpy as np
import screener.smart_money as sm

def _mock_panels(monkeypatch, closes, amounts):
    # closes/amounts: 列表(每日值), 近→远 或 远→近均按升序整理
    idx = pd.date_range("2026-06-01", periods=len(closes), freq="D")
    close = pd.DataFrame({"000001": closes}, index=idx)
    amount = pd.DataFrame({"000001": amounts}, index=idx)
    monkeypatch.setattr("backtest.signals._uni_panels",
                        lambda u, codes: (close, amount))
    # 跳过 spot DB（chip_distribution 内部查 stock_spot）
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])

def test_chip_trend_delta_sign(monkeypatch):
    # 固定 spot + 价格上行会让 profit_ratio 下降(delta<0)。
    # 验证 delta>0: 价格下行 + spot 居中 -> 后期窗口更多 close<spot -> profit_ratio 上升。
    n = 90
    closes = list(np.linspace(12, 8, n))   # 递减 12->8
    amounts = [1e8]*n
    _mock_panels(monkeypatch, closes, amounts)
    r = sm.chip_distribution("000001", window=60, spot_price=10.0)
    assert "trend" in r
    assert r["trend"].get("profit_ratio_delta") is not None
    assert r["trend"]["profit_ratio_delta"] > 0   # 严格大于0,真正验证趋势
    assert "profit_ratio_5d" in r["trend"]
    assert len(r["trend"]["profit_ratio_5d"]) == 5

def test_chip_trend_no_history(monkeypatch):
    monkeypatch.setattr("backtest.signals._uni_panels", lambda u, codes: (None, None))
    monkeypatch.setattr(sm.db, "query_rows", lambda table, where="", params=(), order_by="", limit=0: [])
    r = sm.chip_distribution("000001", window=60, spot_price=10.0)
    assert r["need_history"] is True
    assert r.get("trend") is None or r["trend"] == {}

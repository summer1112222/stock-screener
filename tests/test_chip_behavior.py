# -*- coding: utf-8 -*-
"""筹码分布 + _forward_returns 抽函数 P1 测试。

合成数据 mock backtest.signals._uni_panels，不触网（沿用 tests/ 模式）。
不 mock db：chip_distribution 传 spot_price 参数绕过 stock_spot 查询路径。
合规相关测试见 test_chip_nan_to_none（防 allow_nan=False 500 回归）。
"""
import numpy as np
import pandas as pd

from backtest import signals as bt_sig
from data import db
from screener import smart_money as sm_query


def _chip_synth(n=60, code="c0", seed=7):
    """合成单 code close/amount（价格带上行趋势，跨 10 便于获利盘判断）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    px = 10 + np.cumsum(rng.normal(0, 0.3, n))
    close = pd.DataFrame(px, index=dates, columns=[code])
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, n), index=dates, columns=[code])
    return close, amount


def _has_nan(x):
    """递归检查返回结构是否含 NaN（防 starlette allow_nan=False 500）。"""
    if x is None:
        return False
    if isinstance(x, float):
        return np.isnan(x)
    if isinstance(x, list):
        return any(_has_nan(i) for i in x)
    if isinstance(x, dict):
        return any(_has_nan(v) for v in x.values())
    return False


def test_chip_avg_cost_weighted(monkeypatch):
    """加权平均成本 = Σ(close×amount)/Σ(amount)。"""
    close, amount = _chip_synth()
    monkeypatch.setattr(bt_sig, "_uni_panels",
                        lambda u, c, with_ohlc=False: (close, amount))
    spot = float(close["c0"].iloc[-1])
    res = sm_query.chip_distribution("c0", window=60, spot_price=spot)
    assert res["need_history"] is False
    c = close["c0"].values
    a = amount["c0"].values
    expect = float((c * a).sum() / a.sum())
    assert abs(res["avg_cost"] - round(expect, 4)) < 1e-3
    assert res["spot_source"] == "param"
    assert res["spot"] == round(spot, 4)


def test_chip_profit_ratio(monkeypatch):
    """构造：前半价格 5(低于现价10)、后半价格 15(高于现价10)，等额成交 → 获利盘=50%。"""
    n = 60
    dates = pd.bdate_range("2022-01-01", periods=n)
    px = np.array([5.0] * 30 + [15.0] * 30)
    close = pd.DataFrame(px, index=dates, columns=["c0"])
    amount = pd.DataFrame(np.ones(n) * 1e8, index=dates, columns=["c0"])
    monkeypatch.setattr(bt_sig, "_uni_panels",
                        lambda u, c, with_ohlc=False: (close, amount))
    res = sm_query.chip_distribution("c0", window=60, spot_price=10.0)
    assert abs(res["profit_ratio"] - 0.5) < 1e-3
    assert abs(res["loss_ratio"] - 0.5) < 1e-3
    assert res["avg_cost"] == 10.0  # (5*30+15*30)/60 = 10


def test_chip_nan_to_none(monkeypatch):
    """注入 NaN 到 close/amount，出口结构不得含 NaN（防 500 回归）。"""
    close, amount = _chip_synth()
    close.iloc[5, 0] = np.nan
    amount.iloc[10, 0] = np.nan
    monkeypatch.setattr(bt_sig, "_uni_panels",
                        lambda u, c, with_ohlc=False: (close, amount))
    res = sm_query.chip_distribution("c0", window=60, spot_price=10.0)
    assert not _has_nan(res), "出口含 NaN → JSONResponse allow_nan=False 会 500"


def test_chip_need_history(monkeypatch):
    """_uni_panels 返回空（无 stock_daily 历史）→ need_history=True 不抛。"""
    monkeypatch.setattr(bt_sig, "_uni_panels",
                        lambda u, c, with_ohlc=False: (None, None))
    res = sm_query.chip_distribution("c0", window=60, spot_price=10.0)
    assert res["need_history"] is True
    assert res["avg_cost"] is None
    assert res["distribution"] == []
    # code 不在 columns 同样降级
    empty = pd.DataFrame(columns=["c0"])
    monkeypatch.setattr(bt_sig, "_uni_panels",
                        lambda u, c, with_ohlc=False: (empty, empty))
    res2 = sm_query.chip_distribution("c0", window=60, spot_price=10.0)
    assert res2["need_history"] is True


def test_forward_returns_basic():
    """_forward_returns：无 stop_loss = close[t+k]/close[t]-1；fee_bps 双边扣。"""
    dates = pd.bdate_range("2022-01-01", periods=10)
    close = pd.DataFrame({"c0": [10, 11, 12, 13, 14, 15, 16, 17, 18, 20]},
                         index=dates, columns=["c0"])
    fwd = bt_sig._forward_returns(close, k=2)
    # t=0: close[2]/close[0]-1 = 12/10-1 = 0.2
    assert abs(fwd["c0"].iloc[0] - 0.2) < 1e-9
    fwd_fee = bt_sig._forward_returns(close, k=2, fee_bps=10)
    assert abs(fwd_fee["c0"].iloc[0] - (0.2 - 2 * 10 / 1e4)) < 1e-9


def test_forward_returns_stop_loss_floor():
    """stop_loss 封底：触及 close*(1-stop_loss) 时收益截断为 -stop_loss。"""
    dates = pd.bdate_range("2022-01-01", periods=5)
    close = pd.DataFrame({"c0": [10.0, 10.0, 10.0, 10.0, 10.0]},
                         index=dates, columns=["c0"])
    low = pd.DataFrame({"c0": [9.9, 9.0, 9.0, 9.0, 9.9]},  # t+1 起 low=9.0 < 10*0.92
                       index=dates, columns=["c0"])
    fwd = bt_sig._forward_returns(close, k=2, stop_loss=0.08, low=low)
    # t=0：前视 low(9.0) <= 10*(1-0.08)=9.2 → 截断 -0.08
    assert abs(fwd["c0"].iloc[0] - (-0.08)) < 1e-9


# ---------- 主力行为序列(P2) ----------

def _sm_rows(code="c0", series=None, channel="资金流"):
    """合成 smart_money_action 行。series: list[(date_str, amount)]。"""
    return [{"date": d, "code": code, "channel": channel,
             "amount": a, "name": code, "market": "股票"}
            for d, a in (series or [])]


def _mock_smq(rows):
    """mock db.query_rows：仅 smart_money_action 返回合成行，其余空。"""
    return lambda table, where="", params=(), order_by="", limit=0: \
        rows if table == "smart_money_action" else []


def test_behavior_streak(monkeypatch):
    """连续5日净流入→streak_inflow=5；顶层取资金流口径。"""
    rows = _sm_rows(series=[(f"2026-08-0{d}", 1e8) for d in range(1, 6)])
    monkeypatch.setattr(db, "query_rows", _mock_smq(rows))
    res = sm_query.behavior_series("c0", days=30)
    assert res["channels"]["资金流"]["streak_inflow"] == 5
    assert res["streak_inflow"] == 5  # 顶层资金流口径
    assert res["channels"]["资金流"]["streak_outflow"] == 0
    assert res["cum_inflow"] > 0


def test_behavior_multi_channel_partial(monkeypatch):
    """多通道并行：资金流入+北向流出+龙虎榜空→各自正确，缺通道不崩。"""
    rows = _sm_rows(series=[(f"2026-08-0{d}", 1e8) for d in range(1, 4)],
                    channel="资金流")
    rows += _sm_rows(series=[(f"2026-08-0{d}", -5e7) for d in range(1, 4)],
                     channel="北向")
    monkeypatch.setattr(db, "query_rows", _mock_smq(rows))
    res = sm_query.behavior_series("c0", days=30)
    assert res["channels"]["资金流"]["streak_inflow"] == 3
    assert res["channels"]["北向"]["streak_outflow"] == 3
    assert res["channels"]["北向"]["cum_inflow"] < 0
    assert "龙虎榜" not in res["channels"]  # 无数据通道不进 channels，不崩


def test_behavior_no_records(monkeypatch):
    """无主力动向记录→channels 空 + note 提示，不抛。"""
    monkeypatch.setattr(db, "query_rows", _mock_smq([]))
    res = sm_query.behavior_series("c0", days=30)
    assert res["channels"] == {}
    assert "无主力动向记录" in res["note"]


def test_behavior_margin_accel(monkeypatch):
    """近5日均额 > 近全程均额 → margin_accel > 0(加速)。"""
    # 前15日小额，后5日大额 → 加速
    series = [(f"2026-07-{20+d:02d}", 1e7) for d in range(15)] + \
             [(f"2026-08-0{d}", 1e9) for d in range(1, 6)]
    rows = _sm_rows(series=series)
    monkeypatch.setattr(db, "query_rows", _mock_smq(rows))
    res = sm_query.behavior_series("c0", days=60)
    accel = res["channels"]["资金流"]["margin_accel"]
    assert accel is not None and accel > 0

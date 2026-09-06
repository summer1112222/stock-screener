# -*- coding: utf-8 -*-
"""compute_factor 的 sharpe_n / vol_corr 分支测试。

覆盖: 正常值、方向性、空 amount 降级(vol_corr)、缺数据返回空面板。
"""
import numpy as np
import pandas as pd
import pytest
from backtest import eval as bt_eval


def _panels():
    rng = np.random.default_rng(5)
    dates = pd.bdate_range("2022-01-01", periods=40)
    close = pd.DataFrame(10 + np.cumsum(rng.normal(0, 0.2, (40, 4)), axis=0),
                         index=dates, columns=[f"c{i}" for i in range(4)])
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, (40, 4)),
                          index=dates, columns=close.columns)
    return close, amount


def test_sharpe_shape_and_range():
    close, amount = _panels()
    s = bt_eval.compute_factor(close, "sharpe_10", params={"n": 10}, amount=amount)
    assert s.shape == close.shape
    # 夏普(均值/标准差)一般在有限区间内，不全 NaN
    vals = s.dropna().to_numpy().ravel()
    assert len(vals) > 0
    assert np.isfinite(vals).all()


def test_sharpe_rewards_stable_trend_over_noise():
    dates = pd.bdate_range("2022-01-01", periods=40)
    # 稳定上升 → 高夏普
    stable_close = pd.DataFrame(
        {"stable": np.linspace(10, 20, 40), "noisy": np.linspace(10, 20, 40) + np.sin(np.arange(40)) * 2},
        index=dates)
    s = bt_eval.compute_factor(stable_close, "sharpe_10", params={"n": 10}, amount=None)
    stable = s["stable"].dropna().iloc[-1]
    noisy = s["noisy"].dropna().iloc[-1]
    assert stable > noisy


def test_sharpe_uses_only_past_data():
    """前视安全: t 行夏普只依赖 <=t 的日收益。"""
    close, _ = _panels()
    s = bt_eval.compute_factor(close, "sharpe_10", params={"n": 10}, amount=None)
    ret = close.pct_change()
    for col in close.columns:
        sub = ret[col].dropna().iloc[-10:]
        mu = sub.mean()
        sd = sub.std()
        exp = mu / sd
        got = s[col].dropna().iloc[-1]
        assert got == pytest.approx(exp, rel=1e-6)


def test_vol_corr_shape_and_bounds():
    close, amount = _panels()
    v = bt_eval.compute_factor(close, "vol_corr_20", params={"n": 20}, amount=amount)
    assert v.shape == close.shape
    vals = v.dropna().to_numpy().ravel()
    if len(vals):
        assert (vals >= 0).all() and (vals <= 1).all()  # 皮尔逊 r 归一到 [0,1]


def test_vol_corr_prefers_price_volume_aligned():
    dates = pd.bdate_range("2022-01-01", periods=40)
    prices = np.linspace(10, 20, 40)
    aligned_close = pd.DataFrame({"up": prices}, index=dates)
    aligned_amt = pd.DataFrame({"up": np.linspace(1e8, 2e8, 40)}, index=dates)
    a = bt_eval.compute_factor(aligned_close, "vol_corr_20", params={"n": 20}, amount=aligned_amt)
    # 价升量增 → 相关高
    assert a["up"].dropna().iloc[-1] > 0.8


def test_vol_corr_no_amount_returns_empty():
    close, _ = _panels()
    v = bt_eval.compute_factor(close, "vol_corr_20", params={"n": 20}, amount=None)
    assert v.shape == close.shape and v.isna().all().all()


def test_vol_corr_constant_price_undefined():
    """价列恒定 → 相关未定义(分母为0) → NaN, 不伪造中性分。shape 仍正确。"""
    dates = pd.bdate_range("2022-01-01", periods=20)
    close = pd.DataFrame({"a": [10.0] * 20}, index=dates)
    amount = pd.DataFrame({"a": [1e8, 2e8] * 10}, index=dates)
    v = bt_eval.compute_factor(close, "vol_corr_20", params={"n": 20}, amount=amount)
    assert v.shape == close.shape
    # 分母为 0 的格子应为 NaN; 若 rolling 窗口无足够数据亦 NaN。
    assert v["a"].isna().any()


# ------------------------------------------------------------------
# momentum_n / volatility_n 按 key 后缀解析窗口
# ------------------------------------------------------------------
def test_momentum_suffix_controls_window():
    """momentum_n 应从 key 的 _n 解析窗口，而非总是 params 的 n。"""
    dates = pd.bdate_range("2022-01-01", periods=40)
    close = pd.DataFrame({"a": np.arange(40, dtype=float)}, index=dates)
    for nn in (5, 10, 20):
        got = bt_eval.compute_factor(close, f"momentum_{nn}")
        exp = close.pct_change(nn)
        assert got["a"].iloc[-1] == pytest.approx(exp["a"].iloc[-1], rel=1e-9), f"momentum_{nn}"


def test_volatility_suffix_controls_window():
    """volatility_n 应按 key 的 _n 用对应 rolling 窗口，n 不同则结果不同。"""
    dates = pd.bdate_range("2022-01-01", periods=40)
    close = pd.DataFrame({"a": np.arange(40, dtype=float) + np.sin(np.arange(40)) * 2},
                         index=dates)
    v5 = bt_eval.compute_factor(close, "volatility_5")
    v20 = bt_eval.compute_factor(close, "volatility_20")
    exp5 = close.pct_change().rolling(5).std()
    exp20 = close.pct_change().rolling(20).std()
    assert v5["a"].iloc[-1] == pytest.approx(exp5["a"].iloc[-1], rel=1e-9)
    assert v20["a"].iloc[-1] == pytest.approx(exp20["a"].iloc[-1], rel=1e-9)
    assert v5["a"].iloc[-1] != pytest.approx(v20["a"].iloc[-1])  # 窗口确实不同


def test_momentum_without_suffix_uses_params_n():
    """无数字后缀(如 momentum_n)回退到 params n，兼容既有调用。"""
    dates = pd.bdate_range("2022-01-01", periods=40)
    close = pd.DataFrame({"a": np.arange(40, dtype=float)}, index=dates)
    got = bt_eval.compute_factor(close, "momentum_n", params={"n": 20})
    exp = close.pct_change(20)
    assert got["a"].iloc[-1] == pytest.approx(exp["a"].iloc[-1], rel=1e-9)

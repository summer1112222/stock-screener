# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import signals as bt_sig


def _synth(n=60, n_codes=3):
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-01", periods=n)
    px = 10 + np.cumsum(rng.normal(0, 0.5, (n, n_codes)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=[f"c{i}" for i in range(n_codes)])
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, (n, n_codes)),
                          index=dates, columns=close.columns)
    return close, amount


def test_backtest_signals_returns_winrate(monkeypatch):
    close, amount = _synth()
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))
    res = bt_sig.backtest_signals("stock", list(close.columns),
                                  signal_types=["ma_breakout","rsi_oversold"],
                                  k_days=5, benchmark=None)
    assert "error" not in res
    assert res["n_scanned"] == close.shape[1]
    for r in res["rows"]:
        assert r["n_samples"] >= 0
        if r["n_samples"] >= 10:
            assert 0.0 <= r["abs_win_rate"] <= 1.0
        else:
            assert "样本不足" in (r.get("note") or "")


def test_backtest_signals_too_short(monkeypatch):
    close, amount = _synth(n=20)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))
    res = bt_sig.backtest_signals("stock", list(close.columns), k_days=5, benchmark=None)
    assert "error" in res


def test_backtest_signals_excess_winrate(monkeypatch):
    """excess_win_rate 路径：合成 benchmark 非 None 时，触发的信号应有 excess_win_rate
    数值（[0,1] 内）或 None，不能因聚合 bug 恒为 None。"""
    close, amount = _synth(n=80, n_codes=4)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))

    # 合成基准 close：与 close 同 index，略偏以产生超额
    bench_close = close.mean(axis=1) * 1.0
    bench_df = pd.DataFrame({"date": bench_close.index.astype(str),
                             "close": bench_close.values})

    import data.history as bt_hist
    monkeypatch.setattr(bt_hist, "fetch_benchmark_hist",
                        lambda code, start="19900101", end="20991231":
                            (bench_df, True, None))

    res = bt_sig.backtest_signals("stock", list(close.columns),
                                  signal_types=["ma_breakout", "golden_cross",
                                                "momentum_up"],
                                  k_days=5, benchmark="sh000300")
    assert "error" not in res
    has_trigger = False
    has_excess_val = False
    for r in res["rows"]:
        if r.get("n_samples", 0) >= 10:
            has_trigger = True
            ewr = r.get("excess_win_rate")
            # benchmark 非 None 时，触发信号的 excess_win_rate 应为数值（不是恒 None）
            if ewr is not None:
                assert 0.0 <= ewr <= 1.0
                has_excess_val = True
    assert has_trigger, "合成数据应至少触发一个信号"
    # 至少有一个信号的 excess_win_rate 被算出数值（证明 excess 路径生效）
    assert has_excess_val, "excess_win_rate 全为 None，excess 聚合路径未生效"


def test_scan_signals_has_signal_keys(monkeypatch):
    """scan_signals 返回的每条 row 应含 signal_keys(type key 列表)，
    与 signals 显示串一一对应。口径4 依赖此字段做胜率加权。"""
    close, amount = _synth(n=60, n_codes=3)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))
    res = bt_sig.scan_signals("stock", list(close.columns))
    for r in res.get("rows", []):
        assert "signal_keys" in r
        assert len(r["signal_keys"]) == len(r["signals"])
        for k in r["signal_keys"]:
            assert k in {"ma_breakout", "golden_cross", "volume_surge",
                         "rsi_oversold", "momentum_up"}


def _synth_ohlc(n=90, n_codes=3, crash_at=55):
    """合成 close/amount/high/low；在 crash_at 起制造**持续**暴跌(close 永久下台阶、
    low 更低)——使无止损的 t→t+k 收益为深负，止损版被封在 -stop_loss。"""
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2022-01-01", periods=n)
    px = 10 + np.cumsum(rng.normal(0, 0.3, (n, n_codes)), axis=0)
    close = pd.DataFrame(px, index=dates, columns=[f"c{i}" for i in range(n_codes)])
    high = close + rng.uniform(0.05, 0.3, (n, n_codes))
    low = close - rng.uniform(0.05, 0.3, (n, n_codes))
    # 持续暴跌：crash_at 起 close/high/low 永久 ×0.8(相对入场 pre 约 -20%，触发 8% 止损)
    close.iloc[crash_at:] = close.iloc[crash_at:] * 0.8
    high.iloc[crash_at:] = high.iloc[crash_at:] * 0.8
    low.iloc[crash_at:] = low.iloc[crash_at:] * 0.8
    amount = pd.DataFrame(rng.uniform(1e8, 1e9, (n, n_codes)),
                          index=dates, columns=close.columns)
    return close, amount, high, low


def test_scan_min_hits_filters(monkeypatch):
    """min_hits=2 时 rows 全 hits>=2；min_hits=1 应包含更多或同等。"""
    close, amount = _synth(n=80, n_codes=4)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))
    res1 = bt_sig.scan_signals("stock", list(close.columns), min_hits=1)
    res2 = bt_sig.scan_signals("stock", list(close.columns), min_hits=2)
    for r in res2.get("rows", []):
        assert r["hits"] >= 2
        assert "combo" in r and r["combo"]  # combo 非空
    assert res1.get("min_hits") == 1 and res2.get("min_hits") == 2
    assert len(res2["rows"]) <= len(res1["rows"])  # 共振过滤只会更少或相等


def test_backtest_combo_min_hits(monkeypatch):
    """min_hits=2 时回测结果应含 signal='combo≥2' 行。"""
    close, amount = _synth(n=90, n_codes=4)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))
    res = bt_sig.backtest_signals("stock", list(close.columns),
                                  signal_types=["ma_breakout", "golden_cross",
                                                "momentum_up"],
                                  k_days=5, benchmark=None, min_hits=2)
    assert "error" not in res
    assert res["min_hits"] == 2
    sigs = [r["signal"] for r in res["rows"]]
    assert "combo≥2" in sigs
    combo = next(r for r in res["rows"] if r["signal"] == "combo≥2")
    assert combo["n_samples"] >= 0


def test_backtest_stop_loss(monkeypatch):
    """stop_loss 封底属性：止损版每笔收益≥-stop_loss(故 mean/median≥-stop_loss)；
    且持续暴跌数据下，止损版 mean_ret ≥ 无止损版(深负被截断)。"""
    close, amount, high, low = _synth_ohlc(n=90, n_codes=3, crash_at=55)

    def panel(u, c, with_ohlc=False):
        return (close, amount, high, low) if with_ohlc else (close, amount)

    monkeypatch.setattr(bt_sig, "_uni_panels", panel)
    sigs = ["ma_breakout", "momentum_up"]
    r0 = bt_sig.backtest_signals("stock", list(close.columns),
                                 signal_types=sigs, k_days=5, benchmark=None, stop_loss=None)
    r1 = bt_sig.backtest_signals("stock", list(close.columns),
                                 signal_types=sigs, k_days=5, benchmark=None, stop_loss=0.08)
    assert "error" not in r1 and r1["stop_loss"] == 0.08
    by0 = {r["signal"]: r for r in r0["rows"]}
    for r in r1["rows"]:
        if r.get("n_samples", 0) >= 10:
            # 封底：止损版每个统计量不低于 -stop_loss
            assert r["mean_ret"] >= -0.08 - 1e-9
            assert r["median_ret"] >= -0.08 - 1e-9
            base = by0.get(r["signal"])
            if base and base.get("n_samples", 0) >= 10:
                # 持续暴跌下，止损把深负收益截断 → mean 不低于无止损
                assert r["mean_ret"] >= base["mean_ret"] - 1e-9


def test_backtest_fee_bps(monkeypatch):
    """fee_bps=10 的 mean_ret 应比 fee_bps=0 小约 2*1e-3(双边成本)。"""
    close, amount = _synth(n=90, n_codes=3)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c, with_ohlc=False: (close, amount))
    r0 = bt_sig.backtest_signals("stock", list(close.columns),
                                 signal_types=["ma_breakout", "momentum_up"],
                                 k_days=5, benchmark=None, fee_bps=0)
    r1 = bt_sig.backtest_signals("stock", list(close.columns),
                                 signal_types=["ma_breakout", "momentum_up"],
                                 k_days=5, benchmark=None, fee_bps=10)
    assert r1["fee_bps"] == 10
    by0 = {r["signal"]: r for r in r0["rows"]}
    for r in r1["rows"]:
        base = by0.get(r["signal"])
        if base and base.get("n_samples", 0) >= 10 and r.get("n_samples", 0) >= 10:
            # 双边费率 2*10bp = 0.002
            assert abs((base["mean_ret"] - r["mean_ret"]) - 0.002) < 1e-6

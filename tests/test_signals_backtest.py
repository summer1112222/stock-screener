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
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c: (close, amount))
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
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c: (close, amount))
    res = bt_sig.backtest_signals("stock", list(close.columns), k_days=5, benchmark=None)
    assert "error" in res


def test_backtest_signals_excess_winrate(monkeypatch):
    """excess_win_rate 路径：合成 benchmark 非 None 时，触发的信号应有 excess_win_rate
    数值（[0,1] 内）或 None，不能因聚合 bug 恒为 None。"""
    close, amount = _synth(n=80, n_codes=4)
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c: (close, amount))

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
    monkeypatch.setattr(bt_sig, "_uni_panels", lambda u, c: (close, amount))
    res = bt_sig.scan_signals("stock", list(close.columns))
    for r in res.get("rows", []):
        assert "signal_keys" in r
        assert len(r["signal_keys"]) == len(r["signals"])
        for k in r["signal_keys"]:
            assert k in {"ma_breakout", "golden_cross", "volume_surge",
                         "rsi_oversold", "momentum_up"}

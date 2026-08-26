import pandas as pd
import numpy as np
from backtest import signals


def test_signal_backtest_accepts_next_open_mode(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    close = pd.DataFrame({"A": np.linspace(10, 12, 30)}, index=dates)
    amount = pd.DataFrame({"A": [100.0] * 30}, index=dates)
    open_prices = close + 0.1
    monkeypatch.setattr(signals, "_uni_panels", lambda *args, **kwargs: (
        close, amount, None, None, open_prices
    ) if kwargs.get("with_ohlc") else (close, amount))
    result = signals.backtest_signals("stock", ["A"], signal_types=["momentum_up"],
                                      k_days=3, benchmark=None, execution_mode="next_open",
                                      open_prices=open_prices)
    assert result["execution_mode"] == "next_open"
    assert result["rows"]

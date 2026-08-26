import pandas as pd
from backtest import engine


def test_run_backtest_uses_next_open_when_supplied():
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    close = pd.DataFrame({"A": [10] * 10, "B": [10] * 10}, index=idx)
    factor = pd.DataFrame({"A": [2] * 10, "B": [1] * 10}, index=idx)
    opening = close.copy()
    opening.loc[idx[5], "A"] = 12
    result = engine.run_backtest(close, factor, topn=1, freq="W", cost_bps=0,
                                 open_prices=opening, execution_mode="next_open")
    assert result["execution_mode"] == "next_open"
    assert result["equity_curve"][str(idx[5].date())] > 1.0


def test_run_backtest_default_remains_close_mode():
    idx = pd.date_range("2026-01-01", periods=4, freq="D")
    close = pd.DataFrame({"A": [10, 10, 10, 10]}, index=idx)
    factor = close.copy()
    result = engine.run_backtest(close, factor, topn=1, freq="W")
    assert result["execution_mode"] == "close"

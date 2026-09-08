import pandas as pd

from screener.indicators import (
    ema,
    ma_alignment,
    price_volume_corr,
    rolling_return,
    rolling_volatility,
    rsi,
    sma,
    volume_ratio,
)


def test_sma_and_ema_are_series_indicators():
    values = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert pd.isna(sma(values, 2).iloc[0])
    assert sma(values, 2).iloc[1:].tolist() == [1.5, 2.5, 3.5]
    assert ema(values, 2).round(6).tolist() == [1.0, 1.666667, 2.555556, 3.518519]


def test_rsi_uses_simple_rolling_gains_and_losses():
    values = pd.Series([1.0, 2.0, 3.0, 2.0, 3.0])
    result = rsi(values, 2)
    assert pd.isna(result.iloc[0])
    assert result.iloc[2] == 100.0
    assert result.iloc[3] == 50.0


def test_return_volatility_and_volume_ratio_preserve_missing_warmup():
    close = pd.Series([10.0, 11.0, 12.0, 10.0])
    volume = pd.Series([100.0, 200.0, 300.0, 200.0])
    assert round(rolling_return(close, 2).iloc[2], 10) == 0.2
    assert pd.isna(rolling_volatility(close, 3).iloc[1])
    assert volume_ratio(volume, 2).iloc[-1] == 0.8


def test_price_volume_corr_and_ma_alignment():
    close = pd.Series([1.0, 2.0, 3.0, 4.0])
    volume = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert round(price_volume_corr(close, volume, 3).iloc[-1], 6) == 1.0
    aligned = ma_alignment(close, (2, 3))
    assert list(aligned.columns) == ["ma2", "ma3"]
    assert aligned.iloc[-1].tolist() == [3.5, 3.0]

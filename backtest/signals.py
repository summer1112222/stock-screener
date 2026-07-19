# -*- coding: utf-8 -*-
"""买卖信号扫描：在已抓历史的标的上，按规则算"今日触发"。

个人本地用，规则机械触发，非AI推荐。
信号类型：
  ma_breakout   收价上穿 MA20(昨<=MA20，今>MA20)
  golden_cross  MA5 上穿 MA20
  volume_surge  今日成交额 > 5日均额 × 2
  rsi_oversold  RSI(14) < 30
  momentum_up   20日动量为正且今日收红
依赖 *_daily 历史表(需先 /api/backtest/fetch 拉该 codes 历史)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data import db
from data.history import _UNIVERSE


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _uni_panels(universe: str, codes: list[str]):
    table, _, key = _UNIVERSE[universe]
    rows = db.query_rows(table)
    if not rows:
        return None, None
    df = pd.DataFrame(rows)
    df = df[df[key].isin(codes)]
    if df.empty:
        return None, None
    close = df.pivot_table(index="date", columns=key, values="close", aggfunc="last").sort_index()
    close.index = pd.to_datetime(close.index)
    close = close.astype(float)
    amount = None
    if "amount" in df.columns:
        amount = df.pivot_table(index="date", columns=key, values="amount", aggfunc="last").sort_index()
        amount.index = pd.to_datetime(amount.index)
        amount = amount.astype(float)
    return close, amount


def scan_signals(universe: str, codes: list[str],
                 signal_types: list[str] | None = None) -> dict:
    """扫描今日触发的标的。返回 {rows, n_scanned, signals}。"""
    if not codes:
        return {"rows": [], "n_scanned": 0, "error": "需提供 codes(已抓历史的标的)"}
    signal_types = signal_types or ["ma_breakout", "golden_cross", "volume_surge",
                                    "rsi_oversold", "momentum_up"]
    close, amount = _uni_panels(universe, codes)
    if close is None or close.empty:
        return {"rows": [], "n_scanned": 0, "error": "无历史数据，先 /api/backtest/fetch"}
    if len(close) < 25:
        return {"rows": [], "n_scanned": len(close.columns),
                "error": "历史不足25日，无法算MA/RSI"}
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    last_close = close.iloc[-1]
    prev_close = close.iloc[-2] if len(close) >= 2 else None
    last_ma5, prev_ma5 = ma5.iloc[-1], ma5.iloc[-2]
    last_ma20, prev_ma20 = ma20.iloc[-1], ma20.iloc[-2]
    mom20 = close.pct_change(20).iloc[-1]
    rsi = _rsi(close, 14).iloc[-1]
    vol_avg5 = amount.rolling(5).mean().iloc[-1] if amount is not None else None
    last_amt = amount.iloc[-1] if amount is not None else None

    out = []
    for code in close.columns:
        triggers = []
        signal_keys = []
        if "ma_breakout" in signal_types and prev_close is not None:
            if prev_close[code] <= prev_ma20[code] and last_close[code] > last_ma20[code]:
                triggers.append(f"上穿MA20({round(last_ma20[code],2)})")
                signal_keys.append("ma_breakout")
        if "golden_cross" in signal_types:
            if prev_ma5[code] <= prev_ma20[code] and last_ma5[code] > last_ma20[code]:
                triggers.append("MA5金叉MA20")
                signal_keys.append("golden_cross")
        if "volume_surge" in signal_types and last_amt is not None and vol_avg5 is not None:
            if vol_avg5[code] and last_amt[code] > vol_avg5[code] * 2:
                triggers.append(f"放量({last_amt[code]/vol_avg5[code]:.1f}×5日均)")
                signal_keys.append("volume_surge")
        if "rsi_oversold" in signal_types:
            if rsi[code] is not None and not np.isnan(rsi[code]) and rsi[code] < 30:
                triggers.append(f"RSI超卖({round(rsi[code],1)})")
                signal_keys.append("rsi_oversold")
        if "momentum_up" in signal_types:
            if mom20[code] > 0 and last_close[code] > prev_close[code]:
                triggers.append(f"20日动量为正({round(mom20[code]*100,1)}%)")
                signal_keys.append("momentum_up")
        if triggers:
            out.append({"code": code, "close": round(float(last_close[code]), 4),
                         "signals": triggers, "signal_keys": signal_keys})
    return {"rows": out, "n_scanned": len(close.columns),
            "signal_types": signal_types}


def backtest_signals(universe: str, codes: list[str],
                     signal_types: list[str] | None = None,
                     k_days: int = 5, benchmark: str | None = "sh000300") -> dict:
    """历史胜率回测：对每信号扫历史每个交易日 t，若 t 日触发则记 t→t+k 收益。
    合规：历史触发统计事实，非预测。"""
    if not codes:
        return {"error": "需提供 codes(已抓历史的标的)"}
    signal_types = signal_types or ["ma_breakout", "golden_cross", "volume_surge",
                                   "rsi_oversold", "momentum_up"]
    close, amount = _uni_panels(universe, codes)
    if close is None or close.empty:
        return {"error": "无历史数据，先 /api/backtest/fetch"}
    need = 25 + k_days
    if len(close) < need:
        return {"error": f"历史不足{need}日(25+k_days)"}

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    rsi = _rsi(close, 14)
    vol_avg5 = amount.rolling(5).mean() if amount is not None else None
    mom20 = close.pct_change(20)
    fwd = close.shift(-k_days) / close - 1

    bench_fwd = None
    if benchmark:
        try:
            from data.history import fetch_benchmark_hist
            bdf, ok, _ = fetch_benchmark_hist(benchmark, start="19900101", end="20991231")
            if ok and not bdf.empty and "close" in bdf.columns:
                bs = bdf.set_index("date")["close"].astype(float).sort_index()
                bs.index = pd.to_datetime(bs.index)
                bs = bs.reindex(close.index).ffill()
                bench_fwd = bs.shift(-k_days) / bs - 1
        except Exception:
            bench_fwd = None

    masks = {}
    if "ma_breakout" in signal_types:
        masks["ma_breakout"] = (close.shift(1) <= ma20.shift(1)) & (close > ma20)
    if "golden_cross" in signal_types:
        masks["golden_cross"] = (ma5.shift(1) <= ma20.shift(1)) & (ma5 > ma20)
    if "volume_surge" in signal_types and vol_avg5 is not None:
        masks["volume_surge"] = (amount > vol_avg5 * 2) & amount.notna() & (vol_avg5 > 0)
    if "rsi_oversold" in signal_types:
        masks["rsi_oversold"] = rsi < 30
    if "momentum_up" in signal_types:
        masks["momentum_up"] = (mom20 > 0) & (close > close.shift(1))

    out = []
    for s in signal_types:
        m = masks.get(s)
        if m is None or not m.any().any():
            out.append({"signal": s, "triggers": 0, "abs_win_rate": None,
                        "excess_win_rate": None, "mean_ret": None,
                        "median_ret": None, "n_samples": 0, "note": "无触发"})
            continue
        sel = m & fwd.notna()
        # 多 code 下 .dropna(how='any') 会丢任何含 NaN 行，严重低估；改按元素取有限值。
        rets = fwd.values[sel.values]
        rets = rets[np.isfinite(rets)]
        n = len(rets)
        if n < 10:
            out.append({"signal": s, "triggers": int(m.sum().sum()),
                        "abs_win_rate": None, "excess_win_rate": None,
                        "mean_ret": None, "median_ret": None,
                        "n_samples": n, "note": "样本不足(<10)"})
            continue
        abs_wr = float((rets > 0).mean())
        exc_wr = None
        if bench_fwd is not None:
            bench_arr = bench_fwd.reindex(m.index).fillna(np.nan).values.reshape(-1, 1)
            bench_mat = pd.DataFrame(np.tile(bench_arr, (1, len(m.columns))),
                                     index=m.index, columns=m.columns)
            excess = (fwd.values[sel.values] - bench_mat.values[sel.values])
            excess = excess[np.isfinite(excess)]
            if len(excess) > 0:
                exc_wr = float((excess > 0).mean())
        out.append({
            "signal": s, "triggers": int(m.sum().sum()),
            "abs_win_rate": round(abs_wr, 4),
            "excess_win_rate": round(exc_wr, 4) if exc_wr is not None else None,
            "mean_ret": round(float(np.mean(rets)), 4),
            "median_ret": round(float(np.median(rets)), 4),
            "n_samples": n, "note": "",
        })
    return {"rows": out, "n_scanned": len(close.columns),
            "k_days": k_days, "signals": signal_types}

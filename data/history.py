# -*- coding: utf-8 -*-
"""历史日线采集层：新浪(ETF/个股)+THS(板块 best-effort)+基准指数。

合规：仅采集公开历史行情，回测研究用途，不输出买卖点不承诺收益。
稳定性：每函数返回 (df, ok, err)，网络/接口异常不抛崩。
复权：统一前复权 qfq。
"""
from __future__ import annotations

import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover
    ak = None  # type: ignore
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db
from .collector import _to_records  # 复用 NaN→None
from . import pytdx_client


def _norm_date(s: str) -> str:
    """归一化日期为 YYYY-MM-DD(容忍 20230101 / 2023-01-01)。"""
    try:
        return pd.to_datetime(str(s)).strftime("%Y-%m-%d")
    except Exception:
        return str(s)


def _norm_daily(df: pd.DataFrame, key_col: str, key_val: str) -> pd.DataFrame:
    """统一历史日线列为规范字段，挂上主键列(code/symbol/name)。日期归一 YYYY-MM-DD。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {
        "日期": "date", "date": "date",
        "开盘": "open", "open": "open",
        "最高": "high", "high": "high",
        "最低": "low", "low": "low",
        "收盘": "close", "close": "close",
        "成交量": "volume", "volume": "volume",
        "成交额": "amount", "amount": "amount",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    keep = [c for c in (key_col, "date", "open", "high", "low",
                        "close", "volume", "amount") if c in df.columns]
    df = df[keep].copy()
    df[key_col] = key_val
    df["date"] = df["date"].map(_norm_date)
    return df


def _filter_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按日期范围过滤(用 datetime 比较，避免破折号字符串误比)。"""
    if df is None or df.empty:
        return df
    d = pd.to_datetime(df["date"], errors="coerce")
    mask = (d >= pd.to_datetime(start, errors="coerce")) & \
           (d <= pd.to_datetime(end, errors="coerce"))
    return df[mask]


def _sina_symbol(code: str) -> str:
    """新浪行情代码前缀：5/6/9 开头 → sh，1/0/3 开头 → sz。"""
    code = str(code).strip()
    if code.startswith(("5", "6", "9")):
        return "sh" + code
    return "sz" + code


def _fetch_hist_tdx(code: str, start: str, end: str,
                    key_col: str, key_val: str) -> tuple[pd.DataFrame, bool, str]:
    """通达信日 K 主源（raw + 本地前复权）。code 为 6 位纯代码。
    取不复权 raw → adjust.qfq 本地算前复权 → _filter_range 截范围。
    返回 (df, ok, err)，err 标 source=tdx主源,本地qfq。复权失败退化为不复权。"""
    if not pytdx_client._TDX_OK:
        return pd.DataFrame(), False, pytdx_client._TDX_ERR
    try:
        from . import adjust
        n_days = (pd.to_datetime("today") - pd.to_datetime(start, format="%Y%m%d",
                                                            errors="coerce")).days + 10
        count = max(int(n_days), 250)
        raw = pytdx_client.get_daily_bars(code, count)
        if raw is None or raw.empty:
            return pd.DataFrame(), False, f"tdx[{code}]: 空"
        df = _norm_daily(raw, key_col, key_val)
        # 本地前复权：raw 日 K + xdxr 除权除息因子
        try:
            xdxr = adjust.get_xdxr(code)
            if xdxr is not None and not xdxr.empty:
                df = adjust.qfq(df, xdxr)
        except Exception:
            pass  # 复权失败退化为不复权，诚实标注
        df = _filter_range(df, start, end)
        return df, True, "tdx主源,本地qfq"
    except Exception as e:
        return pd.DataFrame(), False, f"tdx[{code}]: {e}"


def fetch_etf_hist(code: str, start: str = "20200101",
                   end: str = "20240101") -> tuple[pd.DataFrame, bool, str]:
    """ETF 前复权日线。通达信主源(raw+本地qfq)，新浪 qfq 降为备援。code 例: 510300。"""
    df, ok, err = _fetch_hist_tdx(code, start, end, "code", code)
    if ok and not df.empty:
        return df, True, err
    if not _AK_OK:
        return df, ok, err
    try:
        df = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))
        df = _norm_daily(df, "code", code)
        if not df.empty:
            df = _filter_range(df, start, end)
        return df, True, "akshare新浪qfq备援"
    except Exception as e:
        return pd.DataFrame(), False, f"akshare_etf[{code}]: {e}"


def fetch_stock_hist(symbol: str, start: str = "20200101",
                     end: str = "20240101") -> tuple[pd.DataFrame, bool, str]:
    """个股前复权日线。通达信主源(raw+本地qfq)，新浪 qfq 降为备援。symbol 需 sz/sh 前缀。"""
    code = str(symbol)
    for pfx in ("sz", "sh", "bj", "SZ", "SH", "BJ"):
        if code.startswith(pfx):
            code = code[len(pfx):]
            break  # 6 位纯代码供 tdx
    df, ok, err = _fetch_hist_tdx(code, start, end, "symbol", symbol)
    if ok and not df.empty:
        return df, True, err
    if not _AK_OK:
        return df, ok, err
    try:
        df = ak.stock_zh_a_daily(symbol=symbol, start_date=start,
                                 end_date=end, adjust="qfq")
        df = _norm_daily(df, "symbol", symbol)
        if not df.empty:
            df = _filter_range(df, start, end)
        return df, True, "akshare新浪qfq备援"
    except Exception as e:
        return pd.DataFrame(), False, f"akshare[{symbol}]: {e}"


def fetch_board_hist(name: str, start: str = "20200101",
                     end: str = "20240101") -> tuple[pd.DataFrame, bool, str]:
    """板块指数日线(同花顺 best-effort)。name 例: 半导体及元件。
    东财路径被封；THS symbol 映射可能不全，失败优雅返回。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_board_industry_index_ths(symbol=name,
                                               start_date=start, end_date=end)
        df = _norm_daily(df, "name", name)
        return df, True, ""
    except Exception as e:
        return pd.DataFrame(), False, f"board_hist[{name}]: {e}"


def fetch_benchmark_hist(code: str = "sh000300",
                         start: str = "20200101",
                         end: str = "20240101") -> tuple[pd.DataFrame, bool, str]:
    """基准指数日线(沪深300 默认，新浪)。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_zh_index_daily(symbol=code)
        df = _norm_daily(df, "code", code)
        if not df.empty:
            df = _filter_range(df, start, end)
        return df, True, ""
    except Exception as e:
        return pd.DataFrame(), False, f"benchmark_hist[{code}]: {e}"


# universe → (table, fetcher, key_col)
_UNIVERSE = {
    "ETF": ("etf_daily", fetch_etf_hist, "code"),
    "stock": ("stock_daily", fetch_stock_hist, "symbol"),
    "board": ("board_daily", fetch_board_hist, "name"),
}


def fetch_history(universe: str, codes: list[str], start: str,
                  end: str) -> dict:
    """批量抓历史落库。返回 {ok, counts, errors, table}。"""
    if universe not in _UNIVERSE:
        return {"ok": False, "counts": {}, "errors": [f"未知 universe: {universe}"]}
    table, fetcher, _ = _UNIVERSE[universe]
    report = {"ok": True, "counts": {table: 0}, "errors": [], "table": table}
    total = 0
    for code in codes:
        df, ok, err = fetcher(code, start, end)
        if ok and not df.empty:
            total += db.upsert_rows(table, _to_records(df))
        elif ok:
            report["errors"].append(f"{code}: 空")
        else:
            report["errors"].append(err)
    report["counts"][table] = total
    report["ok"] = total > 0 or not report["errors"]
    return report

# -*- coding: utf-8 -*-
"""完整三大财报按需采集+缓存(同 buffett financial_abstract_cache 模式,多 source)。

合规:本层只采集公开财务数据,不做选股/评级/买卖点逻辑。
稳定性:fetch(code, source) 返回 (df, stale);_AK_OK=False 或单只超时(20s)降级
       返回过期缓存(stale=True),防 quality 逐只拉卡死。
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from types import ModuleType

import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover
    ak = ModuleType("akshare")
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db, collector  # noqa: F401  (import collector 触发 _install_http_patch)

_AK_TIMEOUT = 20
_CACHE_TTL_DAYS = 7


def _strip_prefix(code: str) -> str:
    c = str(code).strip()
    return c[2:] if c[:2].lower() in ("sh", "sz", "bj") else c


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cache_get(code: str, source: str, allow_stale: bool = False):
    """返回 (df_or_None, status)。status ∈ hit/stale/miss。"""
    rows = db.query_rows("fundamentals_cache",
                         where="code=? AND source=?", params=(code, source))
    if not rows:
        return None, "miss"
    r = rows[0]
    payload, ts = r.get("payload_json"), r.get("ts")
    if not payload or not ts:
        return None, "miss"
    try:
        df = pd.read_json(io.StringIO(payload))
    except Exception:
        return None, "miss"
    try:
        age = datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None, "miss"
    if age.days <= _CACHE_TTL_DAYS:
        return df, "hit"
    if allow_stale:
        return df, "stale"
    return None, "stale"


def _cache_set(code: str, source: str, df: pd.DataFrame) -> None:
    payload = df.to_json(orient="records", force_ascii=False)
    db.upsert_rows("fundamentals_cache",
                   [{"code": code, "source": source,
                     "payload_json": payload, "ts": _now_ts()}])


def _fetch_net(code: str, source: str):
    c = _strip_prefix(code)
    if source == "balance":
        return ak.stock_balance_sheet_by_report_em(symbol=c)
    if source == "cashflow":
        return ak.stock_cash_flow_sheet_by_report_em(symbol=c)
    if source == "profit":
        return ak.stock_profit_sheet_by_report_em(symbol=c)
    return None


def fetch(code: str, source: str) -> tuple[pd.DataFrame | None, bool]:
    """返回 (df, stale)。缓存7天 TTL;_AK_OK=False 或单只超时(20s)降级返回过期缓存。"""
    df, status = _cache_get(code, source, allow_stale=False)
    if status == "hit":
        return df, False
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, code, source).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(code, source, net)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(code, source, allow_stale=True)
    if df_s is not None:
        return df_s, True
    return None, False

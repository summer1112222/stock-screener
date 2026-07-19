# -*- coding: utf-8 -*-
"""研报评级+千股千评采集层。

合规:本层只采集公开机构研报/千股千评,归类为"机构视角机械汇总",
     不荐股、不输出买卖点、不承诺收益。
稳定性:fetch_reports 返回 (df, ok, err),异常不抛崩;fetch_comments 按需+缓存。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
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
from .models import RESEARCH_REPORT_ALIASES
from .fundamentals import _cache_get, _cache_set, _strip_prefix, _AK_TIMEOUT


def _to_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def fetch_reports(recent_days: int = 30) -> tuple[pd.DataFrame, bool, str]:
    """研报(列表型,进 refresh_all)。stock_research_report_em 按日期范围取近 N 日。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=recent_days)).strftime("%Y%m%d")
    try:
        df = ak.stock_research_report_em(start_date=start, end_date=end)
        norm = _normalize_report(df)
        if not norm.empty:
            norm["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return norm, True, ""
    except Exception as e:
        return pd.DataFrame(), False, f"research: {e}"


def _normalize_report(df: pd.DataFrame) -> pd.DataFrame:
    """RESEARCH_REPORT_ALIASES 归一,只保留规范字段,缺列补 None。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {}
    for col in df.columns:
        key = RESEARCH_REPORT_ALIASES.get(col)
        if key and key not in rename.values():
            rename[col] = key
    df = df.rename(columns=rename)
    keep = [v for v in RESEARCH_REPORT_ALIASES.values() if v in df.columns
            and v != "ts"]
    return df[keep].copy() if keep else pd.DataFrame()


def query_reports(code: str | None = None, days: int = 30,
                  limit: int = 200) -> dict:
    """从 research_report 表查近 N 日研报;code 非空则按 code 过滤。pub_date 降序。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    where, params = ["pub_date >= ?"], [since]
    if code:
        where.append("code = ?")
        params.append(code)
    rows = db.query_rows("research_report", where=" AND ".join(where),
                         params=tuple(params), order_by="pub_date DESC",
                         limit=limit)
    return {"rows": rows, "total": len(rows)}


def fetch_comments(code: str) -> tuple[dict | None, bool]:
    """千股千评(per-code 按需+缓存,source=comments)。超时降级返回 stale 缓存。"""
    code = _strip_prefix(code)
    cached, status = _cache_get(code, "comments", allow_stale=False)
    if status == "hit":
        return {"rows": cached.to_dict("records")}, False
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                df = ex.submit(lambda: ak.stock_comment_detail(symbol=code)
                               ).result(timeout=_AK_TIMEOUT)
            if df is not None and not df.empty:
                _cache_set(code, "comments", df)
                return {"rows": _to_records(df)}, False
        except (FuturesTimeout, Exception):
            pass
    cached_s, _ = _cache_get(code, "comments", allow_stale=True)
    if cached_s is not None:
        return {"rows": cached_s.to_dict("records")}, True
    return None, False

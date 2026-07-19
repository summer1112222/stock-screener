# -*- coding: utf-8 -*-
"""数据采集层：调用 AKShare 抓板块/ETF 数据，归一化字段，写 SQLite。

合规：本层只采集公开行情/资金流数据，不做任何选股/评级/买卖点逻辑。
稳定性：每个采集函数返回 (df, ok, err)，网络/接口异常不抛崩，由 refresh_all 汇总。
字段容错：AKShare 版本间列名可能微调，用 ALIASES 归一到规范键，缺列置 None。
"""
from __future__ import annotations

import os
import time
import pandas as pd
import requests
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError as ReqConnectionError,
)

try:
    import akshare as ak
    _AK_OK = True
    _AK_ERR = ""
except Exception as e:  # pragma: no cover - 装依赖失败时的兜底
    ak = None  # type: ignore
    _AK_OK = False
    _AK_ERR = f"akshare 未安装或导入失败: {e}"

from . import db
from .models import (BOARD_ALIASES, FUND_FLOW_ALIASES, ETF_ALIASES,
                     STOCK_SPOT_ALIASES, ST_LIST_ALIASES)


# ------------------------------------------------------------------
# HTTP 健壮化：东财 push2 接口对 python-requests 默认 UA 直接 502/断连，
# 且偶发 RemoteDisconnected。这里全局注入浏览器 UA+Referer，并对连接异常
# /502/503/504 做指数退避重试。仅对 eastmoney 域名生效，不影响其它请求。
# ------------------------------------------------------------------
_EASTMONEY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_EASTMONEY_REFERER = "https://quote.eastmoney.com/"
_MAX_RETRIES = 4
# 东财被封时走代理：设 SCREENER_HTTPS_PROXY=http://host:port (或 HTTPS_PROXY)。
_PROXY = os.environ.get("SCREENER_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") \
    or os.environ.get("SCREENER_HTTP_PROXY") or os.environ.get("HTTP_PROXY")


def _install_http_patch() -> None:
    _orig_request = requests.Session.request
    proxies = {"http": _PROXY, "https": _PROXY} if _PROXY else None

    def _patched_request(self, method, url, **kwargs):
        if "eastmoney.com" in url:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", _EASTMONEY_UA)
            headers.setdefault("Referer", _EASTMONEY_REFERER)
            kwargs["headers"] = headers
            if proxies:
                kwargs.setdefault("proxies", proxies)

        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = _orig_request(self, method, url, **kwargs)
            except (ReqConnectionError, ChunkedEncodingError) as e:
                last_exc = e
                if attempt + 1 < _MAX_RETRIES:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise
            # 502/503/504 多为东财侧瞬时过载，退避后重试
            if resp.status_code in (502, 503, 504) and attempt + 1 < _MAX_RETRIES:
                time.sleep(0.8 * (attempt + 1))
                continue
            return resp
        raise last_exc  # pragma: no cover

    requests.Session.request = _patched_request


_install_http_patch()


# akshare stock_sector_fund_flow_rank 入参映射：
#   sector_type 合法值 = 行业资金流 / 概念资金流 / 地域资金流 (非 行业/概念)
#   indicator   合法值 = 今日 / 5日 / 10日 (无 20日)
_SECTOR_TYPE_MAP = {
    "行业": "行业资金流",
    "概念": "概念资金流",
    "地域": "地域资金流",
}
_FLOW_INDICATORS_OK = ("今日", "5日", "10日")


# ------------------------------------------------------------------
# 字段归一化
# ------------------------------------------------------------------
def _normalize(df: pd.DataFrame, aliases: dict) -> pd.DataFrame:
    """把 AKShare 中文列名重命名为规范键，只保留命中规范键的列，缺列补 None。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rename_map = {}
    for col in df.columns:
        key = aliases.get(col)
        if key and key not in rename_map.values():
            rename_map[col] = key
    df = df.rename(columns=rename_map)
    # 只保留规范字段集(去重:aliases 同一规范键常含中英两键,致 keep 重复)
    keep = []
    seen = set()
    for v in aliases.values():
        if v in df.columns and v not in seen:
            seen.add(v)
            keep.append(v)
    return df[keep].copy()


def _to_records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    # NaN → None，便于 SQLite 写入(走 astype(object) 确保 float NaN 也转 None)
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


# ------------------------------------------------------------------
# 单类采集 (返回 df; 异常返回空 df + 错误)
# ------------------------------------------------------------------
def _industry_ths_df() -> pd.DataFrame:
    """同花顺行业板块汇总(东财被封时的备援源)。
    列含: 板块/涨跌幅/净流入/领涨股 等——可同时供给板块表与 今日/行业 资金流。"""
    return ak.stock_board_industry_summary_ths()


def _from_ths_industry_boards() -> pd.DataFrame:
    src = _industry_ths_df()
    return pd.DataFrame({
        "name": src.get("板块"),
        "code": None,
        "change_pct": pd.to_numeric(src.get("涨跌幅"), errors="coerce"),
        "total_market_cap": None,
        "turnover_rate": None,
        "turnover_amount": pd.to_numeric(src.get("总成交额"), errors="coerce"),
        "leading_stock": src.get("领涨股"),
        "up_count": pd.to_numeric(src.get("上涨家数"), errors="coerce"),
        "down_count": pd.to_numeric(src.get("下跌家数"), errors="coerce"),
        "leading_stock_change": pd.to_numeric(src.get("领涨股-涨跌幅"), errors="coerce"),
        "constituent_count": None,
        "event": None,
    })


def _from_ths_concept_boards() -> pd.DataFrame:
    """同花顺概念板块汇总(东财被封时的备援源)。
    列: 概念名称/驱动事件/龙头股/成分股数量 —— 无涨跌幅/资金流(富数据仅东财有)。"""
    src = ak.stock_board_concept_summary_ths()
    return pd.DataFrame({
        "name": src.get("概念名称"),
        "code": None,
        "change_pct": None,
        "total_market_cap": None,
        "turnover_rate": None,
        "turnover_amount": None,
        "leading_stock": src.get("龙头股"),
        "up_count": None,
        "down_count": None,
        "leading_stock_change": None,
        "constituent_count": pd.to_numeric(src.get("成分股数量"), errors="coerce"),
        "event": src.get("驱动事件"),
    })


def _from_ths_industry_flow_today() -> pd.DataFrame:
    """从行业 THS 汇总的 净流入 列派生 今日/行业 资金流。"""
    src = _industry_ths_df()
    return pd.DataFrame({
        "name": src.get("板块"),
        "indicator": "今日",
        "sector_type": "行业",
        "main_net_inflow": pd.to_numeric(src.get("净流入"), errors="coerce"),
        "super_large_net": None,
        "large_net": None,
        "medium_net": None,
        "small_net": None,
    })


def fetch_industry_boards() -> tuple[pd.DataFrame, bool, str]:
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    # 东财优先(字段全)；被封则落同花顺汇总(字段较稀，但含涨跌幅/领涨股/净流入)
    try:
        df = ak.stock_board_industry_name_em()
        return _normalize(df, BOARD_ALIASES), True, ""
    except Exception:
        pass
    try:
        return _from_ths_industry_boards(), True, "(THS备援)"
    except Exception as e:
        return pd.DataFrame(), False, f"industry_boards: 东财被封且THS失败: {e}"


def fetch_concept_boards() -> tuple[pd.DataFrame, bool, str]:
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_board_concept_name_em()
        return _normalize(df, BOARD_ALIASES), True, ""
    except Exception:
        pass
    # 概念无涨跌幅/资金流的THS源，退用 concept_summary_ths(龙头股/成分股数量/驱动事件)
    try:
        return _from_ths_concept_boards(), True, "(THS备援,概念富数据受限)"
    except Exception as e:
        return pd.DataFrame(), False, f"concept_boards: 东财被封且THS失败: {e}"


def fetch_sector_fund_flow(indicator: str = "今日",
                           sector_type: str = "行业") -> tuple[pd.DataFrame, bool, str]:
    """板块资金流排名。indicator: 今日/5日/10日；sector_type: 行业/概念(内部映射为行业资金流/概念资金流)。

    akshare 1.18.x 的 stock_sector_fund_flow_rank 入参已变：
    sector_type 必须是 行业资金流/概念资金流/地域资金流；indicator 不支持 20日。
    东财 push2 被封时，仅 今日/行业 可从同花顺行业汇总的 净流入 列备援；其余优雅失败。
    """
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    st = _SECTOR_TYPE_MAP.get(sector_type)
    if st is None:
        return pd.DataFrame(), False, f"fund_flow: 不支持的 sector_type={sector_type!r}"
    if indicator not in _FLOW_INDICATORS_OK:
        return pd.DataFrame(), False, (
            f"fund_flow: 不支持的 indicator={indicator!r}(仅 今日/5日/10日)")
    try:
        df = ak.stock_sector_fund_flow_rank(indicator=indicator,
                                            sector_type=st)
        df = _normalize(df, FUND_FLOW_ALIASES)
        if not df.empty:
            df["indicator"] = indicator
            df["sector_type"] = sector_type
        return df, True, ""
    except Exception:
        pass
    # 东财被封 → 仅 今日/行业 有 THS 备援
    if indicator == "今日" and sector_type == "行业":
        try:
            return _from_ths_industry_flow_today(), True, "(THS备援)"
        except Exception as e:
            return pd.DataFrame(), False, f"fund_flow[今日/行业 THS]: {e}"
    return pd.DataFrame(), False, (
        f"fund_flow[{indicator}/{sector_type}]: 东财被封且无THS备援")


def _to_num(v):
    """转 float，NaN/异常→None（新浪字段含字符串/NaN）。"""
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _sina_etf_to_records(df: pd.DataFrame) -> list[dict]:
    """fund_etf_category_sina(新浪实时) → etf_spot 规范记录；代码去 sh/sz/bj 前缀(与东财一致)。"""
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        raw = str(r.get("代码") or "")
        code = raw[2:] if raw[:2].lower() in ("sh", "sz", "bj") else raw
        out.append({
            "code": code,
            "name": r.get("名称"),
            "latest_price": _to_num(r.get("最新价")),
            "change_pct": _to_num(r.get("涨跌幅")),
            "turnover_amount": _to_num(r.get("成交额")),
            "turnover_rate": None,  # 新浪源无换手率
        })
    return out


def fetch_etf_spot() -> tuple[pd.DataFrame, bool, str]:
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    # 东财优先(字段全，含换手率)
    try:
        df = ak.fund_etf_spot_em()
        norm = _normalize(df, ETF_ALIASES)
        if not norm.empty:
            return norm, True, ""
    except Exception:
        pass
    # 新浪备援(实时，~382只，无换手率)；东财 push2 被封时启用
    try:
        df = ak.fund_etf_category_sina()
        recs = _sina_etf_to_records(df)
        return pd.DataFrame(recs), True, "(新浪备援,无换手率)"
    except Exception as e:
        return pd.DataFrame(), False, f"etf_spot: 东财被封且新浪失败: {e}"


def fetch_stock_spot() -> tuple[pd.DataFrame, bool, str]:
    """全市场个股 spot。新浪 stock_zh_a_spot 免代理(~5500只，量价字段)优先；
    东财 stock_zh_a_spot_em 字段更全(PE/PB/市值)但被封，代理(SCREENER_HTTPS_PROXY)后可用。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    err_sina = ""
    try:
        df = ak.stock_zh_a_spot()  # 新浪，慢(~60s)但通
        return _normalize(df, STOCK_SPOT_ALIASES), True, "(新浪,无PE/PB)"
    except Exception as e:
        err_sina = str(e)
    try:
        df = ak.stock_zh_a_spot_em()  # 东财，需代理
        return _normalize(df, STOCK_SPOT_ALIASES), True, "(东财)"
    except Exception as e:
        return pd.DataFrame(), False, f"stock_spot: 新浪={err_sina}; 东财={e}"


def fetch_st_list() -> tuple[pd.DataFrame, bool, str]:
    """ST/*ST 全名单(东财 stock_zh_a_st_em)。无 THS 备援,被封标不可用不崩。
    st_type 由 name 前缀解析。"""
    if not _AK_OK:
        return pd.DataFrame(), False, _AK_ERR
    try:
        df = ak.stock_zh_a_st_em()
        norm = _normalize(df, ST_LIST_ALIASES)
        if not norm.empty:
            def _t(n):
                s = str(n or "")
                return "*ST" if s.startswith("*ST") else "ST" if s.startswith("ST") else "其他"
            norm["st_type"] = norm["name"].map(_t)
            return norm, True, ""
        return pd.DataFrame(), False, "st_list: 空结果"
    except Exception as e:
        return pd.DataFrame(), False, f"st_list: {e}"


# ------------------------------------------------------------------
# 全量刷新入口
# ------------------------------------------------------------------
def refresh_all() -> dict:
    """串行抓取全量 → 写 SQLite → 记录更新时间。
    返回汇总报告 {ok: bool, counts: {...}, errors: [...]}。"""
    db.init_db()
    report = {"ok": True, "counts": {}, "errors": []}
    n_ok = 0

    df, ok, err = fetch_industry_boards()
    if ok:
        n = db.upsert_rows("industry_board", _to_records(df))
        report["counts"]["industry_board"] = n
        n_ok += 1
    else:
        report["errors"].append(err)
        report["counts"]["industry_board"] = 0

    df, ok, err = fetch_concept_boards()
    if ok:
        n = db.upsert_rows("concept_board", _to_records(df))
        report["counts"]["concept_board"] = n
        n_ok += 1
    else:
        report["errors"].append(err)
        report["counts"]["concept_board"] = 0

    # 资金流：2 个 sector_type × 3 个 indicator 全量抓(akshare 不支持 20日)
    flow_count = 0
    for sector_type in ("行业", "概念"):
        for indicator in ("今日", "5日", "10日"):
            df, ok, err = fetch_sector_fund_flow(indicator, sector_type)
            if ok:
                flow_count += db.upsert_rows("sector_fund_flow",
                                             _to_records(df))
            else:
                report["errors"].append(err)
    report["counts"]["sector_fund_flow"] = flow_count

    df, ok, err = fetch_etf_spot()
    if ok:
        n = db.upsert_rows("etf_spot", _to_records(df))
        report["counts"]["etf_spot"] = n
        n_ok += 1
    else:
        report["errors"].append(err)
        report["counts"]["etf_spot"] = 0

    # 个股全市场 spot(东财；直连被封需 SCREENER_HTTPS_PROXY 代理)
    df, ok, err = fetch_stock_spot()
    if ok:
        n = db.upsert_rows("stock_spot", _to_records(df))
        report["counts"]["stock_spot"] = n
        n_ok += 1
    else:
        report["errors"].append(err)
        report["counts"]["stock_spot"] = 0

    # ST 全名单(东财 stock_zh_a_st_em)
    df, ok, err = fetch_st_list()
    if ok:
        n = db.upsert_rows("st_list", _to_records(df))
        report["counts"]["st_list"] = n
        n_ok += 1
    else:
        report["errors"].append(err)
        report["counts"]["st_list"] = 0

    # 至少有一类成功才算本次刷新有效，更新时间
    if n_ok > 0 or flow_count > 0:
        report["ok"] = True
        report["update_time"] = db.stamp_update_time()
    else:
        report["ok"] = False
        report["update_time"] = db.last_update_time() or "(无)"
    return report

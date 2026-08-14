# -*- coding: utf-8 -*-
"""通达信(pytdx)行情直连客户端 —— 免 key、TCP 7709、不走 akshare。

定位：**按需备援源**（同 history.fetch_history 性质，不进 refresh_all）。
- 历史日 K 线：akshare(东财) 被封/失败时备援，**不复权**（诚实标 source=tdx）。
- 单股实时五档行情：spot 缺价时兜底，盘中实时（servertime 精确到毫秒）。
- 公司信息文本块：融资融券/股东研究/主力追踪/财务分析等 16 类（按需取，不解析）。

合规：通达信行情服务器直取，机械汇总/观察清单，非荐股非买卖信号，盈亏自负。
复权限制：pytdx get_security_bars 返回不复权数据，落库与 qfq 行混在同一 daily 表
（schema 无复权标记列，不增设列），回测除权缺口存在——此处不解决，仅在 err 标注。
"""
from __future__ import annotations

import threading
import pandas as pd

try:
    from pytdx.hq import TdxHq_API
    _TDX_OK = True
    _TDX_ERR = ""
except Exception as e:  # pragma: no cover
    TdxHq_API = None  # type: ignore
    _TDX_OK = False
    _TDX_ERR = f"pytdx 未安装或导入失败: {e}"

# 服务器池（已验证 115.238.90.165 可用；其余常见备选，连第一个成功的）
_SERVERS = [
    ("115.238.90.165", 7709),
    ("221.231.141.60", 7709),
    ("218.75.126.9", 7709),
    ("115.238.82.194", 7709),
    ("119.147.212.81", 7709),
]
_TIMEOUT = 8
_CAT_DAY = 4  # get_security_bars category: 4=日线


def _market(code: str) -> int | None:
    """6 位代码 → 通达信 market 号：5/6/9→沪=1，0/3→深=0，4/8→北交=2。
    北交所 pytdx market 号需实测确认，不确定时返回 None 由调用方降级。"""
    c = str(code).strip()
    if len(c) != 6 or not c.isdigit():
        return None
    head = c[0]
    if head in ("5", "6", "9"):
        return 1
    if head in ("0", "1", "3"):  # 1 开头=深市 ETF(1599xx/16xxxx)
        return 0
    if head in ("4", "8"):
        return 2  # 北交，未实测，失败则上层降级
    return None


def _project_symbol(code: str) -> str:
    """6 位代码 → 项目 stock_daily.symbol 列格式(sz000001/sh600519)。
    规则与 history._sina_symbol 一致，保证落库行与现有一致。"""
    c = str(code).strip()
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    return "sz" + c


# ---------- 连接管理（单例 + 锁，FastAPI 多线程安全） ----------
_api: "TdxHq_API | None" = None
_lock = threading.Lock()
_connected_host: str | None = None


def _get_api():
    """返回已连接的 TdxHq_API 单例；断线/未连则轮询服务器池重连。不可用返 None。"""
    global _api, _connected_host
    if not _TDX_OK:
        return None
    with _lock:
        if _api is not None and _connected_host:
            # 心跳探测：能取到任意行情即视为活连接
            try:
                _api.get_security_quotes([(0, "000001")])
                return _api
            except Exception:
                try:
                    _api.disconnect()
                except Exception:
                    pass
                _api = None
                _connected_host = None
        # 重连：轮询服务器池
        api = TdxHq_API()
        for host, port in _SERVERS:
            try:
                if api.connect(host, port, time_out=_TIMEOUT):
                    _api = api
                    _connected_host = host
                    return _api
            except Exception:
                continue
        return None


def _nan(v):
    """float，NaN/异常→None（防 JSONResponse allow_nan=False 500）。"""
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ---------- 公开接口 ----------

def get_quote(codes: list[str]) -> list[dict]:
    """批量实时五档行情。codes 为 6 位纯代码列表。
    返回规范 dict 列表：{code, price, last_close, open, high, low,
    vol(手), amount(元), bid1-5, ask1-5, bid_vol1-5, ask_vol1-5}。
    get_security_quotes 单批≤80，超量自动分批。失败返空 list（不抛崩）。"""
    if not _TDX_OK or not codes:
        return []
    pairs = []
    for c in codes:
        m = _market(c)
        if m is not None:
            pairs.append((m, str(c).strip()))
    if not pairs:
        return []
    api = _get_api()
    if api is None:
        return []
    out: list[dict] = []
    with _lock:
        for i in range(0, len(pairs), 80):
            batch = pairs[i:i + 80]
            try:
                df = api.to_df(api.get_security_quotes(batch))
            except Exception:
                continue
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                out.append({
                    "code": r.get("code"),
                    "price": _nan(r.get("price")),
                    "last_close": _nan(r.get("last_close")),
                    "open": _nan(r.get("open")),
                    "high": _nan(r.get("high")),
                    "low": _nan(r.get("low")),
                    "vol": _nan(r.get("vol")),
                    "amount": _nan(r.get("amount")),
                    "b_vol": _nan(r.get("b_vol")),  # 外盘(主动买量)
                    "s_vol": _nan(r.get("s_vol")),  # 内盘(主动卖量)
                    "bid1": _nan(r.get("bid1")), "ask1": _nan(r.get("ask1")),
                    "bid2": _nan(r.get("bid2")), "ask2": _nan(r.get("ask2")),
                    "bid3": _nan(r.get("bid3")), "ask3": _nan(r.get("ask3")),
                    "bid4": _nan(r.get("bid4")), "ask4": _nan(r.get("ask4")),
                    "bid5": _nan(r.get("bid5")), "ask5": _nan(r.get("ask5")),
                    "bid_vol1": _nan(r.get("bid_vol1")), "ask_vol1": _nan(r.get("ask_vol1")),
                    "bid_vol2": _nan(r.get("bid_vol2")), "ask_vol2": _nan(r.get("ask_vol2")),
                    "bid_vol3": _nan(r.get("bid_vol3")), "ask_vol3": _nan(r.get("ask_vol3")),
                    "bid_vol4": _nan(r.get("bid_vol4")), "ask_vol4": _nan(r.get("ask_vol4")),
                    "bid_vol5": _nan(r.get("bid_vol5")), "ask_vol5": _nan(r.get("ask_vol5")),
                })
    return out


_BATCH = 800  # get_security_bars 单次返回上限（偏移分页）


def get_daily_bars(code: str, count: int = 250) -> pd.DataFrame:
    """个股/ETF 日 K 线（不复权）。count 默认近 250 日，>800 自动偏移分页累加。
    返回规范 DataFrame：date(YYYY-MM-DD)/open/high/low/close/volume/amount/symbol。
    列名对齐 history._norm_daily 规范，可直接落 stock_daily/etf_daily。"""
    if not _TDX_OK or count <= 0:
        return pd.DataFrame()
    m = _market(code)
    if m is None:
        return pd.DataFrame()
    api = _get_api()
    if api is None:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    got = 0
    with _lock:
        while got < count:
            want = min(_BATCH, count - got)
            try:
                bars = api.get_security_bars(_CAT_DAY, m, str(code).strip(), got, want)
                df = api.to_df(bars)
            except Exception:
                break
            if df is None or df.empty:
                break
            frames.append(df)
            got += len(df)
            if len(df) < want:
                break  # 到头了
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={"vol": "volume", "datetime": "date"})
    df["date"] = df["date"].astype(str).str.slice(0, 10)
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["symbol"] = _project_symbol(code)
    df["code"] = str(code).strip()
    keep = [c for c in ("date", "open", "high", "low", "close",
                        "volume", "amount", "symbol", "code") if c in df.columns]
    return df[keep].reset_index(drop=True)


def get_company_info(code: str, category: str) -> dict:
    """公司信息文本块（按需取，不解析）。category ∈ 龙虎榜单/主力追踪/股东研究/
    财务分析/公司概况/股本结构/研究报告/业内点评 等 16 类。

    注意：通达信"龙虎榜单"类别实际含【融资融券/资金流向/涨跌幅异动/大宗交易】，
    并非游资席位龙虎榜（后者走 finshare get_lhb）。第一版返回原始文本，
    前端预格式显示；表格解析留后续迭代。

    返回 {code, category, content, ok, err}。"""
    base = {"code": code, "category": category,
            "content": "", "ok": False, "err": ""}
    if not _TDX_OK:
        base["err"] = _TDX_ERR
        return base
    m = _market(code)
    if m is None:
        base["err"] = f"无法识别 market: {code}"
        return base
    api = _get_api()
    if api is None:
        base["err"] = "通达信服务器全不可用"
        return base
    with _lock:
        try:
            cats = api.get_company_info_category(m, str(code).strip())
        except Exception as e:
            base["err"] = f"类别查询失败: {e}"
            return base
    target = None
    for c in cats or []:
        name = c.get("name") if isinstance(c, dict) else None
        if name and category in str(name):
            target = c
            break
    if not target:
        base["err"] = f"无此类别: {category}"
        return base
    with _lock:
        try:
            content = api.get_company_info_content(
                m, str(code).strip(),
                target.get("filename"), target.get("start"), target.get("length"))
            base["content"] = content if isinstance(content, str) else str(content)
            base["ok"] = True
        except Exception as e:
            base["err"] = f"文本取失败: {e}"
    return base


def get_xdxr(code: str) -> pd.DataFrame:
    """个股除权除息/股本变动全历史记录（用于本地算前复权）。
    依赖 _get_api/_market。失败/无记录返空 DataFrame（不抛崩）。
    返回 pytdx 原始列：year/month/day/category/name/fenhong/peigujia/
    songzhuangu/peigu/suogu/...（category 1=除权除息,5=股本变化）。"""
    if not _TDX_OK:
        return pd.DataFrame()
    m = _market(code)
    if m is None:
        return pd.DataFrame()
    api = _get_api()
    if api is None:
        return pd.DataFrame()
    with _lock:
        try:
            df = api.to_df(api.get_xdxr_info(m, str(code).strip()))
        except Exception:
            return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    return df.reset_index(drop=True)


def list_company_categories(code: str) -> list[str]:
    """列某股可取的公司信息类别名（供前端下拉/调试）。"""
    if not _TDX_OK:
        return []
    m = _market(code)
    if m is None:
        return []
    api = _get_api()
    if api is None:
        return []
    with _lock:
        try:
            cats = api.get_company_info_category(m, str(code).strip())
        except Exception:
            return []
    return [str(c.get("name")) for c in (cats or [])
            if isinstance(c, dict) and c.get("name")]

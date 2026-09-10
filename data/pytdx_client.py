# -*- coding: utf-8 -*-
"""通达信(pytdx)行情直连客户端 —— 免 key、TCP 7709、不走 akshare。

定位：**按需备援源**（同 history.fetch_history 性质，不进 refresh_all）。
- 历史日 K 线：akshare(东财) 被封/失败时备援，**不复权**（诚实标 source=tdx）。
- 单股实时五档行情：spot 缺价时兜底，盘中实时（servertime 精确到毫秒）。
- 公司信息文本块：融资融券/股东研究/主力追踪/财务分析等 16 类（按需取，不解析）。
- 财务概要：`get_finance_info` 提供流通股本/总股本/净利润/每股净资产等，供本地机械计算市值/PE。

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


def _pure_code(code: str) -> str:
    """统一 TDX 入参：接受 sh/sz/bj 前缀或纯 6 位代码。"""
    c = str(code or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if c.startswith(prefix) and c[len(prefix):].isdigit():
            return c[len(prefix):]
    return c


def _market(code: str) -> int | None:
    """6 位代码 → 通达信 market 号：5/6/9→沪=1，0/3→深=0，4/8→北交=2。
    北交所 pytdx market 号需实测确认，不确定时返回 None 由调用方降级。"""
    c = _pure_code(code)
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
    if not _lock.acquire(timeout=_TIMEOUT):
        return None
    try:
        if _api is not None and _connected_host:
            # 心跳探测：能取到任意行情即视为活连接
            try:
                # 连接未断开不代表行情通道仍可用；部分 TDX 节点会在
                # TCP 尚存时返回空列表。空响应也必须触发重连，避免
                # FastAPI 长驻进程一直复用失效连接而新进程却能取到行情。
                probe = _api.get_security_quotes([(0, "000001")])
                if probe:
                    return _api
            except Exception:
                pass
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
    finally:
        _lock.release()


def _nan(v):
    """float，NaN/异常→None（防 JSONResponse allow_nan=False 500）。"""
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _to_df_safe(api, raw) -> "pd.DataFrame":
    """``api.to_df`` 守卫:pytdx ``to_df(None)`` 返回**伪非空** ``{'value': None}``
    单行 DataFrame(把 None 包成一行),绕过 ``.empty`` 空守卫 → 下游 ``df["date"]``
    KeyError 崩 ``get_daily_bars``、``get_quote`` 返回全 None 字段行。

    raw 为 None/空时直接返空 DataFrame;to_df 抛异常也返空。调用方原有的
    ``if df is None or df.empty`` 守卫因此对 None raw 正确生效(旧实现对 None raw
    拿到伪非空 df 致守卫失效)。"""
    if not raw:
        return pd.DataFrame()
    try:
        return api.to_df(raw)
    except Exception:
        return pd.DataFrame()


# ---------- 公开接口 ----------

def get_finance_info(code: str) -> dict:
    """取通达信财务概要(流通股本/总股本/净利润/每股净资产等)。
    code 为 6 位纯代码；失败返空 dict，不让采集流程崩溃。"""
    if not _TDX_OK:
        return {}
    c = _pure_code(code)
    m = _market(c)
    if m is None:
        return {}
    api = _get_api()
    if api is None:
        return {}
    with _lock:
        try:
            info = api.get_finance_info(m, c)
        except Exception:
            return {}
    return dict(info) if info else {}


def get_quote(codes: list[str]) -> list[dict]:
    """批量实时五档行情。codes 为 6 位纯代码列表。
    返回规范 dict 列表：{code, price, last_close, open, high, low,
    vol(手), amount(元), bid1-5, ask1-5, bid_vol1-5, ask_vol1-5}。
    get_security_quotes 单批≤80，超量自动分批。失败返空 list（不抛崩）。"""
    if not _TDX_OK or not codes:
        return []
    pairs = []
    for c in codes:
        pure = _pure_code(c)
        m = _market(pure)
        if m is not None:
            pairs.append((m, pure))
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
                df = _to_df_safe(api, api.get_security_quotes(batch))
            except Exception:
                # 批量端点失败时仍保留逐只重试机会。
                df = pd.DataFrame()
            rows = [] if (df is None or df.empty) else list(df.iterrows())
            # 批量请求偶发只返回空行或不完整结果；对缺失代码逐只重试，
            # 让次日强势/深查主力优先拿到 TDX 盘口，而不是静默降级为空。
            valid_codes = set()
            for _, r in rows:
                raw_code = r.get("code")
                if raw_code is not None:
                    valid_codes.add(str(raw_code).strip())
                if raw_code is None:
                    continue
                out.append({
                    "code": str(raw_code),
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
            missing = [(m, c) for m, c in batch if c not in valid_codes]
            for pair in missing:
                try:
                    retry_df = _to_df_safe(api, api.get_security_quotes([pair]))
                except Exception:
                    continue
                if retry_df is None or retry_df.empty:
                    continue
                for _, r in retry_df.iterrows():
                    raw_code = r.get("code")
                    if raw_code is None or str(raw_code).strip() in valid_codes:
                        continue
                    valid_codes.add(str(raw_code).strip())
                    out.append({
                        "code": str(raw_code),
                        "price": _nan(r.get("price")),
                        "last_close": _nan(r.get("last_close")),
                        "open": _nan(r.get("open")),
                        "high": _nan(r.get("high")),
                        "low": _nan(r.get("low")),
                        "vol": _nan(r.get("vol")),
                        "amount": _nan(r.get("amount")),
                        "b_vol": _nan(r.get("b_vol")), "s_vol": _nan(r.get("s_vol")),
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
                bars = api.get_security_bars(_CAT_DAY, m, _pure_code(code), got, want)
                df = _to_df_safe(api, bars)
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
    # 列守卫:pytdx 版本差异/异常返回可能不含 datetime/vol 列,直接 rename→df["date"]
    # 会 KeyError 崩(旧实现对 None bars 经 to_df 得伪非空 {'value'} df 致此路径必崩)。
    if "datetime" in df.columns:
        df = df.rename(columns={"vol": "volume", "datetime": "date"})
    elif "date" not in df.columns:
        return pd.DataFrame()  # 无可识别日期列,放弃(降级上游 akshare 备援)
    if "date" in df.columns:
        df["date"] = df["date"].astype(str).str.slice(0, 10)
        df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["symbol"] = _project_symbol(_pure_code(code))
    df["code"] = _pure_code(code)
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
    pure = _pure_code(code)
    m = _market(pure)
    if m is None:
        base["err"] = f"无法识别 market: {code}"
        return base
    api = _get_api()
    if api is None:
        base["err"] = "通达信服务器全不可用"
        return base
    with _lock:
        try:
            cats = api.get_company_info_category(m, pure)
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
                m, pure,
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
    pure = _pure_code(code)
    m = _market(pure)
    if m is None:
        return pd.DataFrame()
    api = _get_api()
    if api is None:
        return pd.DataFrame()
    with _lock:
        try:
            df = _to_df_safe(api, api.get_xdxr_info(m, pure))
        except Exception:
            return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    # 守卫:伪非空 {'value'} df(to_df(None) 产物)无 xdxr 数据列→返空
    if "category" not in df.columns and "year" not in df.columns:
        return pd.DataFrame()
    return df.reset_index(drop=True)


def list_company_categories(code: str) -> list[str]:
    """列某股可取的公司信息类别名（供前端下拉/调试）。"""
    if not _TDX_OK:
        return []
    pure = _pure_code(code)
    m = _market(pure)
    if m is None:
        return []
    api = _get_api()
    if api is None:
        return []
    with _lock:
        try:
            cats = api.get_company_info_category(m, pure)
        except Exception:
            return []
    return [str(c.get("name")) for c in (cats or [])
            if isinstance(c, dict) and c.get("name")]


def get_block_members(category: str = "all") -> dict[str, list[str]]:
    """从通达信板块文件读取板块→股票代码成员映射。

    category 支持 ``industry``/``concept``/``all``。TDX 的板块文件包含
    本地板块快照，不含板块资金流；调用方应使用实时行情计算机械热度，不能
    将此结果描述为机构资金流。获取失败返回空字典，不阻断筛选流程。
    """
    if not _TDX_OK:
        return {}
    # block.dat=自定义板块+指数组，block_zs.dat=通达信行业分类(行业板块在此)，
    # block_gn.dat=概念板块。industry 读 block_zs.dat 才能匹配到目标个股。
    files = {
        "industry": ("block_zs.dat",),
        "concept": ("block_gn.dat",),
        "all": ("block.dat", "block_zs.dat", "block_gn.dat"),
    }.get(str(category).lower())
    if not files:
        return {}
    api = _get_api()
    if api is None:
        return {}
    try:
        from pytdx.reader.block_reader import BlockReader, BlockReader_TYPE_FLAT
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    try:
        with _lock:
            for blockfile in files:
                meta = api.get_block_info_meta(blockfile) or {}
                total = int(meta.get("size") or 0)
                if total <= 0:
                    continue
                raw = bytearray()
                chunk = 0x7530
                for start in range(0, total, chunk):
                    piece = api.get_block_info(
                        blockfile, start, min(chunk, total - start))
                    if piece:
                        raw.extend(piece)
                rows = BlockReader().get_data(raw, BlockReader_TYPE_FLAT)
                for row in rows:
                    name = str(row.get("blockname") or "").strip()
                    code = _pure_code(row.get("code"))
                    # 文件中偶尔会出现损坏/混入代码的组名；过滤后不猜板名。
                    if (not name or "\x00" in name or len(name) > 24
                            or not code.isdigit() or len(code) != 6):
                        continue
                    out.setdefault(name, []).append(code)
    except Exception:
        return {}
    return {name: list(dict.fromkeys(codes)) for name, codes in out.items()}

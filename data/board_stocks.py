# -*- coding: utf-8 -*-
"""板块成分股采集：同花顺直取（东财成分股接口被封 RemoteDisconnected，无备援）。

THS 行业 thshy/、概念 gn/ 列表页含板块名→code 映射；详情页
`thshy/detail/code/{code}/.../page/{n}/` 与 `gn/detail/code/{code}/.../page/{n}/`
含静态成分股表(序号/代码/名称/现价/涨跌幅/换手/量比/振幅/流通市值/市盈率)，
pandas read_html 解析，每页 20 可翻页。THS 不封东财 IP，是成分股唯一可靠源。

按需实时拉取，内存缓存板块 name→code 映射(7 天 TTL，参照 fundamentals_cache)。
NaN→None(参照 collector._to_records)，避免 starlette JSONResponse allow_nan=False 500。

合规：机械聚合成分股行情，观察清单非荐股非买卖信号，盈亏自负。
"""
from __future__ import annotations
import time, re, io, math
import pandas as pd
import requests

_H = {"User-Agent": "Mozilla/5.0", "Referer": "http://q.10jqka.com.cn/"}
_TTL = 7 * 86400
# 板块名→code 映射缓存：{"industry": {"map": {...}, "ts": 0}, "concept": {...}}
_CACHE: dict[str, dict] = {"industry": {"map": {}, "ts": 0}, "concept": {"map": {}, "ts": 0}}


def _prefix(code: str) -> str:
    """6 位代码加交易所前缀(供个股分析/历史接口)。
    北交所:920xxx(新代号)/43xxxx/83xxxx/87xxxx/8xxxxx → bj；
    沪市:6xxxxx(主板/科创688)/900xxx(沪B) → sh；
    深市:0xxxxx(主板)/30xxxx(创业)/200xxx(深B) → sz。"""
    c = str(code).strip()
    if not c or not c[:1].isdigit():
        return c
    # 北交所代号段(新920/老43/83/87/8)
    if c[:3] == "920" or c[:2] in ("43", "83", "87") or c[:1] == "8":
        return "bj" + c
    # 沪市:6开头(主板/科创), 900沪B
    if c[:1] == "6" or c[:3] == "900":
        return "sh" + c
    # 深市:0主板/30创业/200深B
    if c[:1] in "03" or c[:2] == "20":
        return "sz" + c
    return c


def _fetch_map(cat: str) -> dict[str, str]:
    """拉 THS 板块列表页，建 name→code 映射(7 天 TTL 缓存)。"""
    key = "industry" if cat in ("行业", "industry") else "concept"
    ent = _CACHE[key]
    if ent["map"] and time.time() - ent["ts"] < _TTL:
        return ent["map"]
    url = "http://q.10jqka.com.cn/thshy/" if key == "industry" else "http://q.10jqka.com.cn/gn/"
    r = requests.get(url, headers=_H, timeout=15)
    r.encoding = r.apparent_encoding or "gbk"
    seg = "thshy" if key == "industry" else "gn"
    pat = rf"/{seg}/detail/code/(\d+)/[^\"]*\"[^>]*>([^<]+)<"
    items = re.findall(pat, r.text)
    m = {name.strip(): code for code, name in items if name.strip()}
    ent["map"] = m
    ent["ts"] = time.time()
    return m


def _f(v) -> float | None:
    """转 float，NaN/异常→None(防 allow_nan=False 500)。"""
    if v is None:
        return None
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _s(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _fuzzy_match(m: dict, board: str) -> str | None:
    """精确匹配失败时的近似匹配：板名包含 THS 名 或 THS 名包含板名。
    取最长(最具体)命中，避免"汽车"误匹到过宽名。"""
    if not board or not m:
        return None
    cand = [name for name in m if name in board or board in name]
    if cand:
        cand.sort(key=len, reverse=True)
        return m[cand[0]]
    return None


def _em_col(cols, *kws) -> object | None:
    """东财成分股 DataFrame 列名容错(版本间微调)，按关键字首命中。"""
    for c in cols:
        cs = str(c)
        if any(k in cs for k in kws):
            return c
    return None


def _normalize_em(df) -> list[dict]:
    """东财成分股 DataFrame → 统一记录(字段与 THS 路径一致，标 source=em)。
    列名按关键字容错：代码/名称/最新价/涨跌幅/换手/量比/振幅/市盈率/流通市值。"""
    if df is None or len(df) == 0:
        return []
    cols = list(df.columns)
    c_code = _em_col(cols, "代码")
    c_name = _em_col(cols, "名称")
    c_price = _em_col(cols, "最新价", "现价")
    c_chg = _em_col(cols, "涨跌幅")
    c_turn = _em_col(cols, "换手")
    c_vr = _em_col(cols, "量比")
    c_amp = _em_col(cols, "振幅")
    c_pe = _em_col(cols, "市盈率")
    c_mcap = _em_col(cols, "流通市值")
    out = []
    for _, r in df.iterrows():
        c = str(r.get(c_code, "")).strip() if c_code else ""
        if c.replace(".", "").isdigit() and len(c) <= 6:
            c = c.split(".")[0].zfill(6)  # 东财代码也可能丢前导零
        if not c or not c.isdigit() or len(c) != 6:
            continue
        out.append({
            "code": _prefix(c), "raw_code": c, "source": "em",
            "name": _s(r.get(c_name)) if c_name else None,
            "price": _f(r.get(c_price)) if c_price else None,
            "change_pct": _f(r.get(c_chg)) if c_chg else None,
            "turnover_rate": _f(r.get(c_turn)) if c_turn else None,
            "volume_ratio": _f(r.get(c_vr)) if c_vr else None,
            "amplitude": _f(r.get(c_amp)) if c_amp else None,
            "pe": _f(r.get(c_pe)) if c_pe else None,
            "circulating_market_cap": _s(r.get(c_mcap)) if c_mcap else None,
        })
    return out


def _fetch_constituents_em(board: str, category: str) -> list[dict]:
    """东财直取成分股(板名来自东财时最准；akshare symbol=板名直接取，无需 name→code 映射)。
    板块表名源东财 stock_board_industry_name_em，成分股同源最匹配。
    东财 push2 在部分出口 IP 被封(502/RemoteDisconnected/挂死)→抛异常由上层降级 THS。
    akshare 未装(本地开发环境)→ ImportError 同样降级。"""
    import akshare as ak  # 按需 import，独立于 THS requests 路径
    from data import collector
    collector._install_http_patch()  # 给东财域名注入 UA/Referer+重试，否则默认 UA 被 502
    is_ind = category in ("行业", "industry")
    fn = ak.stock_board_industry_cons_em if is_ind else ak.stock_board_concept_cons_em
    df = fn(symbol=board)
    return _normalize_em(df)


# 东财 cons 端点被封时会挂死(akshare 无显式 timeout)→ daemon 线程 + queue 硬超时。
# 超时即放弃降级 THS，避免每次点击挂死。失败缓存：首次判定不可用后跳过东财，直奔 THS。
_EM_TIMEOUT = 8.0
_EM_AVAILABLE: bool | None = None  # None=未知 True=可用 False=不可用(缓存)


def _fetch_constituents_em_timed(board: str, category: str) -> list[dict]:
    """带硬超时的东财直取。东财可用→成功(快，<2s)；被封/挂死→超时抛异常降级 THS。"""
    global _EM_AVAILABLE
    if _EM_AVAILABLE is False:           # 之前判定东财不可用，直奔 THS 避免每次等满超时
        raise RuntimeError("em cons previously unavailable")
    import threading, queue
    q: "queue.Queue" = queue.Queue()

    def _run():
        try:
            q.put(("ok", _fetch_constituents_em(board, category)))
        except Exception as e:  # noqa: BLE001
            q.put(("err", e))

    threading.Thread(target=_run, daemon=True).start()
    try:
        kind, val = q.get(timeout=_EM_TIMEOUT)
    except queue.Empty:
        _EM_AVAILABLE = False           # 挂死 → 缓存失败，后续点击直奔 THS
        raise RuntimeError("em cons timeout (push2 blocked?)")
    if kind == "ok":
        _EM_AVAILABLE = True
        return val
    _EM_AVAILABLE = False               # 异常(被封) → 缓存失败
    raise val


def fetch_constituents(board: str, category: str = "行业", max_pages: int = 25) -> list[dict]:
    """拉板块成分股。优先东财直取(板名来自东财时最准)，东财不可用/超时降级 THS 静态表。

    返回 list[dict]：code(带前缀)/raw_code/name/price/change_pct/turnover_rate/
    volume_ratio/amplitude/pe/circulating_market_cap(字符串如"36.33亿")/source(em|ths)。
    东财被封 + 板块名不在 THS 列表 → 抛异常，由路由 catch 降级。"""
    # 1. 东财直取(带硬超时；挂死/被封缓存失败并降级 THS，避免点击挂死)
    try:
        rows = _fetch_constituents_em_timed(board, category)
        if rows:
            return rows
    except Exception:
        pass  # 东财不可用/超时 → 降级 THS
    # 2. THS 静态表(板名来自 THS summary 时；name→code 映射 + 模糊匹配)
    m = _fetch_map(category)
    code = m.get(board) or _fuzzy_match(m, board)
    if not code:
        raise ValueError(f"板块[{board}]未在 THS {category}列表中找到(东财亦不可用；可能名称不匹配)")
    is_ind = category in ("行业", "industry")
    base = "http://q.10jqka.com.cn/thshy/detail/code/" if is_ind else "http://q.10jqka.com.cn/gn/detail/code/"
    rows: list[dict] = []
    for p in range(1, max_pages + 1):
        url = f"{base}{code}/field/code/order/desc/page/{p}/"
        r = requests.get(url, headers=_H, timeout=15)
        r.encoding = r.apparent_encoding or "gbk"
        try:
            # pandas 3.0 默认 read_html flavor 走 bs4→html5lib(html5lib 未装即 ImportError)，
            # 显式 flavor='lxml'(lxml 已 pin requirements，THS 页实测可解析)。
            dfs = pd.read_html(io.StringIO(r.text), flavor='lxml')
        except (ValueError, ImportError):
            break  # 无表(末页或反爬页)
        if not dfs:
            break
        df = dfs[0]
        if len(df) == 0:
            break
        # 末页判定：第一列"序号"非数字或行数<20(不足一页)
        started = False
        for _, row in df.iterrows():
            cv = row.get("代码", "")
            c = str(cv).strip()
            # read_html 把代码列读成 int，前导零丢失(002966→2966, 000001→1)；zfill(6) 补回
            if c.replace(".", "").isdigit() and len(c) <= 6:
                c = c.split(".")[0].zfill(6)
            if not c or not c.isdigit() or len(c) != 6:
                continue
            started = True
            rows.append({
                "code": _prefix(c),
                "raw_code": c,
                "source": "ths",
                "name": _s(row.get("名称")),
                "price": _f(row.get("现价")),
                "change_pct": _f(row.get("涨跌幅(%)")),
                "turnover_rate": _f(row.get("换手(%)")),
                "volume_ratio": _f(row.get("量比")),
                "amplitude": _f(row.get("振幅(%)")),
                "pe": _f(row.get("市盈率")),
                "circulating_market_cap": _s(row.get("流通市值")),
            })
        if not started or len(df) < 20:
            break  # 不足一页=末页
    return rows

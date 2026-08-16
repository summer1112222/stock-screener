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
from . import pytdx_client

get_company_info = pytdx_client.get_company_info  # 模块级别名,便于测试 monkeypatch

_AK_TIMEOUT = 20
_CACHE_TTL_DAYS = 7

_TDX_FIN_CATEGORY = "财务分析"


def _parse_cn_amount(s):
    """解析中文金额/百分比字符串→float。亿×1e8/万×1e4/纯数字/含%去符号,空/异常→None。"""
    if s is None:
        return None
    t = str(s).strip().replace("%", "")
    if not t or t == "-":
        return None
    neg = False
    if t.startswith("-"):
        neg = True
        t = t[1:].strip()
    mult = 1.0
    if t.endswith("亿"):
        t = t[:-1]; mult = 1e8
    elif t.endswith("万"):
        t = t[:-1]; mult = 1e4
    try:
        v = float(t) * mult
    except (ValueError, TypeError):
        return None
    return -v if neg else v


def _split_table_rows(content: str, start_marker: str):
    """从 content 定位 start_marker(如'【资产负债表摘要】')后的第一个表格,
    返回 [行, ...], 每行=[单元, ...](已去首尾空白与表格边框符)。
    表格以 ┌ 开头、┘ 结尾;行以 ｜ 分列。无表返 []。"""
    idx = content.find(start_marker)
    if idx < 0:
        return []
    seg = content[idx:]
    # 跳到第一个 ┌(表格起点)
    p = seg.find("┌")
    if p < 0:
        return []
    seg = seg[p:]
    # 表底为 └...┘ 行;┘ 是表底右下角
    q = seg.find("┘")
    if q < 0:
        return []
    table = seg[: q + 1]
    rows = []
    for line in table.splitlines():
        line = line.strip()
        if not line or line.startswith("┌") or line.startswith("├") or line.startswith("└"):
            continue
        if "｜" not in line:
            continue
        cells = [c.strip() for c in line.split("｜")]
        # 去首尾空单元(split 首尾 ｜ 产生空串)
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if cells:
            rows.append(cells)
    return rows


def _build_wide_df(rows: list[list[str]]):
    """rows[0]=表头(报告期列,首单元=指标类别名), rows[1:]=科目行(首单元=指标名,余=数值)。
    返宽表 df:列=['指标', 报告期1, ...];数值经 _parse_cn_amount。"""
    if not rows or len(rows) < 2:
        return None
    header = rows[0]
    cols = ["指标"] + header[1:]
    data = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        name = r[0]
        vals = [_parse_cn_amount(v) for v in r[1:]]
        # 补齐列数
        while len(vals) < len(cols) - 1:
            vals.append(None)
        data.append([name] + vals[: len(cols) - 1])
    if not data:
        return None
    return pd.DataFrame(data, columns=cols)


def _transpose_three_table(rows: list[list[str]]):
    """rows[0]=表头(首单元='指标(单位:元)',余=报告期), rows[1:]=科目行。
    转置为行=报告期 列=科目(含'报告期'列),数值经 _parse_cn_amount。
    降序保持(表头本最新在前)。"""
    if not rows or len(rows) < 2:
        return None
    header = rows[0][1:]  # 报告期
    body = rows[1:]
    recs = []
    for i, period in enumerate(header):
        period_clean = str(period).strip()
        rec = {"报告期": period_clean}
        for r in body:
            # 需要能取到 r[1+i](第 i 个报告期对应的值)
            if len(r) < 2 + i:
                continue
            label = str(r[0]).strip()
            rec[label] = _parse_cn_amount(r[1 + i])
        recs.append(rec)
    if not recs:
        return None
    return pd.DataFrame(recs)


def parse_tdx_financial(code: str) -> dict:
    """解析 tdx '财务分析' 文本。返 {abstract, balance, cashflow, profit}。
    abstract=摘要宽表(行=指标列=报告期,兼容 buffett._row_pairs);
    balance/cashflow/profit=三大表(行=报告期列=科目含'报告期',兼容 _pick_col_sum/_pick_row_fields)。
    tdx 取失败/空→全 None,不抛崩。"""
    base = {"abstract": None, "balance": None, "cashflow": None, "profit": None}
    c = _strip_prefix(str(code).strip())
    try:
        info = get_company_info(c, _TDX_FIN_CATEGORY)
    except Exception:
        return base
    if not isinstance(info, dict) or not info.get("ok") or not info.get("content"):
        return base
    content = info["content"]
    # abstract: 合并财务指标各子表(主要/盈利/偿债/运营/发展)为单宽表
    abs_frames = []
    for marker in ("【主要财务指标】", "【盈利能力指标】", "【偿债能力指标】",
                   "【运营能力指标】", "【发展能力指标】"):
        rows = _split_table_rows(content, marker)
        df = _build_wide_df(rows)
        if df is not None:
            abs_frames.append(df)
    if abs_frames:
        abstract = pd.concat(abs_frames, ignore_index=True).drop_duplicates(subset=["指标"])
        base["abstract"] = abstract
    # 三大表摘要
    b_rows = _split_table_rows(content, "【资产负债表摘要】")
    base["balance"] = _transpose_three_table(b_rows)
    p_rows = _split_table_rows(content, "【利润表摘要】")
    base["profit"] = _transpose_three_table(p_rows)
    c_rows = _split_table_rows(content, "【现金流量表摘要】")
    base["cashflow"] = _transpose_three_table(c_rows)
    return base


def _strip_prefix(code: str) -> str:
    c = str(code).strip()
    return c[2:] if c[:2].lower() in ("sh", "sz", "bj") else c


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _cache_get(code: str, source: str, allow_stale: bool = False):
    """返回 (df_or_None, status)。status ∈ hit/stale/miss。
    code 经 _strip_prefix 归一(与 _cache_set 对称),使 fetch_abstract 预填的带前缀
    code 与 fundamentals.fetch 的查询键一致→预填命中秒回。"""
    code = _strip_prefix(code)
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
    code = _strip_prefix(code)  # 与 _cache_get 对称归一,使预填命中
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
    """返回 (df, stale)。缓存7天TTL;tdx 主源(parse_tdx_financial 解析后命中本 source)
    →akshare 备援。buffett.analyze 内 fetch_abstract 已预填三大表缓存时命中秒回;
    独立调时 miss→自 parse 一次。_AK_OK=False 或单只超时(20s)降级返回过期缓存。"""
    c = _strip_prefix(code)
    df, status = _cache_get(c, source, allow_stale=False)
    if status == "hit":
        # 空 df 哨兵(fetch_abstract 预填的"tdx 缺本源"标记)→返 None 秒回,不重 parse
        return (df if (df is not None and not df.empty) else None), False
    # tdx 主源:一次解析含全部 source,命中本 source 缓存后返
    try:
        parsed = parse_tdx_financial(c)
    except Exception:
        parsed = None
    if parsed:
        tdf = parsed.get(source)
        if tdf is not None and not tdf.empty:
            try:
                _cache_set(c, source, tdf)
            except Exception:
                pass
            return tdf, False
        # tdx 解析成功但无本 source(数据缺口,如个股无资产负债表摘要)→返 None 不烧 akshare
        return None, False
    # tdx 解析失败(parsed None:连接抖动/空 content)→akshare 备援
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, c, source).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(c, source, net)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(c, source, allow_stale=True)
    if df_s is not None and not df_s.empty:
        return df_s, True
    return None, False

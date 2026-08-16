# -*- coding: utf-8 -*-
"""巴菲特式基本面分析(v2)：年化EPS+相对估值分位+FCF代理+杠杆校正+负面预筛。

改进点(相对v1)：
- EPS 用年报(报告期 12/31)EPS，PE=现价/年报EPS，避免季度累计失真。
- 估值改"横截相对分位"：在扫描集内对盈利收益率 z-score，标相对便宜/合理/相对贵
  (不再用绝对"盈利收益率>2×国债"死门槛——A股整体偏贵时无意义)。
- FCF 代理=经营现金流量净额(年报)；FCF/净利润≈盈利质量。
- 权益乘数=1/(1-资产负债率)，杠杆校正 ROE=ROE/权益乘数，识别杠杆撑高的假ROE。
- 负面预筛：商誉/净资产>30%、资产负债率>75% 标红或排除。
- 毛利率/ROE/净利率 用年报序列看均值+趋势(末-首)。
合规：护城河来源(品牌/网络/切换成本)需人工判断；输出研究优先级，不给买卖指令。
仅个股；ETF 不适用。
"""
from __future__ import annotations

import io
from datetime import datetime
from types import ModuleType

import numpy as np
import pandas as pd

try:
    import akshare as ak
    _AK_OK = True
except Exception:  # pragma: no cover
    ak = ModuleType("akshare")  # 占位模块，允许测试 monkeypatch 属性
    ak.stock_financial_abstract = None  # type: ignore[attr-defined]
    _AK_OK = False

from data import db, fundamentals


_CACHE_TTL_DAYS = 7

# 轻量 DCF 假设：cost of equity = 国债2.5% + 风险溢价6.5%（可调；机械假设非真实折现率）
_COST_OF_EQUITY = 0.09


def _cagr(series: list[float], min_n: int = 3) -> float | None:
    """复合年增长率。series 为年报值升序或降序均可（取首末两端）。
    min_n 控制最少年数（< min_n 返回 None）。负值/零端点→None（增长无意义）。"""
    vals = [v for v in series if v is not None]
    if len(vals) < min_n:
        return None
    first, last = float(vals[-1]), float(vals[0])   # -1 旧 / 0 新
    if first <= 0 or last <= 0:
        return None
    n = len(vals) - 1
    if n <= 0:
        return None
    return float((last / first) ** (1.0 / n) - 1.0)


def _consistent_years(series: list[float], threshold: float) -> int:
    """从最旧年起数连续达到阈值的年数。series 年报值（_annual 输出，新→旧）。"""
    s = [v for v in series if v is not None]
    count = 0
    for v in reversed(s):   # _annual 新→旧，reversed 得旧→新
        if v >= threshold:
            count += 1
        else:
            break
    return count


def _strip_prefix(code: str) -> str:
    c = str(code).strip()
    return c[2:] if c[:2].lower() in ("sh", "sz", "bj") else c


def _cache_get(code: str, allow_stale: bool = False):
    """返回 (df_or_None, status)。status ∈ hit/stale/miss。allow_stale 时 stale 也返回 df。"""
    rows = db.query_rows("financial_abstract_cache", where="code=?", params=(code,))
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


def _cache_set(code: str, df: pd.DataFrame) -> None:
    payload = df.to_json(orient="records", force_ascii=False)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.upsert_rows("financial_abstract_cache",
                   [{"code": code, "payload_json": payload, "ts": ts}])


from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# 单只 akshare 财务摘要拉取超时秒，防 quality(stock) 口径2 逐只拉取卡死
_AK_TIMEOUT = 20

# 运行时熔断:akshare 财报接口连续失败(超时/异常)≥3 次→熔断30min。
# 期间 quality 口径2 跳过 analyze_many 直奔 spot估值代理,省掉 80只×20s deadline 白烧
# (akshare 被封是常态,见 CLAUDE.md;熔断后30min自动重试恢复)。模块级单例,多线程读写近似值。
import time as _btime
_CONSEC_FAIL = 0          # 连续失败计数(成功即归零)
_BLOCKED_UNTIL = 0.0      # 熔断到期时间戳;0=未熔断
_BLOCK_WINDOW = 1800.0    # 熔断窗口(秒)


def _note_fetch(ok: bool) -> None:
    """记录单次拉取结果,维护熔断状态(供 fetch_abstract 调,亦可测试直调)。"""
    global _CONSEC_FAIL, _BLOCKED_UNTIL
    if ok:
        _CONSEC_FAIL = 0
        _BLOCKED_UNTIL = 0.0
    else:
        _CONSEC_FAIL += 1
        if _CONSEC_FAIL >= 3:
            _BLOCKED_UNTIL = _btime.time() + _BLOCK_WINDOW


def akshare_blocked() -> bool:
    """akshare 财报接口是否处于熔断(连续失败)。quality 口径2 调:True→跳 buffett。"""
    return _btime.time() < _BLOCKED_UNTIL


def _fetch_net(code: str):
    """实际拉取调用，单独抽出便于线程超时包装。"""
    return ak.stock_financial_abstract(symbol=_strip_prefix(code))


def fetch_abstract(code: str) -> tuple[pd.DataFrame | None, bool]:
    """返回 (df, stale)。缓存7天TTL;tdx 主源(parse_tdx_financial 解析财务分析文本)
    →akshare 备援。tdx 解析成功时 abstract 缓存本表(financial_abstract_cache)+三大表
    预填 fundamentals_cache(供 fundamentals.fetch 命中秒回)。熔断:_note_fetch 记 tdx/akshare
    结果,连续≥3失败→akshare_blocked() 熔断30min(quality 口径2 跳 buffett 省 deadline)。
    _AK_OK=False 或单只超时(20s)时降级返回过期缓存(stale=True)。"""
    code = str(code).strip()
    df, status = _cache_get(code, allow_stale=False)
    if status == "hit":
        # 空 df 哨兵(prefetch 预填的"tdx 无 abstract"标记)→返 None 秒回,不重 parse
        return (df if (df is not None and not df.empty) else None), False
    # tdx 主源:一次解析含 abstract+三大表,分解缓存
    # _parse_tdx_with_timeout 超时守卫:tdx 全挂时单只上限 _TDX_PARSE_TIMEOUT(20s),
    # 超→None→`if parsed:` False→_note_fetch(False) 计熔断 + akshare 备援,不无限阻塞
    parsed = fundamentals._parse_tdx_with_timeout(code)
    if parsed:
        abs_df = parsed.get("abstract")
        if abs_df is not None and not abs_df.empty:
            try:
                _cache_set(code, abs_df)
            except Exception:
                pass
            # 预填三大表缓存(fundamentals 域),供 fundamentals.fetch 命中秒回。
            # tdx 缺某源(数据缺口,如个股无资产负债表摘要)时,仅当缓存 miss 写空 df 哨兵
            # (防覆盖 akshare 既得真实数据)→fundamentals.fetch 命中哨兵返 None 秒回,
            # 避免每源各重 parse_tdx_financial 一次(pytdx 单连接 Lock 串行,
            # N 只×3 重 parse 是 quality 批处理 deadline 40s 只出 16 的主瓶颈)
            for s in ("balance", "cashflow", "profit"):
                tdf = parsed.get(s)
                if tdf is not None and not tdf.empty:
                    try:
                        fundamentals._cache_set(code, s, tdf)
                    except Exception:
                        pass
                else:
                    _, st = fundamentals._cache_get(code, s, allow_stale=False)
                    if st == "miss":
                        try:
                            fundamentals._cache_set(code, s, pd.DataFrame())
                        except Exception:
                            pass
            _note_fetch(True)
            return abs_df, False
        # tdx 解析成功但无 abstract(数据缺口,如科创板/创业板新 tdx 无财务分析)→返 None 不烧 akshare
        return None, False
    _note_fetch(False)  # tdx 解析失败(parsed None:连接/空)→计熔断,走 akshare 备援
    # akshare 备援(原逻辑)
    if _AK_OK:
        ok = False
        net = None
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, code).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(code, net)
                ok = True
        except (FuturesTimeout, Exception):
            ok = False
        _note_fetch(ok)  # 成功重置;超时/异常/空结果均计失败,≥3次熔断
        if ok:
            return net, False
    df_s, _ = _cache_get(code, allow_stale=True)
    if df_s is not None and not df_s.empty:
        return df_s, True
    return None, False


def _row_pairs(df: pd.DataFrame, keyword: str) -> list[tuple[str, float]]:
    """返回 [(报告期, 值), ...]，按报告期降序(最新在前)。容忍 %/逗号。"""
    if df is None or "指标" not in df.columns:
        return []
    mask = df["指标"].astype(str).str.contains(keyword, na=False)
    if not mask.any():
        return []
    row = df[mask].iloc[0]
    pairs = []
    for col, val in row.items():
        if col in ("指标", "选项"):
            continue
        t = str(val).strip().replace(",", "").replace("，", "").rstrip("%").strip()
        if t in ("", "—", "--", "nan", "None"):
            continue
        try:
            pairs.append((str(col), float(t)))
        except ValueError:
            continue
    # 按报告期降序
    pairs.sort(key=lambda x: x[0], reverse=True)
    return pairs


def _annual(pairs, latest_only=False):
    """取年报(报告期以1231结尾,容忍带破折号格式)序列。"""
    ann = [v for p, v in pairs if str(p).replace("-", "").endswith("1231")]
    return ann[0] if (latest_only and ann) else ann


def _latest(pairs):
    return pairs[0][1] if pairs else None


def _pick_col_sum(df: pd.DataFrame, candidates: list[str]) -> float | None:
    """从完整财报表(行=报告期,列=科目)取最近年报(报告期 endswith 1231)行的命中科目值。
    列名模糊匹配(contains),NaN→None。"""
    if df is None or df.empty:
        return None
    date_col = None
    for c in list(df.columns):
        if str(c) in ("报告期", "报告日期", "REPORT_DATE", "统计截止日期"):
            date_col = c
            break
    row = None
    if date_col is not None:
        for _, r in df.iterrows():
            # 报告期带破折号，去破折号后判年报（同 _annual，否则"2024-12-31"尾4字符是"2-31"漏判）
            if str(r.get(date_col) or "").replace("-", "").endswith("1231"):
                row = r
                break
    if row is None:
        row = df.iloc[0]
    for col in df.columns:
        if col == date_col:
            continue
        cs = str(col)
        if any(k in cs for k in candidates):
            v = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
            if pd.notna(v):
                return float(v)
            return None
    return None


def _pick_row_fields(df: pd.DataFrame, field_map: dict) -> dict:
    """从完整财报表取**同一最新年报行**的多科目值（所有者收益/杜邦/ROIC 需同口径）。

    field_map: {label: [contains 关键词]}。定位最新年报行(报告期 endswith 1231)，
    对每个 label 找行内列名 contains 任一关键词的命中值，返回 {label: float|None}。
    NaN→None。找不到年报行退化为首行。"""
    out = {label: None for label in field_map}
    if df is None or df.empty:
        return out
    date_col = None
    for c in list(df.columns):
        if str(c) in ("报告期", "报告日期", "REPORT_DATE", "统计截止日期"):
            date_col = c
            break
    row = None
    if date_col is not None:
        for _, r in df.iterrows():
            # 报告期带破折号("2024-12-31")，去破折号后 endswith 1231 判年报（同 _annual）
            if str(r.get(date_col) or "").replace("-", "").endswith("1231"):
                row = r
                break
    if row is None:
        row = df.iloc[0]
    for label, kws in field_map.items():
        for col in df.columns:
            if col == date_col:
                continue
            cs = str(col)
            if any(k in cs for k in kws):
                v = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
                if pd.notna(v):
                    out[label] = float(v)
                break   # 每个 label 命中第一个匹配列即可
    return out


def _spot(code: str) -> dict:
    # where code=? 走主键索引查单行(旧实现 db.query_rows("stock_spot") 全表扫~5200行,
    # analyze_many 8 worker 并发×80 只 = 80 次全表扫+5200行转换,GIL/内存争用致硬 stall)
    rows = db.query_rows("stock_spot", where="code=?", params=(code,))
    return rows[0] if rows else {}


def analyze(code: str) -> dict:
    code = str(code).strip()
    df, stale = fetch_abstract(code)
    spot = _spot(code)
    res = {"code": code, "name": spot.get("name"), "applies_to": "个股(企业)",
           "note": "护城河来源(品牌/网络/切换成本)需读年报人工判断；PE用年报EPS年化；相对估值为扫描集内横截分位。"}
    res["stale_data"] = bool(stale)

    # best-effort 完整现金流表,取真实 FCF(经营-资本开支) + 所有者收益科目
    cf_df, cf_stale = fundamentals.fetch(code, "cashflow")
    real_fcf = None
    fcf_source = "摘要代理(经营现金流量净额)"
    owner_earnings = None
    if cf_df is not None and not cf_df.empty:
        ocf = _pick_col_sum(cf_df, ["经营活动产生的现金流量净额",
                                    "经营活动现金流量净额"])
        capex = _pick_col_sum(cf_df,
                              ["购建固定资产、无形资产及其他长期资产支付的现金",
                               "购建固定资产无形资产和其他长期资产支付的现金"])
        if ocf is not None:
            real_fcf = (ocf - capex) if capex is not None else ocf
            fcf_source = "完整现金流表(经营-资本开支)"
            if cf_stale:
                res["stale_data"] = True
        # 所有者收益 = 净利润 + 折旧摊销 - 资本开支 - 营运资本增加（巴菲特1986定义）
        # 需 ni_ann（下方才取），此处先取科目，ni 稍后拼
        cf_fields = _pick_row_fields(cf_df, {
            "ocf": ["经营活动产生的现金流量净额", "经营活动现金流量净额"],
            "capex": ["购建固定资产、无形资产及其他长期资产支付的现金",
                      "购建固定资产无形资产和其他长期资产支付的现金"],
            "depreciation": ["固定资产折旧", "折旧", "无形资产摊销", "摊销"],
            "wc_increase": ["营运资金的增加", "营运资金增加", "经营性应收项目的增加",
                            "经营性应付项目的减少"],
        })

    # 完整利润表/资产负债表（杜邦分解 + ROIC；各 20s 超时兜底，同 cf 模式）
    profit_df, p_stale = fundamentals.fetch(code, "profit")
    balance_df, b_stale = fundamentals.fetch(code, "balance")
    if p_stale or b_stale:
        res["stale_data"] = True
    p_fields = _pick_row_fields(profit_df, {
        "revenue": ["营业收入", "营业总收入"],
        "interest": ["利息费用", "财务费用"],
        "income_tax": ["所得税费用", "所得税"],
    }) if profit_df is not None else {k: None for k in ("revenue", "interest", "income_tax")}
    b_fields = _pick_row_fields(balance_df, {
        "total_assets": ["资产总计", "资产合计"],
        "equity_total": ["股东权益合计", "所有者权益合计", "所有者权益"],
        "accounts_payable": ["应付账款"],
        "notes_payable": ["应付票据"],
        "other_payable": ["其他应付款"],
    }) if balance_df is not None else {k: None for k in ("total_assets", "equity_total",
                                                          "accounts_payable", "notes_payable",
                                                          "other_payable")}

    roe_p = _row_pairs(df, "净资产收益率")
    gross_p = _row_pairs(df, "毛利率")
    net_p = _row_pairs(df, "销售净利率")
    debt_p = _row_pairs(df, "资产负债率")
    eps_p = _row_pairs(df, "基本每股收益")
    bps_p = _row_pairs(df, "每股净资产")
    ocf_p = _row_pairs(df, "经营现金流量净额")
    ni_p = _row_pairs(df, "净利润")
    goodwill_p = _row_pairs(df, "商誉")
    equity_p = _row_pairs(df, "股东权益合计")  # 净资产

    roe_ann = _annual(roe_p)
    gross_ann = _annual(gross_p)
    net_ann = _annual(net_p)
    debt_latest = _latest(debt_p)
    eps_ann = _annual(eps_p, latest_only=True)
    bps_latest = _latest(bps_p)
    ocf_ann = _annual(ocf_p, latest_only=True)
    ni_ann = _annual(ni_p, latest_only=True)
    gw_latest = _latest(goodwill_p)
    eq_latest = _latest(equity_p)

    ratios = {}
    if roe_ann:
        ratios["roe_avg"] = round(float(np.mean(roe_ann)), 2)
        ratios["roe_min"] = round(float(np.min(roe_ann)), 2)
        ratios["roe_trend"] = round(float(roe_ann[0] - roe_ann[-1]), 2)  # 末-首(正=上升)
    if gross_ann:
        ratios["gross_margin_avg"] = round(float(np.mean(gross_ann)), 2)
        ratios["gross_margin_trend"] = round(float(gross_ann[0] - gross_ann[-1]), 2)
    if net_ann:
        ratios["net_margin_avg"] = round(float(np.mean(net_ann)), 2)
    if debt_latest is not None:
        ratios["debt_ratio_latest"] = round(float(debt_latest), 2)
        ratios["equity_multiplier"] = round(1.0 / (1 - debt_latest / 100), 2) if debt_latest < 100 else None
        # 杠杆校正 ROE = ROE / 权益乘数 ≈ ROA 经营视角
        if roe_ann and ratios.get("equity_multiplier"):
            ratios["leverage_adj_roe"] = round(float(np.mean(roe_ann)) / ratios["equity_multiplier"], 2)
    if eps_ann:
        ratios["eps_annual"] = round(float(eps_ann), 4)
    if bps_latest:
        ratios["bps_latest"] = round(float(bps_latest), 4)
    if real_fcf is not None:
        ratios["fcf_proxy"] = round(float(real_fcf), 2)
        ratios["fcf_source"] = fcf_source
        if ni_ann:
            ratios["fcf_to_netincome"] = round(float(real_fcf / ni_ann), 2)
    elif ocf_ann and ni_ann:
        ratios["fcf_proxy"] = round(float(ocf_ann), 2)
        ratios["fcf_source"] = fcf_source
        ratios["fcf_to_netincome"] = round(float(ocf_ann / ni_ann), 2)

    # ---- 所有者收益（巴菲特1986：净利润+折旧摊销-资本开支-营运资本增加）----
    if ni_ann is not None and cf_df is not None and not cf_df.empty:
        dep = cf_fields.get("depreciation") or 0.0
        cap = cf_fields.get("capex") or 0.0
        wc = cf_fields.get("wc_increase") or 0.0
        owner_earnings = float(ni_ann) + dep - cap - wc
        ratios["owner_earnings"] = round(owner_earnings, 2)
        ratios["owner_earnings_to_ni"] = round(owner_earnings / ni_ann, 2) if ni_ann else None

    # ---- 杜邦分解 ROE = 净利率 × 资产周转率 × 权益乘数 ----
    rev = p_fields.get("revenue")
    ta = b_fields.get("total_assets")
    eq_b = b_fields.get("equity_total")
    if rev and ta and eq_b and ni_ann and rev > 0 and ta > 0 and eq_b > 0:
        nm = float(ni_ann) / float(rev)
        at = float(rev) / float(ta)
        em = float(ta) / float(eq_b)
        roe_d = nm * at * em * 100   # 小数→%
        ratios["dupont"] = {
            "net_margin": round(nm * 100, 2),
            "asset_turn": round(at, 3),
            "equity_mult": round(em, 3),
            "roe_dupont": round(roe_d, 2),
        }
        if ratios.get("roe_avg") and abs(roe_d - ratios["roe_avg"]) > 3:
            ratios["dupont"]["note"] = "与报告ROE偏差>3pt(口径差异)"

    # ---- ROIC = NOPAT / 投入资本 ----
    interest = p_fields.get("interest")
    income_tax = p_fields.get("income_tax")
    ap = b_fields.get("accounts_payable") or 0.0
    npl = b_fields.get("notes_payable") or 0.0
    opl = b_fields.get("other_payable") or 0.0
    if ni_ann is not None and ta and ta > 0 and interest is not None:
        pretax = float(ni_ann) + (income_tax or 0.0)
        tax_rate = (float(income_tax) / pretax) if (pretax and pretax > 0) else 0.25
        nopat = float(ni_ann) + float(interest) * (1 - tax_rate)
        non_int_liab = float(ap) + float(npl) + float(opl)
        invested_capital = float(ta) - non_int_liab
        if invested_capital > 0:
            roic = nopat / invested_capital * 100   # 小数→%
            ratios["roic"] = round(roic, 2)
            ratios["roic_tag"] = ("高资本回报" if roic > 15 else "良好" if roic > 10 else "偏弱")

    # ---- 增长性与持久性 ----
    rev_p = _row_pairs(df, "营业总收入") or _row_pairs(df, "营业收入")
    rev_ann = _annual(rev_p) if rev_p else []
    ratios["rev_cagr"] = round(_cagr(rev_ann) * 100, 2) if _cagr(rev_ann) is not None else None
    ratios["eps_cagr"] = round(_cagr(_annual(eps_p)) * 100, 2) if _cagr(_annual(eps_p)) is not None else None
    bps_ann = _annual(bps_p) if bps_p else []
    bps_cagr = _cagr(bps_ann)
    ratios["bps_cagr"] = round(bps_cagr * 100, 2) if bps_cagr is not None else None
    ratios["ni_cagr"] = round(_cagr(_annual(ni_p)) * 100, 2) if _cagr(_annual(ni_p)) is not None else None
    if roe_ann:
        ratios["roe_consistent_yr"] = _consistent_years(roe_ann, 15.0)

    if gw_latest is not None and eq_latest:
        ratios["goodwill_to_equity_pct"] = round(float(gw_latest / eq_latest * 100), 2)
    res["ratios"] = ratios

    # ---- 负面红旗 ----
    red_flags = []
    if ratios.get("goodwill_to_equity_pct") and ratios["goodwill_to_equity_pct"] > 30:
        red_flags.append(f"商誉/净资产={ratios['goodwill_to_equity_pct']}%(减值风险)")
    if ratios.get("debt_ratio_latest") is not None and ratios["debt_ratio_latest"] > 75:
        red_flags.append(f"资产负债率={ratios['debt_ratio_latest']}%(高杠杆)")
    if ratios.get("fcf_to_netincome") is not None and ratios["fcf_to_netincome"] < 0.5:
        red_flags.append(f"FCF/净利润={ratios['fcf_to_netincome']}(盈利质量差)")
    if ratios.get("owner_earnings_to_ni") is not None and ratios["owner_earnings_to_ni"] < 0.5:
        red_flags.append(f"所有者收益/净利润={ratios['owner_earnings_to_ni']}(赚非现金)")
    if ratios.get("roe_trend") is not None and ratios["roe_trend"] < -3:
        red_flags.append(f"ROE下行{ratios['roe_trend']}")
    dp = ratios.get("dupont") or {}
    if dp.get("equity_mult") and dp["equity_mult"] > 3.0:
        red_flags.append(f"权益乘数={dp['equity_mult']}(ROE靠杠杆撑)")
    res["red_flags"] = red_flags

    # ---- 护城河(财务质量) ----
    score = 0
    if roe_ann and ratios.get("roe_avg") and ratios["roe_avg"] > 15:
        score += 2
    elif roe_ann and ratios.get("roe_avg") and ratios["roe_avg"] > 10:
        score += 1
    if gross_ann and ratios.get("gross_margin_avg") and ratios["gross_margin_avg"] > 40:
        score += 2
    elif gross_ann and ratios.get("gross_margin_avg") and ratios["gross_margin_avg"] > 25:
        score += 1
    if ratios.get("leverage_adj_roe") and ratios["leverage_adj_roe"] > 10:
        score += 1  # 经营视角也强
    if roe_ann and len(roe_ann) >= 2 and ratios.get("roe_trend") is not None and ratios["roe_trend"] >= 0:
        score += 1  # 稳定/上升
    # 财务护城河代理增强（A7）
    if ratios.get("roic") and ratios["roic"] > 15:
        score += 2  # 真资本回报，比 ROE 更纯
    elif ratios.get("roic") and ratios["roic"] > 10:
        score += 1
    if ratios.get("roe_consistent_yr") and ratios["roe_consistent_yr"] >= 5:
        score += 1  # 持久
    if ratios.get("bps_cagr") and ratios["bps_cagr"] > 8:
        score += 1  # 内生增长
    if gross_ann and len(gross_ann) >= 3:
        gm_mean = float(np.mean(gross_ann))
        if gm_mean > 0 and (float(np.std(gross_ann)) / gm_mean) < 0.15:
            score += 1  # 毛利率稳定（定价权代理）
    # 负面扣分
    if red_flags:
        score -= min(2, len(red_flags))
    res["moat_score"] = score
    res["moat_tag"] = ("宽(财务质量达标)" if score >= 5 else "窄(部分达标)"
                       if score >= 3 else "无/弱" if score >= 1 else "不确定(数据不足)")
    res["moat_note"] = "财务护城河代理(品牌/网络/切换成本等定性护城河需读年报判断)"

    # ---- 估值(年报EPS派生PE) ----
    price = spot.get("latest_price")
    pe = spot.get("pe")
    pb = spot.get("pb")
    if not pe and price and eps_ann:
        try:
            pe = round(float(price) / float(eps_ann), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pe = None
    if not pb and price and bps_latest:
        try:
            pb = round(float(price) / float(bps_latest), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pb = None
    res["pe"] = pe
    res["pb"] = pb
    res["latest_price"] = price
    res["eps_annual"] = ratios.get("eps_annual")
    if pe:
        try:
            res["earnings_yield_pct"] = round(100.0 / float(pe), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # 绝对估值标签(盈利收益率 vs 国债2.5%)
    abs_val = "不确定(缺PE)"
    if res.get("earnings_yield_pct") is not None:
        ey = res["earnings_yield_pct"]
        if ey > 5:
            abs_val = "便宜(盈利收益率>2×国债)"
        elif ey > 2.5:
            abs_val = "合理(盈利收益率>国债)"
        else:
            abs_val = "贵(盈利收益率<国债)"
    res["valuation_abs"] = abs_val
    res["valuation_tag"] = abs_val  # 横截相对分位在 rank_top 里赋

    # ---- 轻量 DCF：内在价值 + 安全边际（Gordon-on-Book 剩余收益模型）----
    # justified_pb = (sustainable_roe - g) / (r - g)；g 用 BPS CAGR 反推（零新数据源）。
    # IV = justified_pb × bps_latest；MoS = (IV - price)/IV。
    # 假设机械透明，非真实内在价值，研究优先级非买卖信号。
    r = _COST_OF_EQUITY
    iv = None
    mos = None
    dcf_note = ""
    if ratios.get("roe_avg") and bps_latest and bps_cagr is not None:
        sustainable_roe = ratios["roe_avg"] / 100.0
        g = max(0.0, min(bps_cagr, sustainable_roe))   # 截断 [0, roe]
        if g >= r:
            dcf_note = "g≥r模型失效(增长不可持续假设)，建议人工判断"
            res["dcf_assumptions"] = {
                "sustainable_roe": round(sustainable_roe, 4),
                "growth_g": round(g, 4),
                "cost_of_equity_r": r,
                "justified_pb": None,
                "bps_latest": float(bps_latest),
                "n_years": len(bps_ann),
                "note": dcf_note,
            }
        elif sustainable_roe > 0 and r > g:
            justified_pb = (sustainable_roe - g) / (r - g)
            if justified_pb > 0:
                iv = justified_pb * float(bps_latest)
                if price:
                    try:
                        mos = (iv - float(price)) / iv
                    except (TypeError, ValueError, ZeroDivisionError):
                        mos = None
                dcf_note = f"假设 ROE={ratios['roe_avg']}% g={round(g*100,1)}% r={r*100:.0f}%"
                res["dcf_assumptions"] = {
                    "sustainable_roe": round(sustainable_roe, 4),
                    "growth_g": round(g, 4),
                    "cost_of_equity_r": r,
                    "justified_pb": round(justified_pb, 3),
                    "bps_latest": float(bps_latest),
                    "n_years": len(bps_ann),
                    "note": dcf_note,
                }
    res["intrinsic_value"] = round(iv, 2) if iv is not None else None
    res["margin_of_safety"] = round(mos, 3) if mos is not None else None

    # 综合 IV 安全边际进绝对估值标签（与盈利收益率并存，互为印证）
    if mos is not None:
        if mos > 0.3:
            iv_val = f"便宜(IV安全边际{round(mos*100)}%)"
        elif mos >= 0:
            iv_val = f"合理(IV安全边际{round(mos*100)}%)"
        else:
            iv_val = f"贵(高于IV {round(abs(mos)*100)}%)"
        res["valuation_abs"] = iv_val
        res["valuation_tag"] = iv_val

    # 综合优先级(研究优先，非买卖指令)
    priority = "低"
    if "宽" in res["moat_tag"] and ("便宜" in abs_val or "合理" in abs_val
                                    or (mos is not None and mos > 0)):
        priority = "高(财务强+估值有安全边际，值得深读年报)"
    elif "宽" in res["moat_tag"]:
        priority = "中高(财务强，估值偏贵)"
    elif "窄" in res["moat_tag"] and "便宜" in abs_val:
        priority = "中(估值便宜但护城河一般)"
    elif "无" in res["moat_tag"] or "贵" in abs_val:
        priority = "低(护城河弱或估值贵)"
    res["priority"] = priority

    res["decision"] = ("研究优先级排序，买卖动作由你自己决定。优先级≠买卖信号，"
                       "需结合护城河来源(年报)、行业、周期判断。")
    # NaN→None
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, float) and (o != o):
            return None
        return o
    return _clean(res)


def shortlist_by_turnover(min_turnover: float = 5e8, k: int = 80,
                          limit_pct: float = 9.9) -> list[str]:
    """可买入 shortlist：排除ST/涨停/停牌/成交额不足，按成交额取前K(默认扩到80)。"""
    rows = db.query_rows("stock_spot")
    if not rows:
        return []
    df = pd.DataFrame(rows)
    mask = pd.Series(True, index=df.index)
    if "name" in df.columns:
        mask &= ~df["name"].astype(str).str.contains("ST", case=False, na=False)
    if "latest_price" in df.columns:
        lp = pd.to_numeric(df["latest_price"], errors="coerce")
        mask &= lp.notna() & (lp > 0)
    if "change_pct" in df.columns:
        mask &= pd.to_numeric(df["change_pct"], errors="coerce").fillna(-99) < limit_pct
    if "turnover_amount" in df.columns:
        ta = pd.to_numeric(df["turnover_amount"], errors="coerce").fillna(0)
        mask &= ta >= min_turnover
        df = df.assign(_ta=ta)[mask].sort_values("_ta", ascending=False).head(k)
    else:
        df = df[mask].head(k)
    return df["code"].tolist()


def prefetch_financial(codes: list[str]) -> None:
    """串行预解析所有 code 的 tdx 财报,填 abstract+三大表缓存(含缺源空 df 哨兵)。

    单线程串行 parse 避免在 analyze_many 的 8 worker 并发下争用 pytdx 单 TCP 连接 Lock
    (并发时 parse_tdx_financial 单次 0.15s 膨胀至 ~1-3s 的重连抖动,N 只并发 deadline 40s
    仅完成~16)。预热后并行 analyze 阶段全缓存命中 0 次 pytdx。已有新鲜 abstract 缓存的
    code 跳过(暖跑秒回)。abstract=None 的 code 也预填三大表哨兵,使其 fundamentals.fetch
    命中哨兵返 None 秒回(否则 analyze 内 fetch×3 各重 parse 一次=4× 冗余 TCP)。"""
    for c in codes:
        _, st = _cache_get(c, allow_stale=False)
        if st == "hit":
            continue  # 新鲜 abstract 缓存→跳过(暖跑秒回)
        parsed = fundamentals._parse_tdx_with_timeout(c)
        if not parsed:
            continue
        abs_df = parsed.get("abstract")
        if abs_df is not None and not abs_df.empty:
            try:
                _cache_set(c, abs_df)
            except Exception:
                pass
        else:
            # abstract 缺(tdx 无此代码财务分析)→写空 df 哨兵,使 fetch_abstract 命中
            # 返 None 秒回不重 parse(否则 8 worker 并发争用 pytdx Lock 致 40s 挂起)
            _, ast = _cache_get(c, allow_stale=False)
            if ast == "miss":
                try:
                    _cache_set(c, pd.DataFrame())
                except Exception:
                    pass
        # 三大表:有数据写真实 df,缺源(含 abstract=None 的全缺)写空 df 哨兵(仅 miss 时,
        # 防覆盖 akshare 既得真实数据)→fundamentals.fetch 命中哨兵返 None 不重 parse
        for s in ("balance", "cashflow", "profit"):
            tdf = parsed.get(s)
            if tdf is not None and not tdf.empty:
                try:
                    fundamentals._cache_set(c, s, tdf)
                except Exception:
                    pass
            else:
                _, sst = fundamentals._cache_get(c, s, allow_stale=False)
                if sst == "miss":
                    try:
                        fundamentals._cache_set(c, s, pd.DataFrame())
                    except Exception:
                        pass


def analyze_many(codes: list[str], deadline_s: float | None = None) -> list[dict]:
    """并发 analyze（max_workers=8）；单只超时由 fetch_abstract 的 _AK_TIMEOUT 兜底。

    deadline_s: 总体截止秒数(从调用起算)。到点后用 as_completed 返回**已完成**的
        部分结果、放弃未完成项——避免 akshare 全被封时 N×_AK_TIMEOUT/8workers
        长阻塞(ex.map 干等全部完成，80只shortlist最坏~200s)。None=不限(旧行为)。
        quality 口径2 传 60s：拿到部分 buffett 分位也比 200s 挂起强，quality
        仍可凭口径1/3/4 + 部分口径2 产出有效主清单。
    """
    out = []
    # 串行预解析填缓存,避免 8 worker 并发争用 pytdx 单连接 Lock 致 parse 抖动
    # (并发 parse 0.15s→1-3s,N 只 deadline 40s 仅完成~16;预热后并行阶段全缓存命中)
    prefetch_financial(codes)
    def _one(c):
        try:
            r = analyze(c)
            if r and r.get("pe") is not None:
                return r
        except Exception:
            pass
        return None
    if deadline_s is None:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(_one, codes):
                if r is not None:
                    out.append(r)
        return out
    # 有截止时间：submit 全部 + as_completed 边收边到点停，放弃未完成(不阻塞等全部)
    from concurrent.futures import as_completed
    import time as _time
    remain = max(deadline_s, 0.001)
    ex = ThreadPoolExecutor(max_workers=8)
    try:
        futs = {ex.submit(_one, c): c for c in codes}
        try:
            for fut in as_completed(futs, timeout=remain):
                r = fut.result()  # _one 内已吞异常，返 None 或 dict
                if r is not None:
                    out.append(r)
        except FuturesTimeout:
            pass  # 截止到，返回已完成部分(其余放弃)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)  # 不阻塞等残余线程(≤20s自亡)
    return out


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    std = s.std(ddof=0)
    return (s - s.mean()) / std if std else s * 0


def _build_reasons(r: dict, order: str, rank_pos: int,
                   min_turnover: float, shortlist_k: int) -> list[str]:
    """从该标的已有判定结果机械拼出选择理由(解释筛选结果，非推荐)。

    仅叙事化已有标签/数值，不引入新判断。措辞用"通过/达标/分位"，避免买卖指令。
    """
    rt = r.get("ratios", {}) or {}
    reasons: list[str] = []
    # 1. 入池路径：可买入 shortlist
    reasons.append(
        f"可买入shortlist(成交额≥{min_turnover/1e8:.1f}亿·排除ST/涨停/停牌·取前{shortlist_k})")
    # 2. 负面预筛
    flags = r.get("red_flags") or []
    if flags:
        reasons.append("负面红旗: " + "；".join(flags))
    else:
        reasons.append("通过负面预筛(商誉/净资产·资产负债率·FCF质量)")
    # 3. 财务质量(护城河) —— 列关键驱动
    parts = []
    if rt.get("roe_avg") is not None:
        parts.append(f"ROE均{rt['roe_avg']}%")
    if rt.get("roic") is not None:
        parts.append(f"ROIC{rt['roic']}%")
    if rt.get("gross_margin_avg") is not None:
        parts.append(f"毛利率{rt['gross_margin_avg']}%")
    if rt.get("leverage_adj_roe") is not None:
        parts.append(f"杠杆校正ROE{rt['leverage_adj_roe']}%")
    if rt.get("roe_consistent_yr"):
        parts.append(f"连续{rt['roe_consistent_yr']}年ROE>15")
    if rt.get("bps_cagr") is not None:
        parts.append(f"BPS CAGR{rt['bps_cagr']}%")
    tag = r.get("moat_tag") or ""
    reasons.append(f"{tag}(" + "/".join(parts) + ")" if parts else (tag or "财务数据不足"))
    # 4. 估值（盈利收益率 + 内在价值安全边际，互为印证）
    ey = r.get("earnings_yield_pct")
    val_tag = r.get("valuation_tag") or r.get("valuation_abs") or ""
    val_parts = [val_tag] if val_tag else []
    if ey is not None:
        val_parts.append(f"盈利收益率{ey}%")
    if r.get("intrinsic_value") is not None:
        val_parts.append(f"IV={r['intrinsic_value']}")
    if r.get("margin_of_safety") is not None:
        val_parts.append(f"安全边际{round(r['margin_of_safety']*100)}%")
    reasons.append("估值: " + "，".join(val_parts) if val_parts else "估值缺PE")
    # 5. 排名
    order_label = {"priority": "优先级", "valuation_asc": "估值低→高",
                   "valuation_desc": "估值高→低"}.get(order, order)
    reasons.append(f"集内第{rank_pos}名({order_label}排序)")
    return reasons


def rank_top(results: list[dict], order: str = "valuation_desc",
             n: int = 10, min_turnover: float = 5e8,
             shortlist_k: int = 80) -> list[dict]:
    """排序+相对估值分位+负面预筛+选择理由。
    order: valuation_desc(贵→便宜) / valuation_asc(便宜→贵) / priority。
    相对估值：扫描集内盈利收益率 z-score，分位<25%→相对便宜, >75%→相对贵。
    负面预筛：商誉/净资产>30% 或 资产负债率>75% 或 FCF/净利润<0.3 → 排除。
    reasons：从已有判定机械拼出入选理由(非推荐)。
    """
    # 负面预筛
    def bad(r):
        rt = r.get("ratios", {})
        if rt.get("goodwill_to_equity_pct") and rt["goodwill_to_equity_pct"] > 30:
            return True
        if rt.get("debt_ratio_latest") is not None and rt["debt_ratio_latest"] > 75:
            return True
        if rt.get("fcf_to_netincome") is not None and rt["fcf_to_netincome"] < 0.3:
            return True
        if rt.get("owner_earnings_to_ni") is not None and rt["owner_earnings_to_ni"] < 0.3:
            return True   # 所有者收益/净利润过低（赚非现金）排除
        return False
    results = [r for r in results if not bad(r)]
    if not results:
        return []

    # 横截相对估值分位(盈利收益率 z-score)
    eys = pd.Series([r.get("earnings_yield_pct") or 0 for r in results])
    z = _zscore(eys)
    for i, r in enumerate(results):
        pct = float((eys.rank(pct=True).iloc[i]))  # 0..1，越高=越便宜
        r["valuation_rel_pct"] = round(pct, 3)
        r["valuation_tag"] = ("相对便宜" if pct >= 0.75 else "相对合理"
                               if pct >= 0.25 else "相对贵") + f"(集内{int(pct*100)}%)"

    if order == "priority":
        prio = {"高": 0, "中高": 1, "中": 2, "低": 3}
        # priority 同档内按安全边际 MoS 降序（MoS 越大越便宜）
        ordered = sorted(results,
                         key=lambda r: (prio.get((r.get("priority") or "低")[:2], 9),
                                        -(r.get("margin_of_safety") or -1)))
    # valuation_asc: 盈利收益率高(便宜)排前
    elif order == "valuation_asc":
        ordered = sorted(results, key=lambda r: -(r.get("earnings_yield_pct") or 0))
    # valuation_desc: 贵排前 = 盈利收益率低排前
    else:
        ordered = sorted(results, key=lambda r: (r.get("earnings_yield_pct") or 0))

    top = ordered[:n]
    for i, r in enumerate(top, 1):
        r["reasons"] = _build_reasons(r, order, i, min_turnover, shortlist_k)
    return top

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

from data import db


_CACHE_TTL_DAYS = 7


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


def _fetch_net(code: str):
    """实际拉取调用，单独抽出便于线程超时包装。"""
    return ak.stock_financial_abstract(symbol=_strip_prefix(code))


def fetch_abstract(code: str) -> tuple[pd.DataFrame | None, bool]:
    """返回 (df, stale)。缓存7天TTL；_AK_OK=False 或单只超时(20s)时降级返回过期缓存(stale=True)。
    超时包装防 akshare hang 导致 quality(stock) 卡死。"""
    df, status = _cache_get(code, allow_stale=False)
    if status == "hit":
        return df, False
    if _AK_OK:
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                net = ex.submit(_fetch_net, code).result(timeout=_AK_TIMEOUT)
            if net is not None and not net.empty:
                _cache_set(code, net)
                return net, False
        except (FuturesTimeout, Exception):
            pass
    df_s, _ = _cache_get(code, allow_stale=True)
    if df_s is not None:
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
    """取年报(报告期以1231结尾)序列。"""
    ann = [v for p, v in pairs if p.endswith("1231")]
    return ann[0] if (latest_only and ann) else ann


def _latest(pairs):
    return pairs[0][1] if pairs else None


def _spot(code: str) -> dict:
    for r in db.query_rows("stock_spot"):
        if r.get("code") == code:
            return r
    return {}


def analyze(code: str) -> dict:
    code = str(code).strip()
    df, stale = fetch_abstract(code)
    spot = _spot(code)
    res = {"code": code, "name": spot.get("name"), "applies_to": "个股(企业)",
           "note": "护城河来源(品牌/网络/切换成本)需读年报人工判断；PE用年报EPS年化；相对估值为扫描集内横截分位。"}
    res["stale_data"] = bool(stale)

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
    if ocf_ann and ni_ann and ni_ann:
        ratios["fcf_proxy"] = round(float(ocf_ann), 2)
        ratios["fcf_to_netincome"] = round(float(ocf_ann / ni_ann), 2)  # 盈利质量
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
    if ratios.get("roe_trend") is not None and ratios["roe_trend"] < -3:
        red_flags.append(f"ROE下行{ratios['roe_trend']}")
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
    # 负面扣分
    if red_flags:
        score -= min(2, len(red_flags))
    res["moat_score"] = score
    res["moat_tag"] = ("宽(财务质量达标)" if score >= 5 else "窄(部分达标)"
                       if score >= 3 else "无/弱" if score >= 1 else "不确定(数据不足)")

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

    # 综合优先级(研究优先，非买卖指令)
    priority = "低"
    if "宽" in res["moat_tag"] and ("便宜" in abs_val or "合理" in abs_val):
        priority = "高(财务强+估值不贵，值得深读年报)"
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


def analyze_many(codes: list[str]) -> list[dict]:
    """并发 analyze（max_workers=8）；单只超时由 fetch_abstract 的 _AK_TIMEOUT 兜底。"""
    out = []
    def _one(c):
        try:
            r = analyze(c)
            if r and r.get("pe") is not None:
                return r
        except Exception:
            pass
        return None
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(_one, codes):
            if r is not None:
                out.append(r)
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
    if rt.get("gross_margin_avg") is not None:
        parts.append(f"毛利率{rt['gross_margin_avg']}%")
    if rt.get("leverage_adj_roe") is not None:
        parts.append(f"杠杆校正ROE{rt['leverage_adj_roe']}%")
    tag = r.get("moat_tag") or ""
    reasons.append(f"{tag}(" + "/".join(parts) + ")" if parts else (tag or "财务数据不足"))
    # 4. 估值
    ey = r.get("earnings_yield_pct")
    val_tag = r.get("valuation_tag") or r.get("valuation_abs") or ""
    if ey is not None:
        reasons.append(f"{val_tag}，盈利收益率{ey}%")
    else:
        reasons.append(val_tag or "估值缺PE")
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
        ordered = sorted(results, key=lambda r: prio.get((r.get("priority") or "低")[:2], 9))
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

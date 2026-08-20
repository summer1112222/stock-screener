# -*- coding: utf-8 -*-
"""次日强势 5 步流程(硬剔除+软打分混合)。

**不触网不新增表**: 复用 stock_spot/sector_fund_flow/stock_daily。
step3 复用 backtest.signals._uni_panels 取 close 面板(仅取数)+本地算 MA。
step5 从 industry_board 成分表反查 code→board(同 daily_strong 套路)。

5 步(对齐用户方法论):
  step1 入选: 涨幅>5% + 换手率>3% + 股价<50
  step2 雷区: 流通市值 10-200亿 + PE<=150 且非亏损 + 非ST
  step3 形态: 多头排列(5/10/20 MA 向上发散) 或 放量突破(站稳60日线+量翻倍)
  step4 量价: 量比>2.5 强度 + 涨幅<7% 避追高
  step5 板块助攻: 所属板块(行业)热度前5 + 板块内>=2 只涨停

混合编排: step1/2/3/5 硬剔除(通过/不通过), step4 软打分(0-100 排序)。
排序键: 硬通过数×10 + 软分 降序。30s 进程缓存。
合规(个人自用放松): 次日强势清单——机械 5 步流程排序观察清单。
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from data import db

_SCAN_K = 200       # 粗筛后精算上限(按涨幅降序)
_CACHE: dict[tuple, tuple] = {}
_CACHE_TTL = 30


def _nan(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _to_f(v):
    try:
        f = float(v)
        return f if not (math.isnan(f) or math.isinf(f)) else None
    except (TypeError, ValueError):
        return None


def _step1_pass(s, p) -> bool:
    """入选门槛: 涨幅>min_change_pct + 换手>min_turnover + 股价<max_price。"""
    chg = _to_f(s.get("change_pct"))
    tr = _to_f(s.get("turnover_rate"))
    px = _to_f(s.get("latest_price"))
    if chg is None or tr is None or px is None:
        return False
    return chg > p["min_change_pct"] and tr > p["min_turnover"] and px < p["max_price"]


def _step2_pass(s, p, st_set=None) -> bool:
    """雷区剔除: 市值[min_mv,max_mv] + PE<=max_pe 且非亏损 + 非ST。
    st_set=预加载的 ST code 集合(st_list 表; stock_spot 无 st_type 列)。"""
    mc = _to_f(s.get("circulating_market_cap"))
    pe = _to_f(s.get("pe"))
    if not p.get("exclude_st", True):
        is_st = False
    else:
        is_st = (str(s.get("code")) in st_set) if st_set is not None else bool(s.get("st_type"))
    if mc is None or mc < p["min_mv"] or mc > p["max_mv"]:
        return False
    if pe is None or pe <= 0 or pe > p["max_pe"]:
        return False
    if is_st:
        return False
    return True


# ------------------------------------------------------------------
# step3 批量 MA(复用 backtest.signals._uni_panels)
# ------------------------------------------------------------------

def _ma_arrange_batch(universe: str, codes: list[str], days: int = 60) -> dict:
    """批量算 5/10/20/60 MA + 量。返 {code: ma_info}。
    ma_info: {ma5,ma10,ma20,ma60,bullish_align,volume_breakout,bearish,converged,
              need_history,last_vol,vol_avg20}。"""
    out = {c: {"ma5": None, "ma10": None, "ma20": None, "ma60": None,
               "bullish_align": False, "volume_breakout": False,
               "bearish": False, "converged": False, "need_history": False,
               "last_vol": None, "vol_avg20": None} for c in codes}
    if not codes:
        return out
    try:
        from backtest import signals as _sig
        close, amount = _sig._uni_panels(universe, codes)
    except Exception:
        return out
    if close is None or close.empty:
        return out
    window = max(days, 60)
    for c in codes:
        if c not in close.columns:
            out[c]["need_history"] = True
            continue
        s = close[c].dropna().tail(window)
        if len(s) < 60:
            out[c]["need_history"] = True
            continue
        ma5 = s.rolling(5).mean().iloc[-1]
        ma10 = s.rolling(10).mean().iloc[-1]
        ma20 = s.rolling(20).mean().iloc[-1]
        ma60 = s.rolling(60).mean().iloc[-1]
        ma20_prev = s.rolling(20).mean().iloc[-6] if len(s) >= 6 else ma20
        last_close = s.iloc[-1]
        out[c].update({"ma5": _nan(ma5), "ma10": _nan(ma10), "ma20": _nan(ma20),
                       "ma60": _nan(ma60)})
        out[c]["bullish_align"] = bool(ma5 > ma10 > ma20 and ma20 > ma20_prev)
        out[c]["bearish"] = bool(ma5 < ma10 < ma20)
        if min(ma5, ma10, ma20) > 0:
            spread = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / min(ma5, ma10, ma20)
            out[c]["converged"] = bool(spread < 0.005)
        if amount is not None and c in amount.columns:
            amt = amount[c].dropna()
            if len(amt) >= 20:
                last_vol = amt.iloc[-1]
                vol_avg20 = amt.iloc[-20:].mean()
                out[c]["last_vol"] = _nan(last_vol)
                out[c]["vol_avg20"] = _nan(vol_avg20)
                out[c]["volume_breakout"] = bool(
                    last_close > ma60 and last_vol >= 2 * vol_avg20)
    return out


def _step3_pass(info: dict) -> bool:
    """形态过滤: 多头排列 OR 放量突破 通过; 空头排列 剔除。"""
    if info.get("need_history"):
        return False
    if info.get("bearish"):
        return False
    return info.get("bullish_align") or info.get("volume_breakout")


# ------------------------------------------------------------------
# step4 软打分
# ------------------------------------------------------------------

def _step4_score(s) -> float:
    """软打分(0-100): 量比>2.5 强度(0.5权重) + 涨幅<7% 避追高(0.5权重)。"""
    vr = _to_f(s.get("volume_ratio"))
    chg = _to_f(s.get("change_pct"))
    a = _clip((vr - 1.0) / 1.5) if vr is not None else 0.0
    if chg is None:
        b = 0.0
    elif chg < 7:
        b = 1.0
    elif chg < 9.8:
        b = (9.8 - chg) / 2.8
    else:
        b = 0.0
    return round(_clip(0.5 * a + 0.5 * b) * 100, 2)


# ------------------------------------------------------------------
# step5 板块助攻(行业资金流 + 按需成分股)
# ------------------------------------------------------------------

def _code_key(code) -> str:
    """将 spot 的纯代码与 board_stocks 的 sh/sz/bj 代码归一到同一键。"""
    s = str(code or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix) and s[len(prefix):].isdigit():
            s = s[len(prefix):]
            break
    return s.zfill(6) if s.isdigit() else s


def _rank_sector_flow(sff: list) -> list[dict]:
    """按行业今日主力净流入降序排名，保留名称和排名。"""
    ranked = sorted(
        [x for x in (sff or []) if x.get("name")],
        key=lambda x: _to_f(x.get("main_net_inflow")) or -1e18,
        reverse=True,
    )
    return [{"name": x.get("name"), "rank": i + 1} for i, x in enumerate(ranked)]


def _board_members_batch(board_names: list[str]) -> dict[str, list[str]]:
    """按需取得板块成分股，返 board→归一化 code 列表。

    `industry_board` 只存板块汇总，没有 members/stocks 列；不能假装从该表反查。
    成分股接口本身是按需采集，失败时返回空并让 step5 诚实不通过。
    """
    if not board_names:
        return {}
    try:
        from data import board_stocks
    except Exception:
        return {}
    out = {}
    for board in board_names:
        try:
            rows = board_stocks.fetch_constituents(board, "行业") or []
            members = []
            for row in rows:
                key = _code_key(row.get("code") or row.get("raw_code"))
                if key:
                    members.append(key)
            out[str(board)] = list(dict.fromkeys(members))
        except Exception:
            out[str(board)] = []
    return out


def _step5_pass(code: str, ranked_sectors: list[dict], board_members: dict,
                spot_by_code: dict) -> tuple[bool, dict]:
    """板块助攻: 行业资金流热度前5 + 板块内至少2只涨停(≥9.8%)。

    一个股票可能属于多个行业，按热度最高的命中板块计；成员数据失败时不猜测，
    返回 False。`board_members` 由主流程批量取得，避免每只股票重复触网。
    """
    base = {"board": None, "board_rank": None, "board_zt_count": None}
    key = _code_key(code)
    for sector in ranked_sectors:
        name = str(sector.get("name") or "")
        members = set(board_members.get(name, []))
        if key not in members:
            continue
        base["board"] = name
        base["board_rank"] = sector.get("rank")
        zt = 0
        for member in members:
            spot = spot_by_code.get(member)
            chg = _to_f(spot.get("change_pct")) if spot else None
            if chg is not None and chg >= 9.8:
                zt += 1
        base["board_zt_count"] = zt
        return base["board_rank"] <= 5 and zt >= 2, base
    return False, base


# ------------------------------------------------------------------
# 连续五因子评分
# ------------------------------------------------------------------

_FACTOR_WEIGHTS = {
    "price_volume": 0.25,
    "liquidity_scale": 0.20,
    "trend": 0.25,
    "quote": 0.15,
    "board_assist": 0.15,
}


def _rank_pct(values: dict[str, float | None]) -> dict[str, float | None]:
    """把横截面原始值转为 0-1 分位；缺失保持 None，避免伪造零分。"""
    valid = [(c, float(v)) for c, v in values.items() if _to_f(v) is not None]
    if not valid:
        return {c: None for c in values}
    ordered = sorted(valid, key=lambda x: x[1])
    n = len(ordered)
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i + 1
        while j < n and ordered[j][1] == ordered[i][1]:
            j += 1
        # average rank，保证并列值获得相同分数
        pct = ((i + j - 1) / 2) / (n - 1) if n > 1 else 1.0
        for k in range(i, j):
            ranks[ordered[k][0]] = round(pct, 6)
        i = j
    return {c: ranks.get(c) for c in values}


def _weighted_score(factor_maps: dict[str, dict[str, float | None]],
                    codes: list[str]) -> tuple[dict[str, float], dict[str, dict], dict[str, float]]:
    """按可用因子重新归一权重，返 score、逐股因子分和覆盖率。"""
    pct_maps = {name: _rank_pct(vals) for name, vals in factor_maps.items()}
    scores, details, coverage = {}, {}, {}
    total = sum(_FACTOR_WEIGHTS.values())
    for code in codes:
        present = {name: pct_maps[name].get(code)
                   for name in _FACTOR_WEIGHTS
                   if pct_maps[name].get(code) is not None}
        denom = sum(_FACTOR_WEIGHTS[name] for name in present)
        score = (sum(_FACTOR_WEIGHTS[name] * float(value)
                     for name, value in present.items()) / denom * 100
                 if denom else 0.0)
        scores[code] = round(score, 2)
        details[code] = {name: round(float(pct_maps[name][code]) * 100, 2)
                         if pct_maps[name].get(code) is not None else None
                         for name in _FACTOR_WEIGHTS}
        coverage[code] = round(denom / total, 4)
    return scores, details, coverage


def _factor_price_volume(s, market_median: float | None) -> float | None:
    """实时量价原始分：涨幅温和、量比充分且跑赢市场。"""
    chg, vr = _to_f(s.get("change_pct")), _to_f(s.get("volume_ratio"))
    if chg is None and vr is None:
        return None
    volume = _clip((vr - 1.0) / 2.0) if vr is not None else 0.0
    # 3%-7% 是观察区间，过热涨幅不直接奖励。
    temper = 1.0 if chg is None or 3.0 <= chg <= 7.0 else max(0.0, 1.0 - abs(chg - 5.0) / 10.0)
    relative = _clip((chg - (market_median or 0.0) + 5.0) / 10.0) if chg is not None else 0.0
    return 0.45 * temper + 0.35 * volume + 0.20 * relative


def _factor_liquidity_scale(s) -> float | None:
    """流动性/规模原始分：换手和流通市值处于可观察区间更高。"""
    mv, tr = _to_f(s.get("circulating_market_cap")), _to_f(s.get("turnover_rate"))
    if mv is None and tr is None:
        return None
    mv_score = _clip(1.0 - abs(mv - 100.0) / 100.0) if mv is not None else 0.0
    tr_score = _clip(1.0 - abs(tr - 6.0) / 8.0) if tr is not None else 0.0
    return (0.55 * mv_score + 0.45 * tr_score
            if mv is not None and tr is not None else mv_score + tr_score)


def _factor_trend(ma_info: dict) -> float | None:
    """历史趋势原始分；无至少60日历史时明确缺失。"""
    if not ma_info or ma_info.get("need_history"):
        return None
    parts = []
    if ma_info.get("bullish_align"):
        parts.append(1.0)
    elif ma_info.get("bearish"):
        parts.append(0.0)
    else:
        parts.append(0.45)
    if ma_info.get("volume_breakout"):
        parts.append(1.0)
    elif ma_info.get("vol_avg20"):
        parts.append(_clip((_to_f(ma_info.get("last_vol")) or 0.0) /
                           max(_to_f(ma_info.get("vol_avg20")) or 1.0, 1.0) / 2.0))
    if ma_info.get("ma60") and ma_info.get("ma5"):
        parts.append(_clip((_to_f(ma_info["ma5"]) / max(_to_f(ma_info["ma60"]), 1e-9) - 0.9) / 0.3))
    return float(np.mean(parts)) if parts else None


def _quote_factor(q: dict | None) -> float | None:
    """TDX 五档/内外盘供求原始分，方向仅作为观察因子。"""
    if not q or _to_f(q.get("price")) is None:
        return None
    bvol, svol = _to_f(q.get("b_vol")), _to_f(q.get("s_vol"))
    active = ((bvol - svol) / (bvol + svol)
              if bvol is not None and svol is not None and bvol + svol > 0 else None)
    bid = sum((_to_f(q.get(f"bid_vol{i}")) or 0.0) * (_to_f(q.get(f"bid{i}")) or 0.0)
              for i in range(1, 6))
    ask = sum((_to_f(q.get(f"ask_vol{i}")) or 0.0) * (_to_f(q.get(f"ask{i}")) or 0.0)
              for i in range(1, 6))
    imbalance = (bid - ask) / (bid + ask) if bid + ask > 0 else None
    values = [x for x in (active, imbalance) if x is not None]
    return float(np.mean([(x + 1.0) / 2.0 for x in values])) if values else None


def _factor_board(assisted: bool, info: dict) -> float | None:
    """板块热度+涨停扩散；板块数据不可用时返回缺失而非惩罚。"""
    rank, zt = info.get("board_rank"), info.get("board_zt_count")
    if rank is None and zt is None:
        return None
    heat = _clip((6.0 - float(rank)) / 5.0) if _to_f(rank) is not None else 0.0
    spread = _clip(float(zt or 0) / 4.0) if zt is not None else 0.0
    return 0.65 * heat + 0.35 * spread


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------

def nextday_strong_rank(universe: str = "stock",
                        codes: list[str] | None = None,
                        limit: int = 50, days: int = 30,
                        min_change_pct: float = 5.0,
                        min_turnover: float = 3.0,
                        max_price: float = 50.0,
                        min_mv: float = 10.0, max_mv: float = 200.0,
                        max_pe: float = 150.0,
                        exclude_st: bool = True) -> dict:
    """次日强势五因子连续评分，兼容保留旧步骤诊断字段。

    五因子先横截面 rank-pct，再按可用因子重归一加权；旧 step 字段仅用于
    解释筛选条件，不再主导排序。TDX 盘口失败时 quote 因子诚实缺失。
    """
    p = {"min_change_pct": min_change_pct, "min_turnover": min_turnover,
         "max_price": max_price, "min_mv": min_mv, "max_mv": max_mv,
         "max_pe": max_pe, "exclude_st": exclude_st}
    key = (universe, tuple(codes or []), limit, days, min_change_pct,
           min_turnover, max_price, min_mv, max_mv, max_pe, exclude_st)
    now = datetime.now()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]).total_seconds() < _CACHE_TTL:
        return hit[1]

    base = {"universe": universe, "count": 0, "items": [], "limit": limit,
            "days": days, "filters": p,
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S")}

    spot_all = db.query_rows("stock_spot", limit=0)
    all_spot = list(spot_all)
    # 预加载 ST 名单
    try:
        st_rows = db.query_rows("st_list", limit=0)
        st_map = {str(r.get("code")): r.get("st_type") for r in st_rows}
    except Exception:
        st_map = {}
    st_set = set(st_map)
    if codes:
        cset = {str(c) for c in codes}
        spot_all = [s for s in spot_all if str(s.get("code")) in cset]

    if not spot_all:
        base["note"] = "stock_spot 为空，先 /api/refresh 采集"
        base["market_median_chg"] = None
        _CACHE[key] = (now, base)
        return base

    # 诊断缺失字段(新浪源缺换手率/市值/PE/量比,东财被封时)
    _missing = [f for f in ("turnover_rate", "circulating_market_cap", "pe", "volume_ratio")
                if not any(s.get(f) is not None for s in spot_all)]
    if _missing:
        base["note"] = (f"关键字段缺失: {', '.join(_missing)}——"
                        f"当前 spot 源(新浪)不含这些字段,"
                        f"设 SCREENER_HTTPS_PROXY 代理后 /api/refresh 走东财可补全")

    median_chg = _median([_to_f(s.get("change_pct")) for s in all_spot])
    base["market_median_chg"] = _nan(median_chg)

    # 粗筛(codes 限定时不粗筛)
    if not codes:
        # 前端“全部”以 min_change_pct=0 表示，不再隐含涨幅硬过滤；默认
        # 阈值仍用于缩小精算名单，避免全市场 TDX 五档请求过大。
        if min_change_pct <= 0:
            cand = list(spot_all)
        else:
            cand = [s for s in spot_all
                    if (_to_f(s.get("change_pct")) or -99) > min_change_pct]
        cand.sort(key=lambda s: _to_f(s.get("change_pct")) or -99, reverse=True)
        cand = cand[:_SCAN_K]
    else:
        cand = spot_all

    codes_k = [str(s.get("code")) for s in cand]

    # step3 批量 MA
    ma_info = _ma_arrange_batch(universe, codes_k, days)

    # TDX 盘口因子：仅对候选小名单取实时五档，失败时该因子缺失而非零分
    quote_by_code = {}
    try:
        from data import pytdx_client
        quote_by_code = {str(q.get("code")): q
                         for q in pytdx_client.get_quote(codes_k)
                         if q.get("code")}
    except Exception:
        quote_by_code = {}

    # step5 板块助攻: 行业资金流排名 + 按需取得前5热板块成分股
    try:
        sff = db.query_rows("sector_fund_flow",
                            where="sector_type = ? AND indicator = ?",
                            params=("行业", "今日"), limit=0)
    except Exception:
        sff = []
    ranked_sectors = _rank_sector_flow(sff)
    top5 = [s["name"] for s in ranked_sectors[:5] if s.get("name")]
    # 批量取前5板块成员列表(按需,可能触网)
    board_members = _board_members_batch(top5) if top5 else {}
    # spot_by_code 索引: 归一化 6 位纯代码 → spot dict
    spot_by_code = {_code_key(s.get("code")): s for s in all_spot if s.get("code")}

    # 先计算每个因子的原始值，再做横截面 rank-pct；不把缺失伪装成 0。
    factor_maps = {name: {} for name in _FACTOR_WEIGHTS}
    details_by_code = {}
    board_by_code = {}
    pass_by_code = {}
    for s in cand:
        code = str(s.get("code"))
        mi = ma_info.get(code, {})
        s5, bd = _step5_pass(code, ranked_sectors, board_members, spot_by_code)
        board_by_code[code] = bd
        pass_by_code[code] = s5
        factor_maps["price_volume"][code] = _factor_price_volume(s, median_chg)
        factor_maps["liquidity_scale"][code] = _factor_liquidity_scale(s)
        factor_maps["trend"][code] = _factor_trend(mi)
        factor_maps["quote"][code] = _quote_factor(quote_by_code.get(code))
        factor_maps["board_assist"][code] = _factor_board(s5, bd)

    scores, factor_scores, coverage = _weighted_score(factor_maps, codes_k)
    items = []
    for s in cand:
        code = str(s.get("code"))
        name = s.get("name") or code
        mi = ma_info.get(code, {})
        bd = board_by_code.get(code, {})
        s1 = _step1_pass(s, p)
        s2 = _step2_pass(s, p, st_set)
        s3 = _step3_pass(mi)
        s4 = _step4_score(s)
        s5 = pass_by_code.get(code, False)
        hard = sum([s1, s2, s3, s5])
        items.append({
            "code": code, "name": name,
            "change_pct": _nan(_to_f(s.get("change_pct"))),
            "turnover_rate": _nan(_to_f(s.get("turnover_rate"))),
            "latest_price": _nan(_to_f(s.get("latest_price"))),
            "circulating_market_cap": _nan(_to_f(s.get("circulating_market_cap"))),
            "pe": _nan(_to_f(s.get("pe"))),
            "st_type": st_map.get(code) or s.get("st_type"),
            "volume_ratio": _nan(_to_f(s.get("volume_ratio"))),
            "board": bd.get("board"), "board_rank": bd.get("board_rank"),
            "board_zt_count": bd.get("board_zt_count"),
            "step1_pass": s1, "step2_pass": s2, "step3_pass": s3,
            "step4_score": s4, "step5_pass": s5,
            "hard_pass": hard,
            "need_history": mi.get("need_history", False),
            "factor_scores": factor_scores.get(code, {}),
            "score_coverage": coverage.get(code, 0.0),
            "quote_available": code in quote_by_code,
            "score": scores.get(code, 0.0),
        })

    items.sort(key=lambda x: (x["score"], x["hard_pass"]), reverse=True)
    items = items[:max(0, limit)]
    for i, it in enumerate(items):
        it["rank"] = i + 1

    base["count"] = len(items)
    base["items"] = items
    _CACHE[key] = (now, base)
    return base


def _median(values):
    xs = [v for v in values if v is not None]
    if not xs:
        return None
    return float(np.median(xs))
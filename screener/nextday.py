# -*- coding: utf-8 -*-
"""次日强势概率排序(7 因子统一评分版：含趋势形态+板块助攻)。

**不触网**: 资金连续用 smart_money._behavior_batch(批量一次 DB 查询,不调 finshare)；
主力阶段用 batch 结果简易映射(不调 main_force_phase,避免其内部 finshare 触网)；
趋势形态复用 signals._uni_panels 取 close 面板(仅取数)+本地算 MA，不新增采集；
板块助攻从 industry_board 成分表反查 code→board。
**不新增表**: 复用 stock_spot/smart_money_action/stock_daily/industry_board。

7 因子(经验先验,非回归校准,合计=1.0):
  量价强势 0.20 | 换手市值 0.15 | 资金连续 0.15 | 主力阶段 0.10
  筹码收集 0.15 | 趋势形态 0.15 | 板块助攻 0.10

mode="legacy" 用旧 5 因子权重(合计=1.0)，跳过趋势形态和板块助攻。

强度归一: 各因子 strength∈[0,1](主力阶段出货为 -1.0,趋势形态空头为 -1.0)。
score∈[0,100]。粗筛: 涨幅≥min_change_pct AND 量比≥min_volume_ratio。
30s 进程缓存。合规(个人自用放松): 次日强势清单——机械因子排序观察清单。
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from data import db
from . import smart_money

WEIGHTS = {
    "量价强势": 0.20,
    "换手市值": 0.15,
    "资金连续": 0.15,
    "主力阶段": 0.10,
    "筹码收集": 0.15,
    "趋势形态": 0.15,
    "板块助攻": 0.10,
}
WEIGHTS_LEGACY = {
    "量价强势": 0.25,
    "换手市值": 0.20,
    "资金连续": 0.20,
    "主力阶段": 0.15,
    "筹码收集": 0.20,
}
PHASE_SCORE = {"吸筹": 1.0, "出货": -1.0, "流入": 0.4, "观望": 0.2}
_SCAN_K = 200
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


def _hard_gate_pass(s, params, st_set=None) -> bool:
    """硬门槛过滤(每日漏斗)。返 True=通过。
    params: {min_turnover, max_price, min_mv, max_mv, max_pe, exclude_st}。
    默认值(0/9999/0/999999/9999/False)不触发过滤。"""
    min_turn = params.get("min_turnover", 0)
    if min_turn > 0:
        tr = _to_f(s.get("turnover_rate"))
        if tr is None or tr < min_turn:
            return False
    max_px = params.get("max_price", 9999)
    if max_px < 9999:
        px = _to_f(s.get("latest_price"))
        if px is None or px > max_px:
            return False
    min_mv = params.get("min_mv", 0)
    max_mv = params.get("max_mv", 999999)
    if min_mv > 0 or max_mv < 999999:
        mc = _to_f(s.get("circulating_market_cap"))
        if mc is None or mc < min_mv or mc > max_mv:
            return False
    max_pe = params.get("max_pe", 9999)
    if max_pe < 9999:
        pe = _to_f(s.get("pe"))
        if pe is None or pe <= 0 or pe > max_pe:
            return False
    if params.get("exclude_st") and st_set:
        if str(s.get("code")) in st_set:
            return False
    return True


def _f_涨幅区间(chg):
    """文章条件2:3-5%最佳(1.0),1-3%/5-9.8%次之(0.5),涨停≥9.8%(0.3),<1%(0.2),跌(<0)(0)。"""
    if chg is None:
        return 0.0
    if chg < 0:
        return 0.0
    if chg < 1:
        return 0.2
    if chg < 3 or 5 <= chg < 9.8:
        return 0.5
    if 3 <= chg < 5:
        return 1.0
    return 0.3  # 涨停


def _f_量比(vr):
    """文章条件3:量比>1 有资金活跃。tanh((vr-1)*1.5),vr<1→0(clip)。"""
    if vr is None or vr <= 0:
        return 0.0
    return _clip(math.tanh((vr - 1) * 1.5))


def _f_跑赢大盘(chg, median):
    """文章条件7:跑赢大盘。tanh((chg-median)*2),不及→0(clip)。"""
    if chg is None or median is None:
        return 0.0
    return _clip(math.tanh((chg - median) * 2))


def _f_量价强势(s, median_chg) -> tuple[float, dict]:
    chg = _to_f(s.get("change_pct"))
    vr = _to_f(s.get("volume_ratio"))
    a = _f_涨幅区间(chg)
    b = _f_量比(vr)
    c = _f_跑赢大盘(chg, median_chg)
    strength = _clip(0.4 * a + 0.3 * b + 0.3 * c)
    return strength, {"change_pct": _nan(chg), "volume_ratio": _nan(vr),
                      "market_median_chg": _nan(median_chg)}


def _f_换手市值(s) -> tuple[float, dict]:
    tr = _to_f(s.get("turnover_rate"))  # %
    mc = _to_f(s.get("circulating_market_cap"))  # 亿元
    if tr is None or tr <= 0:
        h = 0.0
    elif 5 <= tr <= 10:
        h = 1.0
    elif 3 <= tr < 5 or 10 < tr <= 15:
        h = 0.5
    else:
        h = 0.2
    if mc is None or mc <= 0:
        m = 0.0
    elif 50 <= mc <= 200:
        m = 1.0
    elif 20 <= mc < 50 or 200 < mc <= 500:
        m = 0.5
    else:
        m = 0.2
    strength = _clip(0.5 * h + 0.5 * m)
    return strength, {"turnover_rate": _nan(tr),
                      "circulating_market_cap": _nan(mc)}


def _f_资金连续(b: dict) -> tuple[float, dict]:
    streak = b.get("streak_inflow") or 0
    accel = b.get("margin_accel")
    s1 = math.tanh((streak or 0) / 8)
    s2 = _clip(math.tanh((accel or 0) / 1e7)) if accel is not None else 0.0
    strength = _clip(0.6 * s1 + 0.4 * s2)
    return strength, {"streak_inflow": streak, "margin_accel": _nan(accel)}


def _f_主力阶段(b: dict) -> tuple[float, dict, str]:
    si = b.get("streak_inflow") or 0
    so = b.get("streak_outflow") or 0
    if si >= 3:
        score, phase = PHASE_SCORE["吸筹"], "吸筹"
    elif so >= 3:
        score, phase = PHASE_SCORE["出货"], "出货"
    elif si > 0:
        score, phase = PHASE_SCORE["流入"], "流入"
    else:
        score, phase = PHASE_SCORE["观望"], "观望"
    conf = min(max(si, so) / 5, 1.0)
    return score * conf, {"streak_inflow": si, "streak_outflow": so,
                          "confidence": round(conf, 4)}, phase


def _f_筹码收集(chip: dict) -> tuple[float, dict]:
    trend = chip.get("trend") or {}
    cd = trend.get("chip_concentration_delta")
    strength = _clip(math.tanh(-(cd or 0) * 5)) if cd is not None else 0.0
    return strength, {"chip_concentration_delta": _nan(cd)}


# ------------------------------------------------------------------
# 新增因子：趋势形态（批量 MA）
# ------------------------------------------------------------------

def _f_趋势形态_batch(codes: list[str], close, amount) -> dict:
    """批量算趋势形态 strength: 多头排列→1.0, 放量突破→0.8, 粘合→0.5,
    无明显→0.3, 空头→-1.0。返 {code: {strength, detail}}。"""
    out = {c: {"strength": 0.0, "bullish_align": False, "volume_breakout": False,
               "bearish": False, "converged": False, "need_history": False}
           for c in codes}
    if close is None or close.empty:
        return out
    if close.index.name == "date":
        close = close.copy()
    window = 60
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
        bullish = bool(ma5 > ma10 > ma20 and ma20 > ma20_prev)
        bearish = bool(ma5 < ma10 < ma20)
        converged = bool(min(ma5, ma10, ma20) > 0 and
                         (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / min(ma5, ma10, ma20) < 0.005)
        vb = False
        if amount is not None and c in amount.columns:
            amt = amount[c].dropna()
            if len(amt) >= 20:
                last_vol = amt.iloc[-1]
                vol_avg20 = amt.iloc[-20:].mean()
                vb = bool(last_close > ma60 and last_vol >= 2 * vol_avg20)
        out[c].update({
            "bullish_align": bullish, "bearish": bearish,
            "converged": converged, "volume_breakout": vb,
            "need_history": False,
        })
        if bearish:
            out[c]["strength"] = -1.0
        elif bullish:
            out[c]["strength"] = 1.0
        elif vb:
            out[c]["strength"] = 0.8
        elif converged:
            out[c]["strength"] = 0.5
        else:
            out[c]["strength"] = 0.3
    return out


# ------------------------------------------------------------------
# 新增因子：板块助攻
# ------------------------------------------------------------------

def _f_板块助攻_batch(codes: list[str], spots: list) -> dict:
    """批量算板块助攻 strength: 行业净流入排名 + 板块内涨停数。
    每只 code 从 industry_board 成分表反查 board，再用 sector_fund_flow 行业资金流判热度。
    返 {code: {strength, board, board_rank, sector_zt, sector_members}}。
    board 列缺失或无法映射时 strength=0.5 中性。"""
    out = {c: {"strength": 0.5, "board": None, "board_rank": None,
               "sector_zt": 0, "sector_members": 0} for c in codes}
    try:
        boards = db.query_rows("industry_board", limit=0) or []
    except Exception:
        return out
    if not boards:
        return out

    try:
        flows = db.query_rows("sector_fund_flow",
                              where="sector_type = ? AND indicator = ?",
                              params=("行业", "今日"), limit=0) or []
    except Exception:
        flows = []
    flow_map = {}
    for row in flows:
        name = row.get("name")
        if name is None:
            continue
        flow_map[str(name)] = _to_f(row.get("main_net_inflow")) or -1e18
    ranked_boards = [name for name, _ in sorted(flow_map.items(),
                                                key=lambda kv: kv[1],
                                                reverse=True)]

    # 建 code→board 映射 + 板块统计
    code_to_boards = {}  # code -> list of board names
    board_sectors = {}   # board -> {codes, members count}
    for b in boards:
        nm = b.get("name")
        if not nm:
            continue
        members = b.get("members") or b.get("stocks") or []
        mcodes = [str(m) for m in members]
        board_sectors[nm] = {"codes": set(mcodes), "count": len(mcodes)}
        for m in mcodes:
            code_to_boards.setdefault(m, []).append(nm)

    # 涨停判断：change_pct>=9.8%
    zt_set = set()
    for s in spots:
        chg = _to_f(s.get("change_pct"))
        if chg is not None and chg >= 9.8:
            zt_set.add(str(s.get("code")))

    # 每只 code 的板块热度排名
    for c in codes:
        bnames = code_to_boards.get(c, [])
        if not bnames:
            out[c] = {"strength": 0.5, "board": None, "board_rank": None,
                      "sector_zt": 0, "sector_members": 0}
            continue
        # 选第一个板块（主行业）
        bn = bnames[0]
        sector = board_sectors.get(bn, {})
        scodes = sector.get("codes", set())
        zt_in_sector = sum(1 for zc in zt_set if zc in scodes)
        # 热度排名：按行业净流入降序
        rank = ranked_boards.index(bn) + 1 if bn in ranked_boards else None
        rank_ok = rank is not None and rank <= 5
        zt_ok = zt_in_sector >= 2
        if rank_ok and zt_ok:
            strength = 1.0
        elif rank_ok:
            strength = 0.7
        elif zt_ok:
            strength = 0.6
        else:
            strength = 0.4
        out[c] = {"strength": strength, "board": bn, "board_rank": rank,
                  "sector_zt": zt_in_sector, "sector_members": sector.get("count", 0)}

    return out


def _median(values):
    xs = [v for v in values if v is not None]
    if not xs:
        return None
    return float(np.median(xs))


def nextday_strong_rank(universe: str = "stock",
                        codes: list[str] | None = None,
                        limit: int = 50, days: int = 30,
                        min_change_pct: float = 1.0,
                        min_volume_ratio: float = 0.5,
                        min_turnover: float = 0.0,
                        max_price: float = 9999.0,
                        min_mv: float = 0.0,
                        max_mv: float = 999999.0,
                        max_pe: float = 9999.0,
                        exclude_st: bool = False,
                        mode: str = "full") -> dict:
    """次日强势概率排序。返 {universe, count, items, ts, weights, filters}。

    mode='full'(默认) 7 因子统一评分；mode='legacy' 旧 5 因子(跳过趋势形态+板块助攻)。
    粗筛: change_pct>=min_change_pct AND volume_ratio>=min_volume_ratio, 可叠加
    每日漏斗硬门槛(min_turnover/max_price/min_mv/max_mv/max_pe/exclude_st)。
    codes 非空: 限定标的集(绕过粗筛但仍走硬门槛)。粗筛后>200 按涨幅降序截。
    items 每行: code/name/score(0-100)/rank/phase/factors/detail。
    """
    key = (universe, tuple(codes or []), limit, days,
           min_change_pct, min_volume_ratio, min_turnover, max_price,
           min_mv, max_mv, max_pe, exclude_st, mode)
    now = datetime.now()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]).total_seconds() < _CACHE_TTL:
        return hit[1]

    w = WEIGHTS_LEGACY if mode == "legacy" else WEIGHTS
    base = {"universe": universe, "count": 0, "items": [], "limit": limit,
            "days": days, "weights": dict(w), "mode": mode,
            "filters": {"min_change_pct": min_change_pct,
                        "min_volume_ratio": min_volume_ratio,
                        "min_turnover": min_turnover,
                        "max_price": max_price,
                        "min_mv": min_mv,
                        "max_mv": max_mv,
                        "max_pe": max_pe,
                        "exclude_st": exclude_st},
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S")}

    spot_all = db.query_rows("stock_spot", limit=0)
    if codes:
        cset = {str(c) for c in codes}
        spot_all = [s for s in spot_all if str(s.get("code")) in cset]

    if not spot_all:
        base["note"] = "stock_spot 为空，先 /api/refresh 采集"
        _CACHE[key] = (now, base)
        return base

    try:
        st_rows = db.query_rows("st_list", limit=0)
        st_set = {str(r.get("code")) for r in st_rows if r.get("code") is not None}
    except Exception:
        st_set = set()

    median_chg = _median([_to_f(s.get("change_pct")) for s in spot_all])
    hard_params = {"min_turnover": min_turnover, "max_price": max_price,
                   "min_mv": min_mv, "max_mv": max_mv, "max_pe": max_pe,
                   "exclude_st": exclude_st}

    # 粗筛
    if not codes:
        cand = []
        for s in spot_all:
            chg = _to_f(s.get("change_pct"))
            vr = _to_f(s.get("volume_ratio"))
            if chg is not None and chg >= min_change_pct and \
               (vr is not None and vr >= min_volume_ratio) and \
               _hard_gate_pass(s, hard_params, st_set):
                cand.append(s)
        if not cand:
            cand = [s for s in spot_all
                    if (_to_f(s.get("change_pct")) or -99) >= min_change_pct and
                    _hard_gate_pass(s, hard_params, st_set)]
        cand.sort(key=lambda s: _to_f(s.get("change_pct")) or -99, reverse=True)
        cand = cand[:_SCAN_K]
    else:
        cand = [s for s in spot_all if _hard_gate_pass(s, hard_params, st_set)]

    codes_k = [str(s.get("code")) for s in cand]

    # 批量资金流连续性
    try:
        batch = smart_money._behavior_batch(codes_k, days)
    except Exception:
        batch = {}

    # 趋势形态（批量 MA）
    trend_info = {}
    if mode != "legacy":
        try:
            from backtest import signals as _sig
            close, amount = _sig._uni_panels(universe, codes_k)
            trend_info = _f_趋势形态_batch(codes_k, close, amount)
        except Exception:
            trend_info = {c: {"strength": 0.0, "need_history": True} for c in codes_k}

    # 板块助攻
    board_info = {}
    if mode != "legacy":
        try:
            board_info = _f_板块助攻_batch(codes_k, cand)
        except Exception:
            board_info = {c: {"strength": 0.5} for c in codes_k}

    items = []
    for s in cand:
        code = str(s.get("code"))
        name = s.get("name") or code
        b = batch.get(code, {})

        s1, d1 = _f_量价强势(s, median_chg)
        s2, d2 = _f_换手市值(s)
        s3, d3 = _f_资金连续(b)
        s4, d4, phase = _f_主力阶段(b)
        try:
            chip = smart_money.chip_distribution(code, window=60)
        except Exception:
            chip = {}
        s5, d5 = _f_筹码收集(chip)

        factors = {"量价强势": round(s1, 4), "换手市值": round(s2, 4),
                   "资金连续": round(s3, 4), "主力阶段": round(s4, 4),
                   "筹码收集": round(s5, 4)}

        if mode != "legacy":
            ti = trend_info.get(code, {})
            s6 = ti.get("strength", 0.0)
            bi = board_info.get(code, {})
            s7 = bi.get("strength", 0.5)
            factors["趋势形态"] = round(s6, 4)
            factors["板块助攻"] = round(s7, 4)
            detail = {**d1, **d2, **d3, **d4, **d5,
                      "trend_type": "bullish" if ti.get("bullish_align") else
                                    "bearish" if ti.get("bearish") else
                                    "breakout" if ti.get("volume_breakout") else
                                    "converged" if ti.get("converged") else
                                    "none",
                      "board": bi.get("board"),
                      "board_rank": bi.get("board_rank"),
                      "sector_zt": bi.get("sector_zt"),
                      "sector_members": bi.get("sector_members")}
            score_raw = (w["量价强势"] * s1 + w["换手市值"] * s2 +
                         w["资金连续"] * s3 + w["主力阶段"] * s4 +
                         w["筹码收集"] * s5 + w["趋势形态"] * s6 +
                         w["板块助攻"] * s7)
        else:
            detail = {**d1, **d2, **d3, **d4, **d5}
            score_raw = (w["量价强势"] * s1 + w["换手市值"] * s2 +
                         w["资金连续"] * s3 + w["主力阶段"] * s4 +
                         w["筹码收集"] * s5)

        score = round(_clip(score_raw) * 100, 2)

        items.append({
            "code": code, "name": name, "score": score,
            "score_raw": round(score_raw, 4), "phase": phase,
            "factors": factors, "detail": detail,
        })

    items.sort(key=lambda x: x["score_raw"], reverse=True)
    items = items[:max(0, limit)]
    for i, it in enumerate(items):
        it["rank"] = i + 1
        del it["score_raw"]

    base["count"] = len(items)
    base["market_median_chg"] = _nan(median_chg)
    base["items"] = items
    _CACHE[key] = (now, base)
    return base
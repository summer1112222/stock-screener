# -*- coding: utf-8 -*-
"""每日强势股筛选(5步漏斗+板块助攻，混合:硬剔除+软打分)。

**不触网不新增表**: 复用 stock_spot/sector_fund_flow/stock_daily。
step3 复用 backtest.signals._uni_panels 取 close 面板(仅取数不复用触发语义)+本地算 MA。
step5 复用 board_money_link 的 code→board 反查套路(内联,不调 board_money_link 避免多算 net_intensity)。

5步(对齐用户方法论):
  step1 入选: 涨幅>5% + 换手>3% + 股价<50
  step2 雷区: 流通市值 10-200亿 + PE<=150且非亏损 + 非ST
  step3 形态: 多头排列(5/10/20 MA向上发散) 或 放量突破(站稳60日线+量翻倍)
  step4 软打分: 量比>2.5强度 + 涨幅<7%避追高 (分时项永久降级,0不崩)
  step5 板块助攻: 所属板块热度前5 + 板块内>=2涨停股

混合编排: step1/2/3/5 硬剔除(通过/不通过), step4 软打分(0-100排序)。
排序键: 硬通过数×10 + 软分 降序。30s 进程缓存。
合规(个人自用放松): 每日强势清单——机械漏斗+板块助攻排序观察清单。
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


def _step2_pass(s, p) -> bool:
    """雷区剔除: 市值[min_mv,max_mv] + PE<=max_pe且非亏损 + 非ST。"""
    mc = _to_f(s.get("circulating_market_cap"))
    pe = _to_f(s.get("pe"))
    st = s.get("st_type")
    if mc is None or mc < p["min_mv"] or mc > p["max_mv"]:
        return False
    # pe 为空/负=亏损→剔除; pe>max_pe→剔除
    if pe is None or pe <= 0 or pe > p["max_pe"]:
        return False
    if st:  # 非空=ST/*ST
        return False
    return True


from backtest import signals as _sig


def _ma_arrange_batch(universe: str, codes: list[str]) -> dict:
    """批量算 5/10/20/60 MA + 量。返 {code: ma_info}。
    ma_info: {ma5,ma10,ma20,ma60,bullish_align,volume_breakout,bearish,converged,need_history,last_vol,vol_avg20}
    无历史/<60日→need_history=True,该股 step3 跳过不崩。
    """
    out = {c: {"ma5": None, "ma10": None, "ma20": None, "ma60": None,
               "bullish_align": False, "volume_breakout": False,
               "bearish": False, "converged": False, "need_history": False,
               "last_vol": None, "vol_avg20": None} for c in codes}
    if not codes:
        return out
    try:
        close, amount = _sig._uni_panels(universe, codes)
    except Exception:
        return out
    if close is None or close.empty:
        return out
    for c in codes:
        if c not in close.columns:
            out[c]["need_history"] = True
            continue
        s = close[c].dropna()
        if len(s) < 60:
            out[c]["need_history"] = True
            continue
        ma5 = s.rolling(5).mean().iloc[-1]
        ma10 = s.rolling(10).mean().iloc[-1]
        ma20 = s.rolling(20).mean().iloc[-1]
        ma60 = s.rolling(60).mean().iloc[-1]
        ma20_prev = s.rolling(20).mean().iloc[-6] if len(s) >= 6 else ma20  # 5日前
        last_close = s.iloc[-1]
        out[c].update({"ma5": _nan(ma5), "ma10": _nan(ma10), "ma20": _nan(ma20),
                       "ma60": _nan(ma60)})
        # 多头排列: ma5>ma10>ma20 且 ma20 较5日前上行(向上发散)
        out[c]["bullish_align"] = bool(
            ma5 > ma10 > ma20 and ma20 > ma20_prev)
        # 空头排列: ma5<ma10<ma20
        out[c]["bearish"] = bool(ma5 < ma10 < ma20)
        # 均线粘合: (max-min)/min < 0.5%
        if min(ma5, ma10, ma20) > 0:
            spread = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / min(ma5, ma10, ma20)
            out[c]["converged"] = bool(spread < 0.005)
        # 放量突破: close>ma60 且 当日量>=2×过去20日均量
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
    """形态过滤: 多头排列 OR 放量突破 通过; 空头排列 剔除。
    均线粘合(converged)不单独剔除——粘合后放量突破是有效形态(粘合后突破正是买点)。"""
    if info.get("need_history"):
        return False
    if info.get("bearish"):
        return False
    return info.get("bullish_align") or info.get("volume_breakout")


def _step4_score(s) -> float:
    """软打分(0-100,排序用): 量比>2.5强度(0.5权重) + 涨幅<7%避追高(0.5权重)。
    分时项永久降级(无数据源),0不崩。"""
    vr = _to_f(s.get("volume_ratio"))
    chg = _to_f(s.get("change_pct"))
    # 量比强度: >=2.5满分, 线性缩放到[0,1]
    a = _clip((vr - 1.0) / 1.5) if vr is not None else 0.0
    # 涨幅温和: <7%满分, 7-9.8%线性扣至0, >=9.8%为0
    if chg is None:
        b = 0.0
    elif chg < 7:
        b = 1.0
    elif chg < 9.8:
        b = (9.8 - chg) / 2.8
    else:
        b = 0.0
    return round(_clip(0.5 * a + 0.5 * b) * 100, 2)


def _step5_pass(code, spots, sff) -> tuple[bool, dict]:
    """板块助攻(行业口径): 板块净流入排名前5 + 板块内>=2涨停股。
    返 (pass, {board, board_rank, board_zt_count})。无board→pass=False。"""
    code = str(code)
    base = {"board": None, "board_rank": None, "board_zt_count": None}
    board = None
    for s in spots:
        if str(s.get("code")) == code:
            board = s.get("board")
            break
    if not board:
        return False, base
    base["board"] = board
    # 板块净流入排名(降序)
    if sff:
        ranked = sorted(sff, key=lambda x: _to_f(x.get("main_net_inflow")) or -1e18,
                        reverse=True)
        names = [r.get("name") for r in ranked]
        if board in names:
            idx = names.index(board)
            base["board_rank"] = idx + 1
    # 板块内涨停股(change_pct>=9.8%)
    intra = [s for s in spots if s.get("board") == board]
    zt = 0
    for s in intra:
        chg = _to_f(s.get("change_pct"))
        if chg is not None and chg >= 9.8:
            zt += 1
    base["board_zt_count"] = zt
    ok = base["board_rank"] is not None and base["board_rank"] <= 5 and zt >= 2
    return ok, base

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

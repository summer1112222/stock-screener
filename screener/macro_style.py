# -*- coding: utf-8 -*-
"""全市场宏观风格基调状态机：把 market_daily 最新行捏成 进攻/均衡/防御 三态 + 强度。

B 宏观风格维度（B1 地基层，纯函数）：把"全市场风险偏好"从量价温度表量化成
一个连续 score∈[-1,1]（>+0.2 进攻 / <-0.2 防御 / 其余均衡）与强度 |score|，
供 quality 精排 / smart_money 清单作**排序上下文/标注**——B2 用它调制板块政策命中
加成、B3 用它调制政策事件催化权重。本层自身不改任何排序签名。

输入是 market_daily 最新行 dict（由调用方 market.latest() 传入，本模块不查库不触网），
缺字段按"可用因子加权重归一"处理（同 nextday._weighted_score 范式），缺失从分母排除，
全缺失诚实返 neutral/strength 0。

合规：宏观风格为公开市场温度(估值分位/涨停家数/两融/涨跌家数)的机械横截观察，
非择时信号、非买卖建议，措辞"市场风格机械观察"。
"""
from __future__ import annotations
import math

# 因子权重（经验先验，非 IC 校准）：情绪(涨停)/杠杆(两融)/估值(空间)/宽度(量能)
_W = {"zt": 0.30, "margin": 0.25, "pe": 0.25, "breadth": 0.20}
_ZT_REF = 50        # 涨停家数参考中轴：达此数情绪满分进攻
_MARGIN_REF = 50.0  # 两融环比参考量级(亿)：±50 亿达 tanh 中段饱和
_STYLE_EDGE = 0.2   # 风格判定阈值


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _factor_pe(pe_pct) -> float | None:
    """估值分位：低分位→进攻(有空间)，高分位→防御(偏贵)。pe_pct∈[0,1]→[-1,1]。
    缺失/越界 → None。"""
    v = _f(pe_pct)
    if v is None or not (0.0 <= v <= 1.0):
        return None
    return (0.5 - v) * 2.0


def _factor_zt(zt_count) -> float | None:
    """涨停家数：情绪热→进攻，冷→防御。绝对阈值 min(zt/ZT_REF,1)*2-1 ∈[-1,1]，饱和。"""
    v = _f(zt_count)
    if v is None or v < 0:
        return None
    return min(v / _ZT_REF, 1.0) * 2.0 - 1.0


def _factor_margin(margin_chg) -> float | None:
    """两融环比(亿)：扩张→进攻(杠杆加)，收缩→防御。tanh 归一，大额饱和。"""
    v = _f(margin_chg)
    if v is None:
        return None
    return math.tanh(v / _MARGIN_REF)


def _factor_breadth(up_count, down_count) -> float | None:
    """涨跌家数比：up/(up+down)>0.5→进攻。合计 0/缺失 → None（不除零）。"""
    up, dn = _f(up_count), _f(down_count)
    if up is None or dn is None or (up + dn) <= 0:
        return None
    return (up / (up + dn) - 0.5) * 2.0


def macro_style(market: dict | None) -> dict:
    """全市场宏观风格判定。

    market: market_daily 最新行 dict，可用键 pe_pct/zt_count/margin_chg/up_count/down_count。
    返回 {style: "attack"|"neutral"|"defense", score∈[-1,1], strength∈[0,1],
      coverage∈[0,1]（已用因子权重占比：单因子独唱<多因子共振），
          drivers: {factor: {raw, contrib, weight}}}。
    纯函数、不触网；因子缺失按可用加权重归一，全缺失 → neutral/strength 0。
    """
    m = market or {}
    fns = {
        "zt": lambda: _factor_zt(m.get("zt_count")),
        "margin": lambda: _factor_margin(m.get("margin_chg")),
        "pe": lambda: _factor_pe(m.get("pe_pct")),
        "breadth": lambda: _factor_breadth(m.get("up_count"), m.get("down_count")),
    }

    acc, wsum, drivers = 0.0, 0.0, {}
    for name, fn in fns.items():
        fi = fn()
        if fi is None:
            continue
        w = _W[name]
        contrib = w * fi
        acc += contrib
        wsum += w
        drivers[name] = {"raw": m.get({"zt": "zt_count", "margin": "margin_chg",
                                       "pe": "pe_pct", "breadth": "breadth"}[name]),
                         "contrib": round(contrib, 4), "weight": w}

    tw = sum(_W.values()) or 1.0
    coverage = round(wsum / tw, 4) if wsum > 0 else 0.0
    score = round(acc / wsum, 4) if wsum > 0 else 0.0
    if score > _STYLE_EDGE:
        style = "attack"
    elif score < -_STYLE_EDGE:
        style = "defense"
    else:
        style = "neutral"
    return {"style": style, "score": score, "strength": round(abs(score), 4),
            "coverage": coverage, "drivers": drivers}

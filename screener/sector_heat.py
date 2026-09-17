# -*- coding: utf-8 -*-
"""行业景气排序加成层：板块资金验证 + 政策主题命中 → 行业景气分位。

把"板块资金净流入排名 + 板块上涨宽度 + 静态政策命中"捏成一个横截排序上下文，
供 quality 精排 / smart_money 清单作**排序加成**（不改共振签名、不荐板块）。

数据源三表只读（sector_fund_flow / industry_board / stock_spot），由调用方以
list[dict] 传入；个股→板块用调用方给的 board_members 反查（如
screener.nextday._board_members_batch）。本模块不触网、纯函数。

合规：行业景气为机械横截排序上下文（资金净流入排名 + 上涨宽度 + 政策命中），
非板块涨跌预测、非买卖信号。
"""
from __future__ import annotations
import pandas as pd

# 静态政策主题 → 命中板块名列表。从 gov.cn 政策要点人工维护，非实时抓取。
# 预填 2026-09 六大主线（先进制造/电子信息/智能家居消费/基础研究算力/人形机器人/智能驾驶）。
POLICY_THEMES: dict[str, list[str]] = {
    "先进制造": ["先进制造", "工业母机", "机器人", "高端装备"],
    "电子信息": ["电子", "半导体", "消费电子", "面板"],
    "智能家居消费": ["智能家居", "家用电器", "厨卫电器"],
    "基础研究(算力/AI)": ["CPO", "算力", "AI应用", "光模块"],
    "人形机器人": ["人形机器人", "减速器", "传感器"],
    "智能驾驶": ["智能驾驶", "汽车零部件", "车联网"],
}
# 把主题的板块名制成 flat 命中集，policy_hit 判"该板块名是否命中任一政策主题"
_HIT_BOARDS: set[str] = {b for ls in POLICY_THEMES.values() for b in ls}


def policy_hit(board: str) -> float:
    """命中静态政策主题 → 0.05 提前量加成；未命中/空 → 0.0。"""
    if not board:
        return 0.0
    return 0.05 if str(board).strip() in _HIT_BOARDS else 0.0


def rank_pct(series: "pd.Series") -> "pd.Series":
    """横截 rank-pct(0-1, 越大越好)：平均秩, 最小值→0.0, 最大值→1.0；n<=1 → 0.5。"""
    if series.empty:
        return series
    if len(series) <= 1:
        return pd.Series({k: 0.5 for k in series.index}, dtype=float)
    r = series.rank(method="average")
    n = len(series)
    return (r - 1) / (n - 1)


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def board_heat(fund_flow: list[dict], board_rows: list[dict]) -> dict[str, float]:
    """板块景气分位：0.6*资金净流入 rank-pct + 0.4*上涨宽度 rank-pct。

    breadth = up_count/(up_count+down_count)；缺该板 fund_flow/board_rows → 该板缺席。
    返回 board 名 → heat ∈ [0,1]（横截后）。"""
    inflow = {str(r.get("name")): _f(r.get("main_net_inflow")) for r in fund_flow}
    inflow = {k: v for k, v in inflow.items() if v is not None}
    brd = {}
    for r in board_rows:
        n = str(r.get("name"))
        up = _f(r.get("up_count")); dn = _f(r.get("down_count"))
        if up is None or dn is None or (up + dn) == 0:
            brd[n] = None
        else:
            brd[n] = up / (up + dn)
    # 只对同时有资金与宽度证据的板块打分（缺一不可，避免单边失真）
    boards = [n for n in brd if n in inflow and brd[n] is not None]
    if not boards:
        return {}
    s_in = pd.Series({n: inflow[n] for n in boards})
    s_w = pd.Series({n: brd[n] for n in boards})  # type: ignore[dict-item]
    pin = rank_pct(s_in)
    pwd = rank_pct(s_w)
    return {n: round(0.6 * float(pin[n]) + 0.4 * float(pwd[n]), 4) for n in boards}


def pick_board(code: str, member_map: dict[str, set[str]], heat: dict[str, float]) -> str | None:
    """code 命中板块（member_map 含之）中 heat 最高者；无命中 → None。"""
    best, best_h = None, None
    for board, codes in (member_map or {}).items():
        if code not in codes:
            continue
        h = heat.get(str(board))
        if h is None:
            continue
        if best_h is None or h > best_h:
            best, best_h = str(board), h
    return best


def attach_sector_heat(rows: list[dict], fund_flow: list[dict],
                       board_rows: list[dict], member_map: dict[str, set[str]]) -> None:
    """原地给每行加 sector_heat(所属最优板块景气)与 policy_hit(政策加成)。

    rows 需含 code；无板块/无景气 → sector_heat=None, policy_hit=0.0，诚实缺失。"""
    heat = board_heat(fund_flow, board_rows)
    for r in rows:
        board = pick_board(str(r.get("code") or ""), member_map, heat)
        r["sector_heat"] = heat.get(str(board)) if board else None
        r["policy_hit"] = policy_hit(str(board) if board else "")
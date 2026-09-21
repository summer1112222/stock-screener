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
# 2026-09-20 校准：板块名对齐 `industry_board` 真实行业板块名（THS 行业分类）。
#   原来混入了风格/概念名（"先进制造"/"智能驾驶"/"算力"/"CPO"/"人形机器人"/"减速器"/"传感器"等）——
#   这些在 industry_board 里不存在，个股→板块反查 `_board_members_batch("行业")` 用 TDX 行业板块文件
#   也查不到，导致 policy_hit 对这些个股永远 0。改用真实行业板块名，反查才能命中。
POLICY_THEMES: dict[str, list[str]] = {
    "先进制造": ["专用设备", "通用设备", "自动化设备", "机床工具", "工控设备", "工程机械", "机器人", "机械设备", "激光设备"],
    "电子信息": ["电子", "半导体", "半导体材料", "半导体设备", "消费电子", "消费电子零部件及组装", "面板", "分立器件", "被动元件", "印制电路板", "光学元件", "光学光电子", "集成电路制造", "集成电路封测", "数字芯片设计", "模拟芯片设计"],
    "智能家居消费": ["家用电器", "白色家电", "黑色家电", "厨卫电器", "厨房电器", "厨房小家电", "小家电", "清洁小家电", "家居用品", "定制家居", "卫浴电器"],
    "基础研究(算力/AI)": ["通信设备", "通信网络设备及器件", "通信线缆及配套", "通信终端及配件", "计算机", "计算机设备", "软件开发", "垂直应用软件", "IT服务"],
    "人形机器人": ["机器人", "电机", "自动化设备", "工控设备", "专用设备"],
    "智能驾驶": ["汽车零部件", "汽车电子电气系统", "汽车", "乘用车", "电动乘用车", "商用车", "车身附件及饰件"],
    # 2026-09-20 增补：从 gov.cn/新浪近期政策抓取——国家部署改造城市地下管网(基建) + 国常会老龄化医疗护理扩容(养老)
    # 板块名对齐 THS 行业分类真实行业板块名(反查命中；勿用"城市更新/养老"这类概念名——industry_board 无)
    "城市更新(地下管网)": ["水泥", "玻璃玻纤", "基础建设", "工程咨询服务", "装修建材",
                          "专业工程", "房屋建设", "钢铁", "管材"],
    "养老大健康": ["化学制药", "中药", "生物制品", "医疗服务", "医疗器械", "医疗研发外包",
                  "医药商业"],
}
# 把主题的板块名制成 flat 命中集，policy_hit 判"该板块名是否命中任一政策主题"
_HIT_BOARDS: set[str] = {b for ls in POLICY_THEMES.values() for b in ls}

# B2 宏观风格调制（2026-09-20）：按 style 动态放大/缩小板块的政策/风格加成。
# 进攻主线 = POLICY_THEMES 里的高贝塔成长主题（先进制造/电子信息/算力/机器人/智驾）；
# 防御板块 = 红利/低波/抗周期行业手动维护（不在政策主题里，policy 加成归零，仅风格加成生效）。
_OFFENSIVE_THEMES = ("先进制造", "电子信息", "基础研究(算力/AI)", "人形机器人", "智能驾驶")
_OFFENSIVE_BOARDS: set[str] = {b for t in _OFFENSIVE_THEMES for b in POLICY_THEMES.get(t, [])}
_DEFENSIVE_BOARDS: set[str] = {
    "银行", "证券", "保险", "多元金融", "公用事业", "电力", "燃气", "自来水",
    "食品饮料", "白酒", "啤酒", "煤炭", "高速公路", "航运港口", "物流", "公路铁路运输",
    "林业", "港口", "银行Ⅱ", "道路运输",
    # 2026-09-20 增补：基建与医药为典型防御红利方向——政策在 defense 态放大这些板块风格加成
    "水泥", "玻璃玻纤", "基础建设", "工程咨询服务", "装修建材", "房屋建设", "管材",
    "化学制药", "中药", "医疗器械", "医疗服务", "生物制品", "医药商业",
}
_STYLE_BASE = 0.05    # 政策命中基础加成（与旧 policy_hit 一致）
_STYLE_ADDON = 0.03   # 风格方向加成（进攻主线进攻态 / 防御板块防御态）
_STYLE_MULT = {"attack": 1.6, "neutral": 1.0, "defense": 0.6}


def board_beta(board: str) -> str:
    """板块风格方向：offensive(高贝塔成长) / defensive(防御红利) / neutral。
    防御优先判(防御板块可能也在 policy 但按防御处理)，再判进攻主线；都不中 → neutral。"""
    b = str(board or "").strip()
    if b in _DEFENSIVE_BOARDS:
        return "defensive"
    if b in _OFFENSIVE_BOARDS:
        return "offensive"
    return "neutral"


def policy_addon(board: str, style: str | None) -> tuple[float, float]:
    """按宏风格动态返回 (policy_hit加成, style_hit风格加成)。

    - policy_hit：命中政策主题的板块 0.05 × 风格调制(进攻主线 attack×1.6/defense×0.6/均衡×1.0，
      防御/中性板块恒×1.0)；未命中政策主题 → 0.0。
    - style_hit：风格方向加成 0.03——进攻主线在 attack、防御板块在 defense 态才生效，否则 0。
    style 缺省(None/未知) → 退化为静态政策加成(0.05/0.0)+0 风格加成，兼容旧行为。
    纯函数、不触网；只作排序上下文，不改共振签名。
    """
    b = str(board or "").strip()
    st = str(style or "").strip() if style else ""
    beta = board_beta(b)

    # 政策加成(仅政策主题板块)
    pol = round(_STYLE_BASE if b in _HIT_BOARDS else 0.0, 4)
    if pol and beta == "offensive":
        pol = round(pol * _STYLE_MULT.get(st, 1.0), 4)
    # 风格方向加成
    sh = 0.0
    if st == "attack" and beta == "offensive":
        sh = _STYLE_ADDON
    elif st == "defense" and beta == "defensive":
        sh = _STYLE_ADDON
    return round(pol, 4), round(sh, 4)


def unmatched_themes(available_boards) -> dict[str, list[str]]:
    """诊断：返回每个政策主题里配置了但 available_boards(数据源真实板块名)缺失的板块名。

    供数据就绪后核对——缺失名经个股→板块反查(`_board_members_batch("行业")` 走行业板块)
    命中不到，policy_hit 对其恒 0。喂全量 industry_board 板块名即可定位待补对齐项。
    纯函数、不触网。
    """
    avail = {str(b) for b in (available_boards or set())}
    return {t: [b for b in boards if b not in avail]
            for t, boards in POLICY_THEMES.items()}


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
                       board_rows: list[dict], member_map: dict[str, set[str]],
                       style: dict | None = None, as_of: str | None = None) -> None:
    """原地给每行加 sector_heat(所属最优板块景气)、policy_hit(政策加成)、
    style_hit(风格方向加成)与 policy_event(政策事件标注)。

    rows 需含 code；无板块/无景气 → sector_heat=None, policy_hit=0.0，诚实缺失。
    style(macro_style 返回 dict 含 style 键)给定且有效时 policy_hit/style_hit 按
    宏风格动态调制(见 policy_addon)；缺省 → policy_hit 回退静态 0.05、style_hit=0，
    兼容旧调用与测试。policy_event 复用同一最优板块，为命中政策事件列表
    (无→[]，只标注；style/as_of 透传 policy_events B3 催化权重)。

    若行结构尚无 style_hit 键则补 0.0(前端/上游可能未预期新键，保持原子)。"""
    from screener import policy_events as _pe
    heat = board_heat(fund_flow, board_rows)
    st = (style or {}).get("style") if style else None
    for r in rows:
        board = pick_board(str(r.get("code") or ""), member_map, heat)
        r["sector_heat"] = heat.get(str(board)) if board else None
        if st:
            pol, sh = policy_addon(str(board) if board else "", st)
        else:
            pol, sh = policy_hit(str(board) if board else ""), 0.0
        r["policy_hit"] = pol
        r["style_hit"] = sh
        r["policy_event"] = _pe.event_hit(str(board) if board else "",
                                          as_of=as_of, style=style)
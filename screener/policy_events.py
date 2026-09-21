# -*- coding: utf-8 -*-
"""事件型催化标注层：静态政策事件表 → 相关行业板块命中 → 标注。

把近期政策/监管事件人工录入 POLICY_EVENTS 表，按"事件 → 相关行业板块名"映射，
个股经所属板块反查命中事件时附 policy_event 标签（事件 id/日期/标题/备注）。

与 sector_heat.policy_hit 同范式：只作**排序上下文/标注**——不进排序键、
不改共振签名、不构成买卖信号。板块名对齐 industry_board 真实行业板块名
（与 sector_heat.POLICY_THEMES 校准同源，见 unmatched_themes），确保能经
个股→板块反查（nextday._board_members_batch("行业")）命中。

本模块不触网、纯函数；事件表人工维护，过期事件以 as_of 过滤。
"""
from __future__ import annotations
import math
from datetime import date as _date


# 静态政策事件表：id → {date/title/boards/note/end(可选,过期日)}。
# boards 板块名必须与 industry_board 真实行业板块名一致（否则反查不到个股）。
# 从 gov.cn/央行公告等人工维护，非实时抓取。过期事件可加 end 或直接移除。
POLICY_EVENTS: dict[str, dict] = {
    "property_credit_model_2026-08": {
        "date": "2026-08-28",
        "title": "房地产信贷新模式意见",
        "boards": ["房地产", "房地产开发", "住宅开发", "银行", "白色家电", "家用电器", "家居用品"],
        "note": "促进房地产平稳健康发展，地产链后周期(家电/家居)情绪受益",
    },
    "pbc_notice_22_2026-08": {
        "date": "2026-08-28",
        "title": "央行公告22号(流动性)",
        "boards": ["证券", "银行", "多元金融"],
        "note": "流动性相关安排，资本市场情绪面受益",
    },
    "consumption_promo_2026-09": {
        "date": "2026-09-01",
        "title": "促消费政策部署",
        "boards": ["家用电器", "白色家电", "汽车", "乘用车", "食品饮料", "白酒", "旅游及酒店"],
        "note": "扩大内需促消费，耐用消费品与出行链受益",
    },
    # 2026-09-20 增补：从 gov.cn/新浪近期政策抓取
    "urban_renewal_2026-09": {
        "date": "2026-09-10",
        "title": "城市地下管网更新改造部署",
        "boards": ["水泥", "玻璃玻纤", "基础建设", "工程咨询服务", "装修建材", "专业工程", "房屋建设", "钢铁", "管材"],
        "note": "国家部署建设改造城市地下管网，基建链上游建材/工程受益",
    },
    "aging_healthcare_2026-09": {
        "date": "2026-09-12",
        "title": "应对人口老龄化+医疗康复护理扩容",
        "boards": ["化学制药", "中药", "生物制品", "医疗服务", "医疗器械", "医疗研发外包", "医药商业"],
        "note": "国常会部署积极应对人口老龄化，医疗康复护理扩容利好医药链",
    },
    "smart_home_promo_2026-09": {
        "date": "2026-09-05",
        "title": "促进智能家居消费行动方案",
        "boards": ["家用电器", "白色家电", "黑色家电", "厨卫电器", "厨房小家电", "小家电", "清洁小家电", "家居用品", "定制家居", "卫浴电器"],
        "note": "商务部8部门智能家居行动方案，智能家居/家电板块催化",
    },
    "rv_consumption_2026-09": {
        "date": "2026-09-08",
        "title": "促进房车消费若干措施",
        "boards": ["汽车", "乘用车", "商用车", "汽车零部件", "汽车电子电气系统"],
        "note": "十部门房车消费措施打通堵点，汽车链催化",
    },
}


# B3 催化权重常量（2026-09-20）：宏观风格 × 事件时效 调制催化强度。
_EVENT_HALFLIFE = 7.0       # 时效半衰期(天)：事件 date 距今每过 ~7 天催化权重减半(指数衰减)
_STYLE_EVENT_MULT = 1.5     # 风格对口事件放大倍数(进攻主线 in attack / 防御板块 in defense)
_STYLE_EVENT_PENALTY = 0.8  # 风格不对口事件轻微压制


def _age_days(ev_date: str, as_of: str | None) -> int | None:
    """事件 date 距 as_of 的天数(≥0)；as_of/date 缺省或非法 → None。"""
    if not as_of or not ev_date:
        return None
    try:
        d = _date.fromisoformat(str(ev_date))
        a = _date.fromisoformat(str(as_of))
        return max(0, (a - d).days)
    except ValueError:
        return None


def event_weight(ev_date: str, boards: list[str] | None, as_of: str | None = None,
                 style: dict | None = None) -> float:
    """事件催化强度权重 ≥0：时效衰减 × 风格调制(放大可 >1)。

    - 时效：as_of 给定时按 date 距今指数衰减(半衰期 `_EVENT_HALFLIFE` 天，越新越强)；
      as_of 缺省 → 时效项恒 1.0(不衰减)。已过期事件由调用方过滤。
    - 风格：style 给定时复用 `sector_heat.board_beta` 判事件板块风格方向——
      attack 态放大 offensive 板块事件(×1.5)、defense 态放大 defensive 板块事件(×1.5)；
      事件所有板块都与当前风格不对口 → ×0.8 微压制；neutral/无 style/无板块 → 1.0。

    纯函数、不触网；**只作标注权重、不进排序键**（措辞"事件催化机械标注非买卖信号"）。
    仅裁剪下限到 0(放大分支允许 >1)，返回 round 4 位 float。
    """
    weight = 1.0
    if as_of:
        age = _age_days(ev_date, as_of)
        if age is not None:
            weight *= math.exp(-age / _EVENT_HALFLIFE)
    st = (style or {}).get("style") if style else None
    if st and st != "neutral" and boards:
        from screener import sector_heat as _sh  # lazy import 避循环(sector_heat 引 policy_events)
        fit = 1.0
        for b in boards:
            beta = _sh.board_beta(str(b))
            if (st == "attack" and beta == "offensive") or (st == "defense" and beta == "defensive"):
                fit = max(fit, _STYLE_EVENT_MULT)
        if fit == 1.0:
            fit = _STYLE_EVENT_PENALTY  # 事件板块与当前风格不对口 → 微压制
        weight *= fit
    return round(max(0.0, weight), 4)


def event_hit(board: str, as_of: str | None = None, style: dict | None = None) -> list[dict]:
    """返回该板块命中的政策事件列表([{id,date,title,note,weight,age_days},...])；未命中/空 → []。

    as_of(YYYY-MM-DD) 给定时仅返回"未过期"事件：date<=as_of<=end(或无 end)。
    style(macro_style 返回 dict)给定时每事件附 B3 催化权重 `weight`(风格×时效调制，
    见 event_weight)，只作标注不进排序键。age_days 为时效天数(无 as_of → None)。
    """
    if not board:
        return []
    board = str(board).strip()
    out = []
    for eid, ev in POLICY_EVENTS.items():
        if board not in ev.get("boards", []):
            continue
        if as_of is not None:
            if ev.get("date", "") > as_of:
                continue
            end = ev.get("end")
            if end and as_of > end:
                continue
        out.append({"id": eid, "date": ev.get("date"), "title": ev.get("title"),
                    "note": ev.get("note"),
                    "weight": event_weight(ev.get("date", ""), ev.get("boards", []),
                                           as_of=as_of, style=style),
                    "age_days": _age_days(ev.get("date", ""), as_of)})
    return out


def attach_policy_events(rows: list[dict], member_map: dict[str, set[str]],
                         as_of: str | None = None, style: dict | None = None) -> None:
    """原地给每行附 policy_event：该 code 所属所有板块命中事件聚合去重；无命中 → []。

    member_map: {board: set[code]}（个股→板块反查结果）。只标注不进排序。
    as_of/style 透传 event_hit(B3 催化权重)。
    """
    for r in rows:
        code = str(r.get("code") or "")
        evs: list[dict] = []
        seen: set[str] = set()
        for board, codes in (member_map or {}).items():
            if code not in codes:
                continue
            for e in event_hit(str(board), as_of=as_of, style=style):
                if e["id"] not in seen:
                    seen.add(e["id"])
                    evs.append(e)
        r["policy_event"] = evs

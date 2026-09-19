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
}


def event_hit(board: str, as_of: str | None = None) -> list[dict]:
    """返回该板块命中的政策事件列表([{id,date,title,note},...])；未命中/空 → []。

    as_of(YYYY-MM-DD) 给定时仅返回"未过期"事件：date<=as_of<=end(或无 end)。
    只作标注，不进排序键。
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
                    "note": ev.get("note")})
    return out


def attach_policy_events(rows: list[dict], member_map: dict[str, set[str]],
                         as_of: str | None = None) -> None:
    """原地给每行附 policy_event：该 code 所属所有板块命中事件聚合去重；无命中 → []。

    member_map: {board: set[code]}（个股→板块反查结果）。只标注不进排序。
    """
    for r in rows:
        code = str(r.get("code") or "")
        evs: list[dict] = []
        seen: set[str] = set()
        for board, codes in (member_map or {}).items():
            if code not in codes:
                continue
            for e in event_hit(str(board), as_of=as_of):
                if e["id"] not in seen:
                    seen.add(e["id"])
                    evs.append(e)
        r["policy_event"] = evs

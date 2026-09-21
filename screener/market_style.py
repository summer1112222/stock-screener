# -*- coding: utf-8 -*-
"""宏观风格标注层：静态宏观基调 → 排序上下文标注。

把近期宏观经济数据/政策基调人工录入 MACRO_BASELINE，输出 style_context 供
nextday/quality/smart_money 清单作**排序上下文标注**——只标注、不进排序键、
不改共振签名、不构成买卖信号、不承诺收益。

数据来自每次人工核对官方披露（统计局/央行/发改委，"用上搜索 skill"），非实时
抓取、不触网。MACRO_BASELINE 的 support_boards/weak_boards 板块名对齐
industry_board 真实行业板块名（与 sector_heat.POLICY_THEMES 校准同源），确保
能经个股→板块反查（nextday._board_members_batch("行业")）命中。

本模块纯函数、不新增表。措辞"宏观风格机械标注非择时信号"。
"""
from __future__ import annotations


# 静态宏观基调：人工维护近期经济数据+政策要点。改时同步更新 as_of。
# 板块名必须与 industry_board 真实行业板块名一致（否则反查不到个股，勿用风格/概念名）。
# 依据(2026-09-20 官方源)：8月工业增加值+5.2%(强)、社零1-8月+1.1%(弱)、CPI+0.8%(温和)、
#   PPI+3.8%(回升)；PMI 49.8%(回升仍收缩)；政策扩内需+房地产信贷新模式+十五五重大工程+AI 行动计划。
MACRO_BASELINE: dict = {
    "as_of": "2026-09-20",
    "tilt": "industry>consumer",
    "note": "8月工业增加值+5.2%/社零+1.1%/PPI+3.8%回升;PMI49.8%回升仍收缩;政策扩内需+房地产信贷新模式+十五五重大工程+AI主线",
    # 政策受益行业板块(生产/投资/AI/地产链后周期)
    "support_boards": ["专用设备", "通用设备", "工程机械", "通信设备", "计算机", "半导体", "家用电器", "汽车零部件"],
    # 消费弱相关行业板块(仅弱标注,促消费或修复故不硬剔)
    "weak_boards": ["白酒", "食品饮料", "旅游及酒店", "零售"],
}


def style_context() -> dict:
    """返回宏观风格标注 dict(投影拷贝,改返回不污染 MACRO_BASELINE)。

    输出键：tilt/macro_note/support_boards/weak_boards/as_of/maturity。
    maturity="static" 标注来源为静态人工基线(非实时)。只作排序上下文标注。
    """
    bl = MACRO_BASELINE
    return {
        "tilt": bl.get("tilt"),
        "macro_note": bl.get("note"),
        "support_boards": list(bl.get("support_boards") or []),
        "weak_boards": list(bl.get("weak_boards") or []),
        "as_of": bl.get("as_of"),
        "maturity": "static",
    }
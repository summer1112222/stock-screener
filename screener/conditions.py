# -*- coding: utf-8 -*-
"""筛选条件定义：字段目录 + 条件原语。

合规：条件只对公开数据做阈值/排名过滤，不输出买卖信号、不评级、不喊买卖点。
条件结构: {"field": str, "op": str, "value": any}
  op ∈ gt | gte | lt | lte | between | topn
  - between: value = [lo, hi] (闭区间)
  - topn:    value = N, 取该字段前 N 名 (最后应用，在其它条件过滤之后)
"""
from __future__ import annotations

# 板块可筛选字段 (industry_board/concept_board + 合并资金流后)
BOARD_FIELDS_CAT = [
    {"key": "change_pct", "label": "涨跌幅(%)", "ops": ["gt", "gte", "lt", "lte", "eq", "ne", "between"]},
    {"key": "main_net_inflow", "label": "主力净流入(元)", "ops": ["gt", "lt", "eq", "ne", "between", "topn"]},
    {"key": "super_large_net", "label": "超大单净流入(元)", "ops": ["gt", "lt", "between"]},
    {"key": "large_net", "label": "大单净流入(元)", "ops": ["gt", "lt", "between"]},
    {"key": "turnover_rate", "label": "换手率(%)", "ops": ["gt", "lt", "between"]},
    {"key": "turnover_amount", "label": "成交额(元)", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "total_market_cap", "label": "总市值(元)", "ops": ["gt", "lt", "between"]},
    {"key": "up_count", "label": "上涨家数", "ops": ["gt", "lt", "between"]},
    {"key": "down_count", "label": "下跌家数", "ops": ["gt", "lt", "between"]},
    {"key": "leading_stock_change", "label": "领涨股涨跌幅(%)", "ops": ["gt", "lt", "between"]},
    {"key": "constituent_count", "label": "成分股数量", "ops": ["gt", "lt", "between", "topn"]},
]

# ETF 可筛选字段
ETF_FIELDS_CAT = [
    {"key": "change_pct", "label": "涨跌幅(%)", "ops": ["gt", "gte", "lt", "lte", "eq", "ne", "between"]},
    {"key": "turnover_amount", "label": "成交额(元)", "ops": ["gt", "lt", "eq", "ne", "between", "topn"]},
    {"key": "turnover_rate", "label": "换手率(%)", "ops": ["gt", "lt", "between"]},
    {"key": "latest_price", "label": "最新价(元)", "ops": ["gt", "lt", "between"]},
    # 派生因子(纯筛选维度，不打分不荐股)：由 engine 在查询时实时计算
    {"key": "activity", "label": "活跃度(换手×|涨跌|)", "ops": ["gt", "lt", "between", "topn"], "derived": True},
    {"key": "momentum", "label": "动量(涨跌×换手)", "ops": ["gt", "lt", "between", "topn"], "derived": True},
]

# 个股可筛选字段(取自 STOCK_SPOT_FIELDS)
STOCK_FIELDS_CAT = [
    {"key": "change_pct", "label": "涨跌幅(%)", "ops": ["gt", "gte", "lt", "lte", "eq", "ne", "between"]},
    {"key": "turnover_amount", "label": "成交额(元)", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "turnover_rate", "label": "换手率(%)", "ops": ["gt", "lt", "between"]},
    {"key": "total_market_cap", "label": "总市值(元)", "ops": ["gt", "lt", "between"]},
    {"key": "circulating_market_cap", "label": "流通市值(元)", "ops": ["gt", "lt", "between"]},
    {"key": "pe", "label": "市盈率", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "pb", "label": "市净率", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "amplitude", "label": "振幅(%)", "ops": ["gt", "lt", "between"]},
    {"key": "volume_ratio", "label": "量比", "ops": ["gt", "lt", "between", "topn"]},
    {"key": "latest_price", "label": "最新价(元)", "ops": ["gt", "lt", "between"]},
]

OPS = {
    "gt": "大于",
    "gte": "大于等于",
    "lt": "小于",
    "lte": "小于等于",
    "eq": "等于",
    "ne": "不等于",
    "between": "区间",
    "topn": "前N名",
    "topn_asc": "末N名",
}

VALID_OPS = set(OPS.keys())

# -*- coding: utf-8 -*-
"""数据模型：规范字段 + AKShare 字段别名映射 + SQLite schema 定义。

设计要点：
- AKShare 各接口返回中文列名，且版本间字段可能微调。本模块用 FIELD_ALIASES 把
  可能的列名归一到 CANONICAL_FIELDS 里的规范键，采集层据此做字段容错
  (缺列跳过而非崩)。
- 所有 schema 都是合成/聚合公开行情数据，非敏感生产数据。
"""
from __future__ import annotations

# ------------------------------------------------------------------
# 规范字段键 (内部统一用英文小写键)
# ------------------------------------------------------------------
BOARD_FIELDS = {
    "name", "code", "change_pct", "total_market_cap",
    "turnover_rate", "turnover_amount", "leading_stock",
    "up_count", "down_count", "leading_stock_change",
    "constituent_count", "event",
}
FUND_FLOW_FIELDS = {
    "name", "indicator", "sector_type",
    "main_net_inflow", "super_large_net", "large_net",
    "medium_net", "small_net",
}
ETF_FIELDS = {
    "code", "name", "latest_price", "change_pct",
    "turnover_amount", "turnover_rate",
}

# 个股 spot 规范字段(全市场快照，供"可交易"筛选)
STOCK_SPOT_FIELDS = {
    "code", "name", "latest_price", "change_pct", "turnover_amount",
    "turnover_rate", "total_market_cap", "circulating_market_cap",
    "pe", "pb", "amplitude", "volume_ratio",
}

# ------------------------------------------------------------------
# AKShare 列名 → 规范键 的别名映射 (按类别)
# 命中即可，多写几个可能的同义列名提高容错
# ------------------------------------------------------------------
BOARD_ALIASES = {
    "板块名称": "name", "概念名称": "name", "name": "name",
    "板块代码": "code", "code": "code",
    "板块涨幅": "change_pct", "涨跌幅": "change_pct", "change_pct": "change_pct",
    "总市值": "total_market_cap", "total_market_cap": "total_market_cap",
    "换手率": "turnover_rate", "turnover_rate": "turnover_rate",
    "总成交额": "turnover_amount", "成交额": "turnover_amount", "turnover_amount": "turnover_amount",
    "领涨股票": "leading_stock", "领涨股": "leading_stock", "龙头股": "leading_stock",
    "leading_stock": "leading_stock",
    "上涨家数": "up_count", "up_count": "up_count",
    "下跌家数": "down_count", "down_count": "down_count",
    "领涨股-涨跌幅": "leading_stock_change", "领涨股票-涨跌幅": "leading_stock_change",
    "leading_stock_change": "leading_stock_change",
    "成分股数量": "constituent_count", "constituent_count": "constituent_count",
    "驱动事件": "event", "event": "event",
}

FUND_FLOW_ALIASES = {
    "板块名称": "name", "名称": "name", "name": "name",
    "主力净流入额": "main_net_inflow", "主力净流入": "main_net_inflow",
    "main_net_inflow": "main_net_inflow",
    "主力净流入占比": "main_net_inflow_pct",
    "超大单净流入额": "super_large_net", "超大单净流入": "super_large_net",
    "大单净流入额": "large_net", "大单净流入": "large_net",
    "中单净流入额": "medium_net", "中单净流入": "medium_net",
    "小单净流入额": "small_net", "小单净流入": "small_net",
    "领涨股票": "leading_stock",
}

ETF_ALIASES = {
    "代码": "code", "基金代码": "code", "code": "code",
    "名称": "name", "基金简称": "name", "name": "name",
    "最新价": "latest_price", "最新价/收盘价": "latest_price",
    "latest_price": "latest_price",
    "涨跌幅": "change_pct", "change_pct": "change_pct",
    "成交额": "turnover_amount", "turnover_amount": "turnover_amount",
    "换手率": "turnover_rate", "turnover_rate": "turnover_rate",
}

# 个股 spot 别名(东财 stock_zh_a_spot_em 列名)
STOCK_SPOT_ALIASES = {
    "代码": "code", "code": "code",
    "名称": "name", "name": "name",
    "最新价": "latest_price", "latest_price": "latest_price",
    "涨跌幅": "change_pct", "change_pct": "change_pct",
    "成交额": "turnover_amount", "turnover_amount": "turnover_amount",
    "换手率": "turnover_rate", "换手": "turnover_rate", "turnover_rate": "turnover_rate",
    "总市值": "total_market_cap", "total_market_cap": "total_market_cap",
    "流通市值": "circulating_market_cap", "circulating_market_cap": "circulating_market_cap",
    "市盈率-动态": "pe", "市盈率": "pe", "pe": "pe",
    "市净率": "pb", "pb": "pb",
    "振幅": "amplitude", "amplitude": "amplitude",
    "量比": "volume_ratio", "volume_ratio": "volume_ratio",
}

# ST 全名单规范字段
ST_LIST_FIELDS = {
    "code", "name", "st_type", "latest_price", "change_pct",
}

# 研报评级规范字段(不含 id,靠 UNIQUE 去 REPLACE)
RESEARCH_REPORT_FIELDS = {
    "code", "name", "rating", "title", "org",
    "analyst", "pub_date", "target_price", "ts",
}

# 完整财报+千股千评缓存字段(多 source,7 天 TTL)
FUNDAMENTALS_CACHE_FIELDS = {"code", "source", "payload_json", "ts"}

# ST 全名单 AKShare 列名别名
ST_LIST_ALIASES = {
    "代码": "code", "code": "code",
    "名称": "name", "name": "name",
    "涨跌幅": "change_pct", "change_pct": "change_pct",
    "最新价": "latest_price", "latest_price": "latest_price",
}

# 研报评级 AKShare 列名别名
RESEARCH_REPORT_ALIASES = {
    "代码": "code", "股票代码": "code", "code": "code",
    "名称": "name", "股票简称": "name", "name": "name",
    "评级": "rating", "投资评级": "rating", "rating": "rating",
    "研报标题": "title", "标题": "title", "title": "title",
    "机构": "org", "研究机构": "org", "org": "org",
    "研究员": "analyst", "分析师": "analyst", "analyst": "analyst",
    "日期": "pub_date", "研报日期": "pub_date", "pub_date": "pub_date",
    "目标价": "target_price", "目标价（元）": "target_price",
}

# ------------------------------------------------------------------
# SQLite schema
# ------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS industry_board (
    name TEXT PRIMARY KEY,
    code TEXT,
    change_pct REAL,
    total_market_cap REAL,
    turnover_rate REAL,
    turnover_amount REAL,
    leading_stock TEXT,
    up_count INTEGER,
    down_count INTEGER,
    leading_stock_change REAL,
    constituent_count INTEGER,
    event TEXT
);

CREATE TABLE IF NOT EXISTS concept_board (
    name TEXT PRIMARY KEY,
    code TEXT,
    change_pct REAL,
    total_market_cap REAL,
    turnover_rate REAL,
    turnover_amount REAL,
    leading_stock TEXT,
    up_count INTEGER,
    down_count INTEGER,
    leading_stock_change REAL,
    constituent_count INTEGER,
    event TEXT
);

CREATE TABLE IF NOT EXISTS sector_fund_flow (
    sector_type TEXT,      -- 行业 / 概念
    indicator TEXT,        -- 今日 / 5日 / 10日 / 20日
    name TEXT,
    main_net_inflow REAL,
    super_large_net REAL,
    large_net REAL,
    medium_net REAL,
    small_net REAL,
    PRIMARY KEY (sector_type, indicator, name)
);

CREATE TABLE IF NOT EXISTS etf_spot (
    code TEXT PRIMARY KEY,
    name TEXT,
    latest_price REAL,
    change_pct REAL,
    turnover_amount REAL,
    turnover_rate REAL
);

CREATE TABLE IF NOT EXISTS stock_spot (
    code TEXT PRIMARY KEY,
    name TEXT,
    latest_price REAL,
    change_pct REAL,
    turnover_amount REAL,
    turnover_rate REAL,
    total_market_cap REAL,
    circulating_market_cap REAL,
    pe REAL,
    pb REAL,
    amplitude REAL,
    volume_ratio REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT,
    buy_date TEXT,
    buy_price REAL,
    shares REAL,
    note TEXT,
    ts TEXT
);

-- 历史日线(前复权 qfq)，供回测/因子评价
CREATE TABLE IF NOT EXISTS etf_daily (
    code TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS stock_daily (
    symbol TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS board_daily (
    name TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    PRIMARY KEY (name, date)
);

CREATE TABLE IF NOT EXISTS smart_money_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    market TEXT NOT NULL,
    channel TEXT NOT NULL,
    actor TEXT,
    action TEXT,
    amount REAL,
    rank INTEGER,
    as_of TEXT,
    raw TEXT,
    ts TEXT,
    UNIQUE(date, code, channel, actor, action)
);
CREATE INDEX IF NOT EXISTS idx_sm_date  ON smart_money_action(date);
CREATE INDEX IF NOT EXISTS idx_sm_code  ON smart_money_action(code);
CREATE INDEX IF NOT EXISTS idx_sm_actor ON smart_money_action(actor);

-- buffett 财务摘要缓存(按 code 单行存整张最新摘要 JSON，7 天 TTL)
CREATE TABLE IF NOT EXISTS financial_abstract_cache (
    code TEXT PRIMARY KEY,
    payload_json TEXT,
    ts TEXT
);

-- ST 全名单快照(同 spot 模式,code 主键覆盖)
CREATE TABLE IF NOT EXISTS st_list (
    code TEXT PRIMARY KEY,
    name TEXT,
    st_type TEXT,
    latest_price REAL,
    change_pct REAL
);

-- 研报评级(列表型,多机构同日;靠 UNIQUE 去 REPLACE)
CREATE TABLE IF NOT EXISTS research_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT, name TEXT,
    rating TEXT,
    title TEXT,
    org TEXT,
    analyst TEXT,
    pub_date TEXT,
    target_price REAL,
    ts TEXT,
    UNIQUE(code, pub_date, org, title)
);

-- 完整三大财报+千股千评按需缓存(多 source,7 天 TTL)
CREATE TABLE IF NOT EXISTS fundamentals_cache (
    code TEXT, source TEXT,
    payload_json TEXT,
    ts TEXT,
    PRIMARY KEY (code, source)
);
"""

# 历史日线规范字段集(按表区分主键列名)
ETF_DAILY_FIELDS = {
    "code", "date", "open", "high", "low", "close", "volume", "amount",
}
STOCK_DAILY_FIELDS = {
    "symbol", "date", "open", "high", "low", "close", "volume", "amount",
}
BOARD_DAILY_FIELDS = {
    "name", "date", "open", "high", "low", "close", "volume", "amount",
}

# 主力动向记录规范字段集(游资/国家队/外资/资金流四通道统一入表)
SMART_MONEY_FIELDS = {
    "date", "code", "name", "market", "channel", "actor",
    "action", "amount", "rank", "as_of", "raw", "ts",
}

# buffett 财务摘要缓存字段集(payload_json 存整张摘要 JSON，7 天 TTL)
FINANCIAL_CACHE_FIELDS = {"code", "payload_json", "ts"}

# 表名 ↔ 规范字段集
TABLE_FIELDS = {
    "industry_board": BOARD_FIELDS,
    "concept_board": BOARD_FIELDS,
    "sector_fund_flow": FUND_FLOW_FIELDS,
    "etf_spot": ETF_FIELDS,
    "stock_spot": STOCK_SPOT_FIELDS,
    "etf_daily": ETF_DAILY_FIELDS,
    "stock_daily": STOCK_DAILY_FIELDS,
    "board_daily": BOARD_DAILY_FIELDS,
    "smart_money_action": SMART_MONEY_FIELDS,
    "financial_abstract_cache": FINANCIAL_CACHE_FIELDS,
    "st_list": ST_LIST_FIELDS,
    "research_report": RESEARCH_REPORT_FIELDS,
    "fundamentals_cache": FUNDAMENTALS_CACHE_FIELDS,
}

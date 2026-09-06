# -*- coding: utf-8 -*-
"""nextday 5 步流程单测(step1-5 硬剔除+step4 软打分混合)。
mock db.query_rows / _ma_arrange_batch / _board_members_batch，不触网。"""
import pytest
import screener.nextday as nd

# ------------------------------------------------------------------
# 合成数据
# ------------------------------------------------------------------

# 7 只测试股 + 2 只涨停板成员
_SPOT = [
    {"code": "600001", "name": "A股(全通)", "change_pct": 6.5, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 20.0,
     "volume_ratio": 3.0, "st_type": None},
    {"code": "600002", "name": "B股(低涨幅)", "change_pct": 2.0, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 20.0,
     "volume_ratio": 3.0, "st_type": None},
    {"code": "600003", "name": "C股(高PE)", "change_pct": 6.5, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 200.0,
     "volume_ratio": 3.0, "st_type": None},
    {"code": "600004", "name": "D股(空头)", "change_pct": 6.5, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 20.0,
     "volume_ratio": 3.0, "st_type": None},
    {"code": "600005", "name": "E股(低量比)", "change_pct": 6.5, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 20.0,
     "volume_ratio": 1.2, "st_type": None},
    {"code": "600006", "name": "F股(非前5)", "change_pct": 6.5, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 20.0,
     "volume_ratio": 3.0, "st_type": None},
    {"code": "600007", "name": "G股(ST)", "change_pct": 6.5, "turnover_rate": 5.0,
     "latest_price": 30.0, "circulating_market_cap": 50.0, "pe": 20.0,
     "volume_ratio": 3.0, "st_type": "ST"},
    # 涨停板成员(用于板块涨停计数)
    {"code": "600010", "name": "Z股(涨停)", "change_pct": 10.0, "turnover_rate": 8.0,
     "latest_price": 35.0, "circulating_market_cap": 30.0, "pe": 15.0,
     "volume_ratio": 2.0, "st_type": None},
    {"code": "600011", "name": "Y股(涨停)", "change_pct": 9.9, "turnover_rate": 7.0,
     "latest_price": 28.0, "circulating_market_cap": 40.0, "pe": 18.0,
     "volume_ratio": 2.5, "st_type": None},
]

# 行业资金流(按主净流入降序)
_SFF = [
    {"name": "电池", "main_net_inflow": 100.0},
    {"name": "新能源", "main_net_inflow": 80.0},
    {"name": "军工", "main_net_inflow": 60.0},
    {"name": "半导体", "main_net_inflow": 40.0},
    {"name": "医药", "main_net_inflow": 20.0},
    {"name": "消费", "main_net_inflow": 10.0},
]

# 板块成分股映射(前5热板块成员)
_BOARD_MEMBERS = {
    "电池": ["600001", "600010", "600011"],      # rank1, 2 ZT
    "新能源": ["600004", "600010"],               # rank2, 1 ZT
    "军工": ["600005", "600011"],                  # rank3, 1 ZT
    "半导体": ["600006"],                          # rank4, 0 ZT
    "医药": ["600007"],                            # rank5, 0 ZT
    "消费": ["000000"],                            # rank6, 不在前5
}


def _mock_qr(table, where=None, params=None, limit=0, **kw):
    if table == "stock_spot":
        return _SPOT
    if table == "st_list":
        return [{"code": "600007", "st_type": "ST"}]
    if table == "sector_fund_flow":
        return _SFF
    return []


def _mock_ma(universe, codes, days=60):
    """600004 空头排列，其余多头排列；带完整 close/amount 序列供新因子。"""
    out = {}
    for c in codes:
        if c == "600004":
            closes = [30.0 - i * 0.1 for i in range(30)]      # 空头下跌
            amounts = [100.0] * 30
            out[c] = {"ma5": 29.0, "ma10": 30.0, "ma20": 31.0, "ma60": 32.0,
                       "bullish_align": False, "volume_breakout": False,
                       "bearish": True, "converged": False, "need_history": False,
                       "last_vol": 100.0, "vol_avg20": 100.0,
                       "close_series": closes, "amount_series": amounts}
        else:
            closes = [10.0 + i * 0.05 for i in range(30)]     # 温和上升
            amounts = [100.0 + i for i in range(30)]          # 量同步递增
            out[c] = {"ma5": 31.0, "ma10": 30.5, "ma20": 30.0, "ma60": 29.0,
                       "bullish_align": True, "volume_breakout": False,
                       "bearish": False, "converged": False, "need_history": False,
                       "last_vol": 100.0, "vol_avg20": 80.0,
                       "close_series": closes, "amount_series": amounts}
    return out


def _mock_board_members(board_names):
    return {name: _BOARD_MEMBERS.get(name, []) for name in board_names}


def _setup(monkeypatch):
    monkeypatch.setattr(nd.db, "query_rows", _mock_qr)
    monkeypatch.setattr(nd, "_ma_arrange_batch", _mock_ma)
    monkeypatch.setattr(nd, "_board_members_batch", _mock_board_members)
    # 次日模块测试不触网：TDX 补全路径另行用 mock 覆盖。
    monkeypatch.setattr(nd.pytdx_client, "get_quote", lambda codes: [])
    monkeypatch.setattr(nd.pytdx_client, "get_finance_info", lambda code: {})
    nd._CACHE.clear()


# ==================================================================
# 基本排序
# ==================================================================

def test_basic_5factor(monkeypatch):
    """全通过股(600001)排第一，score 是连续因子合成分。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 3
    top = r["items"][0]
    assert top["code"] in {"600001", "600010", "600011"}
    assert top["hard_pass"] == 4
    assert top["score"] > 0
    assert top["score_coverage"] > 0.5
    assert "factor_scores" in top
    assert set(nd._FACTOR_WEIGHTS).issubset(top["factor_scores"])


def test_sort_order(monkeypatch):
    """诊断清单优先展示通过步骤更多的股票，再按综合分排序。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    for i in range(len(r["all_items"]) - 1):
        a, b = r["all_items"][i], r["all_items"][i + 1]
        assert (a["hard_pass"], a["score"], a["score_coverage"]) >= (
            b["hard_pass"], b["score"], b["score_coverage"]
        )


# ==================================================================
# 各步独立测试
# ==================================================================

def test_step1_fail_low_change(monkeypatch):
    """涨幅<5% → step1 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["600001", "600002"], limit=10)
    b = [i for i in r["all_items"] if i["code"] == "600002"][0]
    assert b["step1_pass"] is False
    assert b["hard_pass"] <= 3  # step1 不通过


def test_step2_fail_high_pe(monkeypatch):
    """PE>150 → step2 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    c = [i for i in r["all_items"] if i["code"] == "600003"][0]
    assert c["step2_pass"] is False


def test_step2_fail_st(monkeypatch):
    """ST 股票 → step2 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    g = [i for i in r["all_items"] if i["code"] == "600007"][0]
    assert g["step2_pass"] is False


def test_step3_fail_bearish(monkeypatch):
    """空头排列 → step3 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    d = [i for i in r["all_items"] if i["code"] == "600004"][0]
    assert d["step3_pass"] is False


def test_step4_score_range(monkeypatch):
    """step4_score 在 0-100 之间。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    for it in r["all_items"]:
        assert 0 <= it["step4_score"] <= 100


def test_step4_score_low_volume_ratio(monkeypatch):
    """低量比 → step4_score 较低。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    e = [i for i in r["all_items"] if i["code"] == "600005"][0]
    # 600005: vol_ratio=1.2, change_pct=6.5
    # a=clip((1.2-1.0)/1.5)=0.133, b=1.0 -> (0.5*0.133+0.5*1)*100=56.67
    assert e["step4_score"] < 80  # 低量比导致低分


def test_step5_pass_board_top5_with_zt(monkeypatch):
    """板块热度前5且至少2只涨停 → step5 通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    top = [i for i in r["all_items"] if i["code"] == "600001"][0]
    assert top["step5_pass"] is True
    assert top["board"] == "电池"
    assert top["board_rank"] == 1
    assert top["board_zt_count"] >= 2


def test_step5_fail_not_top5(monkeypatch):
    """板块不在前5热度 → step5 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    # 600006 在半导体(rank4, top5 但 0 ZT)
    f = [i for i in r["all_items"] if i["code"] == "600006"][0]
    # 半导体 rank 4 top5, 但 0 ZT → step5 不通过
    assert f["step5_pass"] is False


# ==================================================================
# 边界情况
# ==================================================================

def test_empty_spot(monkeypatch):
    """stock_spot 为空 → 返回空结果带 note。"""
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    nd._CACHE.clear()
    r = nd.nextday_strong_rank()
    assert r["count"] == 0
    assert "先" in r.get("note", "")


def test_codes_filter(monkeypatch):
    """codes 限定 → 只返回指定股票。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["600001", "600005"], limit=10)
    assert r["count"] == 1
    assert {i["code"] for i in r["items"]} == {"600001"}


def test_codes_filter_accepts_exchange_prefix(monkeypatch):
    """codes 带 sh/sz 前缀时仍能匹配 stock_spot 纯代码。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["sh600001"], limit=10)
    assert r["count"] == 1
    assert r["all_items"][0]["code"] == "600001"


def test_tdx_quote_request_is_capped(monkeypatch):
    """TDX 行情请求只覆盖粗筛小名单，避免全市场逐只重试。"""
    _setup(monkeypatch)
    seen = []

    def _quote(codes):
        seen.append(list(codes))
        return []

    monkeypatch.setattr(nd.pytdx_client, "get_quote", _quote)
    nd.nextday_strong_rank(limit=10)
    assert sum(len(batch) for batch in seen) <= nd._TDX_ENRICH_K


def test_cache_hit(monkeypatch):
    """缓存命中 → 返回相同数据。"""
    _setup(monkeypatch)
    nd.nextday_strong_rank(limit=10)
    # 第二次调用如命中缓存，即使 mock 返回空也能拿到原结果
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 3


def test_limit_works(monkeypatch):
    """limit 限制返回数量。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=3)
    assert r["count"] == 3
    assert len(r["all_items"]) == 3


def test_only_five_step_passes_are_returned(monkeypatch):
    """最终结果只保留五步全部通过的股票。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 3
    assert {item["code"] for item in r["items"]} == {"600001", "600010", "600011"}
    for item in r["items"]:
        assert item["step1_pass"] is True
        assert item["step2_pass"] is True
        assert item["step3_pass"] is True
        assert item["step4_pass"] is True
        assert item["step5_pass"] is True


def test_no_five_step_pass_returns_fallback_observation_list(monkeypatch):
    """严格清单为空时返回按综合分排序的降级观察清单。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["600002"], limit=10)
    assert r["count"] == 1
    assert r["selection_mode"] == "fallback"
    assert r["items"]
    item = r["items"][0]
    assert item["code"] == "600002"
    assert item["failed_steps"]
    assert "五步" in r.get("note", "")


def test_market_median_chg(monkeypatch):
    """market_median_chg 为全市场涨幅中位数。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    # 全市场: 2.0, 6.5, 6.5, 6.5, 6.5, 6.5, 6.5, 9.9, 10.0 → 中位数=6.5
    assert r["market_median_chg"] == 6.5


# ==================================================================
# 纯函数测试
# ==================================================================

def test_step1_pass_logic():
    """_step1_pass: 涨幅>5%+换手>3%+价<50。"""
    p = {"min_change_pct": 5.0, "min_turnover": 3.0, "max_price": 50.0}
    assert nd._step1_pass({"change_pct": 6.0, "turnover_rate": 4.0, "latest_price": 30.0}, p) is True
    assert nd._step1_pass({"change_pct": 4.0, "turnover_rate": 4.0, "latest_price": 30.0}, p) is False
    assert nd._step1_pass({"change_pct": 6.0, "turnover_rate": 2.0, "latest_price": 30.0}, p) is False
    assert nd._step1_pass({"change_pct": 6.0, "turnover_rate": 4.0, "latest_price": 60.0}, p) is False


def test_step2_pass_logic():
    """_step2_pass: 市值[10,200]+PE(0,150]+非ST。"""
    p = {"min_mv": 10.0, "max_mv": 200.0, "max_pe": 150.0}
    st = {"600001"}
    assert nd._step2_pass({"code": "600001", "circulating_market_cap": 50.0, "pe": 20.0}, p, st) is False  # ST
    assert nd._step2_pass({"code": "600002", "circulating_market_cap": 50.0, "pe": 20.0}, p, st) is True
    assert nd._step2_pass({"code": "600003", "circulating_market_cap": 5.0, "pe": 20.0}, p, st) is False  # 市值太小
    assert nd._step2_pass({"code": "600004", "circulating_market_cap": 50.0, "pe": -1.0}, p, st) is False  # 亏损
    assert nd._step2_pass({"code": "600005", "circulating_market_cap": 50.0, "pe": 200.0}, p, st) is False  # PE太高


def test_step3_pass_logic():
    """_step3_pass: 多头排列或放量突破通过，空头排列不通过。"""
    # 多头排列 → 通过
    assert nd._step3_pass({"bullish_align": True, "volume_breakout": False, "bearish": False, "need_history": False}) is True
    # 放量突破 → 通过
    assert nd._step3_pass({"bullish_align": False, "volume_breakout": True, "bearish": False, "need_history": False}) is True
    # 空头排列 → 不通过
    assert nd._step3_pass({"bullish_align": False, "volume_breakout": False, "bearish": True, "need_history": False}) is False
    # 无历史 → 不通过
    assert nd._step3_pass({"bullish_align": False, "volume_breakout": False, "bearish": False, "need_history": True}) is False


def test_step4_score_formula():
    """_step4_score: 量比>2.5 强度(0.5)+涨幅<7% 避追高(0.5)→0-100。"""
    # 理想: 量比3.0+涨幅6.5%
    s = nd._step4_score({"volume_ratio": 3.0, "change_pct": 6.5})
    assert s == 100.0
    # 低量比
    s = nd._step4_score({"volume_ratio": 1.0, "change_pct": 6.5})
    assert s < 60
    # 追高: 涨幅8.5%
    s = nd._step4_score({"volume_ratio": 3.0, "change_pct": 8.5})
    b = (9.8 - 8.5) / 2.8  # 0.464
    expected = round((0.5 * 1.0 + 0.5 * b) * 100, 2)
    assert s == expected
    # 涨停: 涨幅>9.8% → b=0
    s = nd._step4_score({"volume_ratio": 3.0, "change_pct": 10.0})
    assert s == 50.0


def test_code_key():
    """_code_key 归一化带前缀/纯代码。"""
    assert nd._code_key("sh600519") == "600519"
    assert nd._code_key("600519") == "600519"
    assert nd._code_key("sz000001") == "000001"
    assert nd._code_key("000001") == "000001"
    assert nd._code_key("bj430001") == "430001"
    assert nd._code_key(None) == ""


def test_rank_sector_flow():
    """_rank_sector_flow 按主净流入降序排名。"""
    ranked = nd._rank_sector_flow(_SFF)
    assert ranked[0]["name"] == "电池"
    assert ranked[0]["rank"] == 1
    assert ranked[-1]["name"] == "消费"
    assert ranked[-1]["rank"] == 6


def test_rank_tdx_blocks_uses_quote_when_spot_change_missing():
    """spot 缺涨幅时，TDX quote 的 price/last_close 应补足板块热度。"""
    blocks = {"新能源": ["600001", "600010"]}
    spot = {"600001": {"change_pct": None},
            "600010": {"change_pct": 10.0}}
    quotes = {"600001": {"price": 11.0, "last_close": 10.0}}
    ranked = nd._rank_tdx_blocks(blocks, spot, quotes)
    assert ranked[0]["name"] == "新能源"
    assert ranked[0]["board_zt_count"] == 2
    assert ranked[0]["member_coverage"] == 2


def test_structured_step_explanation_distinguishes_fail_and_missing():
    """解释字段区分规则未通过与数据缺失。"""
    p = {"min_change_pct": 5.0, "min_turnover": 3.0, "max_price": 50.0,
         "min_mv": 10.0, "max_mv": 200.0, "max_pe": 150.0}
    statuses, reasons = nd._explain_step_status(
        {"change_pct": 2.0, "turnover_rate": 5.0, "latest_price": 30.0,
         "circulating_market_cap": 50.0, "pe": 20.0, "volume_ratio": 3.0},
        {"need_history": True}, {}, p, 50.0, False)
    assert statuses["step1"] == "fail"
    assert statuses["step2"] == "pass"
    assert statuses["step3"] == "missing"
    assert statuses["step5"] == "missing"
    assert "涨幅" in reasons["step1"]
    assert "历史" in reasons["step3"]


def test_data_quality_reports_source_and_missing_fields():
    quality = nd._data_quality(
        {"change_pct": 6.0, "turnover_rate": None, "pe": 20.0},
        {"need_history": True}, True, 0.5)
    assert quality["source"] == "tdx"
    assert quality["quote_available"] is True
    assert quality["history_available"] is False
    assert quality["score_coverage"] == 0.5
    assert "换手率" in quality["missing"]
    assert "历史日线" in quality["missing"]


def test_nan_guard():
    """_nan 过滤 NaN/Inf/None。"""
    import math
    assert nd._nan(None) is None
    assert nd._nan(float("nan")) is None
    assert nd._nan(float("inf")) is None
    assert nd._nan(3.14) == 3.14
    assert nd._nan("abc") == "abc"


def test_rank_pct_ties_and_missing():
    """横截面分位并列相同，缺失不伪造为最低分。"""
    r = nd._rank_pct({"a": 1.0, "b": 1.0, "c": 3.0, "d": None})
    assert r["a"] == r["b"] == 0.25
    assert r["c"] == 1.0
    assert r["d"] is None


def test_weighted_score_renormalizes_missing():
    factors = {
        "mom_5_1": {"a": 1.0, "b": 0.0},
        "rel_strength": {"a": 1.0, "b": 0.0},
        "sr_10": {"a": None, "b": None},
        "close_vol_corr": {"a": None, "b": None},
        "liq_turnover": {"a": None, "b": None},
    }
    scores, details, coverage = nd._weighted_score(factors, ["a", "b"])
    assert scores["a"] == 100.0 and scores["b"] == 0.0
    # 覆盖率 = 可用权重/全部权重和 = (mom 0.30 + rel 0.20)/0.90，实现 round 到 4 位
    assert coverage["a"] == coverage["b"] == pytest.approx(0.50 / 0.90, rel=1e-3)
    assert details["a"]["sr_10"] is None


def test_open_source_factor_names_and_weights():
    """五因子采用常见开源量价范式因子，而非盘口/板块方向因子。"""
    assert set(nd._FACTOR_WEIGHTS) == {
        "mom_5_1", "rel_strength", "sr_10",
        "close_vol_corr", "liq_turnover",
    }
    # IC 校准后权重不对和为 1：close_vol_corr(逆IC)/sr_10(弱IC) 主动降权，
    # _weighted_score 已按可用因子 denom 归一，无需强制补平。
    assert sum(nd._FACTOR_WEIGHTS.values()) == pytest.approx(0.90)


def test_mom_5_1_prefers_uptrend():
    """短中期动量: 温和上升 > 下跌趋势。"""
    up = nd._factor_mom_5_1({}, {"close_series": [10 + i * 0.2 for i in range(30)]})
    down = nd._factor_mom_5_1({}, {"close_series": [30 - i * 0.2 for i in range(30)]})
    assert up > down


def test_mom_5_1_missing_series_fallback_to_chg():
    """缺历史序列时退化为当日涨幅归一。"""
    v = nd._factor_mom_5_1({"change_pct": 6.0}, {})
    assert v is not None and 0.0 <= v <= 1.0


def test_sr_10_rewards_high_ratio():
    """波动率归一收益: 稳定上涨 > 剧烈震荡(同均值下)。"""
    stable = nd._factor_sr_10({}, {"close_series": [10 + i * 0.1 for i in range(30)]})
    noisy = nd._factor_sr_10({}, {"close_series": [10 + (i % 2) for i in range(30)]})
    assert stable > noisy


def test_sr_10_missing_series_returns_none():
    assert nd._factor_sr_10({}, {}) is None


def test_close_vol_corr_prefers_volume_confirmation():
    """量价共振: 价升量增相关为正 > 价升量缩相关为负。"""
    aligned = nd._factor_close_vol_corr(
        {}, {"close_series": [10 + i * 0.1 for i in range(30)],
             "amount_series": [100 + i * 5 for i in range(30)]})
    diverged = nd._factor_close_vol_corr(
        {}, {"close_series": [10 + i * 0.1 for i in range(30)],
             "amount_series": [100 - i * 5 for i in range(30)]})
    assert aligned > diverged


def test_close_vol_corr_missing_returns_none():
    assert nd._factor_close_vol_corr({}, {"close_series": None, "amount_series": None}) is None


def test_liq_turnover_rewards_size_and_moderate_turnover():
    """流动性质量: 市值充足 + 换手适中 得分高。"""
    good = nd._factor_liq_turnover({"circulating_market_cap": 100.0, "turnover_rate": 6.0})
    bad = nd._factor_liq_turnover({"circulating_market_cap": 1.0, "turnover_rate": 60.0})
    assert good > bad


def test_exclude_st_false(monkeypatch):
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["600007"], exclude_st=False)
    assert r["all_items"][0]["step2_pass"] is True


def test_finance_derived_fields():
    """TDX 财务概要+行情可补流通市值、总市值、换手率；亏损股 PE 为空。"""
    r = nd._finance_derived(
        {"liutongguben": 1_000_000_000, "zongguben": 1_200_000_000,
         "jinglirun": -100_000_000},
        {"price": 10.0, "vol": 500_000},
    )
    assert r["circulating_market_cap"] == 100.0
    assert r["total_market_cap"] == 120.0
    assert r["turnover_rate"] == 5.0
    assert r["pe"] is None


def test_rich_enrich_uses_spot_amount_fallback():
    """TDX quote 缺 vol/amount 时用新浪成交额推导换手率。"""
    r = nd._tdx_rich_enrich(
        {"code": "600001", "latest_price": 10.0, "turnover_amount": 50_000_000},
        {},
        {"600001": {"liutongguben": 1_000_000_000}},
        {},
    )
    assert r["turnover_rate"] == 0.5


def test_ma_arrange_matches_prefixed_history(monkeypatch):
    """spot 纯代码也应能取到 stock_daily 的 sh/sz 前缀历史列。"""
    import pandas as pd
    from backtest import signals

    captured = {}

    def _uni_panels(universe, codes):
        captured["codes"] = codes
        dates = pd.date_range("2026-01-01", periods=60)
        close = pd.DataFrame(
            {"sh600001": [10 + i * 0.01 for i in range(60)]},
            index=dates,
        )
        amount = pd.DataFrame({"sh600001": [100.0] * 60}, index=dates)
        return close, amount

    monkeypatch.setattr(signals, "_uni_panels", _uni_panels)
    info, hist = nd._ma_arrange_batch("stock", ["600001"], 60)
    assert captured["codes"] == ["sh600001"]
    assert info["600001"]["need_history"] is False
    assert hist["600001"] == [100.0] * 60


def test_ma_arrange_auto_fetch_when_missing(monkeypatch):
    """无历史 → 触发 from-TDX 补拉入库后重算，步3与量比均可算出。"""
    import pandas as pd
    from backtest import signals

    panel_calls = []

    def _uni_panels(universe, codes):
        panel_calls.append(1)
        if len(panel_calls) == 1:
            return pd.DataFrame(), pd.DataFrame()
        dates = pd.date_range("2026-01-01", periods=60)
        close = pd.DataFrame(
            {"sh600001": [10 + i * 0.02 for i in range(60)]},
            index=dates,
        )
        amount = pd.DataFrame({"sh600001": [100.0] * 60}, index=dates)
        return close, amount

    calls = []

    def _fill(uni, codes):
        calls.extend(codes)
        # mock 补库成功；第二次 _uni_panels 返回补齐后的面板。
        return 1

    monkeypatch.setattr(signals, "_uni_panels", _uni_panels)
    monkeypatch.setattr(nd, "_fill_missing_history", _fill)
    info, hist = nd._ma_arrange_batch("stock", ["600001"], 60)
    assert calls == ["600001"]
    assert info["600001"]["need_history"] is False


def test_fill_missing_history_uses_tdx_and_isolates_failures(monkeypatch):
    """按需补历史走 history.fetch_stock_hist；单股失败不影响其它股票。"""
    import pandas as pd
    from data import history

    dates = pd.date_range("2026-01-01", periods=2)
    calls = []

    def _fetch(symbol, start, end):
        calls.append(symbol)
        if symbol == "sz000002":
            return pd.DataFrame(), False, "tdx unavailable"
        return pd.DataFrame({
            "symbol": [symbol, symbol],
            "date": dates.strftime("%Y-%m-%d"),
            "open": [10.0, 10.1], "high": [10.2, 10.3],
            "low": [9.8, 9.9], "close": [10.1, 10.2],
            "volume": [100.0, 110.0], "amount": [1000.0, 1100.0],
        }), True, "tdx主源,本地qfq"

    written = []
    monkeypatch.setattr(history, "fetch_stock_hist", _fetch)
    monkeypatch.setattr(nd.db, "upsert_rows",
                        lambda table, rows: written.append((table, list(rows))) or len(written[-1][1]))
    n = nd._fill_missing_history("stock", ["000001", "000002"])
    assert n == 1
    assert sorted(calls) == ["sz000001", "sz000002"]
    assert len(written) == 1
    assert written[0][0] == "stock_daily"


def test_step5_uses_tdx_block_heat_when_flow_is_missing(monkeypatch):
    """行业资金流金额全空时，step5 应改用 TDX 成员+行情计算板块热度。"""
    _setup(monkeypatch)

    def _query(table, where=None, params=None, limit=0, **kw):
        if table == "sector_fund_flow":
            return [{"name": "电池", "main_net_inflow": None},
                    {"name": "新能源", "main_net_inflow": None}]
        return _mock_qr(table, where=where, params=params, limit=limit, **kw)

    tdx_blocks = {"TDX新能源": ["600001", "600010", "600011"],
                  "TDX弱板块": ["600002", "600003"]}
    monkeypatch.setattr(nd.db, "query_rows", _query)
    monkeypatch.setattr(nd.pytdx_client, "get_block_members",
                        lambda category="all": tdx_blocks)
    monkeypatch.setattr(nd, "_board_members_batch",
                        lambda names: {name: tdx_blocks.get(name, []) for name in names})
    nd._CACHE.clear()

    r = nd.nextday_strong_rank(codes=["600001", "600010", "600011"], limit=10)
    top = next(item for item in r["all_items"] if item["code"] == "600001")
    assert top["board"] == "TDX新能源"
    assert top["board_rank"] == 1
    assert top["board_zt_count"] == 2
    assert top["step5_pass"] is True
    # 板块助攻保留为步骤诊断，但不再属于新的五因子评分。
    assert "board_assist" not in nd._FACTOR_WEIGHTS
    assert "mom_5_1" in top["factor_scores"]


def test_tdx_quote_availability_handles_prefixed_spot_code(monkeypatch):
    """spot 使用 sh/sz 前缀时，TDX quote 仍标记为可用。"""
    _setup(monkeypatch)
    monkeypatch.setattr(nd.pytdx_client, "get_quote", lambda codes: [
        {"code": "600001", "price": 31.0, "last_close": 30.0,
         "vol": 100000, "amount": 120000000},
    ])
    nd._CACHE.clear()
    original = list(_SPOT)
    try:
        _SPOT[0]["code"] = "sh600001"
        r = nd.nextday_strong_rank(codes=["sh600001"], limit=10)
    finally:
        _SPOT[0]["code"] = original[0]["code"]
    item = r["items"][0]
    assert item["quote_available"] is True
    assert item["data_source"] == "tdx"


def test_tdx_quote_is_primary_candidate_snapshot(monkeypatch):
    """TDX 行情成功时，次日筛选优先用 TDX 价格与涨跌幅。"""
    _setup(monkeypatch)
    monkeypatch.setattr(nd.pytdx_client, "get_quote", lambda codes: [
        {"code": "600001", "price": 12.0, "last_close": 10.0,
         "vol": 100000, "amount": 120000000},
    ])
    nd._CACHE.clear()
    r = nd.nextday_strong_rank(codes=["600001"], limit=10)
    item = r["all_items"][0]
    assert item["latest_price"] == 12.0
    assert item["change_pct"] == pytest.approx(20.0)
    assert item["data_source"] == "tdx"


def test_step5_tdx_failure_falls_back_to_board_source(monkeypatch):
    """TDX 板块文件失败时，仍保留既有 board_stocks 降级链路。"""
    _setup(monkeypatch)
    monkeypatch.setattr(nd.pytdx_client, "get_block_members", lambda category="all": {})
    calls = []

    def _fallback(names, spot_by_code=None):
        calls.append(list(names))
        return {name: _BOARD_MEMBERS.get(name, []) for name in names}

    monkeypatch.setattr(nd, "_board_members_batch", _fallback)
    nd._CACHE.clear()
    r = nd.nextday_strong_rank(limit=10)
    top = next(item for item in r["all_items"] if item["code"] == "600001")
    assert calls and calls[0][0] == "电池"
    assert top["board"] == "电池"
    assert top["board_zt_count"] == 2


def test_ma_arrange_skip_fetch_when_has_history(monkeypatch):
    """已有历史 → 不触发 TDX 补拉。"""
    import pandas as pd
    from backtest import signals

    def _uni_panels(universe, codes):
        dates = pd.date_range("2026-01-01", periods=60)
        close = pd.DataFrame(
            {"sh600001": [10 + i * 0.01 for i in range(60)]},
            index=dates,
        )
        amount = pd.DataFrame({"sh600001": [100.0] * 60}, index=dates)
        return close, amount

    monkeypatch.setattr(signals, "_uni_panels", _uni_panels)
    monkeypatch.setattr(nd, "_fill_missing_history",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触网")))
    info, hist = nd._ma_arrange_batch("stock", ["600001"], 60)
    assert info["600001"]["need_history"] is False
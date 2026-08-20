# -*- coding: utf-8 -*-
"""nextday 5 步流程单测(step1-5 硬剔除+step4 软打分混合)。
mock db.query_rows / _ma_arrange_batch / _board_members_batch，不触网。"""
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
    """600004 空头排列，其余多头排列。"""
    out = {}
    for c in codes:
        if c == "600004":
            out[c] = {"ma5": 29.0, "ma10": 30.0, "ma20": 31.0, "ma60": 32.0,
                       "bullish_align": False, "volume_breakout": False,
                       "bearish": True, "converged": False, "need_history": False,
                       "last_vol": 100.0, "vol_avg20": 100.0}
        else:
            out[c] = {"ma5": 31.0, "ma10": 30.5, "ma20": 30.0, "ma60": 29.0,
                       "bullish_align": True, "volume_breakout": False,
                       "bearish": False, "converged": False, "need_history": False,
                       "last_vol": 100.0, "vol_avg20": 80.0}
    return out


def _mock_board_members(board_names):
    return {name: _BOARD_MEMBERS.get(name, []) for name in board_names}


def _setup(monkeypatch):
    monkeypatch.setattr(nd.db, "query_rows", _mock_qr)
    monkeypatch.setattr(nd, "_ma_arrange_batch", _mock_ma)
    monkeypatch.setattr(nd, "_board_members_batch", _mock_board_members)
    nd._CACHE.clear()


# ==================================================================
# 基本排序
# ==================================================================

def test_basic_5factor(monkeypatch):
    """全通过股(600001)排第一，score 是连续因子合成分。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 8
    top = r["items"][0]
    assert top["code"] == "600001"
    assert top["hard_pass"] == 4
    assert top["score"] > 0
    assert top["score_coverage"] > 0.5
    assert "factor_scores" in top
    # 五因子分都在 0-100 且保留四位或两位
    for val in top["factor_scores"].values():
        if val is not None:
            assert 0 <= val <= 100


def test_sort_order(monkeypatch):
    """排序按因子分降序，再按 hard_pass 降序。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    for i in range(len(r["items"]) - 1):
        a, b = r["items"][i], r["items"][i + 1]
        if a["score"] == b["score"]:
            assert a["hard_pass"] >= b["hard_pass"]
        else:
            assert a["score"] >= b["score"]


# ==================================================================
# 各步独立测试
# ==================================================================

def test_step1_fail_low_change(monkeypatch):
    """涨幅<5% → step1 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["600001", "600002"], limit=10)
    b = [i for i in r["items"] if i["code"] == "600002"][0]
    assert b["step1_pass"] is False
    assert b["hard_pass"] <= 3  # step1 不通过


def test_step2_fail_high_pe(monkeypatch):
    """PE>150 → step2 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    c = [i for i in r["items"] if i["code"] == "600003"][0]
    assert c["step2_pass"] is False


def test_step2_fail_st(monkeypatch):
    """ST 股票 → step2 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    g = [i for i in r["items"] if i["code"] == "600007"][0]
    assert g["step2_pass"] is False


def test_step3_fail_bearish(monkeypatch):
    """空头排列 → step3 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    d = [i for i in r["items"] if i["code"] == "600004"][0]
    assert d["step3_pass"] is False


def test_step4_score_range(monkeypatch):
    """step4_score 在 0-100 之间。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    for it in r["items"]:
        assert 0 <= it["step4_score"] <= 100


def test_step4_score_low_volume_ratio(monkeypatch):
    """低量比 → step4_score 较低。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    e = [i for i in r["items"] if i["code"] == "600005"][0]
    # 600005: vol_ratio=1.2, change_pct=6.5
    # a=clip((1.2-1.0)/1.5)=0.133, b=1.0 -> (0.5*0.133+0.5*1)*100=56.67
    assert e["step4_score"] < 80  # 低量比导致低分


def test_step5_pass_board_top5_with_zt(monkeypatch):
    """板块热度前5且至少2只涨停 → step5 通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    top = [i for i in r["items"] if i["code"] == "600001"][0]
    assert top["step5_pass"] is True
    assert top["board"] == "电池"
    assert top["board_rank"] == 1
    assert top["board_zt_count"] >= 2


def test_step5_fail_not_top5(monkeypatch):
    """板块不在前5热度 → step5 不通过。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=10)
    # 600006 在半导体(rank4, top5 但 0 ZT)
    f = [i for i in r["items"] if i["code"] == "600006"][0]
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
    assert r["count"] == 2
    assert {i["code"] for i in r["items"]} == {"600001", "600005"}


def test_cache_hit(monkeypatch):
    """缓存命中 → 返回相同数据。"""
    _setup(monkeypatch)
    nd.nextday_strong_rank(limit=10)
    # 第二次调用如命中缓存，即使 mock 返回空也能拿到原结果
    monkeypatch.setattr(nd.db, "query_rows",
                        lambda table, **k: [] if table == "stock_spot" else [])
    r = nd.nextday_strong_rank(limit=10)
    assert r["count"] == 8


def test_limit_works(monkeypatch):
    """limit 限制返回数量。"""
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(limit=3)
    assert r["count"] == 3
    assert len(r["items"]) == 3


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
        "price_volume": {"a": 1.0, "b": 0.0},
        "liquidity_scale": {"a": None, "b": None},
        "trend": {"a": 1.0, "b": 0.0},
        "quote": {"a": None, "b": None},
        "board_assist": {"a": None, "b": None},
    }
    scores, details, coverage = nd._weighted_score(factors, ["a", "b"])
    assert scores["a"] == 100.0 and scores["b"] == 0.0
    assert coverage["a"] == coverage["b"] == 0.5
    assert details["a"]["quote"] is None


def test_exclude_st_false(monkeypatch):
    _setup(monkeypatch)
    r = nd.nextday_strong_rank(codes=["600007"], exclude_st=False)
    assert r["items"][0]["step2_pass"] is True
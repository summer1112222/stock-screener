# tests/test_buffett_value.py
# -*- coding: utf-8 -*-
"""巴菲特框架增强测试：所有者收益/杜邦/ROIC/CAGR/轻量DCF内在价值。
mock 三大表 + 摘要，不触网。运行: pytest tests/test_buffett_value.py
合规：只验机械计算口径，不涉买卖点。
"""
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest import buffett
from data import db


# ---------- 纯函数 ----------
def test_cagr_basic():
    # _cagr 假设输入为新→旧降序（_annual 输出）：[14,13,12,11,10] 新→旧
    # first=vals[-1]=10(旧), last=vals[0]=14(新)，n=4 → (14/10)^(1/4)-1 ≈ 8.77%
    g = buffett._cagr([14, 13, 12, 11, 10])
    assert g is not None
    assert abs(g - ((14 / 10) ** 0.25 - 1)) < 1e-9
    assert abs(g - 0.0877) < 0.001


def test_cagr_too_few_returns_none():
    assert buffett._cagr([10, 11], min_n=3) is None
    assert buffett._cagr([], min_n=3) is None


def test_cagr_nonpositive_endpoint_none():
    # 端点≤0 无意义
    assert buffett._cagr([0, 5, 10], min_n=3) is None
    assert buffett._cagr([-5, 1, 2], min_n=3) is None


def test_consistent_years_counts_from_oldest():
    # _annual 输出新→旧；连续达标从旧端数
    # roe_ann = [18, 16, 14, 12, 10]（新→旧），reversed 旧→新 [10,12,14,16,18]
    # 从旧端连续>15：10(否)→0
    assert buffett._consistent_years([18, 16, 14, 12, 10], 15.0) == 0
    # [16, 18, 14, 12, 10] 旧→新 [10,12,14,18,16]：10否→0
    assert buffett._consistent_years([16, 18, 14, 12, 10], 15.0) == 0
    # [20, 18, 16, 14, 12] 旧→新 [12,14,16,18,20]：12否→0
    assert buffett._consistent_years([20, 18, 16, 14, 12], 15.0) == 0
    # [18, 20, 22, 16, 14] 旧→新 [14,16,22,20,18]：14否→0
    # 换一个连续的：新→旧 [22,20,18,16] 旧→新 [16,18,20,22] 全≥15 →4
    assert buffett._consistent_years([22, 20, 18, 16], 15.0) == 4


# ---------- _pick_row_fields ----------
def test_pick_row_fields_same_annual_row():
    df = pd.DataFrame([{
        "报告期": "2024-12-31",
        "经营活动产生的现金流量净额": 1000.0,
        "购建固定资产、无形资产及其他长期资产支付的现金": 300.0,
        "固定资产折旧": 150.0,
        "营运资金的增加": 50.0,
    }])
    out = buffett._pick_row_fields(df, {
        "ocf": ["经营活动产生的现金流量净额"],
        "capex": ["购建固定资产"],
        "depreciation": ["固定资产折旧", "折旧"],
        "wc_increase": ["营运资金的增加"],
    })
    assert out["ocf"] == 1000.0
    assert out["capex"] == 300.0
    assert out["depreciation"] == 150.0
    assert out["wc_increase"] == 50.0


def test_pick_row_fields_picks_latest_annual_not_quarterly():
    df = pd.DataFrame([
        {"报告期": "2024-09-30", "营业收入": 100.0},
        {"报告期": "2024-12-31", "营业收入": 150.0},
    ])
    out = buffett._pick_row_fields(df, {"revenue": ["营业收入"]})
    assert out["revenue"] == 150.0  # 取年报行而非首行季报


def test_pick_row_fields_empty_df():
    out = buffett._pick_row_fields(pd.DataFrame(), {"x": ["y"]})
    assert out == {"x": None}


# ---------- analyze 端到端（mock 三大表 + 摘要）----------
def _cf_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "经营活动产生的现金流量净额": 1000.0,
        "购建固定资产、无形资产及其他长期资产支付的现金": 300.0,
        "固定资产折旧": 150.0,
        "营运资金的增加": 50.0,
    }])


def _profit_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "营业收入": 5000.0,
        "利息费用": 60.0,
        "所得税费用": 75.0,
    }])


def _balance_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "资产总计": 6000.0,
        "股东权益合计": 3000.0,
        "应付账款": 200.0,
        "应付票据": 100.0,
        "其他应付款": 50.0,
    }])


def _setup_analyze(monkeypatch, tmp_path, abstract_df=None,
                   row_pairs_map=None, spot_price=100.0):
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()
    monkeypatch.setattr(buffett, "fetch_abstract",
                        lambda c: (abstract_df or pd.DataFrame(), False))
    monkeypatch.setattr(buffett, "_spot",
                        lambda c: {"code": c, "name": "X", "latest_price": spot_price})
    monkeypatch.setattr(buffett, "fundamentals",
                        type("M", (), {"fetch": staticmethod(
                            lambda code, src: {
                                "cashflow": (_cf_df(), False),
                                "profit": (_profit_df(), False),
                                "balance": (_balance_df(), False),
                            }.get(src, (None, False)))}))
    if row_pairs_map is not None:
        monkeypatch.setattr(buffett, "_row_pairs",
                            lambda df, kw: row_pairs_map.get(kw, []))


def test_owner_earnings_formula(monkeypatch, tmp_path):
    """所有者收益 = 净利润 + 折旧 - 资本开支 - 营运资本增加。
    ni=800, dep=150, capex=300, wc=50 → 800+150-300-50=600"""
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={"净利润": [("2024-12-31", 800.0)]},
                   spot_price=50.0)
    res = buffett.analyze("600519")
    assert res["ratios"]["owner_earnings"] == 600.0
    assert res["ratios"]["owner_earnings_to_ni"] == round(600.0 / 800.0, 2)


def test_dupont_decomposition(monkeypatch, tmp_path):
    """杜邦：净利率=ni/rev, 周转率=rev/ta, 权益乘数=ta/equity, 乘积≈ROE。
    ni=800,rev=5000,ta=6000,eq=3000 → 0.16×0.833×2.0=0.2667=26.67%"""
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={"净利润": [("2024-12-31", 800.0)]},
                   spot_price=50.0)
    res = buffett.analyze("600519")
    dp = res["ratios"]["dupont"]
    assert dp["net_margin"] == 16.0
    assert abs(dp["asset_turn"] - 0.833) < 0.01
    assert abs(dp["equity_mult"] - 2.0) < 0.01
    assert abs(dp["roe_dupont"] - 26.67) < 0.1


def test_roic_formula(monkeypatch, tmp_path):
    """ROIC = NOPAT/投入资本。
    ni=800, interest=60, tax=75, pretax=875, tax_rate=75/875≈0.0857
    nopat=800+60×(1-0.0857)=800+54.86=854.86
    non_int_liab=200+100+50=350, invested_capital=6000-350=5650
    roic=854.86/5650=15.13%"""
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={"净利润": [("2024-12-31", 800.0)]},
                   spot_price=50.0)
    res = buffett.analyze("600519")
    roic = res["ratios"]["roic"]
    assert abs(roic - 15.13) < 0.2
    assert res["ratios"]["roic_tag"] == "高资本回报"


def test_dcf_intrinsic_value_and_mos(monkeypatch, tmp_path):
    """轻量DCF：bps CAGR 8%、ROE 15%、r 9%
    justified_pb=(0.15-0.08)/(0.09-0.08)=7.0；IV=7×bps；MoS=(IV-price)/IV。
    bps 序列 5 年（新→旧降序，同 _row_pairs 真实输出）[13.6,12.6,11.66,10.8,10]
    → first=10(旧) last=13.6(新) CAGR≈8%；bps_latest=_latest=13.6
    IV=7×13.6=95.2；price=50 → MoS=(95.2-50)/95.2≈0.475"""
    bps_series = [("2024-12-31", 13.6), ("2023-12-31", 12.6),
                  ("2022-12-31", 11.66), ("2021-12-31", 10.8),
                  ("2020-12-31", 10.0)]
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={
                       "净利润": [("2024-12-31", 800.0)],
                       "净资产收益率": [("2024-12-31", 15.0)],
                       "每股净资产": bps_series,
                   }, spot_price=50.0)
    res = buffett.analyze("600519")
    assert res["intrinsic_value"] is not None
    # IV = 7.0 × 13.6 = 95.2
    assert abs(res["intrinsic_value"] - 95.2) < 1.0
    assert res["margin_of_safety"] is not None
    assert abs(res["margin_of_safety"] - 0.475) < 0.02
    assert "便宜" in res["valuation_tag"]


def test_dcf_overvalued_negative_mos(monkeypatch, tmp_path):
    """price > IV → MoS<0，标"贵"。bps_latest=13.6 IV=95.2，price=150 → MoS<0"""
    bps_series = [("2024-12-31", 13.6), ("2023-12-31", 12.6),
                  ("2022-12-31", 11.66), ("2021-12-31", 10.8),
                  ("2020-12-31", 10.0)]
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={
                       "净利润": [("2024-12-31", 800.0)],
                       "净资产收益率": [("2024-12-31", 15.0)],
                       "每股净资产": bps_series,
                   }, spot_price=150.0)
    res = buffett.analyze("600519")
    assert res["margin_of_safety"] < 0
    assert "贵" in res["valuation_tag"]


def test_dcf_model_breaks_when_g_over_r(monkeypatch, tmp_path):
    """g≥r 模型失效：ROE 30%、bps CAGR≈10% → g 截断 min(0.10,0.30)=0.10 ≥ r=0.09 → 失效。
    bps 5 年（新→旧）[14.6,13.3,12.1,11.0,10.0] CAGR=(14.6/10)^0.25-1≈9.95%"""
    bps_series = [("2024-12-31", 14.6), ("2023-12-31", 13.3),
                  ("2022-12-31", 12.1), ("2021-12-31", 11.0),
                  ("2020-12-31", 10.0)]
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={
                       "净利润": [("2024-12-31", 800.0)],
                       "净资产收益率": [("2024-12-31", 30.0)],
                       "每股净资产": bps_series,
                   }, spot_price=50.0)
    res = buffett.analyze("600519")
    assert res["intrinsic_value"] is None  # 模型失效不返回 IV
    assert res["dcf_assumptions"]["note"].find("失效") >= 0 or \
           res["dcf_assumptions"]["note"].find("人工") >= 0


def test_rank_top_prefilters_low_owner_earnings(monkeypatch, tmp_path):
    """owner_earnings_to_ni<0.3 的标的被 rank_top 负面预筛排除。"""
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={"净利润": [("2024-12-31", 800.0)]},
                   spot_price=50.0)
    good = buffett.analyze("600519")   # owner_earnings_to_ni=0.75，通过
    bad_r = dict(good)
    bad_r["ratios"] = dict(good["ratios"])
    bad_r["ratios"]["owner_earnings_to_ni"] = 0.1   # 被排除
    out = buffett.rank_top([good, bad_r], order="priority", n=5)
    codes = [r["code"] for r in out]
    assert "600519" in codes
    assert len(out) == 1   # bad 被排除


def test_reasons_include_iv_and_mos(monkeypatch, tmp_path):
    """_build_reasons 输出含 IV/安全边际叙事行。"""
    bps_series = [("2024-12-31", 13.6), ("2023-12-31", 12.6),
                  ("2022-12-31", 11.66), ("2021-12-31", 10.8),
                  ("2020-12-31", 10.0)]
    _setup_analyze(monkeypatch, tmp_path,
                   row_pairs_map={
                       "净利润": [("2024-12-31", 800.0)],
                       "净资产收益率": [("2024-12-31", 15.0)],
                       "每股净资产": bps_series,
                   }, spot_price=50.0)
    res = buffett.analyze("600519")
    reasons = buffett._build_reasons(res, "priority", 1, 5e8, 80)
    joined = " ".join(reasons)
    assert "IV" in joined
    assert "安全边际" in joined


def test_analyze_many_deadline_returns_partial(monkeypatch):
    """deadline_s 到点返已完成部分、放弃慢项，不等全部(防 akshare 被封时 200s 挂起)。"""
    import time
    buffett = sys.modules.get("backtest.buffett") or __import__("backtest.buffett", fromlist=["x"])

    def _fake_analyze(c):
        if c.startswith("slow"):
            time.sleep(1.5)  # 模拟 akshare 被封耗满超时的慢调用
            return {"code": c, "pe": 10.0}
        return {"code": c, "pe": 10.0}  # 快速成功

    monkeypatch.setattr(buffett, "analyze", _fake_analyze)
    # prefetch_financial 走真实 pytdx(假码慢连)→mock 为 no-op(本测试只测 deadline 机制)
    monkeypatch.setattr(buffett, "prefetch_financial", lambda codes: None)
    codes = ["fast1", "fast2", "fast3", "fast4", "slow1", "slow2", "slow3", "slow4", "slow5"]
    t0 = time.time()
    out = buffett.analyze_many(codes, deadline_s=0.4)
    dt = time.time() - t0
    assert dt < 1.2, f"deadline 未生效，耗时 {dt:.2f}s"
    got = {r["code"] for r in out}
    assert {"fast1", "fast2", "fast3", "fast4"} <= got, f"快项应全部返回: {got}"
    # slow 在 0.4s 截止前未完成 → 不在结果里(若等全部则需 1.5s 且全部返回)
    assert not any(c.startswith("slow") for c in got), "slow 应被 deadline 截断未返回"


def test_analyze_many_no_deadline_waits_all(monkeypatch):
    """deadline_s=None 走旧 ex.map 路径，等全部完成。"""
    import time
    buffett = sys.modules.get("backtest.buffett") or __import__("backtest.buffett", fromlist=["x"])

    seen = []
    def _fake_analyze(c):
        seen.append(c)
        return {"code": c, "pe": 10.0}
    monkeypatch.setattr(buffett, "analyze", _fake_analyze)
    monkeypatch.setattr(buffett, "prefetch_financial", lambda codes: None)
    out = buffett.analyze_many(["a", "b", "c"])  # 默认 None
    assert {r["code"] for r in out} == {"a", "b", "c"}
    assert len(seen) == 3


def test_akshare_blocked_circuit(monkeypatch):
    """连续失败≥3次→熔断;成功→重置;熔断窗口内 akshare_blocked()=True。
    quality 口径2据此跳 buffett 省 40s 白烧(akshare 被封常态)。"""
    buffett = sys.modules.get("backtest.buffett") or __import__("backtest.buffett", fromlist=["x"])
    buffett._CONSEC_FAIL = 0
    buffett._BLOCKED_UNTIL = 0.0
    assert buffett.akshare_blocked() is False
    buffett._note_fetch(False)
    buffett._note_fetch(False)
    assert buffett.akshare_blocked() is False, "2次失败未达熔断阈值3"
    buffett._note_fetch(False)  # 第3次→熔断
    assert buffett.akshare_blocked() is True, "3次连续失败应熔断"
    # 成功拉取→重置熔断
    buffett._note_fetch(True)
    assert buffett.akshare_blocked() is False, "成功应解除熔断"


def test_fetch_abstract_records_fail_on_timeout(monkeypatch):
    """fetch_abstract 网络超时时调 _note_fetch(False)(驱动熔断)。"""
    buffett = sys.modules.get("backtest.buffett") or __import__("backtest.buffett", fromlist=["x"])
    monkeypatch.setattr(buffett, "_AK_OK", True)
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    # tdx 主源不可用(返 None)→走 akshare 备援路径,测 akshare 超时熔断
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial", lambda code: None)
    calls = []
    monkeypatch.setattr(buffett, "_note_fetch", lambda ok: calls.append(ok))
    # _fetch_net hang 致 ThreadPoolExecutor 超时
    import time
    monkeypatch.setattr(buffett, "_fetch_net", lambda code: time.sleep(5))
    monkeypatch.setattr(buffett, "_AK_TIMEOUT", 0.2)
    df, stale = buffett.fetch_abstract("000001")
    assert df is None
    assert False in calls, "超时应调 _note_fetch(False)"


def test_fetch_abstract_records_fail_on_empty_result(monkeypatch):
    """_fetch_net 快速返回空 DataFrame(akshare 被封常态,非 20s 超时)亦计失败驱动熔断。
    旧实现空结果走 fallthrough 不调 _note_fetch,熔断永不触发,quality 口径2 每只白烧 deadline。"""
    buffett = sys.modules.get("backtest.buffett") or __import__("backtest.buffett", fromlist=["x"])
    import pandas as pd
    monkeypatch.setattr(buffett, "_AK_OK", True)
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    # tdx 主源不可用→走 akshare 备援,测空结果熔断
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial", lambda code: None)
    calls = []
    monkeypatch.setattr(buffett, "_note_fetch", lambda ok: calls.append(ok))
    monkeypatch.setattr(buffett, "_fetch_net", lambda code: pd.DataFrame())  # 空 df 快速返回
    monkeypatch.setattr(buffett, "_cache_set", lambda code, df: None)
    df, stale = buffett.fetch_abstract("000001")
    assert df is None, "空结果应降级 stale 缓存/None"
    assert False in calls, "空结果应调 _note_fetch(False) 驱动熔断"


def test_fetch_abstract_records_success_resets_circuit(monkeypatch):
    """成功拉取调 _note_fetch(True) 重置熔断(回归守卫:重构勿漏成功路径)。"""
    buffett = sys.modules.get("backtest.buffett") or __import__("backtest.buffett", fromlist=["x"])
    import pandas as pd
    monkeypatch.setattr(buffett, "_AK_OK", True)
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    # tdx 主源不可用→走 akshare 备援,测成功重置熔断
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial", lambda code: None)
    calls = []
    monkeypatch.setattr(buffett, "_note_fetch", lambda ok: calls.append(ok))
    fake_df = pd.DataFrame({"指标": ["每股收益"], "2024-12-31": [1.0]})
    monkeypatch.setattr(buffett, "_fetch_net", lambda code: fake_df)
    monkeypatch.setattr(buffett, "_cache_set", lambda code, df: None)
    df, stale = buffett.fetch_abstract("000001")
    assert df is fake_df and stale is False
    assert True in calls, "成功应调 _note_fetch(True) 重置熔断"

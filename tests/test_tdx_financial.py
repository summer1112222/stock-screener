# -*- coding: utf-8 -*-
import json
import os

import pandas as pd

from data import fundamentals
from data.fundamentals import _parse_cn_amount

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "tdx_financial_600519.json")


def _load_fix():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def test_parse_cn_amount():
    assert _parse_cn_amount("445.1688亿") == 44516880000.0  # 445.1688×1e8 (plan原文44516800000.0少一个8,数学笔误修正)
    assert _parse_cn_amount("57.0895万") == 570895.0
    assert _parse_cn_amount("5234.12") == 5234.12
    assert _parse_cn_amount("-1.2亿") == -120000000.0
    assert _parse_cn_amount("-") is None
    assert _parse_cn_amount("") is None
    assert _parse_cn_amount("89.5552%") == 89.5552
    assert _parse_cn_amount(None) is None
    assert _parse_cn_amount("  16.75 ") == 16.75  # 空白容忍


def test_parse_tdx_financial_golden(monkeypatch):
    content = _load_fix()
    # mock get_company_info 返 fixture
    monkeypatch.setattr(fundamentals, "get_company_info",
                        lambda code, category: {"code": code, "category": category,
                                                "content": content, "ok": True, "err": ""})
    r = fundamentals.parse_tdx_financial("600519")
    # 1) 四键齐全
    assert set(r.keys()) == {"abstract", "balance", "cashflow", "profit"}
    # 2) abstract 摘要宽表结构
    abs_df = r["abstract"]
    assert abs_df is not None and not abs_df.empty
    assert "指标" in abs_df.columns
    # 报告期列含 2025-12-31
    assert any("2025-12-31" in str(c) for c in abs_df.columns)
    # 含关键指标行(净利润/营业总收入/加权净资产收益率)
    names = abs_df["指标"].astype(str).tolist()
    assert any("净利润" in n for n in names)
    assert any("营业总收入" in n for n in names)
    assert any("加权净资产收益率" in n for n in names)
    # 3) 三大表 df:行=报告期 列含"报告期"
    for k in ("balance", "cashflow", "profit"):
        df = r[k]
        assert df is not None and not df.empty, f"{k} empty"
        assert "报告期" in df.columns, f"{k} 无报告期列"
        # 报告期降序(首行最新)
        first = str(df.iloc[0]["报告期"])
        assert "2026" in first or "2025" in first
    # 4) balance 含资产总额/负债总额/股东权益合计
    bcols = [str(c) for c in r["balance"].columns]
    assert any("资产总额" in c for c in bcols)
    assert any("负债总额" in c for c in bcols)
    # 5) cashflow 含经营活动现金净额
    ccols = [str(c) for c in r["cashflow"].columns]
    assert any("经营活动现金净额" in c for c in ccols)
    # 6) profit 含营业收入/净利润
    pcols = [str(c) for c in r["profit"].columns]
    assert any("营业收入" in c for c in pcols)
    # 7) 数值类型:净利润行某报告期值为 float 非 str
    ni_row = abs_df[abs_df["指标"].astype(str).str.contains("净利润", na=False)].iloc[0]
    val = None
    for c in abs_df.columns:
        if c == "指标":
            continue
        if "2025-12-31" in str(c):
            val = ni_row[c]
            break
    assert isinstance(val, float), f"净利润值非float: {val}"


def test_parse_tdx_financial_fail_returns_none(monkeypatch):
    monkeypatch.setattr(fundamentals, "get_company_info",
                        lambda code, category: {"code": code, "category": category,
                                                "content": "", "ok": False, "err": "连不上"})
    r = fundamentals.parse_tdx_financial("600519")
    assert r == {"abstract": None, "balance": None, "cashflow": None, "profit": None}


# ---- Task 3: buffett.fetch_abstract 改 tdx 主源 ----
import backtest.buffett as buffett


def test_fetch_abstract_tdx_primary(monkeypatch):
    """tdx 解析成功→走 tdx,不调 akshare;abstract+三大表缓存被预填。"""
    abs_df = pd.DataFrame({"指标": ["净利润"], "2025-12-31": [82320000000.0]})
    bal_df = pd.DataFrame({"报告期": ["2025-12-31"], "资产总额": [3e11]})
    parsed = {"abstract": abs_df, "balance": bal_df,
              "cashflow": None, "profit": None}
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial",
                        lambda code: dict(parsed))
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    set_calls = []
    monkeypatch.setattr(buffett, "_cache_set", lambda code, df: set_calls.append(("abstract", code)))
    fset_calls = []
    monkeypatch.setattr(buffett.fundamentals, "_cache_set",
                        lambda code, source, df: fset_calls.append((source, code)))
    monkeypatch.setattr(buffett, "_fetch_net",
                        lambda code: (_ for _ in ()).throw(AssertionError("不应调 akshare")))
    df, stale = buffett.fetch_abstract("600519")
    assert stale is False
    assert df is abs_df
    assert ("abstract", "600519") in set_calls  # abstract 缓存
    assert ("balance", "600519") in fset_calls  # 三大表预填


def test_fetch_abstract_akshare_fallback(monkeypatch):
    """tdx 返全 None→走 akshare 备援。"""
    monkeypatch.setattr(buffett.fundamentals, "parse_tdx_financial",
                        lambda code: {"abstract": None, "balance": None,
                                      "cashflow": None, "profit": None})
    monkeypatch.setattr(buffett, "_cache_get", lambda code, allow_stale=False: (None, "miss"))
    monkeypatch.setattr(buffett, "_AK_OK", True)  # 宿主无 akshare 时强制走备援块
    ak_df = pd.DataFrame({"指标": ["净利润"], "2024-12-31": [1.0]})
    monkeypatch.setattr(buffett, "_fetch_net", lambda code: ak_df)
    monkeypatch.setattr(buffett, "_cache_set", lambda code, df: None)
    df, stale = buffett.fetch_abstract("600519")
    assert df is ak_df
    assert stale is False

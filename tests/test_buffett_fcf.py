# tests/test_buffett_fcf.py
# -*- coding: utf-8 -*-
"""buffett FCF 升级测试:完整现金流表可得时用真实 FCF,失败降级摘要代理。"""
import pandas as pd

from backtest import buffett


def _cashflow_df():
    return pd.DataFrame([{
        "报告期": "2024-12-31",
        "经营活动产生的现金流量净额": 1000.0,
        "购建固定资产、无形资产及其他长期资产支付的现金": 300.0,
    }])


def test_pick_col_sum_finds_annual():
    v = buffett._pick_col_sum(_cashflow_df(),
                              ["经营活动产生的现金流量净额"])
    assert v == 1000.0
    v2 = buffett._pick_col_sum(_cashflow_df(),
                               ["购建固定资产、无形资产及其他长期资产支付的现金"])
    assert v2 == 300.0


def test_pick_col_sum_none_on_empty():
    assert buffett._pick_col_sum(pd.DataFrame(), ["x"]) is None


def test_analyze_real_fcf(monkeypatch, tmp_path):
    """完整现金流表可得 → fcf_source=完整现金流表, fcf_proxy=经营-资本开支。"""
    from data import db
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()
    monkeypatch.setattr(buffett, "fetch_abstract",
                        lambda c: (pd.DataFrame(), False))
    monkeypatch.setattr(buffett, "_spot",
                        lambda c: {"code": c, "name": "X", "latest_price": 100.0})
    monkeypatch.setattr(buffett, "fundamentals",
                        type("M", (), {"fetch": staticmethod(
                            lambda code, src: (_cashflow_df(), False))}))
    monkeypatch.setattr(buffett, "_row_pairs",
                        lambda df, kw: [("2024-12-31", 800.0)] if "净利润" == kw
                        else [])
    res = buffett.analyze("600519")
    assert res["ratios"]["fcf_source"] == "完整现金流表(经营-资本开支)"
    assert res["ratios"]["fcf_proxy"] == 700.0  # 1000-300
    assert res["ratios"]["fcf_to_netincome"] == round(700.0 / 800.0, 2)


def test_analyze_fallback_to_abstract(monkeypatch, tmp_path):
    """完整现金流表失败 → 降级摘要代理。"""
    from data import db
    p = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db.init_db()

    def _abstract_pairs(df, kw):
        if kw == "经营现金流量净额":
            return [("2024-12-31", 900.0)]
        if kw == "净利润":
            return [("2024-12-31", 800.0)]
        return []
    monkeypatch.setattr(buffett, "fetch_abstract",
                        lambda c: (pd.DataFrame({"指标": ["x"]}), False))
    monkeypatch.setattr(buffett, "_spot",
                        lambda c: {"code": c, "name": "X", "latest_price": 100.0})
    monkeypatch.setattr(buffett, "fundamentals",
                        type("M", (), {"fetch": staticmethod(
                            lambda code, src: (None, False))}))
    monkeypatch.setattr(buffett, "_row_pairs", _abstract_pairs)
    res = buffett.analyze("600519")
    assert "摘要代理" in res["ratios"]["fcf_source"]
    assert res["ratios"]["fcf_proxy"] == 900.0

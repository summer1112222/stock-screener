# -*- coding: utf-8 -*-
"""因子研究台测试：合成历史面板，不触网。"""

import numpy as np
import pandas as pd

from backtest import research


def _panels():
    idx = pd.date_range("2025-01-01", periods=80, freq="D")
    close = pd.DataFrame(
        {
            "000001": np.linspace(10.0, 18.0, len(idx)),
            "000002": np.linspace(18.0, 10.0, len(idx)),
            "000003": np.linspace(12.0, 12.5, len(idx)),
            "000004": np.linspace(11.0, 15.0, len(idx)),
        },
        index=idx,
    )
    amount = pd.DataFrame(1e8, index=idx, columns=close.columns)
    return close, amount


def test_rel_strength_factor_direction_and_ic(monkeypatch):
    """相对强度因子: 过去 n 日个股收益减去当日市场横截面中位数。

    - 面板计算用原始相对值(IC 秩相关对单调变换不变, 不需 0-1 clip)
    - 强势股 相对强度 > 弱势股
    - 接入 research 后能跑通 IC/分层
    """
    close, amount = _panels()
    # 直接验证 compute_factor 的方向语义
    rs = research.bt_eval.compute_factor(
        close, "rel_strength_5", params={"n": 5}, amount=amount)
    last = rs.iloc[-1]
    # 000001(10→18 强) 应高于 000002(18→10 弱)
    assert last["000001"] > last["000002"]
    # 首行(前 5 日收益不足窗口)应为 NaN
    assert pd.isna(rs.iloc[0]["000001"])

    # 接入 run_factor_research 编排能产出 IC 报告
    monkeypatch.setattr(
        research.bt_eval,
        "load_panel",
        lambda universe, codes, start, end, field: close if field == "close" else amount,
    )
    result = research.run_factor_research(
        "stock", list(close.columns), "rel_strength_5", "20250101", "20251231",
        forward_days=[5], n=5, n_groups=3,
    )
    assert result["context"]["factor"] == "rel_strength_5"
    horizon = result["horizons"]["5"]
    assert horizon["ic"]["spearman"]["n"] > 0
    assert horizon["coverage"]["factor_cells"] > 0
    assert "groups" in horizon["decile"]


def test_valid_factor_keys_include_rel_strength():
    """compute_factor 支持 rel_strength_n; 未知键抛错。"""
    idx = pd.date_range("2025-01-01", periods=30, freq="D")
    close = pd.DataFrame({"000001": np.linspace(10, 15, 30)}, index=idx)
    out = research.bt_eval.compute_factor(
        close, "rel_strength_20", params={"n": 20})
    assert out is not None and not out.empty
    # 未注册的因子键 → ValueError
    try:
        research.bt_eval.compute_factor(close, "no_such_factor")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_rank_ic_and_multi_horizon(monkeypatch):
    close, amount = _panels()
    monkeypatch.setattr(
        research.bt_eval,
        "load_panel",
        lambda universe, codes, start, end, field: close if field == "close" else amount,
    )

    result = research.run_factor_research(
        "stock", list(close.columns), "momentum_n", "20250101", "20251231",
        forward_days=[1, 5], n=5, n_groups=3,
    )

    assert result["context"]["universe"] == "stock"
    assert result["context"]["factor"] == "momentum_n"
    assert set(result["horizons"]) == {"1", "5"}
    for horizon in result["horizons"].values():
        assert horizon["ic"]["pearson"]["n"] > 0
        assert horizon["ic"]["spearman"]["n"] > 0
        assert "groups" in horizon["decile"]
        assert horizon["coverage"]["factor_cells"] > 0


def test_train_test_split_and_missing_codes(monkeypatch):
    close, amount = _panels()
    close = close.drop(columns=["000004"])
    monkeypatch.setattr(
        research.bt_eval,
        "load_panel",
        lambda universe, codes, start, end, field: close if field == "close" else amount,
    )

    result = research.run_factor_research(
        "stock", ["000001", "000002", "000004"], "momentum_n", "20250101", "20251231",
        forward_days=[5], n=10, n_groups=3, train_frac=0.6,
    )

    assert result["quality"]["missing_codes"] == ["000004"]
    horizon = result["horizons"]["5"]
    assert horizon["cutoff"]
    assert len(horizon["train_range"]) == 2
    assert len(horizon["test_range"]) == 2
    assert horizon["train"]["summary"]["n"] >= 0
    assert horizon["test"]["summary"]["n"] >= 0


def test_empty_history_is_explicit(monkeypatch):
    monkeypatch.setattr(
        research.bt_eval,
        "load_panel",
        lambda *args, **kwargs: pd.DataFrame(),
    )
    result = research.run_factor_research(
        "stock", ["000001"], "momentum_n", "20250101", "20251231",
        forward_days=[1],
    )
    assert result["status"] == "no_history"
    assert result["horizons"] == {}
    assert "无历史数据" in result["message"]


def test_insufficient_sample_is_explicit(monkeypatch):
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    close = pd.DataFrame({"000001": [10, 10.1, 10.2, 10.3]}, index=idx)
    amount = pd.DataFrame(1e8, index=idx, columns=close.columns)
    monkeypatch.setattr(
        research.bt_eval,
        "load_panel",
        lambda universe, codes, start, end, field: close if field == "close" else amount,
    )
    result = research.run_factor_research(
        "stock", ["000001"], "momentum_n", "20250101", "20251231",
        forward_days=[5], n=20,
    )
    assert result["status"] == "insufficient_sample"
    assert result["horizons"]["5"]["status"] == "insufficient_sample"


def test_factor_research_api_wrap(monkeypatch):
    from fastapi.testclient import TestClient
    from api import server

    monkeypatch.setattr(
        server.bt_research,
        "run_factor_research",
        lambda **kwargs: {
            "status": "ok",
            "context": {"factor": kwargs["factor"]},
            "horizons": {},
        },
    )
    client = TestClient(server.app)
    response = client.post(
        "/api/research/factor",
        json={"universe": "stock", "codes": ["000001"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["bt_disclaimer"]
    assert body["disclaimer"]


def _run_with_cost(monkeypatch, **overrides):
    close, amount = _panels()
    monkeypatch.setattr(
        research.bt_eval,
        "load_panel",
        lambda universe, codes, start, end, field: close if field == "close" else amount,
    )
    args = dict(
        universe="stock",
        codes=list(close.columns),
        factor="momentum_n",
        start="20250101",
        end="20251231",
        n=5,
        n_groups=3,
        forward_days=[1],
    )
    args.update(overrides)
    return research.run_factor_research(**args)


def test_cost_after_returns(monkeypatch):
    result = _run_with_cost(monkeypatch, cost_bps=30.0)
    horizon = result["horizons"]["1"]
    assert horizon["cost"]["enabled"] is True
    assert horizon["cost"]["cost_bps"] == 30.0
    assert "groups" in horizon["decile_after_cost"]
    assert "cumulative" in horizon["decile_after_cost"]["long_short"]


def test_walk_forward_block(monkeypatch):
    result = _run_with_cost(monkeypatch, walk_forward=True, forward_days=[5])
    horizon = result["horizons"]["5"]
    assert "walk_forward" in horizon
    wf = horizon["walk_forward"]
    assert "oos_ic_mean" in wf
    assert "overfit_frac" in wf
    assert isinstance(wf.get("segments"), list)


def test_verdict_three_states(monkeypatch):
    result = _run_with_cost(monkeypatch, forward_days=[1], n=20, train_frac=0.8)
    horizon = result["horizons"]["1"]
    assert horizon["verdict"]["state"] in {
        "validated", "weak_sample", "unstable_oos",
        "cost_sensitive", "insufficient_sample",
    }

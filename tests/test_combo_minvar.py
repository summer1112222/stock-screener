# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from backtest import quality as bt_q


def _seed_stock_daily(codes, n=80, seed=13):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    rows = []
    for c in codes:
        px = 10 + np.cumsum(rng.normal(0, 0.3, n))
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i]*1.01, "low": px[i]*0.99,
                         "close": px[i], "volume": 0, "amount": 1e8})
    bt_q.db.upsert_rows("stock_daily", rows)


def test_minvar_weights_sum_to_one():
    codes = [f"c{i}" for i in range(8)]
    _seed_stock_daily(codes)
    bt_q.db.upsert_rows("stock_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e8, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="stock", days=20, combo_method="min_var")
    main = res.get("main", [])
    assert main, "main 为空，min_var 流程未触发"
    total = sum(it.get("weight", 0) for it in main)
    assert abs(total - 1.0) < 1e-6, f"权重和={total} 应为1"
    for it in main:
        assert it["weight"] >= 0


def test_minvar_weights_order_invariant():
    """非字典序 codes：权重必须按 code 对齐，而非按 pivot 后的字典序。
    bug：load_panel pivot_table sort=True→cov 在字典序列上算，返回 w 是
    字典序 codes 的权重；下游 _apply_combo 用 zip(kept, ws) 赋权错配。
    """
    codes = ["c7", "c0", "c3", "c5"]
    _seed_stock_daily(codes, seed=21)
    ws_ab = bt_q._min_var_weights(codes, "stock")
    ws_ba = bt_q._min_var_weights(list(reversed(codes)), "stock")
    assert len(ws_ab) == len(codes)
    assert len(ws_ba) == len(codes)
    # 每个位置的权重必须对应 codes[i]；反转调用则对应反转后的位置
    for i, c in enumerate(codes):
        j = len(codes) - 1 - i  # c 在反转序列中的位置
        assert abs(ws_ab[i] - ws_ba[j]) < 1e-9, (
            f"code {c} 权重不一致：顺序{codes}→{ws_ab} vs "
            f"反转→{ws_ba}（位置 {i}={ws_ab[i]} vs {j}={ws_ba[j]}）"
        )



def test_minvar_singular_degrades():
    codes = ["c0", "c1"]
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2022-01-01", periods=80)
    px = 10 + np.cumsum(rng.normal(0, 0.3, 80))
    rows = []
    for c in codes:
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i], "low": px[i],
                         "close": px[i], "volume": 0, "amount": 1e8})
    bt_q.db.upsert_rows("stock_daily", rows)
    bt_q.db.upsert_rows("stock_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e8, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="stock", days=20, combo_method="min_var")
    # 不崩即通过
    assert isinstance(res, dict)

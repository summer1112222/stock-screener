# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
import pytest
from backtest import quality as bt_q


def _seed_stock_daily(codes, n=80, seed=13):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n)
    rows = []
    for c in codes:
        px = 10 + np.cumsum(rng.normal(0, 0.3, n))
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i]*1.01, "low": px[i]*0.99,
                         "close": px[i], "volume": 0, "amount": 1e8})
    bt_q.db.upsert_rows("stock_daily", rows)


@pytest.fixture
def sm_db_minvar(tmp_path, monkeypatch):
    """隔离 DB 到 tmp_path + seed smart_money_action，让 quality_rank 资金面口径可命中。
    修 pre-existing 缺陷：原测试用默认 stock.db 依赖 main 库已有 smart_money 数据，
    空库(worktree/CI)则资金面口径空、min_dims 不足、min_var 不生成 main 而失败。
    date 取近期(top_by_amount 按 now-days 过滤，旧日期被滤)；amount 差异化(同值→
    zscore=0→分位居中低于 0.6 阈值不命中)。"""
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    monkeypatch.setattr(bt_q.db, "DB_PATH", tmp_path / "t.db")
    bt_q.db.init_db()
    bt_q.db.upsert_rows("smart_money_action", [
        {"date": today, "code": f"c{i}", "name": f"c{i}", "market": "股票",
         "channel": "资金流", "actor": "", "action": "净买入",
         "amount": (i + 1) * 1e7, "as_of": None, "ts": ""} for i in range(8)])


def test_minvar_weights_sum_to_one(sm_db_minvar):
    codes = [f"c{i}" for i in range(8)]
    _seed_stock_daily(codes)
    bt_q.db.upsert_rows("stock_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e9, "turnover_rate": 1.0} for c in codes])
    # min_dims=1 + dim_thresh=0.0：本测试聚焦 min_var 权重和=1 的数学正确性，
    # 非筛选严格度；宽松门槛保证 main 非空触发 combo（空库无真实分位分化）。
    res = bt_q.quality_rank(universe="stock", days=20, combo_method="min_var",
                            min_dims=1, dim_thresh=0.0)
    main = res.get("main", [])
    assert main, "main 为空，min_var 流程未触发"
    total = sum(it.get("weight", 0) for it in main)
    # 权重在 _apply_combo 内 round 到 4 位小数(line 416)，N 只票累积误差可达 ~5e-4，
    # 故容差 1e-3（仍能抓 sum=2/0.5 这类真 bug）。
    assert abs(total - 1.0) < 1e-3, f"权重和={total} 应为1"
    for it in main:
        assert it["weight"] >= 0


def test_minvar_weights_order_invariant(sm_db_minvar):
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



def test_minvar_singular_degrades(sm_db_minvar):
    codes = ["c0", "c1"]
    rng = np.random.default_rng(1)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=80)
    px = 10 + np.cumsum(rng.normal(0, 0.3, 80))
    rows = []
    for c in codes:
        for i, d in enumerate(dates):
            rows.append({"code": c, "date": str(d.date()),
                         "open": px[i], "high": px[i], "low": px[i],
                         "close": px[i], "volume": 0, "amount": 1e8})
    bt_q.db.upsert_rows("stock_daily", rows)
    bt_q.db.upsert_rows("stock_spot", [{"code": c, "name": c, "latest_price": 10.0,
        "change_pct": 0.0, "turnover_amount": 1e9, "turnover_rate": 1.0} for c in codes])
    res = bt_q.quality_rank(universe="stock", days=20, combo_method="min_var")
    # 不崩即通过
    assert isinstance(res, dict)

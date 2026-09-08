import pandas as pd

from backtest.factor_compute import compute_mom_5_1, series_to_rows
from backtest.factor_snapshot import read_snapshots, write_snapshots
from data import db

db.init_db()


def _panel() -> pd.DataFrame:
    # 6 个交易日 × 2 只股票，做成 code 列、date 索引
    idx = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"]
    return pd.DataFrame(
        {
            "000001": [10.0, 10.5, 10.4, 11.0, 11.2, 12.0],
            "000002": [20.0, 20.0, 19.0, 21.0, 22.0, 24.0],
        },
        index=idx,
    )


def test_compute_mom_5_1_matches_close_over_shift5_minus_one():
    out = compute_mom_5_1(_panel())
    assert "000001" in out.columns
    # 第 6 日 = 12.0/10.0-1；第 5 日(索引4)不足 5 日窗口 → NaN
    assert round(out["000001"].iloc[-1], 6) == 0.2
    assert pd.isna(out["000001"].iloc[4])
    assert round(out["000002"].iloc[-1], 6) == 0.2


def test_series_to_rows_pivots_panel_into_long_snapshot_rows():
    rows = series_to_rows(_panel()[["000001"]], "000001", "v1")
    assert len(rows) == 6
    assert rows[0]["code"] == "000001"
    assert rows[0]["date"] == "2026-09-01"
    assert rows[3]["value"] == 11.0


def test_factor_compute_write_and_read_roundtrip():
    version = "roundtrip-v1"  # 独立版本，避免与其他用例在共享 DB 撞唯一键
    mom = compute_mom_5_1(_panel()[["000001"]])
    rows = series_to_rows(mom, "000001", version)
    write_snapshots("mom_5_1", version, rows)
    got = read_snapshots("mom_5_1", version, codes=["000001"])
    # 6 日中仅最后 1 日有足够 5 日窗口 → 只有 1 行非 NaN
    assert len(got) == 1
    assert round(got[0]["value"], 6) == 0.2
    assert got[0]["date"] == "2026-09-06"

import json

from backtest.factor_registry import get_factor, register_factor
from backtest.factor_snapshot import read_snapshots, write_snapshots
from data import db

db.init_db()  # 建表（factor_definition/factor_snapshot）


def test_register_factor_is_idempotent_and_keeps_metadata():
    register_factor("mom_5_1", "v1", "近5日收益剔除最近1日", "1d", ["close"], "close[t-1]/close[t-5]-1")
    register_factor("mom_5_1", "v2", "新版口径", "1d", ["close"], "new_formula")
    item = get_factor("mom_5_1")
    assert item["version"] == "v2"
    assert item["description"] == "新版口径"


def test_write_snapshots_upserts_by_factor_version_code_date():
    write_snapshots("mom_5_1", "v1", [
        {"code": "000001", "date": "2026-09-08", "value": 0.2, "params": {"window": 5}},
        {"code": "000002", "date": "2026-09-08", "value": 0.1},
    ])
    write_snapshots("mom_5_1", "v1", [
        {"code": "000001", "date": "2026-09-08", "value": 0.3, "params": {"window": 5}},
    ])
    rows = read_snapshots("mom_5_1", "v1", codes=["000001"],
                          start="2026-09-08", end="2026-09-08")
    assert len(rows) == 1
    assert rows[0]["value"] == 0.3
    assert json.loads(rows[0]["params_json"]) == {"window": 5}


def test_factor_snapshot_schema_is_created_by_db_init():
    db.init_db()
    # 表可查询且字段可用（结构由 SCHEMA_SQL 创建，不依赖迁移）
    assert db.query_rows("factor_definition", limit=1) is not None
    assert db.query_rows("factor_snapshot", limit=1) is not None


def test_read_snapshots_tolerates_malformed_params_json():
    version = "corrupt-v1"
    db.upsert_rows("factor_snapshot", [{
        "factor_name": "mom_5_1", "factor_version": version,
        "code": "000009", "date": "2026-09-08", "value": 0.1,
        "params_json": "{not-json", "created_ts": "2026-09-08T00:00:00",
    }])
    rows = read_snapshots("mom_5_1", version)
    assert len(rows) == 1
    assert rows[0]["value"] == 0.1
    assert rows[0].get("params") is None  # 损坏 params 不阻断整批读


def test_read_snapshots_batches_large_code_filter():
    version = "batch-v1"
    write_snapshots("mom_5_1", version, [
        {"code": "000001", "date": "2026-09-08", "value": 0.2},
    ])
    codes = [f"{i:06d}" for i in range(1, 1200)]  # > SQLite 变量上限(~999)
    rows = read_snapshots("mom_5_1", version, codes=codes)
    assert len(rows) == 1
    assert rows[0]["code"] == "000001"

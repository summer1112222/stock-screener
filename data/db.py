# -*- coding: utf-8 -*-
"""SQLite 存储层：建表 + 增删改查 + meta(更新时间) 管理。

每次刷新用 INSERT OR REPLACE，按主键覆盖，只留最新快照。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import SCHEMA_SQL, TABLE_FIELDS

# DB 路径可由环境变量覆盖；默认放包外(项目根)，便于 Docker 挂卷持久化而不遮蔽代码包。
DB_PATH = Path(os.environ.get("SCREENER_DB",
                              Path(__file__).resolve().parent.parent / "stock.db"))

# 旧库迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，这里对历史库
# 幂等地 ADD COLUMN (重复执行遇 duplicate column 静默忽略)。
_BOARD_MIGRATIONS = [
    ("industry_board", "turnover_amount", "REAL"),
    ("industry_board", "leading_stock_change", "REAL"),
    ("industry_board", "up_count", "INTEGER"),
    ("industry_board", "down_count", "INTEGER"),
    ("industry_board", "constituent_count", "INTEGER"),
    ("industry_board", "event", "TEXT"),
    ("concept_board", "turnover_amount", "REAL"),
    ("concept_board", "leading_stock_change", "REAL"),
    ("concept_board", "up_count", "INTEGER"),
    ("concept_board", "down_count", "INTEGER"),
    ("concept_board", "constituent_count", "INTEGER"),
    ("concept_board", "event", "TEXT"),
    # 持仓到价提醒：用户自设价位规则触发通知，读时比较 latest_price，非买卖点。
    ("portfolio", "alert_hi", "REAL"),
    ("portfolio", "alert_lo", "REAL"),
    # 自选到价提醒：未买入观察清单，用户自设价位机械标记，非买卖点。
    ("watchlist", "alert_hi", "REAL"),
    ("watchlist", "alert_lo", "REAL"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('industry_board','concept_board','portfolio','watchlist')")
    existing = {r[0] for r in cur.fetchall()}
    for table, col, coltype in _BOARD_MIGRATIONS:
        if table not in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            # 列已存在 (duplicate column name) —— 幂等，跳过
            pass


# 线程本地连接：每线程复用一条长活 SQLite 连接。相比每次 connect+PRAGMA，实测
# 每查询固定开销 ~3.3ms→~0.06ms(98% 节省)，WAL 模式仍保持多连接读并发。连接数
# = 并发线程数(请求线程 + 采集 ThreadPool 等)，FD 可控，进程退出时 OS 回收。
_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    return conn


@contextmanager
def get_conn():
    """SQLite 连接上下文管理器：线程本地连接复用 + 惰性重建。

    语义与旧"每操作新建连接"对齐：
    - 事务提交由调用方显式 conn.commit()；context 退出时若仍有未提交事务则
      rollback 丢弃(等价于旧实现的 close 丢弃，绝不意外保留脏状态)。
    - 连接被外部关闭/损坏时，下次进入经 SELECT 1 健康检查后重建。
    - 并发：WAL 模式(读并发+单写) + busy_timeout 5s(写等待锁而非立即抛
      'database is locked'，防 /api/backtest/fetch 并发写 500)。
    """
    conn = getattr(_local, "_conn", None)
    dbpath = str(DB_PATH)
    # DB_PATH 变化(测试按 tmp 动态切库) → 重建连接，否则残留连接指向已删旧库
    if conn is None or _local.__dict__.get("_dbpath") != dbpath:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = _connect()
        _local._conn = conn
        _local._dbpath = dbpath
    else:
        try:
            conn.execute("SELECT 1")
        except sqlite3.Error:
            # 连接损坏，惰性重建
            try:
                conn.close()
            except Exception:
                pass
            conn = _connect()
            _local._conn = conn
            _local._dbpath = dbpath
    try:
        yield conn
    finally:
        # 未提交事务丢弃(已 commit 则 no-op)，保证复用连接不残留脏事务
        try:
            conn.rollback()
        except sqlite3.Error:
            pass


def init_db() -> None:
    """建表(若不存在)+旧库幂等迁移补列。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)
        conn.commit()


def upsert_rows(table: str, rows: Iterable[dict]) -> int:
    """按表主键 INSERT OR REPLACE 写入若干行；只取该表规范字段，缺列置 None。
    返回写入行数。"""
    fields = TABLE_FIELDS[table]
    cols = list(fields)
    placeholders = ",".join(["?"] * len(cols))
    col_list = ",".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    cleaned = [tuple(r.get(c) for c in cols) for r in rows]
    if not cleaned:
        return 0
    with get_conn() as conn:
        conn.executemany(sql, cleaned)
        conn.commit()
    return len(cleaned)


def query_rows(table: str, where: str = "", params: tuple = (),
               order_by: str = "", limit: int = 0) -> list[dict]:
    """通用查询，返回 dict 列表。"""
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def set_meta(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def get_meta(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
    return row["value"] if row else default


def stamp_update_time() -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_meta("update_time", ts)
    return ts


def last_update_time() -> str:
    return get_meta("update_time", "")

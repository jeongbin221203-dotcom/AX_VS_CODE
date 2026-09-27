"""SQLite 연결 및 스키마 관리."""

import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

import pandas as pd

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    spec          TEXT    DEFAULT '',
    unit          TEXT    DEFAULT 'EA',
    category      TEXT    DEFAULT '미분류',
    safety_stock  REAL    DEFAULT 0,
    unit_price    REAL    DEFAULT 0,
    location      TEXT    DEFAULT '',
    supplier      TEXT    DEFAULT '',
    active        INTEGER DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    tx_type      TEXT    NOT NULL CHECK (tx_type IN ('IN', 'OUT', 'ADJ')),
    qty          REAL    NOT NULL,
    unit_price   REAL    DEFAULT 0,
    tx_date      TEXT    NOT NULL,
    ref_no       TEXT    DEFAULT '',
    partner      TEXT    DEFAULT '',
    note         TEXT    DEFAULT '',
    created_by   TEXT    DEFAULT '',
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tx_material ON transactions(material_id);
CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(tx_date);
CREATE INDEX IF NOT EXISTS idx_mat_active  ON materials(active);
"""

# 재고 = 입고 합 - 출고 합 + 조정(부호 포함) 합
STOCK_EXPR = """
COALESCE(SUM(CASE WHEN t.tx_type = 'IN'  THEN t.qty
                  WHEN t.tx_type = 'OUT' THEN -t.qty
                  ELSE t.qty END), 0)
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """읽기 전용 커넥션. 종료 시 자동 close."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """쓰기 커넥션. 예외 발생 시 롤백, 정상 종료 시 커밋."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with transaction() as conn:
        conn.executescript(SCHEMA)


def query_df(sql: str, params: Sequence = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=tuple(params))


def execute(sql: str, params: Sequence = ()) -> int:
    with transaction() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid


def executemany(sql: str, rows: Iterable[Sequence]) -> None:
    with transaction() as conn:
        conn.executemany(sql, rows)

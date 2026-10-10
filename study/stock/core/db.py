import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT, sector TEXT, marcap REAL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS prices(
  code TEXT NOT NULL, date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume INTEGER,
  PRIMARY KEY(code, date)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS watchlist(
  code TEXT PRIMARY KEY, memo TEXT DEFAULT '', added_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS collect_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, at TEXT, ok INTEGER, message TEXT);
CREATE TABLE IF NOT EXISTS daily_checks(
  code TEXT NOT NULL, date TEXT NOT NULL, close REAL, prob REAL, halt REAL,
  avoid INTEGER, above20 INTEGER, ma20_60 INTEGER, volup INTEGER, liq_ok INTEGER,
  sig0 INTEGER, sig1 INTEGER, sig5 INTEGER, sign10 INTEGER,
  PRIMARY KEY(code, date)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_daily_checks_date ON daily_checks(date);
"""
# daily_checks: 날짜별 매수 신호·AI 확률·점검 값(core/signal_scan.py 가 전 종목을 훑을 때 채움). sig0/sig1/sig5 = 그날·1봉·5봉 안에 난 신호의 비트(ai.SIGNAL_LABELS 순서).


def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)

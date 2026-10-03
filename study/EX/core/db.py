"""SQLite 연결. 요청마다 연결하고 요청이 끝나면 닫는다."""
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    pid TEXT NOT NULL,
    category TEXT NOT NULL,
    ok INTEGER NOT NULL,
    answer TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_attempts_pid ON attempts(pid);
CREATE INDEX IF NOT EXISTS ix_attempts_day ON attempts(created_at);

CREATE TABLE IF NOT EXISTS stars (
    pid TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sheet TEXT,
    rows INTEGER,
    cols INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS build_results (
    id INTEGER PRIMARY KEY,
    task TEXT NOT NULL,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    detail TEXT,
    file_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS exam_results (
    id INTEGER PRIMARY KEY,
    exam TEXT NOT NULL,
    score REAL NOT NULL,
    total REAL NOT NULL,
    passed INTEGER NOT NULL,
    seconds INTEGER,
    detail TEXT,
    file_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init(path):
    conn = connect(path)          # sqlite3 의 with 는 커밋만 하고 닫지 않으므로 직접 닫는다
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get():
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def close(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def setting(key, default=None):
    row = get().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get()
    conn.execute('INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key, value))
    conn.commit()

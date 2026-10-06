"""SQLite 연결. 요청마다 연결하고 요청이 끝나면 닫는다."""
import os
import secrets
import sqlite3

from flask import current_app, g, has_request_context, session

USER_TABLES = ('attempts', 'stars', 'uploads', 'build_results', 'exam_results', 'written_attempts', 'written_results')

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
    pid TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS written_attempts (
    id INTEGER PRIMARY KEY,
    qid TEXT NOT NULL,
    subject TEXT NOT NULL,
    ok INTEGER NOT NULL,
    picked INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    user TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_written_attempts ON written_attempts(user, qid);

CREATE TABLE IF NOT EXISTS written_results (
    id INTEGER PRIMARY KEY,
    level TEXT NOT NULL,
    average REAL NOT NULL,
    passed INTEGER NOT NULL,
    seconds INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    user TEXT NOT NULL DEFAULT ''
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
        for table in USER_TABLES:  # 예전 DB: 사용자 열 추가(기존 기록은 PC 사용자 '')
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
            if 'user' not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user TEXT NOT NULL DEFAULT ''")
        conn.execute('CREATE INDEX IF NOT EXISTS ix_attempts_user ON attempts(user, pid)')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_stars ON stars(user, pid)')
        conn.commit()
    finally:
        conn.close()


DB_BUDGET = 300 * 1024 * 1024        # 공개 서버 DB 파일 한도(방문자 복원·기록이 끝없이 늘지 않게)


def too_big():
    """DB 파일이 한도를 넘었는지(공개 서버에서만 의미)."""
    try:
        path = current_app.config['DATABASE']
        return current_app.config.get('PUBLIC') and os.path.getsize(path) > DB_BUDGET
    except (OSError, RuntimeError, KeyError):
        return False


def user_id():
    """현재 사용자. PC 실행은 '' 하나, 공개 서버는 개인 링크로 연결한 기기 'owner', 그 밖은 브라우저마다 무작위."""
    if not has_request_context() or not current_app.config.get('PUBLIC'):
        return ''
    if session.get('owner'):
        return 'owner'
    if 'uid' not in session:
        session['uid'] = secrets.token_hex(12)
        session.permanent = True
    return session['uid']


def get():
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def close(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def _skey(key):
    u = user_id()
    return f'{u}:{key}' if u else key


def setting(key, default=None):
    row = get().execute('SELECT value FROM settings WHERE key=?', (_skey(key),)).fetchone()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get()
    conn.execute('INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (_skey(key), value))
    conn.commit()

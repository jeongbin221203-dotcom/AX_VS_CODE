"""DB 연결 계층 — SQLite(개발·테스트·소규모) / PostgreSQL(운영·다중 서버) 공용.

  SALES_DATABASE_URL=postgresql://user:pw@host:5432/sales   → PostgreSQL + 커넥션 풀
  (없으면)  SALES_DB_PATH=data/sales.db                      → SQLite 파일

업무 코드는 SQLite 문법(`?` 자리표시자, cur.lastrowid, row[0]/row["col"])으로 작성하고,
PostgreSQL 일 때 이 계층이 다음을 바꿔 준다.
  * `?` → `%s`, SQL 안의 `%` → `%%`
  * INSERT 뒤 `RETURNING id` 를 붙여 lastrowid 제공, `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`
  * NUMERIC → float, 행은 이름·순번 둘 다로 읽히는 Row
방언 차이가 큰 날짜 계산은 days_since()/days_between() SQL 조각을 쓴다.
스키마는 Alembic 마이그레이션(migrations/)이 관리한다 → migrate().
"""
from __future__ import annotations

import atexit
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # sales/
DB_PATH = os.environ.get("SALES_DB_PATH", os.path.join(BASE_DIR, "data", "sales.db"))
DATABASE_URL = os.environ.get("SALES_DATABASE_URL", "")
POOL_MIN = int(os.environ.get("SALES_DB_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("SALES_DB_POOL_MAX", "10"))


def dialect() -> str:
    return "postgresql" if DATABASE_URL.startswith(("postgresql://", "postgres://")) else "sqlite"


def is_pg() -> bool:
    return dialect() == "postgresql"


def describe() -> str:
    """화면 표시용 DB 위치 (비밀번호는 가린다)."""
    if is_pg():
        return re.sub(r"//([^:/@]+):[^@]*@", r"//:****@", DATABASE_URL)
    return os.path.abspath(DB_PATH)


# ---------------------------------------------------------------------------
# 방언별 SQL 조각
# ---------------------------------------------------------------------------
def ym(expr: str) -> str:
    """'YYYY-MM' (날짜는 ISO 문자열로 저장하므로 두 DB 모두 앞 7자리)."""
    return f"substr({expr}, 1, 7)"


def days_since(expr: str) -> str:
    """오늘 - 날짜 (정수 일수)."""
    if is_pg():
        return f"(CURRENT_DATE - CAST(substr({expr}, 1, 10) AS date))"
    return f"CAST(julianday('now') - julianday({expr}) AS INTEGER)"


def days_between(start: str, end: str) -> str:
    """end - start (일수)."""
    if is_pg():
        return f"(CAST(substr({end}, 1, 10) AS date) - CAST(substr({start}, 1, 10) AS date))"
    return f"(julianday({end}) - julianday({start}))"


# ---------------------------------------------------------------------------
# 행 · 커서 · 커넥션 래퍼
# ---------------------------------------------------------------------------
class Row(dict):
    """dict 이면서 row[0] 처럼 순번으로도 읽힌다 (sqlite3.Row 와 같은 사용감)."""

    def __init__(self, names: list[str], values: Iterable[Any]):
        values = list(values)
        super().__init__(zip(names, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, cur, lastrowid: Optional[int] = None):
        self._cur = cur
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cur, "lastrowid", None)
        self.rowcount = cur.rowcount
        self.description = cur.description

    def _names(self) -> list[str]:
        return [d[0] for d in (self._cur.description or [])]

    def fetchone(self) -> Optional[Row]:
        row = self._cur.fetchone()
        return None if row is None else Row(self._names(), row)

    def fetchall(self) -> list[Row]:
        names = self._names()
        return [Row(names, r) for r in self._cur.fetchall()]


_INSERT_RE = re.compile(r"^\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+([A-Za-z_]+)", re.I)
NO_ID_TABLES = {"scheduler_state", "alembic_version", "api_usage", "api_idempotency",
                "company_settings", "form_submissions"}      # id 컬럼이 없는 테이블 (RETURNING id 를 붙이지 않는다)
_OR_IGNORE_RE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO", re.I)


def translate(sql: str, escape_percent: bool = True) -> str:
    """SQLite 문법 SQL → PostgreSQL. (파라미터가 없으면 psycopg 가 % 를 해석하지 않으므로 그대로 둔다)"""
    ignore = bool(_OR_IGNORE_RE.search(sql))
    sql = _OR_IGNORE_RE.sub("INSERT INTO", sql)
    out, quote = [], None
    for ch in sql:
        if quote:
            out.append("%%" if ch == "%" and escape_percent else ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%" and escape_percent:
            out.append("%%")
        else:
            out.append(ch)
    sql = "".join(out)
    if ignore:
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class Connection:
    """sqlite3 / psycopg 커넥션을 같은 방식으로 쓰게 하는 얇은 래퍼."""

    def __init__(self, raw, pg: bool):
        self.raw = raw
        self.pg = pg

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        params = tuple(params)
        if not self.pg:
            return Cursor(self.raw.execute(sql, params))
        m = _INSERT_RE.match(sql)
        want_id = bool(m) and m.group(1).lower() not in NO_ID_TABLES and "RETURNING" not in sql.upper()
        text = translate(sql, escape_percent=bool(params))
        if want_id:
            text = text.rstrip().rstrip(";") + " RETURNING id"
        cur = self.raw.cursor()
        cur.execute(text, params or None)
        new_id = None
        if want_id:
            row = cur.fetchone() if cur.description else None
            new_id = row[0] if row else None
        return Cursor(cur, new_id)

    def executescript(self, script: str) -> None:
        if self.pg:
            self.raw.execute(script)
        else:
            self.raw.executescript(script)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


# PostgreSQL 커넥션 풀 (서버 여러 대 × 스레드 여러 개에서 DB 연결 수를 묶어 둔다)
_pool = None
_pool_lock = threading.Lock()


def _configure_pg(conn) -> None:
    from psycopg.adapt import Loader

    class FloatLoader(Loader):              # NUMERIC(평균·합계·비율) → float
        def load(self, data):
            return float(bytes(data).decode()) if data is not None else None

    conn.adapters.register_loader("numeric", FloatLoader)
    conn.execute("SET TIME ZONE 'Asia/Seoul'")
    conn.commit()


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool
            _pool = ConnectionPool(DATABASE_URL, min_size=POOL_MIN, max_size=POOL_MAX,
                                   configure=_configure_pg, open=True, timeout=30,
                                   kwargs={"autocommit": False})
        return _pool


def _close_at_exit() -> None:
    try:
        close_pool()
    except Exception:   # noqa: BLE001 - 종료 중 정리 실패는 무시
        pass


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def get_conn(db_path: str | None = None):
    """커밋/롤백/반납을 보장하는 커넥션 컨텍스트 매니저.

    db_path 는 SQLite 전용 (테스트·배치에서 다른 파일을 가리킬 때). PostgreSQL 이면 풀에서 빌린다.
    """
    if is_pg() and not db_path:
        with _get_pool().connection() as raw:
            conn = Connection(raw, True)
            try:
                yield conn
                raw.commit()
            except Exception:
                raw.rollback()
                raise
        return
    raw = sqlite3.connect(db_path or DB_PATH, timeout=15, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    try:
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA foreign_keys=ON")
        conn = Connection(raw, False)
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def lock(conn: Connection, name: str) -> None:
    """트랜잭션 범위의 배타 잠금 (감사로그 해시 체인·스케줄러 리더 선출 등)."""
    if conn.pg:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (name,))
    else:
        conn.execute("BEGIN IMMEDIATE")


def try_lock(conn: Connection, name: str) -> bool:
    """잠금을 얻으면 True (이미 다른 서버가 잡고 있으면 False). 트랜잭션이 끝나면 풀린다."""
    if conn.pg:
        return bool(conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(?))", (name,)).fetchone()[0])
    try:
        conn.execute("BEGIN IMMEDIATE")
        return True
    except sqlite3.OperationalError:
        return False


# ---------------------------------------------------------------------------
# 조회 도우미
# ---------------------------------------------------------------------------
def df(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> pd.DataFrame:
    with get_conn(db_path) as conn:
        cur = conn.execute(sql, params)
        names = [d[0] for d in (cur.description or [])]
        rows = [tuple(r._values) for r in cur.fetchall()]
    return pd.DataFrame.from_records(rows, columns=names)


def one(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> Optional[dict]:
    with get_conn(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def rows(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> list[dict]:
    """DataFrame 을 거치지 않은 행 목록 (정수 컬럼의 NULL 이 실수로 바뀌지 않는다 — API 응답용)."""
    with get_conn(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(sql: str, params: Iterable[Any] = (), db_path: str | None = None) -> float:
    with get_conn(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0


# ---------------------------------------------------------------------------
# 마이그레이션 (Alembic)
# ---------------------------------------------------------------------------
def sqlalchemy_url(db_path: str | None = None) -> str:
    if is_pg() and not db_path:
        return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", DATABASE_URL)
    return "sqlite:///" + os.path.abspath(db_path or DB_PATH).replace("\\", "/")


def alembic_config(db_path: str | None = None):
    from alembic.config import Config
    cfg = Config()                     # ini 파일 없이 설정 (명령행 alembic 은 alembic.ini 사용)
    cfg.set_main_option("script_location", os.path.join(BASE_DIR, "migrations").replace("\\", "/"))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(db_path).replace("%", "%%"))
    return cfg


def migrate(db_path: str | None = None, revision: str = "head") -> None:
    """스키마를 최신 버전으로 올린다 (python manage.py db upgrade 와 같다)."""
    from alembic import command
    if not is_pg() or db_path:
        os.makedirs(os.path.dirname(os.path.abspath(db_path or DB_PATH)), exist_ok=True)
    command.upgrade(alembic_config(db_path), revision)


def head_revision() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(db_path: str | None = None) -> Optional[str]:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine
    engine = create_engine(sqlalchemy_url(db_path))
    try:
        with engine.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        engine.dispose()


atexit.register(_close_at_exit)

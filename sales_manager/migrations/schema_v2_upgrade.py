"""v2 기준선 적용 — 0001_baseline 에서 호출한다.

  SQLite     : 빈 DB 는 v2 스키마를 만들고, 전환 전(Streamlit 판) DB 는 데이터를 지우지 않고 v2 로 올린다
  PostgreSQL : v2 스키마를 새로 만든다 (schema_v2_pg.DDL)
"""
import re
import sqlite3

from schema_v2_sqlite import MIGRATIONS, SCHEMA, SCHEMA_ENTERPRISE, SCHEMA_POST


def statements(script: str):
    """트리거(BEGIN ... END;) 안의 ; 를 고려해 SQLite 스크립트를 문장 단위로 나눈다."""
    buf = ""
    for line in script.splitlines(keepends=True):
        if not buf and (not line.strip() or line.strip().startswith("--")):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_ddl(script: str, table: str) -> str:
    return re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);", script, re.S).group(0)


def _rebuild_with_owner_key(conn, table: str, create_sql: str, copy_cols: str) -> None:
    """UNIQUE 키가 담당자 '이름'이던 옛 테이블을 owner_key 기준으로 다시 만든다(데이터 보존)."""
    if "owner_key" in _columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    conn.execute(create_sql)
    conn.execute(f"INSERT INTO {table} ({copy_cols}, owner_id, owner_key) "
                 f"SELECT {copy_cols}, NULL, 'n:' || owner FROM {table}_old")
    conn.execute(f"DROP TABLE {table}_old")


def upgrade_sqlite(conn) -> None:
    """conn: sqlite3 DB-API 커넥션. executescript 를 쓰지 않아 Alembic 트랜잭션을 깨지 않는다."""
    for stmt in statements(SCHEMA + SCHEMA_ENTERPRISE):
        conn.execute(stmt)
    for table, column, ddl in MIGRATIONS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _rebuild_with_owner_key(conn, "targets", _table_ddl(SCHEMA, "targets"),
                            "id, yyyymm, owner, target_amount")
    _rebuild_with_owner_key(conn, "pipeline_snapshots", _table_ddl(SCHEMA_ENTERPRISE, "pipeline_snapshots"),
                            "id, snap_date, yyyymm, owner, category, deal_cnt, amount, weighted")
    for stmt in statements(SCHEMA_POST):
        conn.execute(stmt)
    conn.execute("UPDATE deals SET stage_since = COALESCE(stage_since, created_at) WHERE stage_since IS NULL")
    conn.execute("UPDATE deals SET list_amount = amount WHERE COALESCE(list_amount, 0) = 0")


def upgrade_pg(conn) -> None:
    """conn: psycopg DB-API 커넥션."""
    from schema_v2_pg import DDL
    conn.execute(DDL)

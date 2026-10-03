"""DB 구조 버전 관리 (Alembic과 같은 방식, 추가 라이브러리 없이 SQLite·PostgreSQL 공통).

- 변경 하나 = 리비전 파일 하나: migrations/versions/NNNN_설명.py
      revision = "0002"            # 이 리비전 번호
      down_revision = "0001"       # 바로 앞 리비전 (첫 리비전은 None)
      message = "설명"
      def upgrade(conn): ...       # 올리기 (core/migrate.py 의 도구 add_column·create_table 등을 쓰면 다시 돌려도 안전)
      def downgrade(conn): ...     # 되돌리기 (없으면 되돌릴 수 없는 리비전)
- DB 에는 지금 리비전(schema_version)과 올리고 내린 기록(schema_history)이 남는다.
- 앱이 뜰 때(db.init_db) 자동으로 최신(head)까지 올린다. 서버 여러 대가 동시에 떠도 PostgreSQL 잠금으로 한 대만 올린다.
- 명령: flask --app app db current | history | upgrade [리비전] | downgrade 리비전 | revision -m "설명" | stamp 리비전

0001 은 기준선(이 도구를 들이기 전까지 db.SCHEMA·MIGRATIONS 로 만든 구조)이다. 그 뒤의 구조 변경은 리비전으로만 한다.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from core.utils import now_str

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"

TEMPLATE = '''"""{message}"""
from core.migrate import add_column, create_index, create_table, drop_column, drop_table  # noqa: F401

revision = "{rev}"
down_revision = {down}
message = "{message}"


def upgrade(conn):
    pass


def downgrade(conn):
    pass
'''


class MigrationError(RuntimeError):
    pass


# ── 리비전 읽기 ──────────────────────────────────────────────
def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mm_migration_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ("revision", "down_revision", "upgrade"):
        if not hasattr(mod, attr):
            raise MigrationError(f"{path.name}: '{attr}'가 없습니다.")
    return mod


def chain() -> list[ModuleType]:
    """기준선부터 최신까지 순서대로. 갈래(같은 앞 리비전을 둔 두 파일)나 끊긴 고리는 오류."""
    mods = [_load(p) for p in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py"))]
    by_down: dict = {}
    for m in mods:
        if m.down_revision in by_down:
            raise MigrationError(f"리비전 {by_down[m.down_revision].revision}과 {m.revision}이 같은 앞 리비전"
                                 f"({m.down_revision})을 가집니다. 하나로 이어지게 고치세요.")
        by_down[m.down_revision] = m
    out, cur = [], None
    while cur in by_down:
        out.append(by_down[cur])
        cur = by_down[cur].revision
    if len(out) != len(mods):
        lost = {m.revision for m in mods} - {m.revision for m in out}
        raise MigrationError(f"이어지지 않는 리비전: {', '.join(sorted(lost))}")
    return out


def head() -> str | None:
    c = chain()
    return c[-1].revision if c else None


# ── DB 기록 ──────────────────────────────────────────────────
def _ensure(conn) -> None:
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS schema_version (version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schema_history (
            id {ID}, version TEXT NOT NULL, direction TEXT NOT NULL, message TEXT DEFAULT '', applied_at TEXT NOT NULL);
    """)


def current(conn) -> str | None:
    _ensure(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row[0] if row else None


def _set(conn, rev: str | None, direction: str, message: str) -> None:
    conn.execute("DELETE FROM schema_version")
    if rev is not None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (rev,))
    conn.execute("INSERT INTO schema_history (version, direction, message, applied_at) VALUES (?, ?, ?, ?)",
                 (rev or "(없음)", direction, message, now_str()))


def history(conn) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM schema_history ORDER BY id")]


def _commit(conn) -> None:
    """SQLite(초기화용 날 연결)는 리비전마다 확정한다. PostgreSQL은 init_db 의 한 트랜잭션."""
    if not conn.pg:
        conn.raw.commit()


def upgrade(conn, target: str = "head") -> list[str]:
    """지금 리비전 다음부터 target 까지 올린다. 올린 리비전 목록."""
    revs = chain()
    ids = [m.revision for m in revs]
    target = ids[-1] if target == "head" and ids else target
    if target not in ids:
        raise MigrationError(f"없는 리비전: {target}")
    cur = current(conn)
    if cur is not None and cur not in ids:
        raise MigrationError(f"DB의 리비전 {cur}이 이 프로그램에 없습니다 (더 새 버전의 DB이거나 파일이 빠짐).")
    start = ids.index(cur) + 1 if cur else 0
    end = ids.index(target) + 1
    done = []
    for m in revs[start:end]:
        m.upgrade(conn)
        _set(conn, m.revision, "upgrade", getattr(m, "message", ""))
        _commit(conn)
        done.append(m.revision)
    return done


def downgrade(conn, target: str) -> list[str]:
    """target 리비전 상태로 되돌린다 (target 다음 리비전들의 downgrade 를 거꾸로). 'base' = 기준선 이전(쓰지 말 것)."""
    revs = chain()
    ids = [m.revision for m in revs]
    cur = current(conn)
    if cur is None:
        return []
    if target not in ids:
        raise MigrationError(f"없는 리비전: {target}")
    stop = ids.index(target)
    done = []
    for m in reversed(revs[stop + 1: ids.index(cur) + 1]):
        if not hasattr(m, "downgrade"):
            raise MigrationError(f"리비전 {m.revision}은 되돌릴 수 없습니다.")
        m.downgrade(conn)
        prev = m.down_revision
        _set(conn, prev, "downgrade", getattr(m, "message", ""))
        _commit(conn)
        done.append(m.revision)
    return done


def stamp(conn, rev: str) -> None:
    """구조는 그대로 두고 리비전 기록만 맞춘다 (손으로 맞춘 DB를 이 도구에 맡길 때)."""
    if rev not in [m.revision for m in chain()]:
        raise MigrationError(f"없는 리비전: {rev}")
    _set(conn, rev, "stamp", "")
    _commit(conn)


def create(message: str) -> Path:
    """새 리비전 파일을 만든다 (번호는 다음 숫자, 앞 리비전은 지금 head)."""
    message = message.strip() or "변경"
    prev = head()
    nxt = f"{(int(prev) if prev else 0) + 1:04d}"
    slug = re.sub(r"[^0-9a-zA-Z가-힣]+", "_", message).strip("_")[:40] or "change"
    path = VERSIONS / f"{nxt}_{slug}.py"
    path.write_text(TEMPLATE.format(message=message.replace('"', "'"), rev=nxt,
                                    down=f'"{prev}"' if prev else "None"), encoding="utf-8")
    return path


# ── 리비전에서 쓰는 도구 (다시 돌려도 안전) ────────────────────
def _columns(conn, table: str) -> set[str]:
    from core import db
    return db._columns(conn, table)


def _schema(sql: str, conn) -> str:
    if conn.pg:
        return sql.replace("{ID}", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY").replace("{REAL}", "DOUBLE PRECISION")
    return sql.replace("{ID}", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("{REAL}", "REAL")


def create_table(conn, sql: str) -> None:
    """CREATE TABLE IF NOT EXISTS ... ({ID}, {REAL} 자리표시 사용)."""
    conn.executescript(_schema(sql, conn))


def add_column(conn, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.executescript(f"ALTER TABLE {table} ADD COLUMN {column} {_schema(ddl, conn)}")


def drop_column(conn, table: str, column: str) -> None:
    if column in _columns(conn, table):
        conn.executescript(f"ALTER TABLE {table} DROP COLUMN {column}")


def drop_table(conn, table: str) -> None:
    conn.executescript(f"DROP TABLE IF EXISTS {table}")


def create_index(conn, sql: str) -> None:
    conn.executescript(sql)

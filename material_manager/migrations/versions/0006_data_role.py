"""'데이터 관리' 역할(DATA) — users.role 의 허용 값에 DATA 를 더한다.

SQLite 는 CHECK 제약을 ALTER 로 바꿀 수 없어, SQLite 문서(ALTER TABLE 7절)의 방법대로 표 정의 글자만 바꾼다
(허용 값을 넓히는 것이라 기존 데이터·파일 구조는 그대로). PostgreSQL 은 제약을 지우고 다시 만든다.
"""
revision = "0006"
down_revision = "0005"
message = "사용자 역할에 '데이터 관리'(DATA) 추가"

OLD = "('VIEWER', 'CLERK', 'MANAGER', 'ADMIN')"
NEW = "('VIEWER', 'DATA', 'CLERK', 'MANAGER', 'ADMIN')"


def _sqlite_check(conn, old: str, new: str) -> None:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()[0]
    if old not in sql:
        return                                            # 이미 바뀜 (새로 만든 DB 등)
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute("UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = 'users'", (sql.replace(old, new),))
    conn.execute(f"PRAGMA schema_version = {int(version) + 1}")
    conn.execute("PRAGMA writable_schema = OFF")


def _pg_check(conn, values: str) -> None:
    rows = conn.execute("SELECT con.conname FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
                        "WHERE rel.relname = 'users' AND con.contype = 'c' "
                        "AND pg_get_constraintdef(con.oid) LIKE '%role%'").fetchall()
    for row in rows:
        conn.execute(f'ALTER TABLE users DROP CONSTRAINT "{row[0]}"')
    conn.execute(f"ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN {values})")


def upgrade(conn):
    if conn.pg:
        _pg_check(conn, NEW)
    else:
        _sqlite_check(conn, OLD, NEW)


def downgrade(conn):
    if conn.execute("SELECT 1 FROM users WHERE role = 'DATA' LIMIT 1").fetchone():
        raise RuntimeError("'데이터 관리' 역할 사용자가 있어 되돌릴 수 없습니다. 먼저 역할을 바꾸세요.")
    if conn.pg:
        _pg_check(conn, OLD)
    else:
        _sqlite_check(conn, NEW, OLD)

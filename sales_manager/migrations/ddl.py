"""마이그레이션용 DDL 도우미 — SQLite / PostgreSQL 차이를 한 곳에서 처리한다."""
from alembic import op


def is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def pk() -> str:
    return "BIGSERIAL PRIMARY KEY" if is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def big() -> str:
    """금액·외래키 (PostgreSQL 은 BIGINT)."""
    return "BIGINT" if is_pg() else "INTEGER"


def real() -> str:
    return "DOUBLE PRECISION" if is_pg() else "REAL"


def run(*statements: str) -> None:
    """SQL 을 그대로 실행한다 (SQLAlchemy 의 :이름 바인딩 해석을 거치지 않도록 드라이버로 직접)."""
    bind = op.get_bind()
    for sql in statements:
        sql = sql.strip()
        if sql:
            bind.exec_driver_sql(sql)


def add_columns(table: str, *columns: tuple[str, str]) -> None:
    for name, type_sql in columns:
        run(f"ALTER TABLE {table} ADD COLUMN {name} {type_sql}")


def drop_columns(table: str, *names: str) -> None:
    """downgrade 용. SQLite 3.35+ 와 PostgreSQL 모두 ALTER TABLE DROP COLUMN 을 지원한다."""
    for name in names:
        run(f"ALTER TABLE {table} DROP COLUMN {name}")

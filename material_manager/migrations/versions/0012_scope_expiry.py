"""데이터 범위에 기한·사유 (임시 범위: 휴직 대행·타 창고 지원이 기한이 지나면 자동으로 끝남)."""
from core.migrate import add_column, create_index

revision = "0012"
down_revision = "0011"
message = "user_scopes 기한·사유·등록자"


def upgrade(conn):
    add_column(conn, "user_scopes", "valid_to", "TEXT DEFAULT ''")          # '' = 기한 없음(기본 범위), 날짜 = 그날까지
    add_column(conn, "user_scopes", "reason", "TEXT DEFAULT ''")
    add_column(conn, "user_scopes", "created_by", "TEXT DEFAULT ''")
    add_column(conn, "user_scopes", "created_at", "TEXT DEFAULT ''")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_scope_valid ON user_scopes(valid_to)")


def downgrade(conn):
    conn.executescript("DROP INDEX IF EXISTS idx_scope_valid")
    for col in ("created_at", "created_by", "reason", "valid_to"):
        conn.executescript(f"ALTER TABLE user_scopes DROP COLUMN {col}")

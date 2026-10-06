"""감사로그 해시 체인(봉인 열) · 요청 번호 · 수정 방지 트리거 교체 · API 인증 실패 IP 표."""
from core.migrate import add_column, create_index, create_table, drop_table

revision = "0010"
down_revision = "0009"
message = "감사로그 해시 체인·요청 번호, API 인증 실패 IP별 차단"

# 봉인(seq·prev_hash·hash 채우기)만 허용하고, 그 밖의 수정은 모두 막는다. 이미 봉인된 기록(seq 있음)은 어떤 수정도 불가.
SQLITE_GUARD = """
DROP TRIGGER IF EXISTS audit_no_update;
DROP TRIGGER IF EXISTS audit_seal_only;
CREATE TRIGGER audit_seal_only BEFORE UPDATE ON audit_log
WHEN NOT (OLD.seq IS NULL AND NEW.seq IS NOT NULL
          AND NEW.id IS OLD.id AND NEW.at IS OLD.at AND NEW.user_id IS OLD.user_id AND NEW.user_name IS OLD.user_name
          AND NEW.action IS OLD.action AND NEW.entity IS OLD.entity AND NEW.entity_id IS OLD.entity_id
          AND NEW.detail IS OLD.detail AND NEW.ip IS OLD.ip AND NEW.request_id IS OLD.request_id)
BEGIN SELECT RAISE(ABORT, '감사로그는 수정할 수 없습니다 (봉인 번호·해시만 채울 수 있습니다)'); END;
"""
PG_GUARD = """
CREATE OR REPLACE FUNCTION mm_audit_seal_only() RETURNS trigger AS $$
BEGIN
  IF OLD.seq IS NULL AND NEW.seq IS NOT NULL
     AND NEW.id IS NOT DISTINCT FROM OLD.id AND NEW.at IS NOT DISTINCT FROM OLD.at
     AND NEW.user_id IS NOT DISTINCT FROM OLD.user_id AND NEW.user_name IS NOT DISTINCT FROM OLD.user_name
     AND NEW.action IS NOT DISTINCT FROM OLD.action AND NEW.entity IS NOT DISTINCT FROM OLD.entity
     AND NEW.entity_id IS NOT DISTINCT FROM OLD.entity_id AND NEW.detail IS NOT DISTINCT FROM OLD.detail
     AND NEW.ip IS NOT DISTINCT FROM OLD.ip AND NEW.request_id IS NOT DISTINCT FROM OLD.request_id THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '감사로그는 수정할 수 없습니다 (봉인 번호·해시만 채울 수 있습니다)';
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_no_change ON audit_log;
DROP TRIGGER IF EXISTS audit_seal_only ON audit_log;
CREATE TRIGGER audit_seal_only BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION mm_audit_seal_only();
"""


def upgrade(conn):
    add_column(conn, "audit_log", "request_id", "TEXT DEFAULT ''")
    add_column(conn, "audit_log", "seq", "INTEGER")
    add_column(conn, "audit_log", "prev_hash", "TEXT")
    add_column(conn, "audit_log", "hash", "TEXT")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_log(request_id) WHERE request_id <> ''")
    create_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_seq ON audit_log(seq) WHERE seq IS NOT NULL")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_audit_unsealed ON audit_log(id) WHERE seq IS NULL")
    conn.executescript(PG_GUARD if conn.pg else SQLITE_GUARD)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS api_auth_failures (
            ip           TEXT PRIMARY KEY,
            fails        INTEGER NOT NULL DEFAULT 0,
            window_start TEXT    NOT NULL,
            blocked_at   TEXT    DEFAULT ''
        )""")


def downgrade(conn):
    drop_table(conn, "api_auth_failures")
    if conn.pg:
        conn.executescript("""
            DROP TRIGGER IF EXISTS audit_seal_only ON audit_log;
            CREATE TRIGGER audit_no_change BEFORE UPDATE ON audit_log
                FOR EACH ROW EXECUTE FUNCTION mm_block_change('감사로그는 수정할 수 없습니다');""")
    else:
        conn.executescript("""
            DROP TRIGGER IF EXISTS audit_seal_only;
            CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, '감사로그는 수정할 수 없습니다'); END;""")
    conn.executescript("DROP INDEX IF EXISTS idx_audit_request; DROP INDEX IF EXISTS uq_audit_seq; "
                       "DROP INDEX IF EXISTS idx_audit_unsealed")

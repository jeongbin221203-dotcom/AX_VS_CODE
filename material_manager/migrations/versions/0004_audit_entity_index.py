"""변경 이력 탭(자재·거래처)이 감사로그를 대상별로 빨리 찾도록 색인."""
from core.migrate import create_index

revision = "0004"
down_revision = "0003"
message = "감사로그 대상(entity, entity_id) 색인 — 변경 이력"


def upgrade(conn):
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id, id)")


def downgrade(conn):
    conn.executescript("DROP INDEX IF EXISTS idx_audit_entity")

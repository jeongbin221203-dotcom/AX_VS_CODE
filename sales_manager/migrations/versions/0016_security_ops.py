"""보안·운영 보강 (자재관리와 비교해 추가, 2026-10-03)

  users.session_version : 비밀번호 변경·로그아웃·관리자 강제 종료 때 1 올림 → 다른 기기·복사된 쿠키의 세션도 끊김
  users.messenger_id    : 메신저 개인 메시지 ID (네이버웍스 ID 등이 이메일과 다를 때)
  login_ip_failures     : IP 단위 로그인 실패 (15분 동안 20번 넘으면 그 IP 차단)
  customer_aliases      : 거래처 다른 이름 ('대한상사(주)' → 대한상사) — 엑셀 업로드·ERP 수신에서 이름 맞추기
  unknown_names         : 마스터에 없어 맞추지 못한 거래처 이름 (거래처 > 이름 정리)

Revision ID: 0016_security_ops
Revises: 0015_bulk_indexes
Create Date: 2026-10-03
"""
from ddl import add_columns, big, drop_columns, pk, run

revision = "0016_security_ops"
down_revision = "0015_bulk_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("users", ("session_version", "INTEGER DEFAULT 0"), ("messenger_id", "TEXT"))
    run(f"""
    CREATE TABLE login_ip_failures (
        id      {pk()},
        ip      TEXT NOT NULL,
        at      TEXT NOT NULL
    )""", "CREATE INDEX idx_login_ip ON login_ip_failures(ip, at)", f"""
    CREATE TABLE customer_aliases (
        id          {pk()},
        customer_id {big()} NOT NULL,
        alias       TEXT NOT NULL,
        alias_key   TEXT NOT NULL,
        created_by  TEXT,
        created_at  TEXT NOT NULL
    )""", "CREATE UNIQUE INDEX ux_customer_alias_key ON customer_aliases(alias_key)",
        "CREATE INDEX idx_customer_alias_cust ON customer_aliases(customer_id)", f"""
    CREATE TABLE unknown_names (
        id          {pk()},
        name        TEXT NOT NULL,
        name_key    TEXT NOT NULL,
        source      TEXT NOT NULL,
        seen        INTEGER NOT NULL DEFAULT 1,
        first_seen  TEXT NOT NULL,
        last_seen   TEXT NOT NULL,
        resolved_customer_id {big()},
        resolved_at TEXT
    )""", "CREATE UNIQUE INDEX ux_unknown_name ON unknown_names(name_key, source)")


def downgrade() -> None:
    run("DROP TABLE unknown_names", "DROP TABLE customer_aliases", "DROP INDEX idx_login_ip", "DROP TABLE login_ip_failures")
    drop_columns("users", "session_version", "messenger_id")

"""인사(HR) 연동 · 외부 연동 API

  orgs  : code(인사 조직코드) · active · hr_synced_at
  users : source(local|hr) · position(직위) · hr_synced_at
  api_clients     : 외부 시스템용 API 키 (해시만 저장) · 권한 범위 · 분당 호출 한도 · 대리 사용자
  api_usage       : 분 단위 호출 수 (여러 서버가 같은 한도를 공유)
  api_idempotency : 같은 Idempotency-Key 로 다시 보낸 쓰기 요청은 처음 결과를 돌려준다

Revision ID: 0005_hr_api
Revises: 0004_approval_steps
Create Date: 2026-09-27
"""
from ddl import add_columns, drop_columns, big, pk, run

revision = "0005_hr_api"
down_revision = "0004_approval_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("orgs", ("code", "TEXT"), ("active", "INTEGER DEFAULT 1"), ("hr_synced_at", "TEXT"))
    add_columns("users", ("source", "TEXT DEFAULT 'local'"), ("position", "TEXT"), ("hr_synced_at", "TEXT"))
    run("CREATE UNIQUE INDEX idx_orgs_code ON orgs(code)",
        f"""
    CREATE TABLE api_clients (
        id           {pk()},
        name         TEXT NOT NULL UNIQUE,
        key_prefix   TEXT NOT NULL UNIQUE,
        key_hash     TEXT NOT NULL,
        scopes       TEXT NOT NULL,
        user_id      {big()} NOT NULL REFERENCES users(id),
        rate_limit   INTEGER NOT NULL DEFAULT 120,
        allowed_ips  TEXT,
        created_by   TEXT,
        created_at   TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at   TEXT
    )""",
        """
    CREATE TABLE api_usage (
        client_id  BIGINT NOT NULL,
        win        TEXT NOT NULL,
        hits       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (client_id, win)
    )""",
        f"""
    CREATE TABLE api_idempotency (
        client_id  {big()} NOT NULL,
        idem_key   TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        status     INTEGER NOT NULL,
        response   TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (client_id, idem_key)
    )""")


def downgrade() -> None:
    run("DROP TABLE api_idempotency", "DROP TABLE api_usage", "DROP TABLE api_clients",
        "DROP INDEX idx_orgs_code")
    drop_columns("orgs", "code", "active", "hr_synced_at")
    drop_columns("users", "source", "position", "hr_synced_at")

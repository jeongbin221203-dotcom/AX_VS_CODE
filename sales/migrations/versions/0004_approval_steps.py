"""다단계 결재선 · 대결(위임)

기존 결재 건은 필요권한 한 단계짜리 결재선으로 옮긴다.

Revision ID: 0004_approval_steps
Revises: 0003_catalog_quotes_vat
Create Date: 2026-09-27
"""
from ddl import add_columns, drop_columns, big, pk, run

revision = "0004_approval_steps"
down_revision = "0003_catalog_quotes_vat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE approval_steps (
        id           {pk()},
        approval_id  {big()} NOT NULL REFERENCES approvals(id) ON DELETE CASCADE,
        step_no      INTEGER NOT NULL,
        role         TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT '예정',
        approver     TEXT,
        approver_id  {big()},
        acted_for_id {big()},
        comment      TEXT,
        activated_at TEXT,
        due_at       TEXT,
        escalated_at TEXT,
        decided_at   TEXT,
        UNIQUE (approval_id, step_no)
    )""",
        "CREATE INDEX idx_steps_status ON approval_steps(status, due_at)",
        f"""
    CREATE TABLE delegations (
        id           {pk()},
        from_user_id {big()} NOT NULL REFERENCES users(id),
        to_user_id   {big()} NOT NULL REFERENCES users(id),
        start_date   TEXT NOT NULL,
        end_date     TEXT NOT NULL,
        reason       TEXT,
        created_by   TEXT,
        created_at   TEXT NOT NULL,
        revoked_at   TEXT
    )""",
        "CREATE INDEX idx_deleg_from ON delegations(from_user_id, start_date, end_date)")
    add_columns("approvals", ("current_step", "INTEGER DEFAULT 1"))
    # 기존 결재: 필요권한 한 단계
    run("""
    INSERT INTO approval_steps (approval_id, step_no, role, status, approver, approver_id, comment,
                                activated_at, decided_at)
    SELECT id, 1, COALESCE(required_role, 'MANAGER'),
           CASE status WHEN '대기' THEN '대기' WHEN '승인' THEN '승인' WHEN '반려' THEN '반려' ELSE '취소' END,
           approver, approver_id, comment, requested_at, decided_at
      FROM approvals""")


def downgrade() -> None:
    run("DROP TABLE delegations", "DROP TABLE approval_steps")
    drop_columns("approvals", "current_step")

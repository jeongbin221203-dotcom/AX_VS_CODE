"""데이터 안전 — 매출 동시 수정 충돌 방지(row_version) · 같은 폼 중복 제출 방지(form_submissions)

Revision ID: 0008_data_safety
Revises: 0007_company_settings
Create Date: 2026-10-02
"""
from ddl import add_columns, drop_columns, run

revision = "0008_data_safety"
down_revision = "0007_company_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("sales", ("row_version", "INTEGER DEFAULT 0"))
    run("UPDATE sales SET row_version = 0 WHERE row_version IS NULL",
        """
    CREATE TABLE form_submissions (
        submit_id  TEXT PRIMARY KEY,
        user_id    BIGINT,
        endpoint   TEXT,
        created_at TEXT NOT NULL
    )""", "CREATE INDEX idx_form_sub_created ON form_submissions(created_at)")


def downgrade() -> None:
    run("DROP TABLE form_submissions")
    drop_columns("sales", "row_version")

"""오래 걸리는 업로드·내려받기를 웹 요청 밖에서 — 진행률·결과·오류 목록·만든 파일을 남긴다 (core/bulk.py)

Revision ID: 0021_bulk_tasks
Revises: 0020_bad_debt
Create Date: 2026-10-04
"""
from ddl import big, pk, run

revision = "0021_bulk_tasks"
down_revision = "0020_bad_debt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE bulk_tasks (
        id            {pk()},
        kind          TEXT NOT NULL,
        title         TEXT NOT NULL,
        status        TEXT NOT NULL,
        total         {big()} DEFAULT 0,
        done          {big()} DEFAULT 0,
        ok            {big()} DEFAULT 0,
        skipped       {big()} DEFAULT 0,
        error_count   {big()} DEFAULT 0,
        errors        TEXT,
        message       TEXT,
        file_key      TEXT,
        file_name     TEXT,
        pii           INTEGER DEFAULT 0,
        server        TEXT,
        created_by    TEXT,
        created_by_id {big()},
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        finished_at   TEXT
    )""", "CREATE INDEX idx_bulk_user ON bulk_tasks(created_by_id, id)")


def downgrade() -> None:
    run("DROP TABLE bulk_tasks")

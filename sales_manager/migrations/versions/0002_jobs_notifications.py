"""작업 큐 · 스케줄러 상태 · 알림

Revision ID: 0002_jobs_notifications
Revises: 0001_baseline
Create Date: 2026-09-27
"""
from ddl import add_columns, drop_columns, big, pk, run

revision = "0002_jobs_notifications"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE jobs (
        id           {pk()},
        kind         TEXT NOT NULL,
        payload      TEXT,
        status       TEXT NOT NULL DEFAULT '대기',
        attempts     INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 5,
        run_after    TEXT NOT NULL,
        dedupe_key   TEXT UNIQUE,
        locked_by    TEXT,
        locked_at    TEXT,
        last_error   TEXT,
        result       TEXT,
        created_at   TEXT NOT NULL,
        started_at   TEXT,
        finished_at  TEXT
    )""",
        "CREATE INDEX idx_jobs_ready ON jobs(status, run_after)",
        """
    CREATE TABLE scheduler_state (
        name        TEXT PRIMARY KEY,
        last_slot   TEXT,
        last_run_at TEXT
    )""",
        f"""
    CREATE TABLE notifications (
        id           {pk()},
        user_id      {big()} NOT NULL,
        kind         TEXT NOT NULL,
        title        TEXT NOT NULL,
        body         TEXT,
        link         TEXT,
        created_at   TEXT NOT NULL,
        read_at      TEXT,
        email_status TEXT,
        sent_at      TEXT
    )""",
        "CREATE INDEX idx_notif_user ON notifications(user_id, read_at)")
    add_columns("users", ("notify_email", "INTEGER DEFAULT 1"))


def downgrade() -> None:
    run("DROP TABLE notifications", "DROP TABLE scheduler_state", "DROP TABLE jobs")
    drop_columns("users", "notify_email")

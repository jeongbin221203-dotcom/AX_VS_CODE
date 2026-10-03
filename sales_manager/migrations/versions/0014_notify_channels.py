"""사내 메신저 알림 채널 (잔디 · 네이버웍스 · 카카오워크 · Slack · Teams · 웹훅, core/messenger.py)

  notify_channels : 채널 설정 (비밀값은 config 안에 암호화) · 보낼 알림 종류 · 사용 여부
  notify_log      : 채널별 발송 기록 — (channel_id, notification_id, target) 성공 기록이 있으면 재시도 때 다시 보내지 않는다
  users.notify_messenger : 사용자별 메신저 개인 메시지 받기 (메일의 notify_email 과 같은 방식)

Revision ID: 0014_notify_channels
Revises: 0013_user_prefs
Create Date: 2026-10-03
"""
from ddl import add_columns, big, drop_columns, pk, run

revision = "0014_notify_channels"
down_revision = "0013_user_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE notify_channels (
        id          {pk()},
        kind        TEXT NOT NULL,
        name        TEXT NOT NULL,
        config      TEXT NOT NULL DEFAULT '{{}}',
        kinds       TEXT NOT NULL DEFAULT '[]',
        active      INTEGER NOT NULL DEFAULT 1,
        created_by  TEXT,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""", f"""
    CREATE TABLE notify_log (
        id              {pk()},
        channel_id      {big()} NOT NULL,
        notification_id {big()},
        target          TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL,
        error           TEXT,
        sent_at         TEXT NOT NULL
    )""", "CREATE INDEX idx_notify_log_channel ON notify_log(channel_id, notification_id)")
    add_columns("users", ("notify_messenger", "INTEGER DEFAULT 1"))


def downgrade() -> None:
    drop_columns("users", "notify_messenger")
    run("DROP INDEX idx_notify_log_channel", "DROP TABLE notify_log", "DROP TABLE notify_channels")

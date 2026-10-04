"""알림 채널(사내 메신저)을 화면에서 관리 — 잔디 · 네이버웍스 · 카카오워크 · Slack · Teams · 웹훅."""
from core.migrate import add_column, create_table, drop_column, drop_table

revision = "0005"
down_revision = "0004"
message = "알림 채널(메신저) 관리 표 · 알림에 채널·알림 종류"


def upgrade(conn):
    # 비밀값(웹훅 주소·토큰·키)은 config 에 암호화해서 둔다 (core/messenger.py)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS messenger_channels (
            id          {ID},
            kind        TEXT    NOT NULL,
            name        TEXT    NOT NULL,
            config      TEXT    NOT NULL DEFAULT '{}',
            events      TEXT    NOT NULL DEFAULT '',
            active      INTEGER DEFAULT 1,
            created_by  TEXT    DEFAULT '',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )""")
    add_column(conn, "notifications", "channel_id", "INTEGER")
    add_column(conn, "notifications", "event", "TEXT DEFAULT ''")


def downgrade(conn):
    drop_table(conn, "messenger_channels")
    for col in ("channel_id", "event"):
        drop_column(conn, "notifications", col)

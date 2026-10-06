"""백그라운드 작업(대용량 업로드 반영·큰 엑셀 추출) 기록 · 월말 미지급(GR/IR) 스냅샷."""
from core.migrate import create_index, create_table, drop_table

revision = "0011"
down_revision = "0010"
message = "백그라운드 작업 표, 월말 미지급·GR/IR 스냅샷"


def upgrade(conn):
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS bg_tasks (
            id            {ID},
            kind          TEXT    NOT NULL,
            title         TEXT    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'QUEUED',
            total         INTEGER NOT NULL DEFAULT 0,
            done          INTEGER NOT NULL DEFAULT 0,
            message       TEXT    DEFAULT '',
            result_key    TEXT    DEFAULT '',
            result_name   TEXT    DEFAULT '',
            result_mime   TEXT    DEFAULT '',
            user_id       INTEGER,
            user_name     TEXT    DEFAULT '',
            request_id    TEXT    DEFAULT '',
            created_at    TEXT    NOT NULL,
            started_at    TEXT    DEFAULT '',
            heartbeat_at  TEXT    DEFAULT '',
            finished_at   TEXT    DEFAULT ''
        )""")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_bg_tasks_user ON bg_tasks(user_id, id)")
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS ap_snapshots (
            id             {ID},
            ym             TEXT    NOT NULL,
            po_no          TEXT    NOT NULL,
            supplier       TEXT    DEFAULT '',
            warehouse_id   INTEGER,
            ordered        {REAL}  DEFAULT 0,
            received       {REAL}  DEFAULT 0,
            invoiced       {REAL}  DEFAULT 0,
            gr_ir          {REAL}  DEFAULT 0,
            payment_status TEXT    DEFAULT '',
            backfilled     INTEGER DEFAULT 0,
            created_at     TEXT    NOT NULL
        )""")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_ap_snap_ym ON ap_snapshots(ym)")


def downgrade(conn):
    drop_table(conn, "ap_snapshots")
    drop_table(conn, "bg_tasks")

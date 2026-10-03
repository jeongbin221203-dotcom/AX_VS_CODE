"""알림함 · 대결(결재 위임) · 거래처 병합 · API 키."""
from core.migrate import add_column, create_index, create_table, drop_column, drop_table

revision = "0003"
down_revision = "0002"
message = "알림함(읽음·링크) · 대결 지정 · 결재 독촉 · 거래처 병합 · API 키"


def upgrade(conn):
    # 알림함: channel = 'inbox' 인 줄이 화면 알림. 읽은 시각·바로 가기 주소
    add_column(conn, "notifications", "read_at", "TEXT DEFAULT ''")
    add_column(conn, "notifications", "link", "TEXT DEFAULT ''")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_notify_inbox ON notifications(to_user_id, channel, read_at)")

    # 대결(결재 위임): 기간 동안 to_user 가 from_user 대신 결재한다 (휴가·출장)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS approval_delegations (
            id            {ID},
            from_user_id  INTEGER NOT NULL REFERENCES users(id),
            to_user_id    INTEGER NOT NULL REFERENCES users(id),
            start_date    TEXT    NOT NULL,
            end_date      TEXT    NOT NULL,
            reason        TEXT    DEFAULT '',
            active        INTEGER DEFAULT 1,
            created_by    TEXT    DEFAULT '',
            created_at    TEXT    NOT NULL
        )""")
    # 결재 독촉: 같은 결재를 하루에 한 번만 다시 알린다
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS approval_reminders (
            ref       TEXT PRIMARY KEY,
            sent_at   TEXT NOT NULL,
            count     INTEGER DEFAULT 1
        )""")

    # 거래처 병합: 중복 거래처를 하나로 — 병합된 쪽은 사용중지 + merged_into
    add_column(conn, "partners", "merged_into", "INTEGER")

    # 외부 연동 API 키 (MES·WMS 등이 재고 조회·입출고 등록). 키는 해시만 저장한다.
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS api_keys (
            id            {ID},
            name          TEXT    NOT NULL,
            prefix        TEXT    NOT NULL UNIQUE,      -- 키 앞 8자 (목록에서 알아보기)
            key_hash      TEXT    NOT NULL,
            scopes        TEXT    NOT NULL,             -- 쉼표: stock:read, materials:read, transactions:write
            allowed_ips   TEXT    DEFAULT '',           -- 쉼표, 비우면 어디서나
            warehouse_ids TEXT    DEFAULT '',           -- 쉼표, 비우면 모든 창고
            active        INTEGER DEFAULT 1,
            last_used_at  TEXT    DEFAULT '',
            created_by    TEXT    DEFAULT '',
            created_at    TEXT    NOT NULL,
            revoked_at    TEXT    DEFAULT ''
        )""")


def downgrade(conn):
    for t in ("api_keys", "approval_reminders", "approval_delegations"):
        drop_table(conn, t)
    conn.executescript("DROP INDEX IF EXISTS idx_notify_inbox")
    for table, col in (("notifications", "read_at"), ("notifications", "link"), ("partners", "merged_into")):
        drop_column(conn, table, col)

"""자재관리와 맞춘 통제 (2026-10-06)

  fin_requests.payment_id : 입금 반제 요청 (결재 종류 '입금반제') — 매출 취소 요청은 '매출취소' (sale_id)
  products.erp_synced_at : ERP 마스터로 받은 품목 잠금
  products/users/orgs.row_version : 동시 수정 충돌 검사 (나중에 저장한 사람이 앞사람 수정을 덮어쓰지 않게)
  user_scopes : 역할·조직 범위 밖 데이터를 추가로 볼 수 있게 한 예외 (공동 담당·다른 팀 지원) — kind owner|org

Revision ID: 0023_mm_parity2
Revises: 0022_ar_net_advance
Create Date: 2026-10-06
"""
from ddl import add_columns, big, drop_columns, pk, run

revision = "0023_mm_parity2"
down_revision = "0022_ar_net_advance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("fin_requests", ("payment_id", big()))
    for table in ("products", "users", "orgs"):
        add_columns(table, ("row_version", "INTEGER DEFAULT 0"))
    add_columns("products", ("erp_synced_at", "TEXT"))       # ERP 에서 받은 품목 — 화면에서 코드·이름·정가·과세 못 고침
    run(f"""
    CREATE TABLE user_scopes (
        id          {pk()},
        user_id     {big()} NOT NULL,
        kind        TEXT NOT NULL,
        target_id   {big()} NOT NULL,
        reason      TEXT,
        valid_to    TEXT,
        created_by  TEXT,
        created_at  TEXT NOT NULL
    )""", "CREATE UNIQUE INDEX ux_user_scope ON user_scopes(user_id, kind, target_id)")


def downgrade() -> None:
    run("DROP TABLE user_scopes")
    drop_columns("products", "erp_synced_at")
    for table in ("products", "users", "orgs"):
        drop_columns(table, "row_version")
    drop_columns("fin_requests", "payment_id")

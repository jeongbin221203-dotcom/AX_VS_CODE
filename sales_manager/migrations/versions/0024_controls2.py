"""자재관리와 맞춘 통제 2차 (2026-10-06)

  attachments.uploaded_by_id    : 첨부를 올린 사용자 — 본인이 올린 첨부는 본인이 무효 처리하지 못하게 (직무 분리)
  sales_period_closes.checks    : 월 마감 때의 점검 결과(JSON) — 마감 기록과 함께 남긴다

Revision ID: 0024_controls2
Revises: 0023_mm_parity2
Create Date: 2026-10-06
"""
from ddl import add_columns, big, drop_columns

revision = "0024_controls2"
down_revision = "0023_mm_parity2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("attachments", ("uploaded_by_id", big()))
    add_columns("sales_period_closes", ("checks", "TEXT"))


def downgrade() -> None:
    drop_columns("sales_period_closes", "checks")
    drop_columns("attachments", "uploaded_by_id")

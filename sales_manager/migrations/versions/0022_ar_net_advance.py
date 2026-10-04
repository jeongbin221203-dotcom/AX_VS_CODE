"""월말 채권 스냅샷에 선수금 잔액 — 순채권 = 미수 잔액 − 선수금(돌려줄 돈 포함)

Revision ID: 0022_ar_net_advance
Revises: 0021_bulk_tasks
Create Date: 2026-10-05
"""
from ddl import add_columns, big, drop_columns

revision = "0022_ar_net_advance"
down_revision = "0021_bulk_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("ar_snapshots", ("advance", f"{big()} DEFAULT 0"))


def downgrade() -> None:
    drop_columns("ar_snapshots", "advance")

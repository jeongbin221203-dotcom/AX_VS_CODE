"""입금 반제는 한 번만 — 두 사람이 동시에 같은 입금을 반제해도 DB 가 하나만 받는다 (코드 검토에서 찾은 경쟁 조건)

Revision ID: 0017_payment_reversal_unique
Revises: 0016_security_ops
Create Date: 2026-10-04
"""
from ddl import run

revision = "0017_payment_reversal_unique"
down_revision = "0016_security_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("CREATE UNIQUE INDEX ux_payments_reversal ON payments(reversal_of) WHERE reversal_of IS NOT NULL")


def downgrade() -> None:
    run("DROP INDEX ux_payments_reversal")

"""대손: 법정 사유 · 확정일 · 대손세액 · 회수

  fin_requests.wo_reason_code  : 대손 사유 코드 (credit.WRITEOFF_REASONS)
  fin_requests.wo_event_date   : 사유가 생긴 날 (파산 선고일·부도일·소멸시효 완성일 등)
  fin_requests.bad_debt_vat    : 대손세액 (과세 매출이면 대손금액 × 10/110 — 부가가치세 대손세액공제)
  fin_requests.recovered_amount: 대손 뒤 회수한 금액 (회수하면 대손세액을 다시 납부세액에 더한다)

Revision ID: 0020_bad_debt
Revises: 0019_etax_modify
Create Date: 2026-10-04
"""
from ddl import add_columns, big, drop_columns

revision = "0020_bad_debt"
down_revision = "0019_etax_modify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("fin_requests", ("wo_reason_code", "TEXT"), ("wo_event_date", "TEXT"), ("bad_debt_vat", big()),
                ("recovered_amount", f"{big()} DEFAULT 0"))


def downgrade() -> None:
    drop_columns("fin_requests", "wo_reason_code", "wo_event_date", "bad_debt_vat", "recovered_amount")

"""수정세금계산서(사유 01 기재사항 착오 · 04 계약 해제 · 05 내국신용장 사후개설 · 06 착오 이중발급) 발행

  etax_invoices.modify_code      : 수정 사유 코드 (NULL = 당초 발행)
  etax_invoices.original_etax_id : 고치는 당초 발행 요청
  etax_invoices.supply_amount / vat_amount : 수정분 금액 (당초분을 취소하는 음수)
  etax_invoices.note             : 수정 사유 설명
  ux_etax_active 는 당초 발행에만 적용 (수정분은 같은 매출에 따로 생긴다)

Revision ID: 0019_etax_modify
Revises: 0018_etax_lease
Create Date: 2026-10-04
"""
from ddl import add_columns, big, drop_columns, run

revision = "0019_etax_modify"
down_revision = "0018_etax_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("etax_invoices", ("modify_code", "TEXT"), ("original_etax_id", big()), ("supply_amount", big()),
                ("vat_amount", big()), ("note", "TEXT"))
    run("DROP INDEX ux_etax_active",
        "CREATE UNIQUE INDEX ux_etax_active ON etax_invoices(sale_id) "
        "WHERE status IN ('발행요청','전송중','발행완료') AND modify_code IS NULL",
        "CREATE INDEX idx_etax_original ON etax_invoices(original_etax_id)")


def downgrade() -> None:
    run("DROP INDEX idx_etax_original", "DROP INDEX ux_etax_active",
        "CREATE UNIQUE INDEX ux_etax_active ON etax_invoices(sale_id) WHERE status IN ('발행요청','전송중','발행완료')")
    drop_columns("etax_invoices", "modify_code", "original_etax_id", "supply_amount", "vat_amount", "note")

"""대량 데이터 인덱스 (거래처 1만 · 매출 10만 건 측정에서 찾은 것)

  sales(customer_id)   거래처 목록의 누적매출 · 거래처별 매출·원장 · 매출 수정 화면 권한 확인
  sales(deal_id)       수주인데 매출 없음(데이터 점검) · 영업기회별 매출
  (sales(due_date) 는 넣지 않는다 — '오늘 이전'은 거의 모든 행이라 인덱스를 타면 오히려 느림, 측정 0.40초 vs 전체 읽기)

Revision ID: 0015_bulk_indexes
Revises: 0014_notify_channels
Create Date: 2026-10-03
"""
from ddl import run

revision = "0015_bulk_indexes"
down_revision = "0014_notify_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("CREATE INDEX idx_sale_customer ON sales(customer_id)",
        "CREATE INDEX idx_sale_deal ON sales(deal_id)")


def downgrade() -> None:
    run("DROP INDEX idx_sale_deal", "DROP INDEX idx_sale_customer")

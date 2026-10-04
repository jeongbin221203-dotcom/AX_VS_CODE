"""전자세금계산서 발행: 한 매출에 진행 중·완료 발행은 하나만 + 전송 도중 멈춘 요청 되살리기

  ux_etax_active : 같은 매출에 '발행요청·전송중·발행완료' 가 둘 이상 생기지 않게 (두 사람이 동시에 발행 요청)
  claimed_at     : 워커가 전송을 시작한 시각 — 오래 '전송중' 이면 서버가 도중에 꺼진 것
  sent_at        : 파일 방식에서 ASP 로 넘긴 시각 — 이 뒤로는 승인번호 회신을 기다리는 정상 상태

Revision ID: 0018_etax_lease
Revises: 0017_payment_reversal_unique
Create Date: 2026-10-04
"""
from ddl import add_columns, drop_columns, run

revision = "0018_etax_lease"
down_revision = "0017_payment_reversal_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("etax_invoices", ("claimed_at", "TEXT"), ("sent_at", "TEXT"))
    # 이미 겹쳐 있으면 가장 먼저 만든 것만 남기고 나머지는 실패로 (고유 인덱스를 만들 수 있게)
    run("UPDATE etax_invoices SET status='실패', error='같은 매출의 중복 발행 요청 — 정리됨' "
        "WHERE status IN ('발행요청','전송중','발행완료') AND id NOT IN ("
        "SELECT MIN(id) FROM etax_invoices WHERE status IN ('발행요청','전송중','발행완료') GROUP BY sale_id)",
        "CREATE UNIQUE INDEX ux_etax_active ON etax_invoices(sale_id) "
        "WHERE status IN ('발행요청','전송중','발행완료')")


def downgrade() -> None:
    run("DROP INDEX ux_etax_active")
    drop_columns("etax_invoices", "claimed_at", "sent_at")

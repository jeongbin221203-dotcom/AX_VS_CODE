"""자재관리와 맞춘 데이터 — 입금 내역 · 월 마감 · 월말 채권 스냅샷 · ERP 거래처 마스터 · 품목 규격 · 매출 등록자

  payments            : 입금 한 건 한 건 (입금일·방법·참조번호·출처). 반제는 음수 행 + reversal_of.
                        sales.paid_amount 는 이 합계를 담아 두는 값(빠른 조회용).
  sales_period_closes : 매출 월 마감 이력 (CLOSE / REOPEN). 마감월까지의 매출·입금·취소는 막는다.
  ar_snapshots        : 마감 시점 거래처별 월말 채권 (매출 누계·입금 누계·잔액·연체)
  customers           : erp_synced_at(ERP 가 원본인 항목 잠금), trade_blocked(거래정지)
  products            : spec(규격)
  sales               : created_by_id(등록자 — 본인 매출 취소 제한)

Revision ID: 0011_ledger_close
Revises: 0010_drop_mfa
Create Date: 2026-10-02
"""
from ddl import add_columns, big, drop_columns, pk, run

revision = "0011_ledger_close"
down_revision = "0010_drop_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE payments (
        id          {pk()},
        sale_id     {big()} NOT NULL REFERENCES sales(id),
        pay_date    TEXT NOT NULL,
        amount      {big()} NOT NULL,
        method      TEXT,
        source      TEXT,
        ref_no      TEXT,
        memo        TEXT,
        reversal_of {big()},
        created_by  TEXT,
        created_by_id {big()},
        created_at  TEXT NOT NULL
    )""", "CREATE INDEX idx_pay_sale ON payments(sale_id)", "CREATE INDEX idx_pay_date ON payments(pay_date)",
        f"""
    CREATE TABLE sales_period_closes (
        id             {pk()},
        closed_through TEXT NOT NULL,
        action         TEXT NOT NULL,
        reason         TEXT,
        actor          TEXT,
        at             TEXT NOT NULL
    )""", f"""
    CREATE TABLE ar_snapshots (
        ym          TEXT NOT NULL,
        customer_id {big()} NOT NULL,
        sales_total {big()} NOT NULL,
        paid_total  {big()} NOT NULL,
        balance     {big()} NOT NULL,
        overdue     {big()} NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (ym, customer_id)
    )""")
    add_columns("customers", ("erp_synced_at", "TEXT"), ("trade_blocked", "INTEGER DEFAULT 0"))
    add_columns("products", ("spec", "TEXT"))
    add_columns("sales", ("created_by_id", big()))
    # 기존 누적 입금액은 한 건의 '이관' 입금으로 옮긴다 (입금일을 모르므로 결제기일, 없으면 매출일)
    run("INSERT INTO payments (sale_id, pay_date, amount, method, source, memo, created_by, created_at) "
        "SELECT id, COALESCE(due_date, sale_date), paid_amount, '이관', '이관', '입금 내역 도입 전 누적 입금액', "
        "'system', created_at FROM sales WHERE COALESCE(paid_amount, 0) > 0",
        "UPDATE customers SET trade_blocked = 0 WHERE trade_blocked IS NULL")


def downgrade() -> None:
    drop_columns("sales", "created_by_id")
    drop_columns("products", "spec")
    drop_columns("customers", "erp_synced_at", "trade_blocked")
    run("DROP TABLE ar_snapshots", "DROP TABLE sales_period_closes", "DROP TABLE payments")

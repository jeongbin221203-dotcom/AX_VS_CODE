"""표준원가·차이 · 노무비/경비 배부 · SAP 생산오더 · 오더 정산 · 발주 납기일 · 작업 달력 · 3자 대조 지급 보류 · 마감 점검 기록."""
from core.migrate import add_column, create_table, drop_column, drop_table

revision = "0008"
down_revision = "0007"
message = "원가(표준원가·노무/경비·정산) · 발주 납기일·지급 보류 · 작업 달력 · 마감 점검"


def upgrade(conn):
    # 표준원가: 자재마다 하나 (구매품 = 표준 매입단가, 제품 = BOM 롤업 재료 + 표준시간 × 임률·배부율)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS standard_costs (
            material_id  INTEGER PRIMARY KEY REFERENCES materials(id),
            material     {REAL}  DEFAULT 0,
            labor        {REAL}  DEFAULT 0,
            overhead     {REAL}  DEFAULT 0,
            total        {REAL}  DEFAULT 0,
            minutes      {REAL}  DEFAULT 0,
            basis        TEXT    DEFAULT '',
            set_by       TEXT    DEFAULT '',
            set_at       TEXT    NOT NULL
        )""")
    # 작업지시: 노무비·경비(실제 작업시간 × 임률·배부율), SAP 생산오더 번호, 오더 정산
    add_column(conn, "productions", "labor_cost", "{REAL} DEFAULT 0")
    add_column(conn, "productions", "overhead_cost", "{REAL} DEFAULT 0")
    add_column(conn, "productions", "sap_order_no", "TEXT DEFAULT ''")
    add_column(conn, "productions", "settled_at", "TEXT DEFAULT ''")
    add_column(conn, "productions", "settled_by", "TEXT DEFAULT ''")
    add_column(conn, "productions", "settlement", "TEXT DEFAULT ''")
    # 발주: 납기일(MRP 공급일), 3자 대조 지급 상태 (''=대조 전 · MATCHED · BLOCKED · RELEASED)
    add_column(conn, "purchase_orders", "delivery_date", "TEXT DEFAULT ''")
    add_column(conn, "purchase_orders", "payment_status", "TEXT DEFAULT ''")
    add_column(conn, "purchase_orders", "payment_note", "TEXT DEFAULT ''")
    add_column(conn, "purchase_orders", "payment_checked_at", "TEXT DEFAULT ''")
    # 작업 달력: 회사 휴일 (주말은 회사 설정)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS work_calendar (
            day         TEXT PRIMARY KEY,
            name        TEXT DEFAULT '',
            created_by  TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        )""")
    # 마감 점검 결과 (재공품 · GR/IR · 음수 재고) — 마감할 때 함께 남긴다
    add_column(conn, "period_closes", "checks", "TEXT DEFAULT ''")


def downgrade(conn):
    drop_table(conn, "standard_costs")
    drop_table(conn, "work_calendar")
    for table, cols in (("productions", ("labor_cost", "overhead_cost", "sap_order_no", "settled_at", "settled_by", "settlement")),
                        ("purchase_orders", ("delivery_date", "payment_status", "payment_note", "payment_checked_at")),
                        ("period_closes", ("checks",))):
        for c in cols:
            drop_column(conn, table, c)

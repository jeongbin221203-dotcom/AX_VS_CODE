"""반품·정정 · 대손 · 선수금 · 자동 거래정지 · 거래처 담당자 · 수주

  sales            : sale_kind(매출|반품|정정), original_sale_id(반품·정정의 원매출), order_id·order_item_id(수주에서 납품)
  fin_requests     : 대손 처리·거래정지 해제 결재 요청 (요청자는 결재할 수 없음)
  advances         : 거래처 선수금 원장 (입금 +, 매출 배분 −, 환불 −, 반품 초과분 +)
  customers        : block_source(ERP|자동|수기)·block_reason·blocked_at·block_exempt_until(해제 결재 후 재정지 유예)
  customer_contacts: 거래처 담당자 여러 명 (구매·회계·현업·의사결정). 대표 담당자는 customers.manager·phone·email 과 맞춘다
  sales_orders · sales_order_items : 수주와 품목별 수주·납품 수량(수주 잔량)

Revision ID: 0012_returns_advances_orders
Revises: 0011_ledger_close
Create Date: 2026-10-02
"""
from ddl import add_columns, big, drop_columns, pk, real, run

revision = "0012_returns_advances_orders"
down_revision = "0011_ledger_close"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("sales", ("sale_kind", "TEXT DEFAULT '매출'"), ("original_sale_id", big()),
                ("order_id", big()), ("order_item_id", big()))
    run("UPDATE sales SET sale_kind = '매출' WHERE sale_kind IS NULL",
        "CREATE INDEX idx_sales_original ON sales(original_sale_id)", "CREATE INDEX idx_sales_order ON sales(order_id)")
    run(f"""
    CREATE TABLE fin_requests (
        id              {pk()},
        kind            TEXT NOT NULL,
        sale_id         {big()},
        customer_id     {big()} NOT NULL,
        amount          {big()} NOT NULL DEFAULT 0,
        reason          TEXT NOT NULL,
        required_role   TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT '대기',
        requested_by    TEXT,
        requested_by_id {big()},
        requested_at    TEXT NOT NULL,
        decided_by      TEXT,
        decided_by_id   {big()},
        decided_at      TEXT,
        comment         TEXT
    )""", "CREATE INDEX idx_finreq_status ON fin_requests(status)", f"""
    CREATE TABLE advances (
        id           {pk()},
        customer_id  {big()} NOT NULL,
        entry_date   TEXT NOT NULL,
        amount       {big()} NOT NULL,
        kind         TEXT NOT NULL,
        sale_id      {big()},
        method       TEXT,
        ref_no       TEXT,
        memo         TEXT,
        created_by   TEXT,
        created_at   TEXT NOT NULL
    )""", "CREATE INDEX idx_adv_cust ON advances(customer_id)", f"""
    CREATE TABLE customer_contacts (
        id          {pk()},
        customer_id {big()} NOT NULL,
        name        TEXT NOT NULL,
        dept        TEXT,
        title       TEXT,
        role        TEXT,
        phone       TEXT,
        email       TEXT,
        is_primary  INTEGER NOT NULL DEFAULT 0,
        memo        TEXT,
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""", "CREATE INDEX idx_contact_cust ON customer_contacts(customer_id)", f"""
    CREATE TABLE sales_orders (
        id            {pk()},
        order_no      TEXT NOT NULL UNIQUE,
        customer_id   {big()} NOT NULL,
        deal_id       {big()},
        quote_id      {big()},
        entity_id     {big()},
        currency      TEXT DEFAULT 'KRW',
        fx_rate       {real()} DEFAULT 1,
        order_date    TEXT NOT NULL,
        delivery_date TEXT,
        status        TEXT NOT NULL DEFAULT '진행',
        customer_po   TEXT,
        owner         TEXT,
        owner_id      {big()},
        memo          TEXT,
        created_by    TEXT,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )""", "CREATE INDEX idx_so_customer ON sales_orders(customer_id)", f"""
    CREATE TABLE sales_order_items (
        id            {pk()},
        order_id      {big()} NOT NULL,
        line_no       INTEGER NOT NULL,
        product_id    {big()},
        item_code     TEXT,
        item_name     TEXT NOT NULL,
        unit          TEXT,
        qty           INTEGER NOT NULL,
        unit_price    {big()} NOT NULL,
        tax_type      TEXT NOT NULL DEFAULT '과세',
        delivered_qty INTEGER NOT NULL DEFAULT 0,
        cancelled_qty INTEGER NOT NULL DEFAULT 0
    )""", "CREATE INDEX idx_soi_order ON sales_order_items(order_id)")
    add_columns("customers", ("block_source", "TEXT"), ("block_reason", "TEXT"), ("blocked_at", "TEXT"),
                ("block_exempt_until", "TEXT"))
    run("UPDATE customers SET block_source='ERP' WHERE COALESCE(trade_blocked, 0) = 1 AND erp_synced_at IS NOT NULL",
        "UPDATE customers SET block_source='수기' WHERE COALESCE(trade_blocked, 0) = 1 AND block_source IS NULL",
        # 기존 고객 담당자 1명을 대표 담당자로 옮긴다
        "INSERT INTO customer_contacts (customer_id, name, phone, email, is_primary, role, created_at, updated_at) "
        "SELECT id, manager, phone, email, 1, '대표', created_at, updated_at FROM customers "
        "WHERE COALESCE(manager, '') <> ''")


def downgrade() -> None:
    run("DROP TABLE sales_order_items", "DROP TABLE sales_orders", "DROP TABLE customer_contacts",
        "DROP TABLE advances", "DROP TABLE fin_requests", "DROP INDEX idx_sales_original", "DROP INDEX idx_sales_order")
    drop_columns("customers", "block_source", "block_reason", "blocked_at", "block_exempt_until")
    drop_columns("sales", "sale_kind", "original_sale_id", "order_id", "order_item_id")

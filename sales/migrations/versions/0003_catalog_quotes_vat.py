"""품목·단가 마스터 · 견적 · 부가세(공급가액/세액/합계) 분리

기존 매출은 금액을 공급가액으로 보고 과세(10%) 세액·합계를 채운다.
입금완료 건은 합계만큼 입금된 것으로 맞춘다(채권이 부가세 포함 기준이 되므로).

Revision ID: 0003_catalog_quotes_vat
Revises: 0002_jobs_notifications
Create Date: 2026-09-27
"""
from ddl import add_columns, drop_columns, big, pk, real, run

revision = "0003_catalog_quotes_vat"
down_revision = "0002_jobs_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE products (
        id           {pk()},
        code         TEXT NOT NULL UNIQUE,
        name         TEXT NOT NULL,
        category     TEXT,
        unit         TEXT NOT NULL DEFAULT 'EA',
        list_price   {big()} NOT NULL DEFAULT 0,
        tax_type     TEXT NOT NULL DEFAULT '과세',
        erp_material TEXT,
        active       INTEGER NOT NULL DEFAULT 1,
        memo         TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )""",
        f"""
    CREATE TABLE customer_prices (
        id          {pk()},
        customer_id {big()} NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        product_id  {big()} NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        unit_price  {big()} NOT NULL,
        valid_from  TEXT NOT NULL,
        valid_to    TEXT,
        memo        TEXT,
        created_by  TEXT,
        created_at  TEXT NOT NULL,
        UNIQUE (customer_id, product_id, valid_from)
    )""",
        f"""
    CREATE TABLE quotes (
        id            {pk()},
        quote_no      TEXT NOT NULL,
        revision      INTEGER NOT NULL DEFAULT 1,
        parent_id     {big()},
        customer_id   {big()} NOT NULL REFERENCES customers(id),
        deal_id       {big()} REFERENCES deals(id) ON DELETE SET NULL,
        owner         TEXT NOT NULL,
        owner_id      {big()},
        status        TEXT NOT NULL DEFAULT '작성중',
        title         TEXT,
        issue_date    TEXT NOT NULL,
        valid_until   TEXT,
        terms         TEXT,
        memo          TEXT,
        list_total    {big()} NOT NULL DEFAULT 0,
        supply_amount {big()} NOT NULL DEFAULT 0,
        vat_amount    {big()} NOT NULL DEFAULT 0,
        total_amount  {big()} NOT NULL DEFAULT 0,
        discount_rate {real()} NOT NULL DEFAULT 0,
        sent_at       TEXT,
        decided_at    TEXT,
        row_version   INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        UNIQUE (quote_no, revision)
    )""",
        "CREATE INDEX idx_quote_customer ON quotes(customer_id)",
        "CREATE INDEX idx_quote_deal ON quotes(deal_id)",
        "CREATE INDEX idx_quote_owner ON quotes(owner_id)",
        f"""
    CREATE TABLE quote_items (
        id            {pk()},
        quote_id      {big()} NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
        line_no       INTEGER NOT NULL,
        product_id    {big()} REFERENCES products(id),
        item_code     TEXT,
        item_name     TEXT NOT NULL,
        unit          TEXT NOT NULL DEFAULT 'EA',
        qty           INTEGER NOT NULL DEFAULT 1,
        list_price    {big()} NOT NULL DEFAULT 0,
        unit_price    {big()} NOT NULL DEFAULT 0,
        discount_rate {real()} NOT NULL DEFAULT 0,
        tax_type      TEXT NOT NULL DEFAULT '과세',
        supply_amount {big()} NOT NULL DEFAULT 0,
        vat_amount    {big()} NOT NULL DEFAULT 0
    )""",
        "CREATE INDEX idx_quote_items ON quote_items(quote_id)")

    add_columns("sales",
                ("product_id", big()), ("quote_id", big()),
                ("tax_type", "TEXT DEFAULT '과세'"),
                ("vat_amount", f"{big()} DEFAULT 0"), ("total_amount", f"{big()} DEFAULT 0"))
    # 기존 매출: 금액 = 공급가액, 과세 10%(원 미만 절사). 입금완료 건은 합계만큼 입금으로 맞춘다
    run("UPDATE sales SET tax_type = '과세' WHERE tax_type IS NULL",
        "UPDATE sales SET vat_amount = amount / 10, total_amount = amount + amount / 10",
        "UPDATE sales SET paid_amount = total_amount WHERE status = '입금완료'")


def downgrade() -> None:
    # 부가세 도입 전에는 입금액이 공급가액 기준이었다
    run("UPDATE sales SET paid_amount = amount WHERE paid_amount > amount")
    drop_columns("sales", "product_id", "quote_id", "tax_type", "vat_amount", "total_amount")
    run("DROP TABLE quote_items", "DROP TABLE quotes", "DROP TABLE customer_prices", "DROP TABLE products")

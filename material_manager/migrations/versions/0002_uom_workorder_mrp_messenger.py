"""단위 환산 · 작업지시(공정·재공품·실제 투입) · MRP · 메신저 알림."""
from core.migrate import add_column, create_index, create_table, drop_column, drop_table

revision = "0002"
down_revision = "0001"
message = "단위 환산 · 작업지시(공정·재공품·실제 투입량·실제 원가) · MRP(리드타임·최소발주) · 잔디·네이버웍스 알림"


def upgrade(conn):
    # 자재: 리드타임(구매는 발주→입고, 제품은 착수→완료, 일) · 최소 발주량 · 발주 배수
    add_column(conn, "materials", "lead_time_days", "INTEGER DEFAULT 0")
    add_column(conn, "materials", "min_order_qty", "{REAL} DEFAULT 0")
    add_column(conn, "materials", "order_multiple", "{REAL} DEFAULT 0")

    # 단위 환산: 1 unit = factor × 기본 단위 (예: BOX = 100 EA). 상자 바코드를 찍으면 그 단위로 들어온다.
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS material_units (
            id           {ID},
            material_id  INTEGER NOT NULL REFERENCES materials(id),
            unit         TEXT    NOT NULL,
            factor       {REAL}  NOT NULL,
            barcode      TEXT    DEFAULT '',
            created_at   TEXT    NOT NULL,
            UNIQUE (material_id, unit)
        )""")
    create_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_unit_barcode ON material_units(barcode) WHERE barcode <> ''")
    # 거래에 남기는 입력 단위·수량 (재고는 늘 기본 단위 qty)
    add_column(conn, "transactions", "entry_unit", "TEXT DEFAULT ''")
    add_column(conn, "transactions", "entry_qty", "{REAL}")

    # 공정(라우팅): 제품을 만드는 순서·작업장·표준 시간(개당 분)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS routings (
            id           {ID},
            product_id   INTEGER NOT NULL REFERENCES materials(id),
            seq          INTEGER NOT NULL,
            op_name      TEXT    NOT NULL,
            workcenter   TEXT    DEFAULT '',
            std_minutes  {REAL}  DEFAULT 0,
            note         TEXT    DEFAULT ''
        )""")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_routing_product ON routings(product_id)")

    # 생산 → 작업지시: 계획 → 자재 출고(재공) → 공정 실적 → 완료 입고(실제 원가)
    add_column(conn, "productions", "status", "TEXT DEFAULT 'DONE'")       # PLANNED | RELEASED | DONE | CANCELLED
    add_column(conn, "productions", "source", "TEXT DEFAULT 'QUICK'")      # QUICK(바로 완료) | MANUAL | MRP
    add_column(conn, "productions", "due_date", "TEXT DEFAULT ''")
    add_column(conn, "productions", "start_date", "TEXT DEFAULT ''")
    add_column(conn, "productions", "good_qty", "{REAL} DEFAULT 0")
    add_column(conn, "productions", "scrap_qty", "{REAL} DEFAULT 0")
    add_column(conn, "productions", "planned_cost", "{REAL} DEFAULT 0")    # BOM × 기준단가 (표준)
    add_column(conn, "productions", "completed_at", "TEXT DEFAULT ''")
    conn.execute("UPDATE productions SET good_qty = qty WHERE good_qty = 0 AND status = 'DONE' AND cancelled_at = ''")
    conn.execute("UPDATE productions SET status = 'CANCELLED' WHERE cancelled_at <> ''")
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS production_lines (
            id             {ID},
            production_id  INTEGER NOT NULL REFERENCES productions(id),
            line_no        INTEGER NOT NULL,
            component_id   INTEGER NOT NULL REFERENCES materials(id),
            wh_id          INTEGER REFERENCES warehouses(id),
            planned_qty    {REAL}  NOT NULL,              -- BOM 소요량
            issued_qty     {REAL}  DEFAULT 0,             -- 실제 투입(출고) 누계
            issued_cost    {REAL}  DEFAULT 0,             -- 실제 투입 금액 누계
            std_price      {REAL}  DEFAULT 0,             -- 등록 당시 기준단가
            note           TEXT    DEFAULT ''
        )""")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_prodline ON production_lines(production_id)")
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS wo_operations (
            id             {ID},
            production_id  INTEGER NOT NULL REFERENCES productions(id),
            seq            INTEGER NOT NULL,
            op_name        TEXT    NOT NULL,
            workcenter     TEXT    DEFAULT '',
            std_minutes    {REAL}  DEFAULT 0,
            good_qty       {REAL}  DEFAULT 0,
            scrap_qty      {REAL}  DEFAULT 0,
            minutes        {REAL}  DEFAULT 0,
            status         TEXT    DEFAULT 'WAIT',        -- WAIT | DONE
            worker         TEXT    DEFAULT '',
            done_at        TEXT    DEFAULT '',
            note           TEXT    DEFAULT ''
        )""")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_woop ON wo_operations(production_id)")

    # MRP: 수요(판매·출하 계획 등) → 실행 결과(계획 주문)
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS mrp_demands (
            id           {ID},
            plant_id     INTEGER NOT NULL REFERENCES plants(id),
            material_id  INTEGER NOT NULL REFERENCES materials(id),
            qty          {REAL}  NOT NULL,
            due_date     TEXT    NOT NULL,
            note         TEXT    DEFAULT '',
            active       INTEGER DEFAULT 1,
            created_by   TEXT    DEFAULT '',
            created_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mrp_runs (
            id         {ID},
            plant_id   INTEGER NOT NULL REFERENCES plants(id),
            horizon    TEXT    NOT NULL,
            run_by     TEXT    DEFAULT '',
            run_at     TEXT    NOT NULL,
            summary    TEXT    DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS mrp_plans (
            id            {ID},
            run_id        INTEGER NOT NULL REFERENCES mrp_runs(id),
            kind          TEXT    NOT NULL,               -- BUY(구매) | MAKE(생산)
            material_id   INTEGER NOT NULL REFERENCES materials(id),
            qty           {REAL}  NOT NULL,
            need_date     TEXT    NOT NULL,               -- 이 날까지 있어야 함
            order_date    TEXT    NOT NULL,               -- 리드타임 역산: 이 날 발주·착수
            warehouse_id  INTEGER REFERENCES warehouses(id),
            level         INTEGER DEFAULT 0,
            pegging       TEXT    DEFAULT '',             -- 어느 수요 때문에 생겼는지
            status        TEXT    DEFAULT 'OPEN',         -- OPEN | CONVERTED
            ref           TEXT    DEFAULT ''              -- 바꾼 구매요청·작업지시 번호
        )""")
    create_index(conn, "CREATE INDEX IF NOT EXISTS idx_mrp_plans ON mrp_plans(run_id)")

    # 메신저 알림: 알림마다 보낼 길(channel), 사용자 메신저 아이디(네이버웍스)
    add_column(conn, "notifications", "channel", "TEXT DEFAULT 'email'")    # email | jandi | naverworks
    add_column(conn, "users", "messenger_id", "TEXT DEFAULT ''")


def downgrade(conn):
    for t in ("mrp_plans", "mrp_runs", "mrp_demands", "wo_operations", "production_lines", "routings", "material_units"):
        drop_table(conn, t)
    conn.executescript("DROP INDEX IF EXISTS uq_unit_barcode")
    for table, cols in (("materials", ("lead_time_days", "min_order_qty", "order_multiple")),
                        ("transactions", ("entry_unit", "entry_qty")),
                        ("productions", ("status", "source", "due_date", "start_date", "good_qty", "scrap_qty",
                                         "planned_cost", "completed_at")),
                        ("notifications", ("channel",)), ("users", ("messenger_id",))):
        for c in cols:
            drop_column(conn, table, c)

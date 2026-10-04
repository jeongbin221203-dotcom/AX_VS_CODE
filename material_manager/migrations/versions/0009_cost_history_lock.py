"""표준원가 이력(다시 산정하면 이전 값을 남김) · 완료된 작업지시의 원가·수량 잠금(트리거)."""
from core.migrate import create_table, drop_table

revision = "0009"
down_revision = "0008"
message = "표준원가 이력 · 완료 작업지시 원가 잠금"

SQLITE_LOCK = """
CREATE TRIGGER IF NOT EXISTS prod_done_locked BEFORE UPDATE ON productions
WHEN OLD.status = 'DONE' AND (NEW.material_cost IS NOT OLD.material_cost OR NEW.labor_cost IS NOT OLD.labor_cost
     OR NEW.overhead_cost IS NOT OLD.overhead_cost OR NEW.good_qty IS NOT OLD.good_qty OR NEW.scrap_qty IS NOT OLD.scrap_qty
     OR NEW.product_id IS NOT OLD.product_id)
BEGIN SELECT RAISE(ABORT, '완료된 작업지시의 원가·수량은 바꿀 수 없습니다 (작업지시 취소로 되돌림)'); END;
"""
PG_LOCK = """
CREATE OR REPLACE FUNCTION mm_prod_done_locked() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'DONE' AND (NEW.material_cost IS DISTINCT FROM OLD.material_cost OR NEW.labor_cost IS DISTINCT FROM OLD.labor_cost
     OR NEW.overhead_cost IS DISTINCT FROM OLD.overhead_cost OR NEW.good_qty IS DISTINCT FROM OLD.good_qty
     OR NEW.scrap_qty IS DISTINCT FROM OLD.scrap_qty OR NEW.product_id IS DISTINCT FROM OLD.product_id) THEN
    RAISE EXCEPTION '완료된 작업지시의 원가·수량은 바꿀 수 없습니다 (작업지시 취소로 되돌림)';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS prod_done_locked ON productions;
CREATE TRIGGER prod_done_locked BEFORE UPDATE ON productions FOR EACH ROW EXECUTE FUNCTION mm_prod_done_locked();
"""


def upgrade(conn):
    create_table(conn, """
        CREATE TABLE IF NOT EXISTS standard_cost_history (
            id           {ID},
            material_id  INTEGER NOT NULL,
            material     {REAL}  DEFAULT 0,
            labor        {REAL}  DEFAULT 0,
            overhead     {REAL}  DEFAULT 0,
            total        {REAL}  DEFAULT 0,
            minutes      {REAL}  DEFAULT 0,
            basis        TEXT    DEFAULT '',
            set_by       TEXT    DEFAULT '',
            set_at       TEXT    NOT NULL,
            replaced_by  TEXT    DEFAULT '',
            replaced_at  TEXT    NOT NULL
        )""")
    conn.executescript(PG_LOCK if conn.pg else SQLITE_LOCK)


def downgrade(conn):
    drop_table(conn, "standard_cost_history")
    conn.executescript("DROP TRIGGER IF EXISTS prod_done_locked ON productions; DROP FUNCTION IF EXISTS mm_prod_done_locked();"
                       if conn.pg else "DROP TRIGGER IF EXISTS prod_done_locked;")

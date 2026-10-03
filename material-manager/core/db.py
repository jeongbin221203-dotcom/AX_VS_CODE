"""DB 연결 · 스키마 · 마이그레이션. SQLite(한 서버)와 PostgreSQL(여러 서버)을 같은 코드로 쓴다.

- 코드의 SQL은 '?' 자리표시자로 쓴다. PostgreSQL이면 여기서 %s로 바꾸고, INSERT에는 RETURNING id를 붙인다.
- 행(row)은 이름(row["code"])과 순서(row[0]) 둘 다로 꺼낼 수 있다.
- 쓰기 트랜잭션: SQLite는 BEGIN IMMEDIATE(전체 쓰기 잠금), PostgreSQL은 lock()으로 자재·작업 단위 잠금.
"""

import atexit
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd

import config
from core.utils import now_str

try:
    import psycopg
    from psycopg import errors as pg_errors
except ImportError:           # SQLite만 쓰는 환경
    psycopg = None
    pg_errors = None


def dialect() -> str:
    return "postgres" if config.DATABASE_URL.startswith(("postgres://", "postgresql://")) else "sqlite"


def is_pg() -> bool:
    return dialect() == "postgres"


# 두 DB의 예외를 한꺼번에 잡기 위한 묶음
IntegrityError: tuple = (sqlite3.IntegrityError,) + ((pg_errors.IntegrityError,) if pg_errors else ())
DBError: tuple = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())

# ── 스키마 ──────────────────────────────────────────────────
# {ID} {REAL} {NOCASE}는 DB 종류에 맞게 바뀐다.
SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id            {ID},
    code          TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    spec          TEXT    DEFAULT '',
    unit          TEXT    DEFAULT 'EA',
    category      TEXT    DEFAULT '미분류',
    safety_stock  {REAL}  DEFAULT 0,
    unit_price    {REAL}  DEFAULT 0,
    location      TEXT    DEFAULT '',
    supplier      TEXT    DEFAULT '',
    sap_matnr     TEXT    DEFAULT '',     -- SAP 자재번호
    lot_managed   INTEGER DEFAULT 0,      -- 1이면 로트(배치) 단위로 입출고
    expiry_managed INTEGER DEFAULT 0,     -- 1이면 입고 때 유효기한 필수, 지난 로트는 출고 불가
    sap_synced_at TEXT    DEFAULT '',     -- SAP 마스터 동기화로 마지막 갱신된 시각 (있으면 SAP가 원본)
    plant         TEXT    DEFAULT '',     -- (예전 방식) 플랜트는 이제 창고(warehouses)에 둔다
    sloc          TEXT    DEFAULT '',     -- (예전 방식) 저장위치도 창고에 둔다
    active        INTEGER DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- 조직: 플랜트(공장·법인) > 창고. 창고 하나 = SAP 플랜트 + 저장위치 하나.
CREATE TABLE IF NOT EXISTS plants (
    id          {ID},
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    sap_plant   TEXT    DEFAULT '',
    active      INTEGER DEFAULT 1,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouses (
    id          {ID},
    plant_id    INTEGER NOT NULL REFERENCES plants(id),
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    sap_sloc    TEXT    DEFAULT '',
    active      INTEGER DEFAULT 1,
    created_at  TEXT    NOT NULL
);

-- 거래는 고치거나 지우지 않는다(트리거로 차단). 잘못 등록한 건은 반대 거래(reversal_of)로 취소한다.
CREATE TABLE IF NOT EXISTS transactions (
    id             {ID},
    material_id    INTEGER NOT NULL REFERENCES materials(id),
    tx_type        TEXT    NOT NULL CHECK (tx_type IN ('IN', 'OUT', 'ADJ')),
    qty            {REAL}  NOT NULL,
    unit_price     {REAL}  DEFAULT 0,
    tx_date        TEXT    NOT NULL,
    ref_no         TEXT    DEFAULT '',
    partner        TEXT    DEFAULT '',
    note           TEXT    DEFAULT '',
    created_by     TEXT    DEFAULT '',
    created_at     TEXT    NOT NULL,
    reversal_of    INTEGER REFERENCES transactions(id),
    po_no          TEXT    DEFAULT '',
    po_item        TEXT    DEFAULT '',
    cost_center    TEXT    DEFAULT '',
    movement_type  TEXT    DEFAULT '',
    warehouse_id   INTEGER REFERENCES warehouses(id),
    transfer_no    TEXT    DEFAULT '',     -- 창고 간 이동: 출고·입고 두 행이 같은 번호
    created_by_id  INTEGER,
    approved_by    TEXT    DEFAULT '',     -- 결재를 거친 거래의 승인자
    lot_no         TEXT    DEFAULT '',     -- 로트(배치) 번호. 로트 관리 자재만
    statement_id   INTEGER                 -- 거래명세서로 한꺼번에 등록한 거래 (statements.id)
);

-- 거래명세서: 공급처(입고)·납품처(출고) 명세서 한 장 = 여러 품목 거래. 품목 줄은 transactions.statement_id로 묶인다.
CREATE TABLE IF NOT EXISTS statements (
    id              {ID},
    kind            TEXT    NOT NULL CHECK (kind IN ('IN', 'OUT')),
    statement_no    TEXT    DEFAULT '',     -- 명세서 번호 (공급처가 붙인 번호)
    partner         TEXT    NOT NULL,       -- 공급처 또는 납품처
    partner_biz_no  TEXT    DEFAULT '',
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(id),
    tx_date         TEXT    NOT NULL,
    supply_amount   {REAL}  DEFAULT 0,
    tax_amount      {REAL}  DEFAULT 0,
    line_count      INTEGER DEFAULT 0,
    doc_id          INTEGER,                -- 첨부한 명세서 이미지·PDF (documents.id)
    note            TEXT    DEFAULT '',
    created_by_id   INTEGER,
    created_by      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    cancelled_at    TEXT    DEFAULT '',     -- 명세서 전체 취소 (모든 줄을 한 번에 취소 거래로)
    cancelled_by    TEXT    DEFAULT '',
    cancel_reason   TEXT    DEFAULT ''
);

-- 로트 마스터: 자재별 로트 번호와 유효기한 (첫 입고 때 생긴다)
CREATE TABLE IF NOT EXISTS lots (
    id           {ID},
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    lot_no       TEXT    NOT NULL,
    expiry_date  TEXT    DEFAULT '',
    created_at   TEXT    NOT NULL,
    UNIQUE (material_id, lot_no)
);

CREATE TABLE IF NOT EXISTS documents (
    id               {ID},
    tx_id            INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    doc_type         TEXT    NOT NULL,
    issue_date       TEXT    NOT NULL,
    approval_no      TEXT    DEFAULT '',
    supplier_biz_no  TEXT    DEFAULT '',
    supplier_name    TEXT    DEFAULT '',
    supply_amount    BIGINT  DEFAULT 0,
    tax_amount       BIGINT  DEFAULT 0,
    file_name        TEXT    NOT NULL,
    stored_name      TEXT    NOT NULL UNIQUE,
    mime             TEXT    NOT NULL,
    size             BIGINT  NOT NULL,
    sha256           TEXT    NOT NULL UNIQUE,
    note             TEXT    DEFAULT '',
    created_by       TEXT    DEFAULT '',
    created_at       TEXT    NOT NULL,
    created_by_id    INTEGER
);

CREATE TABLE IF NOT EXISTS users (
    id              {ID},
    username        TEXT    NOT NULL UNIQUE {NOCASE},
    name            TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN ('VIEWER', 'CLERK', 'MANAGER', 'ADMIN')),
    password_hash   TEXT    NOT NULL,
    active          INTEGER DEFAULT 1,
    must_change_pw  INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    locked_until    TEXT    DEFAULT '',
    last_login_at   TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    all_warehouses  INTEGER DEFAULT 1,     -- 1이면 모든 창고, 0이면 user_scopes에 준 플랜트·창고만
    auth_source     TEXT    DEFAULT 'local',   -- local | sso
    sso_subject     TEXT    DEFAULT ''         -- IdP의 사용자 고유값(sub)
);

-- 사용자별 데이터 범위: 플랜트를 주면 그 플랜트의 모든 창고(나중에 생긴 창고 포함), 창고를 주면 그 창고만
CREATE TABLE IF NOT EXISTS user_scopes (
    id            {ID},
    user_id       INTEGER NOT NULL REFERENCES users(id),
    plant_id      INTEGER REFERENCES plants(id),
    warehouse_id  INTEGER REFERENCES warehouses(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         {ID},
    at         TEXT    NOT NULL,
    user_id    INTEGER,
    user_name  TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    entity     TEXT    DEFAULT '',
    entity_id  TEXT    DEFAULT '',
    detail     TEXT    DEFAULT '',
    ip         TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS period_closes (
    id              {ID},
    closed_through  TEXT    NOT NULL,
    action          TEXT    NOT NULL CHECK (action IN ('CLOSE', 'REOPEN')),
    reason          TEXT    DEFAULT '',
    user_name       TEXT    NOT NULL,
    at              TEXT    NOT NULL
);

-- 마감 시점 월말 재고 (자재 × 창고 × 로트)
CREATE TABLE IF NOT EXISTS inventory_snapshots (
    ym            TEXT    NOT NULL,
    material_id   INTEGER NOT NULL REFERENCES materials(id),
    warehouse_id  INTEGER NOT NULL REFERENCES warehouses(id),
    lot_no        TEXT    NOT NULL DEFAULT '',
    qty           {REAL}  NOT NULL,
    PRIMARY KEY (ym, material_id, warehouse_id, lot_no)
);

-- 마감 시점 재고 평가 (자재 × 플랜트 × 평가방법). layers는 선입선출 층 [[수량, 단가], ...]
CREATE TABLE IF NOT EXISTS valuation_snapshots (
    ym           TEXT    NOT NULL,
    method       TEXT    NOT NULL,
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    plant_id     INTEGER NOT NULL REFERENCES plants(id),
    qty          {REAL}  NOT NULL,
    value        {REAL}  NOT NULL,
    layers       TEXT    DEFAULT '',
    PRIMARY KEY (ym, method, material_id, plant_id)
);

-- 구매요청(PR) → 결재 → 발주(PO) → 입고(GR)
CREATE TABLE IF NOT EXISTS purchase_requests (
    id               {ID},
    pr_no            TEXT    NOT NULL UNIQUE,
    warehouse_id     INTEGER NOT NULL REFERENCES warehouses(id),
    need_date        TEXT    DEFAULT '',
    reason           TEXT    DEFAULT '',
    status           TEXT    NOT NULL,        -- PENDING | APPROVED | REJECTED | ORDERED | CANCELLED
    total_amount     {REAL}  DEFAULT 0,
    required_steps   INTEGER DEFAULT 1,       -- 금액에 따라 필요한 결재 단계 수
    requested_by_id  INTEGER,
    requested_by     TEXT    NOT NULL,
    requested_at     TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pr_items (
    id           {ID},
    pr_id        INTEGER NOT NULL REFERENCES purchase_requests(id),
    line_no      INTEGER NOT NULL,
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    qty          {REAL}  NOT NULL,
    est_price    {REAL}  DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pr_approvals (
    id           {ID},
    pr_id        INTEGER NOT NULL REFERENCES purchase_requests(id),
    step         INTEGER NOT NULL,
    approver_id  INTEGER,
    approver     TEXT    NOT NULL,
    decision     TEXT    NOT NULL,            -- APPROVE | REJECT
    comment      TEXT    DEFAULT '',
    at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id             {ID},
    po_no          TEXT    NOT NULL UNIQUE,
    pr_id          INTEGER REFERENCES purchase_requests(id),
    supplier       TEXT    NOT NULL,
    warehouse_id   INTEGER NOT NULL REFERENCES warehouses(id),
    status         TEXT    NOT NULL,          -- PENDING_APPROVAL | OPEN | PARTIAL | CLOSED | CANCELLED
    sap_po_no      TEXT    DEFAULT '',        -- SAP 구매오더 번호 (SAP 입고 전기에 쓴다)
    total_amount   {REAL}  DEFAULT 0,
    note           TEXT    DEFAULT '',
    created_by_id  INTEGER,
    created_by     TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    approved_by    TEXT    DEFAULT '',
    approved_at    TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS po_items (
    id           {ID},
    po_id        INTEGER NOT NULL REFERENCES purchase_orders(id),
    line_no      INTEGER NOT NULL,            -- 10, 20, 30 ... (SAP 품목번호 관례)
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    qty          {REAL}  NOT NULL,
    price        {REAL}  NOT NULL
);

-- 엑셀 양식 설정 (회사 양식 파일은 저장소 forms/<form_key>.xlsx)
CREATE TABLE IF NOT EXISTS excel_forms (
    form_key      TEXT PRIMARY KEY,
    config        TEXT DEFAULT '',
    has_template  INTEGER DEFAULT 0,
    updated_by    TEXT DEFAULT '',
    updated_at    TEXT DEFAULT ''
);

-- SAP 원가센터 (마스터 동기화). 비어 있으면 출고 원가센터를 검사하지 않는다.
CREATE TABLE IF NOT EXISTS cost_centers (
    code       TEXT PRIMARY KEY,
    name       TEXT DEFAULT '',
    active     INTEGER DEFAULT 1,
    synced_at  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sap_outbox (
    id            {ID},
    tx_id         INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    status        TEXT    NOT NULL,
    attempts      INTEGER DEFAULT 0,
    next_try_at   TEXT    DEFAULT '',
    last_error    TEXT    DEFAULT '',
    payload       TEXT    DEFAULT '',
    sap_doc_no    TEXT    DEFAULT '',
    sap_doc_year  TEXT    DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- 결재 요청 (금액이 큰 실사 조정 등). 승인되면 result_tx_id에 거래가 생긴다.
CREATE TABLE IF NOT EXISTS approval_requests (
    id               {ID},
    kind             TEXT    NOT NULL,
    material_id      INTEGER NOT NULL REFERENCES materials(id),
    warehouse_id     INTEGER NOT NULL REFERENCES warehouses(id),
    tx_date          TEXT    NOT NULL,
    qty              {REAL}  NOT NULL,
    amount           {REAL}  NOT NULL,
    payload          TEXT    DEFAULT '',
    status           TEXT    NOT NULL,
    requested_by_id  INTEGER,
    requested_by     TEXT    NOT NULL,
    requested_at     TEXT    NOT NULL,
    decided_by_id    INTEGER,
    decided_by       TEXT    DEFAULT '',
    decided_at       TEXT    DEFAULT '',
    comment          TEXT    DEFAULT '',
    result_tx_id     INTEGER
);

-- 서버 여러 대가 함께 보는 설정값 (최초 설정 코드 등)
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- 배치 작업 잠금(임대). 한 작업은 한 번에 한 서버만 돌린다.
CREATE TABLE IF NOT EXISTS job_locks (
    name           TEXT PRIMARY KEY,
    holder         TEXT DEFAULT '',
    lease_until    TEXT DEFAULT '',
    last_started   TEXT DEFAULT '',
    last_finished  TEXT DEFAULT '',
    last_status    TEXT DEFAULT '',
    last_message   TEXT DEFAULT ''
);

-- 화면 제출 한 번 쓰는 표 (core/once.py): 같은 제출을 두 번 처리하지 않게
CREATE TABLE IF NOT EXISTS form_once (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER,
    status      TEXT NOT NULL,              -- RUN | DONE
    location    TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    id           {ID},
    name         TEXT NOT NULL,
    holder       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT DEFAULT '',
    status       TEXT NOT NULL,
    message      TEXT DEFAULT ''
);
"""

NO_ID_TABLES = {"inventory_snapshots", "valuation_snapshots", "app_settings", "job_locks", "cost_centers", "excel_forms",
                "form_once"}

# 예전 DB에 없던 컬럼 (CREATE TABLE IF NOT EXISTS는 기존 테이블에 컬럼을 더하지 않는다)
MIGRATIONS = [
    ("materials", "sap_matnr", "TEXT DEFAULT ''"),
    ("materials", "plant", "TEXT DEFAULT ''"),
    ("materials", "sloc", "TEXT DEFAULT ''"),
    ("transactions", "reversal_of", "INTEGER REFERENCES transactions(id)"),
    ("transactions", "po_no", "TEXT DEFAULT ''"),
    ("transactions", "po_item", "TEXT DEFAULT ''"),
    ("transactions", "cost_center", "TEXT DEFAULT ''"),
    ("transactions", "movement_type", "TEXT DEFAULT ''"),
    ("transactions", "warehouse_id", "INTEGER REFERENCES warehouses(id)"),
    ("transactions", "transfer_no", "TEXT DEFAULT ''"),
    ("transactions", "created_by_id", "INTEGER"),
    ("transactions", "approved_by", "TEXT DEFAULT ''"),
    ("documents", "created_by_id", "INTEGER"),
    ("users", "all_warehouses", "INTEGER DEFAULT 1"),
    ("materials", "lot_managed", "INTEGER DEFAULT 0"),
    ("materials", "expiry_managed", "INTEGER DEFAULT 0"),
    ("materials", "sap_synced_at", "TEXT DEFAULT ''"),
    ("transactions", "lot_no", "TEXT DEFAULT ''"),
    ("users", "auth_source", "TEXT DEFAULT 'local'"),
    ("users", "sso_subject", "TEXT DEFAULT ''"),
    ("users", "session_ver", "INTEGER DEFAULT 0"),      # 로그아웃하면 올려 그 전 세션 쿠키를 모두 무효로
    ("users", "suspended_by", "TEXT DEFAULT ''"),       # 중지 주체: admin(관리자) | sso(사내 그룹에서 빠짐)
    ("transactions", "statement_id", "INTEGER"),
    ("statements", "cancelled_at", "TEXT DEFAULT ''"),
    ("statements", "cancelled_by", "TEXT DEFAULT ''"),
    ("statements", "cancel_reason", "TEXT DEFAULT ''"),
]

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tx_material ON transactions(material_id);
CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(tx_date);
CREATE INDEX IF NOT EXISTS idx_tx_wh_date  ON transactions(warehouse_id, tx_date);
CREATE INDEX IF NOT EXISTS idx_tx_transfer ON transactions(transfer_no);
CREATE INDEX IF NOT EXISTS idx_mat_active  ON materials(active);
CREATE INDEX IF NOT EXISTS idx_doc_tx      ON documents(tx_id);
CREATE INDEX IF NOT EXISTS idx_doc_date    ON documents(issue_date);
CREATE INDEX IF NOT EXISTS idx_audit_at    ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_audit_ip    ON audit_log(action, ip, at);
CREATE INDEX IF NOT EXISTS idx_outbox_st   ON sap_outbox(status);
CREATE INDEX IF NOT EXISTS idx_appr_st     ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_scope_user  ON user_scopes(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_lot      ON transactions(material_id, warehouse_id, lot_no);
CREATE INDEX IF NOT EXISTS idx_tx_po       ON transactions(po_no, po_item);
CREATE INDEX IF NOT EXISTS idx_pr_status   ON purchase_requests(status);
CREATE INDEX IF NOT EXISTS idx_po_status   ON purchase_orders(status);
CREATE INDEX IF NOT EXISTS idx_once_at     ON form_once(created_at);
CREATE INDEX IF NOT EXISTS idx_tx_statement ON transactions(statement_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_statement_no ON statements(kind, partner, statement_no) WHERE statement_no <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_sso ON users(sso_subject) WHERE sso_subject <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_approval ON documents(approval_no) WHERE approval_no <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_tx_reversal  ON transactions(reversal_of) WHERE reversal_of IS NOT NULL;
"""

SQLITE_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, '감사로그는 수정할 수 없습니다'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, '감사로그는 삭제할 수 없습니다'); END;
CREATE TRIGGER IF NOT EXISTS tx_no_update BEFORE UPDATE ON transactions
BEGIN SELECT RAISE(ABORT, '거래는 수정할 수 없습니다. 취소 거래로 처리하세요'); END;
CREATE TRIGGER IF NOT EXISTS tx_no_delete BEFORE DELETE ON transactions
BEGIN SELECT RAISE(ABORT, '거래는 삭제할 수 없습니다. 취소 거래로 처리하세요'); END;
"""

PG_TRIGGERS = """
CREATE OR REPLACE FUNCTION mm_block_change() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '%', TG_ARGV[0]; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_no_change ON audit_log;
CREATE TRIGGER audit_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION mm_block_change('감사로그는 수정·삭제할 수 없습니다');
DROP TRIGGER IF EXISTS tx_no_change ON transactions;
CREATE TRIGGER tx_no_change BEFORE UPDATE OR DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION mm_block_change('거래는 수정·삭제할 수 없습니다. 취소 거래로 처리하세요');
"""

# 재고 증감 = 입고 합 - 출고 합 + 조정(부호 포함) 합. 취소 거래는 수량 부호가 반대라 그대로 상쇄된다.
STOCK_EXPR = """
COALESCE(SUM(CASE WHEN t.tx_type = 'IN'  THEN t.qty
                  WHEN t.tx_type = 'OUT' THEN -t.qty
                  ELSE t.qty END), 0)
"""


def _schema_sql(text: str) -> str:
    if is_pg():
        return (text.replace("{ID}", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
                .replace("{REAL}", "DOUBLE PRECISION").replace("{NOCASE}", ""))
    return (text.replace("{ID}", "INTEGER PRIMARY KEY AUTOINCREMENT")
            .replace("{REAL}", "REAL").replace("{NOCASE}", "COLLATE NOCASE"))


# ── 커넥션 래퍼 ─────────────────────────────────────────────
class Row(dict):
    """이름(row['code'])과 순서(row[0]) 둘 다로 꺼낼 수 있는 행 (PostgreSQL용).
    이름이 겹치는 컬럼(예: 별칭 없는 COALESCE 두 개)도 순서로는 모두 꺼낼 수 있게 값을 따로 둔다."""

    __slots__ = ("vals",)

    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self.vals = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.vals[key]
        return super().__getitem__(key)


def _pg_row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(names, values)


_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.IGNORECASE)


class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cur, "lastrowid", None)
        self.rowcount = cur.rowcount
        self.description = cur.description

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())


class Conn:
    """sqlite3 / psycopg 커넥션을 같은 방식으로 쓰게 하는 얇은 래퍼."""

    def __init__(self, raw, pg: bool):
        self.raw = raw
        self.pg = pg

    def execute(self, sql: str, params: Sequence = ()) -> Cursor:
        if not self.pg:
            return Cursor(self.raw.execute(sql, tuple(params)))
        m = _INSERT_RE.match(sql)
        returning = bool(m and m.group(1).lower() not in NO_ID_TABLES and "RETURNING" not in sql.upper()
                         and "ON CONFLICT" not in sql.upper())
        q = _pg(sql, params) + (" RETURNING id" if returning else "")
        cur = self.raw.execute(q, tuple(params) if params else None)
        lastrowid = cur.fetchone()["id"] if returning else None
        return Cursor(cur, lastrowid)

    def executemany(self, sql: str, rows: Iterable[Sequence]) -> None:
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        if not self.pg:
            self.raw.executemany(sql, rows)
        else:
            with self.raw.cursor() as cur:
                cur.executemany(_pg(sql, rows[0]), rows)

    def executescript(self, script: str) -> None:
        if not self.pg:
            self.raw.executescript(script)
        else:
            self.raw.execute(script)


def _pg(sql: str, params) -> str:
    """'?' → '%s'. 파라미터가 있을 때만 %를 이스케이프한다(없으면 psycopg가 SQL을 그대로 보낸다)."""
    if not params:
        return sql
    return sql.replace("%", "%%").replace("?", "%s")


_pool = None
_pool_url = ""
_pool_lock = threading.Lock()


def _pg_pool():
    """프로세스(서버)마다 커넥션 풀 하나."""
    global _pool, _pool_url
    with _pool_lock:
        if _pool is None or _pool_url != config.DATABASE_URL:
            from psycopg_pool import ConnectionPool
            if _pool is not None:
                _pool.close()
            _pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=config.DB_POOL_SIZE,
                                   kwargs={"row_factory": _pg_row_factory}, open=True)
            _pool_url = config.DATABASE_URL
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(close_pool)


def connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")      # 커밋이 디스크에 기록된 뒤 끝난다 (정전 때 마지막 거래를 잃지 않게)
    return conn


@contextmanager
def get_conn() -> Iterator[Conn]:
    """읽기용 커넥션."""
    if is_pg():
        pool = _pg_pool()
        raw = pool.getconn()
        raw.autocommit = True
        try:
            yield Conn(raw, True)
        finally:
            pool.putconn(raw)
    else:
        raw = connect_sqlite()
        try:
            yield Conn(raw, False)
        finally:
            raw.close()


@contextmanager
def transaction() -> Iterator[Conn]:
    """쓰기 트랜잭션. 예외 시 롤백, 정상 종료 시 커밋.

    SQLite: BEGIN IMMEDIATE로 쓰기 잠금을 먼저 잡는다 → 재고 확인과 기록 사이에 끼어들 수 없다.
    PostgreSQL: 같은 자재를 바꾸는 작업은 lock(conn, ...)으로 줄 세운다(다른 자재는 동시에 처리).
    """
    if is_pg():
        pool = _pg_pool()
        raw = pool.getconn()
        raw.autocommit = False
        try:
            yield Conn(raw, True)
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            pool.putconn(raw)
        return
    raw = connect_sqlite()
    raw.isolation_level = None
    try:
        raw.execute("BEGIN IMMEDIATE")
        try:
            yield Conn(raw, False)
        except BaseException:
            raw.execute("ROLLBACK")
            raise
        raw.execute("COMMIT")
    finally:
        raw.close()


def lock(conn: Conn, key: str) -> None:
    """트랜잭션이 끝날 때까지 key 단위로 줄 세운다 (PostgreSQL). SQLite는 이미 전체 쓰기 잠금이라 할 일 없음."""
    if conn.pg:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))


def lock_shared(conn: Conn, key: str) -> None:
    """공유 잠금 (PostgreSQL). 같은 key의 lock()(배타)과만 서로 기다린다 — 거래 등록끼리는 막지 않는다."""
    if conn.pg:
        conn.execute("SELECT pg_advisory_xact_lock_shared(hashtext(?))", (key,))


def skip_locked() -> str:
    """배치가 대기열을 가져갈 때 다른 서버가 잡은 행을 건너뛰는 절 (PostgreSQL)."""
    return " FOR UPDATE SKIP LOCKED" if is_pg() else ""


# ── 조회 도우미 ─────────────────────────────────────────────
def frame(conn: Conn, sql: str, params: Sequence = ()) -> pd.DataFrame:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [r.vals if isinstance(r, Row) else tuple(r) for r in cur.fetchall()]
    return pd.DataFrame.from_records(rows, columns=cols, coerce_float=True)


def query_df(sql: str, params: Sequence = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return frame(conn, sql, params)


def scalar(sql: str, params: Sequence = ()) -> Any:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else row[0]


def execute(sql: str, params: Sequence = ()) -> int:
    with transaction() as conn:
        return conn.execute(sql, params).lastrowid


def executemany(sql: str, rows: Iterable[Sequence]) -> None:
    with transaction() as conn:
        conn.executemany(sql, rows)


def in_clause(ids: Iterable[int] | None) -> tuple[str, list]:
    """'IN (?, ?)' 조각. ids가 None이면 제한 없음(''), 비어 있으면 아무것도 안 맞게."""
    if ids is None:
        return "", []
    ids = list(ids)
    if not ids:
        return " IN (NULL)", []
    return f" IN ({','.join('?' * len(ids))})", ids


# ── 초기화 · 마이그레이션 ──────────────────────────────────
def _columns(conn: Conn, table: str) -> set[str]:
    if conn.pg:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))
        return {r["column_name"] for r in rows}
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_snapshots(conn: Conn, default_wh: int) -> None:
    """예전 스냅샷 테이블(창고·로트 컬럼 없음)을 (월, 자재, 창고, 로트) 기본키로 바꾼다."""
    cols = _columns(conn, "inventory_snapshots")
    if {"warehouse_id", "lot_no"} <= cols:
        return
    wh_expr = "warehouse_id" if "warehouse_id" in cols else str(int(default_wh))
    if conn.pg:
        if "warehouse_id" not in cols:
            conn.execute(f"ALTER TABLE inventory_snapshots ADD COLUMN warehouse_id INTEGER NOT NULL DEFAULT {int(default_wh)}")
        conn.executescript("""
            ALTER TABLE inventory_snapshots ADD COLUMN lot_no TEXT NOT NULL DEFAULT '';
            ALTER TABLE inventory_snapshots DROP CONSTRAINT IF EXISTS inventory_snapshots_pkey;
            ALTER TABLE inventory_snapshots ADD PRIMARY KEY (ym, material_id, warehouse_id, lot_no);
        """)
        return
    conn.executescript(f"""
        CREATE TABLE inventory_snapshots_new (
            ym TEXT NOT NULL, material_id INTEGER NOT NULL REFERENCES materials(id),
            warehouse_id INTEGER NOT NULL REFERENCES warehouses(id), lot_no TEXT NOT NULL DEFAULT '',
            qty REAL NOT NULL, PRIMARY KEY (ym, material_id, warehouse_id, lot_no));
        INSERT INTO inventory_snapshots_new (ym, material_id, warehouse_id, lot_no, qty)
            SELECT ym, material_id, {wh_expr}, '', qty FROM inventory_snapshots;
        DROP TABLE inventory_snapshots;
        ALTER TABLE inventory_snapshots_new RENAME TO inventory_snapshots;
    """)


def _default_org(conn: Conn) -> int:
    """플랜트·창고가 하나도 없으면 기본값을 만들고, 창고가 비어 있는 예전 거래를 기본 창고에 넣는다."""
    row = conn.execute("SELECT id FROM warehouses ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        wh = int(row["id"])
    else:
        # 예전에는 SAP 플랜트·저장위치를 자재에 적었다 → 가장 많이 쓴 값을 기본 창고로 옮긴다
        plant = conn.execute("SELECT plant FROM materials WHERE plant <> '' GROUP BY plant "
                             "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        sloc = conn.execute("SELECT sloc FROM materials WHERE sloc <> '' GROUP BY sloc "
                            "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        ts = now_str()
        pid = conn.execute("INSERT INTO plants (code, name, sap_plant, created_at) VALUES (?, ?, ?, ?)",
                           ("P1", "기본 공장", plant["plant"] if plant else "", ts)).lastrowid
        wh = conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, ?, ?, ?, ?)",
                          (pid, "WH1", "기본 창고", sloc["sloc"] if sloc else "", ts)).lastrowid
    if conn.execute("SELECT 1 FROM transactions WHERE warehouse_id IS NULL LIMIT 1").fetchone():
        conn.executescript("DROP TRIGGER IF EXISTS tx_no_update;" if not conn.pg
                           else "DROP TRIGGER IF EXISTS tx_no_change ON transactions;")
        conn.execute("UPDATE transactions SET warehouse_id = ? WHERE warehouse_id IS NULL", (wh,))
    return wh


def init_db() -> None:
    if is_pg():
        with transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('mm_init_db'))")   # 서버 여러 대가 동시에 기동해도 한 번만
            _init(conn)
        return
    raw = connect_sqlite()
    try:
        _init(Conn(raw, False))
        raw.commit()
    finally:
        raw.close()


def _init(conn: Conn) -> None:
    conn.executescript(_schema_sql(SCHEMA))
    for table, column, ddl in MIGRATIONS:
        if column not in _columns(conn, table):
            conn.executescript(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    wh = _default_org(conn)
    _migrate_snapshots(conn, wh)
    conn.executescript(INDEXES)
    conn.executescript(PG_TRIGGERS if conn.pg else SQLITE_TRIGGERS)
    # 2단계 인증 기능은 2026-10-02 삭제했다. 예전 DB에 남은 비밀키·복구 코드는 지운다(칸은 그대로 둔다).
    if "totp_secret" in _columns(conn, "users"):
        conn.execute("UPDATE users SET totp_secret = '', totp_enabled = 0, totp_last_step = 0, recovery_codes = '' "
                     "WHERE totp_secret <> '' OR recovery_codes <> '' OR totp_enabled <> 0")


def reset_database() -> None:
    """테스트 전용: DB를 비운다. 이름에 'test'가 없는 PostgreSQL DB나 기본 SQLite 파일은 거부한다."""
    if is_pg():
        if "test" not in config.DATABASE_URL.rsplit("/", 1)[-1]:
            raise RuntimeError("테스트 DB(이름에 test 포함)만 초기화할 수 있습니다.")
    else:
        from pathlib import Path
        if Path(config.DB_PATH).resolve() == (config.DATA_DIR / "materials.db").resolve():
            raise RuntimeError("운영 DB 파일은 초기화할 수 없습니다.")
    wipe_database()


def wipe_database() -> None:
    """DB를 통째로 비우고 빈 구조를 다시 만든다. 테스트(reset_database)와 시연 초기화(core/demo.py)만 쓴다."""
    if is_pg():
        with transaction() as conn:
            conn.executescript("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    else:
        from pathlib import Path
        for suffix in ("", "-wal", "-shm"):
            Path(f"{config.DB_PATH}{suffix}").unlink(missing_ok=True)
    init_db()

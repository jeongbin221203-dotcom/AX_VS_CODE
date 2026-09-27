"""v2 스키마(SQLite) — 전환 전(Streamlit 판)·초기 Flask 판 DB 를 무손실로 올리는 기준선.

0001_baseline 마이그레이션이 SQLite 일 때 사용한다. 새 변경은 여기 말고 새 마이그레이션 파일에 쓴다.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    biz_no      TEXT,
    industry    TEXT,
    grade       TEXT    DEFAULT 'B',
    owner       TEXT    NOT NULL,
    manager     TEXT,
    phone       TEXT,
    email       TEXT,
    address     TEXT,
    status      TEXT    DEFAULT '활성',
    memo        TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cust_owner ON customers(owner);
CREATE INDEX IF NOT EXISTS idx_cust_name  ON customers(name);

CREATE TABLE IF NOT EXISTS deals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    owner          TEXT    NOT NULL,
    stage          TEXT    NOT NULL DEFAULT '리드',
    amount         INTEGER NOT NULL DEFAULT 0,
    probability    INTEGER NOT NULL DEFAULT 10,
    expected_close TEXT,
    source         TEXT,
    competitor     TEXT,
    memo           TEXT,
    closed_at      TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deal_cust  ON deals(customer_id);
CREATE INDEX IF NOT EXISTS idx_deal_stage ON deals(stage);
CREATE INDEX IF NOT EXISTS idx_deal_owner ON deals(owner);

CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deal_id     INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    act_date    TEXT    NOT NULL,
    act_type    TEXT    NOT NULL,
    owner       TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    next_action TEXT,
    next_date   TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_cust ON activities(customer_id);
CREATE INDEX IF NOT EXISTS idx_act_date ON activities(act_date);

CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deal_id     INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    sale_date   TEXT    NOT NULL,
    item        TEXT    NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 1,
    unit_price  INTEGER NOT NULL DEFAULT 0,
    amount      INTEGER NOT NULL DEFAULT 0,
    owner       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT '입금대기',
    memo        TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sale_date  ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_sale_owner ON sales(owner);

CREATE TABLE IF NOT EXISTS targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    yyyymm        TEXT    NOT NULL,
    owner         TEXT    NOT NULL,
    owner_id      INTEGER,
    owner_key     TEXT    NOT NULL,          -- 사용자 id (미연결 이관 데이터는 'n:이름')
    target_amount INTEGER NOT NULL DEFAULT 0,
    UNIQUE(yyyymm, owner_key)
);
"""


SCHEMA_ENTERPRISE = """
-- 조직도: 본부 → 팀 계층 구조 (parent_id 로 자기참조)
CREATE TABLE IF NOT EXISTS orgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    parent_id  INTEGER REFERENCES orgs(id) ON DELETE SET NULL,
    org_type   TEXT    DEFAULT '팀',
    created_at TEXT    NOT NULL
);

-- 사용자: 로그인 주체이자 데이터 접근 범위의 기준
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_no     TEXT    NOT NULL UNIQUE,
    name       TEXT    NOT NULL,
    role       TEXT    NOT NULL DEFAULT 'REP',
    org_id     INTEGER REFERENCES orgs(id) ON DELETE SET NULL,
    email      TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    pw_hash    TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);

-- 감사로그: 누가 언제 무엇을 바꿨는지 (내부통제/감사 대응)
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

-- 단계 이력: 전환율과 단계별 체류일(Sales Velocity) 산출 근거
CREATE TABLE IF NOT EXISTS deal_stage_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    from_stage    TEXT,
    to_stage      TEXT    NOT NULL,
    changed_at    TEXT    NOT NULL,
    actor         TEXT,
    days_in_stage INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hist_deal ON deal_stage_history(deal_id);

-- 할인 승인 결재 (Deal Desk)
CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    kind          TEXT    NOT NULL DEFAULT '할인승인',
    requested_by  TEXT    NOT NULL,
    requested_at  TEXT    NOT NULL,
    list_amount   INTEGER NOT NULL DEFAULT 0,
    final_amount  INTEGER NOT NULL DEFAULT 0,
    discount_rate REAL    NOT NULL DEFAULT 0,
    required_role TEXT,
    reason        TEXT,
    status        TEXT    NOT NULL DEFAULT '대기',
    approver      TEXT,
    decided_at    TEXT,
    comment       TEXT
);
CREATE INDEX IF NOT EXISTS idx_appr_status ON approvals(status);

-- 주간 파이프라인 스냅샷: 예측 정확도와 파이프라인 변동 추적
CREATE TABLE IF NOT EXISTS pipeline_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    snap_date TEXT    NOT NULL,
    yyyymm    TEXT    NOT NULL,
    owner     TEXT    NOT NULL,
    owner_id  INTEGER,
    owner_key TEXT    NOT NULL,
    category  TEXT    NOT NULL,
    deal_cnt  INTEGER NOT NULL DEFAULT 0,
    amount    INTEGER NOT NULL DEFAULT 0,
    weighted  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(snap_date, yyyymm, owner_key, category)
);
CREATE INDEX IF NOT EXISTS idx_snap_ym ON pipeline_snapshots(yyyymm);

-- ERP 전송 대기열 (Outbox): 업무 트랜잭션과 ERP 전송을 분리해 ERP 장애가 영업 입력을 막지 않게 한다
CREATE TABLE IF NOT EXISTS erp_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type   TEXT    NOT NULL,             -- 매출 / 매출취소
    ref_id     INTEGER NOT NULL,             -- sales.id
    status     TEXT    NOT NULL DEFAULT '대기',   -- 대기 / 전송완료 / 실패 / 취소
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    erp_doc_no TEXT,
    payload    TEXT,                         -- 마지막으로 보낸 내용 (대사·감사 근거)
    created_at TEXT    NOT NULL,
    sent_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON erp_outbox(status);

-- 증빙: 세금계산서 · 전자세금계산서 · 거래명세서 (이미지/PDF/XML). 삭제하지 않고 무효 처리한다
CREATE TABLE IF NOT EXISTS sale_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id         INTEGER NOT NULL REFERENCES sales(id),
    doc_type        TEXT    NOT NULL,
    issue_date      TEXT,
    approval_no     TEXT,
    supplier_biz_no TEXT,
    buyer_biz_no    TEXT,
    supply_amount   INTEGER,
    tax_amount      INTEGER,
    total_amount    INTEGER,
    memo            TEXT,
    file_path       TEXT    NOT NULL,
    file_name       TEXT    NOT NULL,
    mime            TEXT    NOT NULL,
    file_size       INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT    NOT NULL,
    check_result    TEXT,
    uploaded_by     TEXT,
    uploaded_by_id  INTEGER,
    uploaded_at     TEXT    NOT NULL,
    voided_at       TEXT,
    void_reason     TEXT,
    voided_by       TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_sale ON sale_documents(sale_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_approval ON sale_documents(approval_no)
    WHERE approval_no IS NOT NULL AND voided_at IS NULL;
"""

# 마이그레이션(컬럼 추가) 이후에 만들어야 하는 인덱스·트리거
SCHEMA_POST = """
CREATE INDEX IF NOT EXISTS idx_cust_owner_id ON customers(owner_id);
CREATE INDEX IF NOT EXISTS idx_deal_owner_id ON deals(owner_id);
CREATE INDEX IF NOT EXISTS idx_act_owner_id  ON activities(owner_id);
CREATE INDEX IF NOT EXISTS idx_sale_owner_id ON sales(owner_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor   ON audit_log(actor_id);

-- 감사로그는 추가만 가능하다 (관리자 화면·초기화로도 지울 수 없다)
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, '감사로그는 수정할 수 없습니다'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, '감사로그는 삭제할 수 없습니다'); END;
"""

# 기존 DB를 무손실 업그레이드하기 위한 추가 컬럼 정의 (테이블, 컬럼, DDL)
MIGRATIONS = [
    ("deals", "forecast_category", "TEXT DEFAULT 'Pipeline'"),
    ("deals", "lost_reason", "TEXT"),
    ("deals", "list_amount", "INTEGER DEFAULT 0"),
    ("deals", "discount_rate", "REAL DEFAULT 0"),
    ("deals", "approval_status", "TEXT DEFAULT '미요청'"),
    ("deals", "m_metrics", "INTEGER DEFAULT 0"),
    ("deals", "m_econ_buyer", "INTEGER DEFAULT 0"),
    ("deals", "m_criteria", "INTEGER DEFAULT 0"),
    ("deals", "m_process", "INTEGER DEFAULT 0"),
    ("deals", "m_pain", "INTEGER DEFAULT 0"),
    ("deals", "m_champion", "INTEGER DEFAULT 0"),
    ("deals", "stage_since", "TEXT"),
    ("customers", "credit_limit", "INTEGER DEFAULT 0"),
    ("customers", "payment_terms", "INTEGER DEFAULT 30"),
    ("customers", "org_id", "INTEGER"),
    ("sales", "due_date", "TEXT"),
    ("sales", "paid_amount", "INTEGER DEFAULT 0"),
    # 담당자 id 연결 / 동시수정 버전 / ERP·취소 / 결재 주체 / 감사 해시 / 로그인 잠금
    ("customers", "owner_id", "INTEGER"),
    ("customers", "erp_code", "TEXT"),
    ("customers", "row_version", "INTEGER DEFAULT 0"),
    ("deals", "owner_id", "INTEGER"),
    ("deals", "row_version", "INTEGER DEFAULT 0"),
    ("activities", "owner_id", "INTEGER"),
    ("sales", "owner_id", "INTEGER"),
    ("sales", "item_code", "TEXT"),
    ("sales", "cancelled_at", "TEXT"),
    ("sales", "cancel_reason", "TEXT"),
    ("sales", "erp_status", "TEXT"),
    ("sales", "erp_doc_no", "TEXT"),
    ("approvals", "requested_by_id", "INTEGER"),
    ("approvals", "approver_id", "INTEGER"),
    ("audit_log", "actor_id", "INTEGER"),
    ("audit_log", "prev_hash", "TEXT"),
    ("audit_log", "hash", "TEXT"),
    ("users", "failed_logins", "INTEGER DEFAULT 0"),
    ("users", "locked_until", "TEXT"),
    ("users", "pw_changed_at", "TEXT"),
    ("users", "must_change_pw", "INTEGER DEFAULT 0"),
]


-- 전환 전(Streamlit 판) 영업관리 DB 스키마 — 마이그레이션 테스트용
CREATE TABLE activities (
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
CREATE TABLE approvals (
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
CREATE TABLE audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    detail    TEXT
);
CREATE TABLE customers (
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
, credit_limit INTEGER DEFAULT 0, payment_terms INTEGER DEFAULT 30, org_id INTEGER);
CREATE TABLE deal_stage_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id       INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    from_stage    TEXT,
    to_stage      TEXT    NOT NULL,
    changed_at    TEXT    NOT NULL,
    actor         TEXT,
    days_in_stage INTEGER DEFAULT 0
);
CREATE TABLE deals (
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
, forecast_category TEXT DEFAULT 'Pipeline', lost_reason TEXT, list_amount INTEGER DEFAULT 0, discount_rate REAL DEFAULT 0, approval_status TEXT DEFAULT '미요청', m_metrics INTEGER DEFAULT 0, m_econ_buyer INTEGER DEFAULT 0, m_criteria INTEGER DEFAULT 0, m_process INTEGER DEFAULT 0, m_pain INTEGER DEFAULT 0, m_champion INTEGER DEFAULT 0, stage_since TEXT);
CREATE TABLE orgs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    parent_id  INTEGER REFERENCES orgs(id) ON DELETE SET NULL,
    org_type   TEXT    DEFAULT '팀',
    created_at TEXT    NOT NULL
);
CREATE TABLE pipeline_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    snap_date TEXT    NOT NULL,
    yyyymm    TEXT    NOT NULL,
    owner     TEXT    NOT NULL,
    category  TEXT    NOT NULL,
    deal_cnt  INTEGER NOT NULL DEFAULT 0,
    amount    INTEGER NOT NULL DEFAULT 0,
    weighted  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(snap_date, yyyymm, owner, category)
);
CREATE TABLE sales (
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
, due_date TEXT, paid_amount INTEGER DEFAULT 0);
CREATE TABLE targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    yyyymm        TEXT    NOT NULL,
    owner         TEXT    NOT NULL,
    target_amount INTEGER NOT NULL DEFAULT 0,
    UNIQUE(yyyymm, owner)
);
CREATE TABLE users (
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
CREATE INDEX idx_act_cust ON activities(customer_id);
CREATE INDEX idx_act_date ON activities(act_date);
CREATE INDEX idx_appr_status ON approvals(status);
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_cust_name  ON customers(name);
CREATE INDEX idx_cust_owner ON customers(owner);
CREATE INDEX idx_deal_cust  ON deals(customer_id);
CREATE INDEX idx_deal_owner ON deals(owner);
CREATE INDEX idx_deal_stage ON deals(stage);
CREATE INDEX idx_hist_deal ON deal_stage_history(deal_id);
CREATE INDEX idx_sale_date  ON sales(sale_date);
CREATE INDEX idx_sale_owner ON sales(owner);
CREATE INDEX idx_snap_ym ON pipeline_snapshots(yyyymm);
CREATE INDEX idx_users_name ON users(name);

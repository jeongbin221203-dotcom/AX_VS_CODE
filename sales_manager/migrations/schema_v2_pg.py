"""v2 스키마(PostgreSQL) — 0001_baseline 이 PostgreSQL 일 때 만든다.

SQLite 판과 같은 구조. 금액은 BIGINT(수십억 원 대응), 비율은 DOUBLE PRECISION.
날짜·시각은 두 DB 에서 같은 문자열 비교를 쓰도록 ISO-8601 TEXT 로 저장한다.
"""
DDL = """
CREATE TABLE orgs (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    parent_id  BIGINT REFERENCES orgs(id) ON DELETE SET NULL,
    org_type   TEXT DEFAULT '팀',
    created_at TEXT NOT NULL
);
CREATE TABLE users (
    id             BIGSERIAL PRIMARY KEY,
    emp_no         TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'REP',
    org_id         BIGINT REFERENCES orgs(id) ON DELETE SET NULL,
    email          TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    pw_hash        TEXT,
    created_at     TEXT NOT NULL,
    failed_logins  INTEGER DEFAULT 0,
    locked_until   TEXT,
    pw_changed_at  TEXT,
    must_change_pw INTEGER DEFAULT 0
);
CREATE INDEX idx_users_name ON users(name);

CREATE TABLE customers (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    biz_no        TEXT,
    industry      TEXT,
    grade         TEXT DEFAULT 'B',
    owner         TEXT NOT NULL,
    manager       TEXT,
    phone         TEXT,
    email         TEXT,
    address       TEXT,
    status        TEXT DEFAULT '활성',
    memo          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    credit_limit  BIGINT DEFAULT 0,
    payment_terms INTEGER DEFAULT 30,
    org_id        BIGINT,
    owner_id      BIGINT,
    erp_code      TEXT,
    row_version   INTEGER DEFAULT 0
);
CREATE INDEX idx_cust_owner ON customers(owner);
CREATE INDEX idx_cust_name ON customers(name);
CREATE INDEX idx_cust_owner_id ON customers(owner_id);

CREATE TABLE deals (
    id                BIGSERIAL PRIMARY KEY,
    customer_id       BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    owner             TEXT NOT NULL,
    stage             TEXT NOT NULL DEFAULT '리드',
    amount            BIGINT NOT NULL DEFAULT 0,
    probability       INTEGER NOT NULL DEFAULT 10,
    expected_close    TEXT,
    source            TEXT,
    competitor        TEXT,
    memo              TEXT,
    closed_at         TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    forecast_category TEXT DEFAULT 'Pipeline',
    lost_reason       TEXT,
    list_amount       BIGINT DEFAULT 0,
    discount_rate     DOUBLE PRECISION DEFAULT 0,
    approval_status   TEXT DEFAULT '미요청',
    m_metrics         INTEGER DEFAULT 0,
    m_econ_buyer      INTEGER DEFAULT 0,
    m_criteria        INTEGER DEFAULT 0,
    m_process         INTEGER DEFAULT 0,
    m_pain            INTEGER DEFAULT 0,
    m_champion        INTEGER DEFAULT 0,
    stage_since       TEXT,
    owner_id          BIGINT,
    row_version       INTEGER DEFAULT 0
);
CREATE INDEX idx_deal_cust ON deals(customer_id);
CREATE INDEX idx_deal_stage ON deals(stage);
CREATE INDEX idx_deal_owner ON deals(owner);
CREATE INDEX idx_deal_owner_id ON deals(owner_id);

CREATE TABLE activities (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deal_id     BIGINT REFERENCES deals(id) ON DELETE SET NULL,
    act_date    TEXT NOT NULL,
    act_type    TEXT NOT NULL,
    owner       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    next_action TEXT,
    next_date   TEXT,
    created_at  TEXT NOT NULL,
    owner_id    BIGINT
);
CREATE INDEX idx_act_cust ON activities(customer_id);
CREATE INDEX idx_act_date ON activities(act_date);
CREATE INDEX idx_act_owner_id ON activities(owner_id);

CREATE TABLE sales (
    id            BIGSERIAL PRIMARY KEY,
    customer_id   BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deal_id       BIGINT REFERENCES deals(id) ON DELETE SET NULL,
    sale_date     TEXT NOT NULL,
    item          TEXT NOT NULL,
    qty           INTEGER NOT NULL DEFAULT 1,
    unit_price    BIGINT NOT NULL DEFAULT 0,
    amount        BIGINT NOT NULL DEFAULT 0,
    owner         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT '입금대기',
    memo          TEXT,
    created_at    TEXT NOT NULL,
    due_date      TEXT,
    paid_amount   BIGINT DEFAULT 0,
    owner_id      BIGINT,
    item_code     TEXT,
    cancelled_at  TEXT,
    cancel_reason TEXT,
    erp_status    TEXT,
    erp_doc_no    TEXT
);
CREATE INDEX idx_sale_date ON sales(sale_date);
CREATE INDEX idx_sale_owner ON sales(owner);
CREATE INDEX idx_sale_owner_id ON sales(owner_id);

CREATE TABLE targets (
    id            BIGSERIAL PRIMARY KEY,
    yyyymm        TEXT NOT NULL,
    owner         TEXT NOT NULL,
    owner_id      BIGINT,
    owner_key     TEXT NOT NULL,
    target_amount BIGINT NOT NULL DEFAULT 0,
    UNIQUE (yyyymm, owner_key)
);

CREATE TABLE audit_log (
    id        BIGSERIAL PRIMARY KEY,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id BIGINT,
    detail    TEXT,
    actor_id  BIGINT,
    prev_hash TEXT,
    hash      TEXT
);
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_audit_actor ON audit_log(actor_id);

CREATE TABLE deal_stage_history (
    id            BIGSERIAL PRIMARY KEY,
    deal_id       BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    from_stage    TEXT,
    to_stage      TEXT NOT NULL,
    changed_at    TEXT NOT NULL,
    actor         TEXT,
    days_in_stage INTEGER DEFAULT 0
);
CREATE INDEX idx_hist_deal ON deal_stage_history(deal_id);

CREATE TABLE approvals (
    id              BIGSERIAL PRIMARY KEY,
    deal_id         BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL DEFAULT '할인승인',
    requested_by    TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    list_amount     BIGINT NOT NULL DEFAULT 0,
    final_amount    BIGINT NOT NULL DEFAULT 0,
    discount_rate   DOUBLE PRECISION NOT NULL DEFAULT 0,
    required_role   TEXT,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT '대기',
    approver        TEXT,
    decided_at      TEXT,
    comment         TEXT,
    requested_by_id BIGINT,
    approver_id     BIGINT
);
CREATE INDEX idx_appr_status ON approvals(status);

CREATE TABLE pipeline_snapshots (
    id        BIGSERIAL PRIMARY KEY,
    snap_date TEXT NOT NULL,
    yyyymm    TEXT NOT NULL,
    owner     TEXT NOT NULL,
    owner_id  BIGINT,
    owner_key TEXT NOT NULL,
    category  TEXT NOT NULL,
    deal_cnt  INTEGER NOT NULL DEFAULT 0,
    amount    BIGINT NOT NULL DEFAULT 0,
    weighted  BIGINT NOT NULL DEFAULT 0,
    UNIQUE (snap_date, yyyymm, owner_key, category)
);
CREATE INDEX idx_snap_ym ON pipeline_snapshots(yyyymm);

CREATE TABLE erp_outbox (
    id         BIGSERIAL PRIMARY KEY,
    doc_type   TEXT NOT NULL,
    ref_id     BIGINT NOT NULL,
    status     TEXT NOT NULL DEFAULT '대기',
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    erp_doc_no TEXT,
    payload    TEXT,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE INDEX idx_outbox_status ON erp_outbox(status);

CREATE TABLE sale_documents (
    id              BIGSERIAL PRIMARY KEY,
    sale_id         BIGINT NOT NULL REFERENCES sales(id),
    doc_type        TEXT NOT NULL,
    issue_date      TEXT,
    approval_no     TEXT,
    supplier_biz_no TEXT,
    buyer_biz_no    TEXT,
    supply_amount   BIGINT,
    tax_amount      BIGINT,
    total_amount    BIGINT,
    memo            TEXT,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    mime            TEXT NOT NULL,
    file_size       BIGINT NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL,
    check_result    TEXT,
    uploaded_by     TEXT,
    uploaded_by_id  BIGINT,
    uploaded_at     TEXT NOT NULL,
    voided_at       TEXT,
    void_reason     TEXT,
    voided_by       TEXT
);
CREATE INDEX idx_doc_sale ON sale_documents(sale_id);
CREATE UNIQUE INDEX ux_doc_approval ON sale_documents(approval_no)
    WHERE approval_no IS NOT NULL AND voided_at IS NULL;

-- 감사로그는 추가만 가능하다 (관리자·초기화로도 지울 수 없다)
CREATE FUNCTION audit_log_readonly() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION '감사로그는 수정할 수 없습니다';
    END IF;
    RAISE EXCEPTION '감사로그는 삭제할 수 없습니다';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_readonly();
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_readonly();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_readonly();
"""

"""대기업 보완 — 거래처 중복·병합, 2단계 인증, 감사로그 보관 이관, 첨부, 개인정보 요청, 외화, 법인, 전자세금계산서

  customers        : biz_no_norm(숫자만, 중복 검사용), merged_into(병합된 거래처)
  users            : totp_secret(암호화), totp_enabled_at, totp_last_counter(재사용 방지), recovery_codes(해시)
  audit_archives   : 보관기간이 지난 감사로그를 파일로 옮긴 기록 — 옮긴 범위의 감사로그만 지울 수 있다(추가만 가능)
  attachments      : 영업기회·영업활동 첨부(제안서·계약서·회의록)
  pii_requests     : 개인정보 열람·삭제 요청 처리 기록
  entities         : 법인(상호·사업자번호·ERP 회사코드) — deals·quotes·sales.entity_id
  fx_rates         : 환율 — quotes·sales 의 currency · fx_rate · foreign_amount (원화 금액은 그대로 집계에 사용)
  etax_invoices    : 전자세금계산서 발행 요청·결과

Revision ID: 0009_enterprise_extras
Revises: 0008_data_safety
Create Date: 2026-10-02
"""
from ddl import add_columns, big, drop_columns, is_pg, pk, real, run

revision = "0009_enterprise_extras"
down_revision = "0008_data_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_columns("customers", ("biz_no_norm", "TEXT"), ("merged_into", big()))
    run("CREATE INDEX idx_cust_bizno ON customers(biz_no_norm)",
        "UPDATE customers SET biz_no_norm = REPLACE(REPLACE(REPLACE(biz_no, '-', ''), ' ', ''), '.', '') "
        "WHERE biz_no IS NOT NULL AND biz_no <> ''")
    add_columns("users", ("totp_secret", "TEXT"), ("totp_enabled_at", "TEXT"), ("totp_last_counter", big()),
                ("recovery_codes", "TEXT"))
    run(f"""
    CREATE TABLE audit_archives (
        id         {pk()},
        first_id   {big()} NOT NULL,
        last_id    {big()} NOT NULL,
        row_count  INTEGER NOT NULL,
        last_hash  TEXT,
        file_key   TEXT NOT NULL,
        sha256     TEXT NOT NULL,
        created_by TEXT,
        created_at TEXT NOT NULL
    )""", f"""
    CREATE TABLE attachments (
        id          {pk()},
        entity      TEXT NOT NULL,
        entity_id   {big()} NOT NULL,
        kind        TEXT,
        file_key    TEXT NOT NULL,
        file_name   TEXT NOT NULL,
        mime        TEXT NOT NULL,
        size        INTEGER NOT NULL,
        sha256      TEXT NOT NULL,
        memo        TEXT,
        uploaded_by TEXT,
        uploaded_at TEXT NOT NULL,
        voided_at   TEXT,
        voided_by   TEXT,
        void_reason TEXT
    )""", "CREATE INDEX idx_attach_ref ON attachments(entity, entity_id)", f"""
    CREATE TABLE pii_requests (
        id          {pk()},
        kind        TEXT NOT NULL,
        requester   TEXT,
        search_term TEXT NOT NULL,
        matched     INTEGER NOT NULL DEFAULT 0,
        result      TEXT,
        handled_by  TEXT,
        handled_at  TEXT NOT NULL
    )""", f"""
    CREATE TABLE entities (
        id               {pk()},
        code             TEXT NOT NULL UNIQUE,
        name             TEXT NOT NULL,
        biz_no           TEXT,
        ceo              TEXT,
        address          TEXT,
        erp_company_code TEXT,
        sap_sales_org    TEXT,
        active           INTEGER NOT NULL DEFAULT 1,
        created_at       TEXT NOT NULL
    )""", f"""
    CREATE TABLE fx_rates (
        currency  TEXT NOT NULL,
        rate_date TEXT NOT NULL,
        rate      {real()} NOT NULL,
        source    TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (currency, rate_date)
    )""", f"""
    CREATE TABLE etax_invoices (
        id          {pk()},
        sale_id     {big()} NOT NULL,
        status      TEXT NOT NULL,
        issue_date  TEXT NOT NULL,
        approval_no TEXT,
        asp_ref     TEXT,
        xml_key     TEXT,
        document_id {big()},
        error       TEXT,
        requested_by TEXT,
        requested_at TEXT NOT NULL,
        issued_at   TEXT
    )""", "CREATE INDEX idx_etax_sale ON etax_invoices(sale_id)")
    for table in ("deals", "quotes", "sales"):
        add_columns(table, ("entity_id", big()))
    for table in ("quotes", "sales"):
        add_columns(table, ("currency", "TEXT DEFAULT 'KRW'"), ("fx_rate", f"{real()} DEFAULT 1"),
                    ("foreign_amount", real()))
    run("UPDATE sales SET currency='KRW', fx_rate=1 WHERE currency IS NULL",
        "UPDATE quotes SET currency='KRW', fx_rate=1 WHERE currency IS NULL")

    # 감사로그: 보관 이관(audit_archives)에 기록된 범위만 지울 수 있다. 이관 기록 자체는 고치거나 지울 수 없다
    if is_pg():
        run("""
        CREATE OR REPLACE FUNCTION audit_log_readonly() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION '감사로그는 수정할 수 없습니다';
            END IF;
            IF TG_OP = 'DELETE' AND OLD.id <= (SELECT COALESCE(MAX(last_id), 0) FROM audit_archives) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION '감사로그는 삭제할 수 없습니다';
        END;
        $$ LANGUAGE plpgsql""", """
        CREATE FUNCTION audit_archives_readonly() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '감사로그 이관 기록은 바꿀 수 없습니다';
        END;
        $$ LANGUAGE plpgsql""",
            "CREATE TRIGGER trg_archive_no_update BEFORE UPDATE OR DELETE ON audit_archives "
            "FOR EACH ROW EXECUTE FUNCTION audit_archives_readonly()",
            "CREATE TRIGGER trg_archive_no_truncate BEFORE TRUNCATE ON audit_archives "
            "FOR EACH STATEMENT EXECUTE FUNCTION audit_archives_readonly()")
    else:
        run("DROP TRIGGER IF EXISTS trg_audit_no_delete", """
        CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
        WHEN OLD.id > (SELECT COALESCE(MAX(last_id), 0) FROM audit_archives)
        BEGIN SELECT RAISE(ABORT, '감사로그는 삭제할 수 없습니다'); END""", """
        CREATE TRIGGER trg_archive_no_update BEFORE UPDATE ON audit_archives
        BEGIN SELECT RAISE(ABORT, '감사로그 이관 기록은 바꿀 수 없습니다'); END""", """
        CREATE TRIGGER trg_archive_no_delete BEFORE DELETE ON audit_archives
        BEGIN SELECT RAISE(ABORT, '감사로그 이관 기록은 바꿀 수 없습니다'); END""")


def downgrade() -> None:
    if is_pg():
        run("DROP TRIGGER trg_archive_no_update ON audit_archives",
            "DROP TRIGGER trg_archive_no_truncate ON audit_archives",
            "DROP FUNCTION audit_archives_readonly()", """
        CREATE OR REPLACE FUNCTION audit_log_readonly() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION '감사로그는 수정할 수 없습니다';
            END IF;
            RAISE EXCEPTION '감사로그는 삭제할 수 없습니다';
        END;
        $$ LANGUAGE plpgsql""")
    else:
        run("DROP TRIGGER IF EXISTS trg_audit_no_delete", "DROP TRIGGER IF EXISTS trg_archive_no_update",
            "DROP TRIGGER IF EXISTS trg_archive_no_delete", """
        CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT, '감사로그는 삭제할 수 없습니다'); END""")
    run("DROP TABLE etax_invoices", "DROP TABLE fx_rates", "DROP TABLE entities", "DROP TABLE pii_requests",
        "DROP TABLE attachments", "DROP TABLE audit_archives", "DROP INDEX idx_cust_bizno")
    for table in ("quotes", "sales"):
        drop_columns(table, "currency", "fx_rate", "foreign_amount")
    for table in ("deals", "quotes", "sales"):
        drop_columns(table, "entity_id")
    drop_columns("users", "totp_secret", "totp_enabled_at", "totp_last_counter", "recovery_codes")
    drop_columns("customers", "biz_no_norm", "merged_into")

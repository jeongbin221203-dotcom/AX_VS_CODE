"""회사별 설정 — 결재 구간·기한·코드 목록·회사 정보·개인정보 파기를 프로그램 수정 없이 바꾼다."""
from __future__ import annotations

from conftest import login, post, user

from core import company
from core import enterprise as ent
from core import sales_db as db

MEDDIC = {m: "1" for m in db.MEDDIC_FIELDS}


def _restore():
    db.set_context("system", None)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM company_settings")
    company.refresh(force=True)


def test_policy_changes_take_effect(app):
    admin = login(app, "시스템관리자")
    assert login(app, "김영업").get("/admin/settings").status_code == 403
    try:
        res = post(admin, "/admin/settings/save", {
            "section": "policy", "discount_manager_max": "5", "discount_exec_max": "15", "approval_sla_hours": "24",
            "quote_valid_days": "14", "default_payment_terms": "45", "pii_retention_years": "0",
            **{f"prob_{s}": p for s, p in zip(db.OPEN_STAGES, ["5", "20", "40", "60", "85"])}})
        assert res.status_code == 302
        assert db.required_approval_role(7) == "EXEC" and db.required_approval_role(16) == "ADMIN"
        assert db.STAGE_PROB["협상"] == 85 and ent.sla_hours() == 24
        assert "5% 이하: 팀장" in admin.get("/approvals").get_data(as_text=True)
        assert db._one("SELECT id FROM audit_log WHERE action='설정변경'")
        # 잘못된 값은 저장되지 않는다
        bad = post(admin, "/admin/settings/save", {"section": "policy", "discount_manager_max": "30",
                                                   "discount_exec_max": "20"}, follow_redirects=True)
        assert "임원 결재 한도는 팀장" in bad.get_data(as_text=True)
        bad = post(admin, "/admin/settings/save", {"section": "policy", **{f"prob_{s}": "50" for s in db.OPEN_STAGES},
                                                   "prob_리드": "90"}, follow_redirects=True)
        assert "순서로" in bad.get_data(as_text=True)
        # 7% 할인 → 팀장 → 임원 결재선
        rep = login(app, "김영업")
        post(rep, "/customers/save", {"name": "설정상사", "grade": "A", "industry": "제조"})
        cid = db._one("SELECT id, payment_terms FROM customers WHERE name='설정상사'")
        assert int(cid["payment_terms"]) == 45
        post(rep, "/deals/save", {"customer_id": cid["id"], "title": "설정 딜", "stage": "협상", "list_amount": "1000000",
                                  "discount_rate": "7", "expected_close": "2026-12-31", **MEDDIC})
        deal = db._one("SELECT * FROM deals WHERE title='설정 딜'")
        post(rep, "/deals/request-approval", {"deal_id": deal["id"], "reason": "테스트"})
        steps = db._df("SELECT role FROM approval_steps s JOIN approvals a ON a.id=s.approval_id WHERE a.deal_id=? "
                       "ORDER BY step_no", [deal["id"]])
        assert list(steps["role"]) == ["MANAGER", "EXEC"]
    finally:
        _restore()
    assert db.required_approval_role(7) == "MANAGER" and db.STAGE_PROB["협상"] == 80


def test_code_lists_and_company_info(app):
    admin = login(app, "시스템관리자")
    try:
        codes = {"section": "codes", "industries": "제조\n유통\n반도체\n기타", "lead_sources": "\n".join(db.LEAD_SOURCES),
                 "lost_reasons": "\n".join(db.LOST_REASONS), "act_types": "\n".join(db.ACT_TYPES)}
        used = db._df("SELECT DISTINCT industry FROM customers WHERE industry NOT IN ('제조','유통','기타')")
        res = post(admin, "/admin/settings/save", codes, follow_redirects=True)
        if not used.empty:                                         # 쓰이는 업종을 지우면 거부
            assert "지울 수 없습니다" in res.get_data(as_text=True)
            codes["industries"] = "\n".join([*db.INDUSTRIES, "반도체"])
            post(admin, "/admin/settings/save", codes)
        assert "반도체" in db.INDUSTRIES
        assert "반도체" in login(app, "김영업").get("/customers").get_data(as_text=True)
        bad = post(admin, "/admin/settings/save", {"section": "company", "company_name": "x", "company_biz_no": "1234567890",
                                                   "app_title": "x"}, follow_redirects=True)
        assert "사업자번호가 올바르지 않습니다" in bad.get_data(as_text=True)
        post(admin, "/admin/settings/save", {"section": "company", "company_name": "(주)설정테스트", "company_biz_no": "",
                                             "company_ceo": "홍대표", "company_address": "서울", "app_title": "설정CRM"})
        assert "설정CRM" in admin.get("/").get_data(as_text=True)
    finally:
        _restore()
    assert "반도체" not in db.INDUSTRIES


def test_pii_purge_for_closed_customers(app):
    admin = login(app, "시스템관리자")
    db.set_context("system", None)
    cid = db.upsert_customer({"name": "파기상사", "owner_id": user("김영업")["id"], "manager": "홍길동",
                              "phone": "010-1111-2222", "email": "a@b.kr", "status": "종료"})
    with db.get_conn() as conn:
        conn.execute("UPDATE customers SET updated_at='2020-01-01 00:00:00' WHERE id=?", (cid,))
    try:
        assert company.purge_pii()["purged"] == 0                 # 기본은 파기 안 함
        post(admin, "/admin/settings/save", {"section": "policy", "pii_retention_years": "3"})
        assert company.purge_pii(dry_run=True)["purged"] >= 1
        assert post(admin, "/admin/settings/purge", {"confirm": "1"}).status_code == 302
        row = db.get_customer(cid)
        assert row["phone"] is None and row["email"] is None and row["manager"] is None and row["name"] == "파기상사"
        assert db._one("SELECT id FROM audit_log WHERE action='개인정보파기'")
    finally:
        _restore()

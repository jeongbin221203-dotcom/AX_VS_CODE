"""화면 흐름 · 권한 · 통제 로직 (Flask 테스트 클라이언트, 임시 DB).

실행: python -m pytest tests -q
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import COMPANY_BIZ, ROLE_USERS, TMP, biz, csrf, login, post, user

from core import auth as core_auth
from core import sales_db as db

ALL_PAGES = ["/", "/forecast", "/analytics", "/customers", "/deals", "/activities", "/sales",
             "/targets", "/approvals", "/quotes", "/products", "/products?tab=prices", "/data/",
             "/admin/org", "/admin/erp", "/admin/jobs", "/admin/audit", "/admin/data", "/admin/settings",
             "/admin/privacy", "/orders", "/orders?new=1", "/approvals?tab=finance", "/customers?tab=merge", "/activities?tab=files", "/targets?fy=2026"]
MANAGER_PAGES: set[str] = set()      # 결재함은 대결자(담당자 포함)도 쓰므로 모든 역할에 열려 있다
ADMIN_PAGES = {"/admin/org", "/admin/erp", "/admin/jobs", "/admin/audit", "/admin/data", "/admin/settings",
               "/admin/privacy"}


# ============================================================================
# 구동 · 메뉴 권한
# ============================================================================
def test_login_required_and_healthz(app):
    client = app.test_client()
    for page in ALL_PAGES:
        res = client.get(page)
        assert res.status_code == 302 and "/login" in res.headers["Location"], page
    res = client.get("/healthz")
    assert res.status_code == 200 and res.get_json() == {"status": "ok"}


@pytest.mark.parametrize("role", list(ROLE_USERS))
def test_every_menu_renders_per_role(app, role):
    client = login(app, ROLE_USERS[role])
    for page in ALL_PAGES:
        res = client.get(page)
        need_manager = page in MANAGER_PAGES and role == "REP"
        need_admin = page in ADMIN_PAGES and role != "ADMIN"
        expected = 403 if (need_manager or need_admin) else 200
        assert res.status_code == expected, f"{role} {page} → {res.status_code}"


def test_tabs_filters_and_paging_render(app):
    client = login(app, "시스템관리자")
    owner = user("김영업")["id"]
    urls = ["/?tab=stale", "/?tab=upcoming", "/forecast?tab=snapshot", "/forecast?tab=accuracy",
            "/analytics?days=90&tab=competitor", "/analytics?tab=source",
            "/customers?tab=edit&id=1", "/customers?q=대한&grade=A",
            "/deals?tab=edit&id=1", "/deals?tab=move", "/deals?tab=approval", "/deals?open=0&stage=수주",
            "/activities?days=365&page=2", "/activities?tab=delete", "/sales?tab=ar&bucket=정상",
            "/sales?tab=credit", "/sales?sid=1", "/approvals?tab=history&status=승인",
            "/approvals?tab=requested", "/admin/org?tab=users&uid=2", "/admin/org?tab=transfer",
            "/admin/audit?entity=영업기회&limit=100", "/admin/audit?verify=1",
            "/admin/erp?tab=payments", "/admin/erp?tab=settings", "/admin/erp?status=대기",
            "/data/?entity=매출", "/data/export?preset=월간 실적 보고", f"/?ym=2026-01&owner={owner}"]
    assert "2/3쪽" in client.get("/activities?days=365&page=2").get_data(as_text=True)
    for url in urls:                         # 마지막 URL 이 담당자 필터를 세션에 남기므로 페이지 확인을 먼저 한다
        res = client.get(url)
        assert res.status_code == 200, f"{url} → {res.status_code}"


def test_security_headers(app):
    res = login(app, "김영업").get("/")
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy", "Cache-Control"):
        assert header in res.headers


# ============================================================================
# 권한 격리 · 동명이인
# ============================================================================
def test_data_scope_by_role(app):
    counts = {}
    for role, name in ROLE_USERS.items():
        res = login(app, name).get("/deals?open=0&export=deals")
        counts[role] = len(res.get_data().decode("utf-8-sig").strip().splitlines()) - 1
    assert (counts["REP"], counts["MANAGER"], counts["EXEC"]) == (7, 33, 55)
    assert counts["ADMIN"] == counts["EXEC"]


def test_rep_cannot_touch_other_team(app):
    other = db._one("SELECT id FROM customers WHERE owner='박고객'")["id"]
    client = login(app, "김영업")
    assert "수정할 거래처" in client.get(f"/customers?id={other}&tab=edit").get_data(as_text=True)
    assert post(client, "/customers/save", {"id": other, "name": "탈취"}).status_code == 403
    assert post(client, f"/customers/{other}/delete", {"confirm": "1"}).status_code == 403
    # 다른 팀 사용자를 담당자로 지정해 넘기는 것도 막힌다
    mine = db._one("SELECT id FROM customers WHERE owner='김영업'")["id"]
    res = post(client, "/customers/save", {"id": mine, "name": "넘기기", "owner_id": user("박고객")["id"]})
    assert res.status_code == 400 and "권한 범위 밖" in res.get_data(as_text=True)


def test_same_name_users_are_isolated(app):
    admin = login(app, "시스템관리자")
    team2 = db._one("SELECT id FROM orgs WHERE name='영업2팀'")["id"]
    post(admin, "/admin/users/save", {"emp_no": "3001", "name": "김영업", "role": "REP",
                                      "org_id": team2, "active": "1"})
    newbie = login(app, emp_no="3001")
    csv = newbie.get("/customers?export=customers").get_data().decode("utf-8-sig")
    assert len(csv.strip().splitlines()) == 1                  # 헤더만 — 기존 김영업의 거래처가 안 보인다
    # 이름만으로는 담당자를 정할 수 없다
    with pytest.raises(ValueError, match="동명이인"):
        db.set_context("system", None)
        db.resolve_owner("김영업")
    with db.get_conn() as conn:                                 # 뒤 테스트를 위해 비활성화
        conn.execute("UPDATE users SET active=0 WHERE emp_no='3001'")


# ============================================================================
# 할인 결재 통제
# ============================================================================
MEDDIC = {m: "1" for m in db.MEDDIC_FIELDS}


def _deal(client, cid, title, discount, **extra):
    data = {"customer_id": cid, "title": title, "stage": "협상", "list_amount": "100000000",
            "discount_rate": str(discount), "expected_close": "2026-12-31", **MEDDIC, **extra}
    return post(client, "/deals/save", data)


def test_approval_is_invalidated_when_discount_changes(app):
    rep = login(app, "김영업")
    post(rep, "/customers/save", {"name": "결재상사", "grade": "A", "industry": "제조"})
    cid = db._one("SELECT id FROM customers WHERE name='결재상사'")["id"]
    assert _deal(rep, cid, "결재 딜", 5).status_code == 302
    deal = db._one("SELECT * FROM deals WHERE title='결재 딜'")
    post(rep, "/deals/request-approval", {"deal_id": deal["id"], "reason": "경쟁 입찰"})
    appr = db._one("SELECT * FROM approvals WHERE deal_id=?", [deal["id"]])
    mgr = login(app, "한팀장")
    post(mgr, f"/approvals/{appr['id']}/decide", {"decision": "approve", "comment": "OK"})
    assert db.get_deal(deal["id"])["approval_status"] == "승인"

    # 승인 뒤 할인율을 40%로 올리면 승인이 무효가 되고 수주가 막힌다
    row = db.get_deal(deal["id"])
    res = _deal(rep, cid, "결재 딜", 40, id=deal["id"], row_version=row["row_version"])
    assert res.status_code == 302
    assert db.get_deal(deal["id"])["approval_status"] == "미요청"
    post(rep, "/deals/move", {"ids": [deal["id"]], "stage": "수주"})
    assert db.get_deal(deal["id"])["stage"] == "협상"
    assert db._one("SELECT action FROM audit_log WHERE action='승인무효화' AND entity_id=?", [deal["id"]])

    # 대기 중 조건이 바뀐 결재는 결재자가 승인할 수 없다
    post(rep, "/deals/request-approval", {"deal_id": deal["id"], "reason": "재요청"})
    appr2 = db._one("SELECT * FROM approvals WHERE deal_id=? AND status='대기'", [deal["id"]])
    row = db.get_deal(deal["id"])
    _deal(rep, cid, "결재 딜", 45, id=deal["id"], row_version=row["row_version"])
    assert db._one("SELECT status FROM approvals WHERE id=?", [appr2["id"]])["status"] == "취소"


def test_admin_cannot_self_approve(app):
    admin = login(app, "시스템관리자")
    cid = db._one("SELECT id FROM customers WHERE name='결재상사'")["id"]
    _deal(admin, cid, "관리자 딜", 50, owner_id=user("김영업")["id"])
    deal = db._one("SELECT * FROM deals WHERE title='관리자 딜'")
    post(admin, "/deals/request-approval", {"deal_id": deal["id"], "reason": "특가"})
    appr = db._one("SELECT * FROM approvals WHERE deal_id=?", [deal["id"]])
    assert "관리자 딜" not in admin.get("/approvals").get_data(as_text=True)     # 결재함에 안 보임
    assert post(admin, f"/approvals/{appr['id']}/decide", {"decision": "approve"}).status_code == 403
    assert db._one("SELECT status FROM approvals WHERE id=?", [appr["id"]])["status"] == "대기"


def test_optimistic_lock_blocks_lost_update(app):
    rep = login(app, "김영업")
    cid = db._one("SELECT id FROM customers WHERE name='결재상사'")["id"]
    version = db.get_customer(cid)["row_version"]
    assert post(rep, "/customers/save", {"id": cid, "name": "결재상사", "memo": "A 수정",
                                         "row_version": version}).status_code == 302
    res = post(rep, "/customers/save", {"id": cid, "name": "결재상사", "memo": "B 수정(옛 화면)",
                                        "row_version": version})
    assert res.status_code == 400 and "먼저" in res.get_data(as_text=True)
    assert db.get_customer(cid)["memo"] == "A 수정"


def test_admin_force_requires_reason(app):
    admin = login(app, "시스템관리자")
    cid = db._one("SELECT id FROM customers WHERE name='결재상사'")["id"]
    res = post(admin, "/deals/save", {"customer_id": cid, "title": "우회 딜", "stage": "수주",
                                      "list_amount": "1000", "force": "1", "owner_id": user("김영업")["id"]})
    assert res.status_code == 400 and "사유" in res.get_data(as_text=True)
    res = post(admin, "/deals/save", {"customer_id": cid, "title": "우회 딜", "stage": "수주", "list_amount": "1000",
                                      "force": "1", "force_reason": "이관 보정", "owner_id": user("김영업")["id"]})
    assert res.status_code == 302
    assert "이관 보정" in db._one("SELECT detail FROM audit_log WHERE entity='영업기회' ORDER BY id DESC")["detail"]


# ============================================================================
# 삭제 대신 보존 · 매출 취소
# ============================================================================
def test_delete_restrictions_and_sale_cancel(app):
    rep = login(app, "김영업")
    cid = db._one("SELECT id FROM customers WHERE name='결재상사'")["id"]
    res = post(rep, f"/customers/{cid}/delete", {"confirm": "1"}, follow_redirects=True)
    assert "삭제할 수 없습니다" in res.get_data(as_text=True) and db.get_customer(cid)

    post(rep, "/sales/add", {"customer_id": cid, "item": "라이선스", "item_code": "SW-01", "qty": "2",
                             "unit_price": "1500000", "status": "입금대기"})
    sale = db._one("SELECT * FROM sales WHERE customer_id=? ORDER BY id DESC", [cid])
    assert sale["amount"] == 3_000_000 and sale["erp_status"] == "대기"
    assert db._one("SELECT status FROM erp_outbox WHERE ref_id=?", [sale["id"]])["status"] == "대기"

    before = db.kpi_summary(sale["sale_date"][:7])["month_sales"]
    assert post(rep, f"/sales/{sale['id']}/cancel", {"reason": ""}, follow_redirects=True).status_code == 200
    assert db.get_sale(sale["id"])["status"] != "취소"               # 사유 없으면 거부
    post(rep, f"/sales/{sale['id']}/cancel", {"reason": "중복 입력"})
    assert db.get_sale(sale["id"])["status"] == "취소"
    assert db.kpi_summary(sale["sale_date"][:7])["month_sales"] == before - 3_000_000
    assert db._one("SELECT status FROM erp_outbox WHERE ref_id=?", [sale["id"]])["status"] == "취소"


def test_payment_keeps_paid_amount_on_edit(app):
    rep = login(app, "김영업")
    cid = db._one("SELECT id FROM customers WHERE name='결재상사'")["id"]
    post(rep, "/sales/add", {"customer_id": cid, "item": "유지보수", "qty": "1", "unit_price": "2000000"})
    sid = db._one("SELECT id FROM sales WHERE item='유지보수' AND customer_id=?", [cid])["id"]
    post(rep, "/sales/payment", {"sale_id": sid, "amount": "500000"})
    post(rep, f"/sales/{sid}/update", {"status": "부분입금", "amount": "2000000"})
    assert db.get_sale(sid)["paid_amount"] == 500_000                  # 수정해도 입금액 유지
    res = post(rep, "/sales/payment", {"sale_id": sid, "amount": "9000000"}, follow_redirects=True)
    assert "미수금" in res.get_data(as_text=True)                        # 과입금 거부


# ============================================================================
# 목표 · 일괄 등록 · 추출
# ============================================================================
def test_targets_by_user_id(app):
    rep = login(app, "김영업")
    ym = date.today().strftime("%Y-%m")
    post(rep, "/targets/save", {"owner_id": [user("김영업")["id"], user("박고객")["id"]],
                                "amount": ["70000000", "1"]})
    rows = {r.owner: r.target_amount for r in db._df(
        "SELECT owner, target_amount FROM targets WHERE yyyymm=?", [ym]).itertuples()}
    assert rows["김영업"] == 70_000_000 and rows.get("박고객") != 1


def test_import_check_then_run(app):
    rep = login(app, "김영업")
    csv = ("거래처명,담당자,등급\n업로드상사,김영업,A\n권한밖상사,박고객,B\n"
           "사번상사,2003,C\n없는사람상사,홍길동,B\n").encode("utf-8-sig")
    res = post(rep, "/data/import/check", {"entity": "거래처", "file": (io.BytesIO(csv), "up.csv")},
               content_type="multipart/form-data")
    html = res.get_data(as_text=True)
    assert res.status_code == 200 and "권한 범위 밖" in html and "등록된 활성 사용자가 아닙니다" in html
    assert rep.get("/data/import/errors.csv").status_code == 200
    assert post(rep, "/data/import/run", {"confirm": "1"}).status_code == 302
    names = set(db._df("SELECT name FROM customers")["name"])
    assert {"업로드상사", "사번상사"} <= names and not {"권한밖상사", "없는사람상사"} & names
    assert not list((TMP / "storage" / "uploads").glob("*"))        # 임시 파일 정리


def test_rep_deal_import_applies_stage_gate(app):
    rep = login(app, "김영업")
    csv = "거래처명,기회명,담당자,단계,정가\n업로드상사,게이트우회,김영업,협상,10000000\n".encode("utf-8-sig")
    res = post(rep, "/data/import/check", {"entity": "영업기회", "file": (io.BytesIO(csv), "d.csv")},
               content_type="multipart/form-data")
    assert "조건 미충족" in res.get_data(as_text=True)


def test_exports_mask_pii_and_block_formula_injection(app):
    rep = login(app, "김영업")
    post(rep, "/customers/save", {"name": "=HYPERLINK(\"http://evil\")", "phone": "010-1234-5678",
                                  "email": "hong@example.com", "manager": "홍길동"})
    text = rep.get("/customers?export=customers").get_data().decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "010-****-5678" in text and "ho***@example.com" in text
    assert "010-1234-5678" not in text

    url = "/data/export?sources=거래처&csv=거래처&pii=1"
    res = rep.get(url)
    assert res.status_code == 302                                        # 사유 없으면 원문 불가
    text = rep.get(url + "&pii_reason=만족도 조사").get_data().decode("utf-8-sig")
    assert "010-1234-5678" in text
    detail = db._one("SELECT detail FROM audit_log WHERE action='다운로드' ORDER BY id DESC")["detail"]
    assert '"개인정보포함": true' in detail


def test_downloads_are_audited(app):
    admin = login(app, "시스템관리자")
    for url, mime in [("/data/template/매출.csv", "text/csv"),
                      ("/data/template/매출.xlsx", "spreadsheetml"),
                      ("/data/export?preset=내부통제 점검&download=xlsx", "spreadsheetml"),
                      ("/admin/data/backup.xlsx", "spreadsheetml"),
                      ("/sales?export=ar", "text/csv"),
                      ("/admin/audit?export=audit", "text/csv")]:
        before = db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='다운로드'")
        res = admin.get(url)
        assert res.status_code == 200 and mime in res.mimetype, url
        assert db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='다운로드'") == before + 1, url


# ============================================================================
# 증빙 (세금계산서 · 전자세금계산서)
# ============================================================================
def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), (240, 240, 240)).save(buf, "PNG")
    return buf.getvalue()


def _etax_xml(approval: str, supplier: str, buyer: str, supply: int, tax: int, issued: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TaxInvoice xmlns="urn:kr:or:kec:standard:Tax:ReusableAggregateBusinessInformationEntitySchemaModule:1:0">
  <TaxInvoiceDocument><IssueID>{approval}</IssueID><TypeCode>0101</TypeCode><IssueDateTime>{issued}</IssueDateTime></TaxInvoiceDocument>
  <TaxInvoiceTradeSettlement>
    <InvoicerParty><ID>{supplier}</ID><NameText>우리회사</NameText></InvoicerParty>
    <InvoiceeParty><ID>{buyer}</ID><NameText>고객사</NameText></InvoiceeParty>
    <SpecifiedMonetarySummation><ChargeTotalAmount>{supply}</ChargeTotalAmount>
      <TaxTotalAmount>{tax}</TaxTotalAmount><GrandTotalAmount>{supply + tax}</GrandTotalAmount></SpecifiedMonetarySummation>
  </TaxInvoiceTradeSettlement>
</TaxInvoice>""".encode("utf-8")


def _sale_for_docs(rep):
    buyer = biz("220810000")
    post(rep, "/customers/save", {"name": "증빙상사", "biz_no": buyer, "erp_code": "C900"})
    cid = db._one("SELECT id FROM customers WHERE name='증빙상사'")["id"]
    post(rep, "/sales/add", {"customer_id": cid, "item": "장비", "item_code": "HW-01", "qty": "1",
                             "unit_price": "11000000", "sale_date": date.today().isoformat()})
    return db._one("SELECT * FROM sales WHERE customer_id=?", [cid]), buyer


def test_tax_invoice_image_and_xml(app):
    rep = login(app, "김영업")
    sale, buyer = _sale_for_docs(rep)
    sid = sale["id"]
    today = date.today()

    # 1) 이미지 세금계산서 — 항목 직접 입력
    res = post(rep, f"/sales/{sid}/documents",
               {"doc_type": "세금계산서", "issue_date": today.isoformat(), "supplier_biz_no": COMPANY_BIZ,
                "buyer_biz_no": buyer, "supply_amount": "10000000", "tax_amount": "1000000",
                "file": (io.BytesIO(_png()), "scan.png")}, content_type="multipart/form-data",
               follow_redirects=True)
    assert "증빙을 등록했습니다" in res.get_data(as_text=True)
    doc = db._one("SELECT * FROM sale_documents WHERE sale_id=? ORDER BY id DESC", [sid])
    assert doc["mime"] == "image/png" and doc["total_amount"] == 11_000_000 and len(doc["sha256"]) == 64
    img = rep.get(f"/documents/{doc['id']}/file")
    assert img.status_code == 200 and img.mimetype == "image/png"

    # 2) 전자세금계산서 XML — 항목 자동 인식
    approval = today.strftime("%Y%m%d") + "41000000" + "00000001"
    xml = _etax_xml(approval, COMPANY_BIZ, buyer, 10_000_000, 1_000_000, today.strftime("%Y%m%d"))
    post(rep, f"/sales/{sid}/documents", {"doc_type": "", "file": (io.BytesIO(xml), "etax.xml")},
         content_type="multipart/form-data")
    etax = db._one("SELECT * FROM sale_documents WHERE approval_no=?", [approval])
    assert etax["doc_type"] == "전자세금계산서" and etax["supply_amount"] == 10_000_000
    assert rep.get(f"/documents/{etax['id']}/file").headers["Content-Disposition"].startswith("attachment")

    # 3) 같은 승인번호 중복 · 잘못된 사업자번호 · 위장 파일 거부
    res = post(rep, f"/sales/{sid}/documents", {"file": (io.BytesIO(xml), "again.xml")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "이미 등록된 증빙" in res.get_data(as_text=True)
    res = post(rep, f"/sales/{sid}/documents",
               {"doc_type": "세금계산서", "issue_date": today.isoformat(), "supplier_biz_no": "1234567890",
                "buyer_biz_no": buyer, "supply_amount": "1", "tax_amount": "0",
                "file": (io.BytesIO(_png()), "x.png")}, content_type="multipart/form-data", follow_redirects=True)
    assert "사업자등록번호가 올바르지 않습니다" in res.get_data(as_text=True)
    res = post(rep, f"/sales/{sid}/documents",
               {"doc_type": "기타", "file": (io.BytesIO(b"<script>alert(1)</script>"), "fake.png")},
               content_type="multipart/form-data", follow_redirects=True)
    assert db._scalar("SELECT COUNT(*) FROM sale_documents WHERE file_name='fake.png'") == 0
    res = post(rep, f"/sales/{sid}/documents",
               {"doc_type": "기타", "file": (io.BytesIO(b"MZ\x90\x00binary"), "virus.png")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "지원하지 않는 파일" in res.get_data(as_text=True)

    # 4) 다른 팀은 증빙을 볼 수 없다 · 무효 처리는 기록을 남긴다
    other = login(app, "박고객")
    assert other.get(f"/documents/{doc['id']}/file").status_code == 403
    post(rep, f"/documents/{doc['id']}/void", {"reason": "재발행"})
    assert db._one("SELECT voided_at FROM sale_documents WHERE id=?", [doc["id"]])["voided_at"]
    assert "재발행" in rep.get(f"/sales?sid={sid}").get_data(as_text=True)


def test_missing_tax_invoice_metric(app):
    html = login(app, "김영업").get("/sales").get_data(as_text=True)
    assert "세금계산서 미등록" in html


# ============================================================================
# 인증 (password · sso) · 세션
# ============================================================================
def test_password_mode_policy_and_lockout(app, monkeypatch):
    stale = login(app, "시스템관리자")                      # 간편 로그인 시절 세션
    db.set_context("system", None)
    core_auth.set_password(user("시스템관리자")["id"], "Admin#Pass2026")
    monkeypatch.setattr(core_auth, "AUTH_MODE", "password")
    assert "/login" in stale.get("/").headers["Location"]     # 인증 방식이 바뀌면 옛 세션은 끊긴다

    admin = app.test_client()
    res = admin.post("/login", data={"emp_no": "9999", "password": "Admin#Pass2026", "_csrf": csrf(admin)})
    assert res.status_code == 302 and "/login" not in res.headers["Location"]
    rep_id = user("이수주")["id"]
    res = post(admin, "/admin/users/save", {"id": rep_id, "emp_no": "2004", "name": "이수주", "role": "REP",
                                            "org_id": user("이수주")["org_id"], "active": "1",
                                            "temp_password": "short"}, follow_redirects=True)
    assert "비밀번호 규칙" in res.get_data(as_text=True)
    post(admin, "/admin/users/save", {"id": rep_id, "emp_no": "2004", "name": "이수주", "role": "REP",
                                      "org_id": user("이수주")["org_id"], "active": "1",
                                      "temp_password": "Temp!Pass2026"})

    client = app.test_client()
    res = client.post("/login", data={"emp_no": "2004", "password": "Temp!Pass2026", "_csrf": csrf(client)})
    assert res.headers["Location"].endswith("/account/password")        # 임시 비밀번호 → 변경 강제
    assert client.get("/deals").headers["Location"].endswith("/account/password")
    res = post(client, "/account/password", {"current_password": "Temp!Pass2026",
                                             "new_password": "Sales#2026ok", "confirm_password": "Sales#2026ok"})
    assert res.status_code == 302 and client.get("/deals").status_code == 200

    other = app.test_client()
    for _ in range(core_auth.MAX_FAILED_LOGINS):
        other.post("/login", data={"emp_no": "2004", "password": "wrong", "_csrf": csrf(other)})
    res = other.post("/login", data={"emp_no": "2004", "password": "Sales#2026ok", "_csrf": csrf(other)},
                     follow_redirects=True)
    assert "잠긴 계정" in res.get_data(as_text=True)
    post(admin, f"/admin/users/{rep_id}/unlock")
    res = other.post("/login", data={"emp_no": "2004", "password": "Sales#2026ok", "_csrf": csrf(other)})
    assert res.status_code == 302 and "/login" not in res.headers["Location"]


def test_sso_trusts_header_only_from_proxy(app, monkeypatch):
    monkeypatch.setattr(core_auth, "AUTH_MODE", "sso")
    proxy = app.test_client()
    res = proxy.get("/login", headers={"X-Remote-User": "2002"})       # 127.0.0.1 = 신뢰 프록시
    assert res.status_code == 302 and "/login" not in res.headers["Location"]
    # 헤더가 다른 사람으로 바뀌면 세션을 끊는다
    assert "/login" in proxy.get("/", headers={"X-Remote-User": "2003"}).headers["Location"]

    attacker = app.test_client()
    res = attacker.get("/login", headers={"X-Remote-User": "9999"}, environ_base={"REMOTE_ADDR": "10.9.9.9"})
    assert res.status_code == 200 and "SSO 인증 정보가 없습니다" in res.get_data(as_text=True)


def test_session_absolute_timeout(app):
    client = login(app, "김영업")
    with client.session_transaction() as s:
        s["login_at"] = (datetime.now() - timedelta(hours=13)).isoformat()
    assert "/login" in client.get("/").headers["Location"]


def test_login_next_is_same_site_only(app):
    client = app.test_client()
    loc = client.get("/deals?tab=move").headers["Location"]
    assert parse_qs(urlparse(loc).query)["next"] == ["/deals?tab=move"]
    uid = user("김영업")["id"]
    res = client.post("/login?next=/deals?tab=move", data={"user_id": uid, "_csrf": csrf(client)})
    assert res.headers["Location"].endswith("/deals?tab=move")
    client = app.test_client()
    res = client.post("/login?next=//evil.example.com", data={"user_id": uid, "_csrf": csrf(client)})
    assert "evil" not in res.headers["Location"]


def test_csrf_required(app):
    client = login(app, "김영업")
    assert client.post("/customers/save", data={"name": "x"}).status_code == 400

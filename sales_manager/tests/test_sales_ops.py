"""영업 실무: 부가세 · 품목·거래처 특가 · 견적 · 다단계 결재 · 대결 · 결재 독촉."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from conftest import login, post, user

from core import enterprise as ent
from core import sales_db as db

MEDDIC = {m: "1" for m in db.MEDDIC_FIELDS}


def _customer(client, name: str) -> int:
    row = db._one("SELECT id FROM customers WHERE name=?", [name])
    if not row:
        post(client, "/customers/save", {"name": name, "grade": "A", "industry": "제조"})
        row = db._one("SELECT id FROM customers WHERE name=?", [name])
    return int(row["id"])


def _product(code: str) -> dict:
    return db._one("SELECT * FROM products WHERE code=?", [code])


# ============================================================================
# 부가세
# ============================================================================
def test_vat_split_by_tax_type(app):
    assert db.vat_for(1_000_005, "과세") == 100_000          # 10%, 원 단위 절사
    assert db.vat_for(1_000_000, "영세") == 0 and db.vat_for(1_000_000, "면세") == 0

    rep = login(app, "김영업")
    cid = _customer(rep, "부가세상사")
    for tax in ("과세", "면세"):
        res = post(rep, "/sales/add", {"customer_id": cid, "item": f"{tax} 품목", "qty": "3",
                                       "unit_price": "1000000", "status": "입금대기", "tax_type": tax})
        assert res.status_code == 302
    taxed = db._one("SELECT * FROM sales WHERE item='과세 품목'")
    free = db._one("SELECT * FROM sales WHERE item='면세 품목'")
    assert (taxed["amount"], taxed["vat_amount"], taxed["total_amount"]) == (3_000_000, 300_000, 3_300_000)
    assert (free["vat_amount"], free["total_amount"]) == (0, 3_000_000)

    # 미수금은 부가세 포함 합계 기준 — 공급가액만 입금하면 부가세가 남는다
    db.set_context("system", None)
    ent.record_payment(int(taxed["id"]), 3_000_000)
    assert db.get_sale(int(taxed["id"]))["status"] != "입금완료"
    with pytest.raises(ValueError, match="부가세 포함"):
        ent.record_payment(int(taxed["id"]), 400_000)
    ent.record_payment(int(taxed["id"]), 300_000)
    assert db.get_sale(int(taxed["id"]))["status"] == "입금완료"

    page = rep.get("/sales").get_data(as_text=True)
    assert "공급가액" in page and "부가세" in page and "합계" in page


# ============================================================================
# 품목 · 거래처 특가
# ============================================================================
def test_products_and_customer_price(app):
    assert _product("SW-ERP-01"), "샘플 품목이 시드되어야 한다"
    rep = login(app, "김영업")
    cid = _customer(rep, "특가상사")
    # 담당자는 품목·특가를 바꿀 수 없다
    assert post(rep, "/products/save", {"code": "X-1", "name": "무단"}).status_code == 403
    pid = int(_product("SW-ERP-01")["id"])
    assert post(rep, "/products/prices", {"customer_id": cid, "product_id": pid,
                                          "unit_price": "1"}).status_code == 403

    admin = login(app, "시스템관리자")
    assert post(admin, "/products/save", {"code": "SV-NEW-01", "name": "신규 서비스", "unit": "월",
                                          "list_price": "2000000", "tax_type": "과세",
                                          "active": "1"}).status_code == 302
    assert post(admin, "/products/save", {"code": "SV-NEW-01", "name": "중복"},
                follow_redirects=True).get_data(as_text=True).count("이미 있습니다") == 1

    mgr = login(app, "한팀장")
    start = (date.today() - timedelta(days=10)).isoformat()
    assert post(mgr, "/products/prices", {"customer_id": cid, "product_id": pid, "unit_price": "10000000",
                                          "valid_from": start}).status_code == 302
    look = rep.get(f"/products/price?product_id={pid}&customer_id={cid}").get_json()
    assert look["unit_price"] == 10_000_000 and look["source"] == "특가" and look["list_price"] == 12_000_000
    # 다른 거래처는 정가
    other = _customer(rep, "정가상사")
    assert rep.get(f"/products/price?product_id={pid}&customer_id={other}").get_json()["source"] == "정가"

    # 새 특가를 오늘부터 등록하면 이전 특가는 어제로 끝난다
    post(mgr, "/products/prices", {"customer_id": cid, "product_id": pid, "unit_price": "9500000",
                                   "valid_from": date.today().isoformat()})
    rows = db._df("SELECT unit_price, valid_from, valid_to FROM customer_prices "
                  "WHERE customer_id=? AND product_id=? ORDER BY valid_from", [cid, pid])
    assert rows.iloc[0]["valid_to"] == (date.today() - timedelta(days=1)).isoformat()
    assert rep.get(f"/products/price?product_id={pid}&customer_id={cid}").get_json()["unit_price"] == 9_500_000
    assert rep.get("/products?tab=prices").status_code == 200


# ============================================================================
# 견적
# ============================================================================
def _quote_form(cid, lines, **extra):
    data = {"customer_id": cid, "title": "테스트 견적", **extra,
            "product_id": [], "item_name": [], "qty": [], "unit_price": [], "tax_type": []}
    for ln in lines:
        data["product_id"].append(str(ln.get("product_id", "")))
        data["item_name"].append(ln.get("item_name", ""))
        data["qty"].append(str(ln.get("qty", 1)))
        data["unit_price"].append(str(ln.get("unit_price", "")))
        data["tax_type"].append(ln.get("tax_type", "과세"))
    return data


def test_quote_lifecycle_to_sales(app):
    rep = login(app, "김영업")
    cid = _customer(rep, "견적상사")
    edu = int(_product("SV-EDU-01")["id"])          # 면세 900,000/일
    res = post(rep, "/quotes/save", _quote_form(cid, [
        {"product_id": edu, "qty": 2},                                   # 단가 비움 → 정가
        {"item_name": "현장 설치", "qty": 1, "unit_price": 500000}]))
    assert res.status_code == 302
    q = db._one("SELECT * FROM quotes WHERE customer_id=? ORDER BY id DESC", [cid])
    assert q["status"] == "작성중" and q["quote_no"].startswith(f"Q-{date.today().year}-")
    assert (q["supply_amount"], q["vat_amount"], q["total_amount"]) == (2_300_000, 50_000, 2_350_000)
    assert "견적상사" in rep.get(f"/quotes?qid={q['id']}").get_data(as_text=True)

    # 동시 수정 충돌: 옛 row_version 으로 저장하면 막힌다
    body = _quote_form(cid, [{"product_id": edu, "qty": 3}], id=q["id"], row_version=q["row_version"])
    assert post(rep, "/quotes/save", body).status_code == 302
    res = post(rep, "/quotes/save", body, follow_redirects=True)
    assert "먼저" in res.get_data(as_text=True)

    pdf = rep.get(f"/quotes/{q['id']}/pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"

    post(rep, f"/quotes/{q['id']}/send")
    assert db._one("SELECT status FROM quotes WHERE id=?", [q["id"]])["status"] == "발송"
    # 발송된 견적은 수정 불가 → 개정
    post(rep, f"/quotes/{q['id']}/revise")
    rev = db._one("SELECT * FROM quotes WHERE quote_no=? AND revision=2", [q["quote_no"]])
    assert rev and rev["status"] == "작성중"
    assert db._one("SELECT status FROM quotes WHERE id=?", [q["id"]])["status"] == "대체됨"

    post(rep, f"/quotes/{rev['id']}/send")
    post(rep, f"/quotes/{rev['id']}/reject")                          # 사유 없이 거절 불가
    assert db._one("SELECT status FROM quotes WHERE id=?", [rev["id"]])["status"] == "발송"
    post(rep, f"/quotes/{rev['id']}/accept")
    post(rep, f"/quotes/{rev['id']}/convert", {"sale_date": date.today().isoformat()})
    sales = db._df("SELECT * FROM sales WHERE quote_id=?", [rev["id"]])
    assert len(sales) == 1 and int(sales.iloc[0]["total_amount"]) == 2_700_000       # 면세 3일
    assert sales.iloc[0]["tax_type"] == "면세"
    post(rep, f"/quotes/{rev['id']}/convert")                         # 두 번 전환 불가
    assert len(db._df("SELECT id FROM sales WHERE quote_id=?", [rev["id"]])) == 1


def test_quote_send_requires_discount_approval(app):
    rep = login(app, "김영업")
    cid = _customer(rep, "견적결재상사")
    post(rep, "/deals/save", {"customer_id": cid, "title": "견적 딜", "stage": "제안", "list_amount": "12000000",
                              "expected_close": "2026-12-31", **MEDDIC})
    deal = db._one("SELECT * FROM deals WHERE title='견적 딜'")
    sw = int(_product("SW-ERP-01")["id"])
    post(rep, "/quotes/save", _quote_form(cid, [{"product_id": sw, "qty": 1, "unit_price": 10_800_000}],
                                          deal_id=deal["id"]))                   # 10% 할인
    q = db._one("SELECT * FROM quotes WHERE deal_id=?", [deal["id"]])
    res = post(rep, f"/quotes/{q['id']}/send", follow_redirects=True)
    assert "결재가 필요" in res.get_data(as_text=True)
    synced = db.get_deal(deal["id"])
    assert int(synced["amount"]) == 10_800_000 and abs(float(synced["discount_rate"]) - 10) < 0.01

    post(rep, "/deals/request-approval", {"deal_id": deal["id"], "reason": "연간 계약"})
    appr = db._one("SELECT * FROM approvals WHERE deal_id=? AND status='대기'", [deal["id"]])
    post(login(app, "한팀장"), f"/approvals/{appr['id']}/decide", {"decision": "approve", "comment": "OK"})
    post(rep, f"/quotes/{q['id']}/send")
    assert db._one("SELECT status FROM quotes WHERE id=?", [q["id"]])["status"] == "발송"


# ============================================================================
# 다단계 결재 · 대결 · 독촉
# ============================================================================
def _approval_deal(app, title: str, discount: float) -> tuple[dict, dict]:
    rep = login(app, "김영업")
    cid = _customer(rep, "결재선상사")
    post(rep, "/deals/save", {"customer_id": cid, "title": title, "stage": "협상", "list_amount": "100000000",
                              "discount_rate": str(discount), "expected_close": "2026-12-31", **MEDDIC})
    deal = db._one("SELECT * FROM deals WHERE title=?", [title])
    post(rep, "/deals/request-approval", {"deal_id": deal["id"], "reason": "전략 고객"})
    return deal, db._one("SELECT * FROM approvals WHERE deal_id=? AND status='대기'", [deal["id"]])


def test_multi_step_chain_with_delegation(app):
    deal, appr = _approval_deal(app, "2단계 딜", 15)                # 15% → 팀장 → 임원
    steps = db._df("SELECT step_no, role, status FROM approval_steps WHERE approval_id=? ORDER BY step_no",
                   [appr["id"]])
    assert list(steps["role"]) == ["MANAGER", "EXEC"] and list(steps["status"]) == ["대기", "예정"]
    # 1단계 전에는 임원 결재함에 없다
    exec_c = login(app, "정임원")
    assert "2단계 딜" not in exec_c.get("/approvals").get_data(as_text=True)

    mgr = login(app, "한팀장")
    assert "2단계 딜" in mgr.get("/approvals").get_data(as_text=True)
    post(mgr, f"/approvals/{appr['id']}/decide", {"decision": "approve", "comment": "1차 OK"})
    row = db._one("SELECT * FROM approvals WHERE id=?", [appr["id"]])
    assert row["status"] == "대기" and int(row["current_step"]) == 2
    assert db.get_deal(deal["id"])["approval_status"] == "대기"
    # 같은 사람이 다음 단계를 또 결재할 수 없다
    assert post(mgr, f"/approvals/{appr['id']}/decide", {"decision": "approve"}).status_code == 403
    # 임원에게 결재 요청 알림이 갔다
    assert db._one("SELECT id FROM notifications WHERE user_id=? AND kind='결재요청' AND title LIKE ?",
                   [user("정임원")["id"], "%2단계 딜%"])

    # 임원이 출장 → 다른 담당자에게 대결 지정
    admin = login(app, "시스템관리자")
    post(admin, "/admin/users/save", {"emp_no": "4001", "name": "대결대리", "role": "REP", "active": "1",
                                      "org_id": user("김영업")["org_id"] or ""})
    deputy = user("대결대리")
    res = post(exec_c, "/approvals/delegations", {"to_user_id": deputy["id"], "start_date": date.today().isoformat(),
                                                  "end_date": (date.today() + timedelta(days=3)).isoformat(),
                                                  "reason": "해외 출장"})
    assert res.status_code == 302
    dep = login(app, emp_no="4001")
    assert "2단계 딜" in dep.get("/approvals").get_data(as_text=True)
    post(dep, f"/approvals/{appr['id']}/decide", {"decision": "approve", "comment": "대결 승인"})
    final = db._one("SELECT * FROM approvals WHERE id=?", [appr["id"]])
    assert final["status"] == "승인" and db.get_deal(deal["id"])["approval_status"] == "승인"
    step2 = db._one("SELECT * FROM approval_steps WHERE approval_id=? AND step_no=2", [appr["id"]])
    assert int(step2["approver_id"]) == int(deputy["id"]) and int(step2["acted_for_id"]) == int(user("정임원")["id"])
    assert db._one("SELECT id FROM notifications WHERE user_id=? AND kind='결재결과'", [user("김영업")["id"]])
    assert "대결:정임원" in ent.approval_lines([int(appr["id"])])[int(appr["id"])]

    # 대결 취소 뒤에는 더 이상 결재함에 나타나지 않는다
    d_id = db._one("SELECT id FROM delegations WHERE to_user_id=?", [deputy["id"]])["id"]
    assert post(dep, f"/approvals/delegations/{d_id}/revoke").status_code == 302      # 권한 없음 → flash
    assert db._one("SELECT revoked_at FROM delegations WHERE id=?", [d_id])["revoked_at"] is None
    post(exec_c, f"/approvals/delegations/{d_id}/revoke")
    assert db._one("SELECT revoked_at FROM delegations WHERE id=?", [d_id])["revoked_at"]
    _deal2, appr2 = _approval_deal(app, "대결취소 딜", 15)
    post(mgr, f"/approvals/{appr2['id']}/decide", {"decision": "approve", "comment": "OK"})
    assert "대결취소 딜" not in dep.get("/approvals").get_data(as_text=True)


def test_rejection_stops_chain_and_escalation(app):
    _deal, appr = _approval_deal(app, "반려 딜", 25)                 # 25% → 팀장 → 임원 → 관리자
    assert len(db._df("SELECT id FROM approval_steps WHERE approval_id=?", [appr["id"]])) == 3
    mgr = login(app, "한팀장")
    post(mgr, f"/approvals/{appr['id']}/decide", {"decision": "reject", "comment": "근거 부족"})
    statuses = db._df("SELECT status FROM approval_steps WHERE approval_id=? ORDER BY step_no", [appr["id"]])
    assert list(statuses["status"]) == ["반려", "취소", "취소"]
    assert db._one("SELECT status FROM approvals WHERE id=?", [appr["id"]])["status"] == "반려"

    # 기한이 지난 단계는 한 번만 독촉한다
    _deal2, late = _approval_deal(app, "지연 딜", 5)
    past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        conn.execute("UPDATE approval_steps SET due_at=? WHERE approval_id=?", (past, late["id"]))
    db.set_context("system", None)
    assert ent.escalate_overdue_steps()["escalated"] == 1
    assert db._one("SELECT id FROM notifications WHERE user_id=? AND kind='결재독촉'", [user("한팀장")["id"]])
    assert db._one("SELECT id FROM notifications WHERE user_id=? AND kind='결재지연'", [user("정임원")["id"]])
    assert ent.escalate_overdue_steps()["escalated"] == 0

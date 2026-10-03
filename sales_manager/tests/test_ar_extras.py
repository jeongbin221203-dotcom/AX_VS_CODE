"""반품·정정 · 대손 · 선수금·일괄 입금 · 자동 거래정지·해제 결재 · 거래처 담당자 · 수주(분할 납품)."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from conftest import biz, login, post, user

from core import advances as adv
from core import company
from core import contacts as ct
from core import credit
from core import database
from core import enterprise as ent
from core import erp
from core import etax
from core import jobs
from core import orders as so
from core import periods
from core import privacy
from core import returns as rtn
from core import sales_db as db

TODAY = date.today().isoformat()


def _cust(name: str, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"], "erp_code": "R" + str(abs(hash(name)) % 10**6),
                               **extra})


def _sale(cid: int, qty: int = 10, unit: int = 100_000, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_sale({"customer_id": cid, "item": "반품테스트", "item_code": "HW-01", "qty": qty, "unit_price": unit,
                           "owner_id": user("김영업")["id"], "sale_date": TODAY, **extra})


def _paid_matches(sid: int) -> bool:
    s = db.get_sale(sid)
    return int(s["paid_amount"] or 0) == int(db._scalar("SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=?", [sid]))


# ── 1. 반품 · 정정 ───────────────────────────────────────────────────────
def test_partial_return_and_price_correction(app):
    cid = _cust("반품상사")
    sid = _sale(cid)                                  # 10개 × 10만 = 100만 + 부가세 10만
    rep = login(app, "김영업")
    res = post(rep, f"/sales/{sid}/return", {"kind": "반품", "qty": "2", "reason": "불량", "return_date": TODAY},
               follow_redirects=True)
    assert "반품 매출" in res.get_data(as_text=True)
    ret = db._one("SELECT * FROM sales WHERE original_sale_id=? AND sale_kind='반품'", [sid])
    assert (int(ret["qty"]), int(ret["amount"]), int(ret["vat_amount"]), int(ret["total_amount"])) == (-2, -200_000, -20_000, -220_000)
    orig = db.get_sale(sid)
    assert int(orig["paid_amount"]) == 220_000 and _paid_matches(sid)        # 미수에서 상계
    with pytest.raises(ValueError, match="1 ~ 8"):
        rtn.create(sid, "반품", "초과", qty=9)
    r = rtn.create(sid, "정정", "단가 인하", new_unit_price=90_000)       # 남은 8개 × -1만
    corr = db.get_sale(r["sale_id"])
    assert int(corr["amount"]) == -80_000 and int(corr["total_amount"]) == -88_000
    kpi_net = int(db._scalar("SELECT SUM(amount) FROM sales WHERE id=? OR original_sale_id=?", [sid, sid]))
    assert kpi_net == 1_000_000 - 200_000 - 80_000
    # 다 받은 매출을 반품하면 선수금
    sid2 = _sale(cid, qty=1, unit=500_000)
    ent.record_payment(sid2, 550_000)
    r = rtn.create(sid2, "반품", "전량 반품", qty=1)
    assert r["선수금"] == 550_000 and adv.balance(cid) == 550_000
    # 반품이 있는 매출은 통째 취소 불가, 반품 행도 취소 불가
    with pytest.raises(ValueError, match="입금|반품"):          # 반품상계 입금이 있어 통째 취소는 막힌다
        db.cancel_sale(sid, "x")
    with pytest.raises(ValueError, match="반품·정정 행"):
        db.cancel_sale(ret["id"], "x")
    # ERP 반품 전표
    outbox = db._one("SELECT * FROM erp_outbox WHERE ref_id=? AND doc_type='반품'", [ret["id"]])
    doc = erp.build_document(outbox, erp.settings())
    assert doc["doc_type"] == "반품" and doc["sale_kind"] == "반품" and doc["amount"] == -200_000
    # 원장: 매출 · 반품 · 정정이 차변(음수 포함)에, 반품상계는 따로 적지 않는다
    _o, rows = periods.customer_ledger(cid, TODAY, TODAY)
    assert "반품" in set(rows["구분"]) and "정정" in set(rows["구분"])
    assert int(rows["잔액"].iloc[-1]) == 1_100_000 - 220_000 - 88_000 + 550_000 - 550_000 - 550_000


def test_amended_tax_invoice_for_return(app, monkeypatch):
    db.set_context("system", None)
    company.save({"company_name": "(주)수정발행", "company_biz_no": biz("123456789")}, "test")
    monkeypatch.setenv("SALES_ETAX_ADAPTER", "mock")
    try:
        cid = _cust("수정발행상사", biz_no=biz("314159266"))
        sid = _sale(cid, qty=5, unit=200_000)
        etax.request_issue(sid, None, {"name": "t"})
        jobs.run_pending()
        r = rtn.create(sid, "반품", "1개 반품", qty=1)
        etax.request_issue(r["sale_id"], None, {"name": "t"})
        jobs.run_pending()
        row = db._one("SELECT * FROM etax_invoices WHERE sale_id=?", [r["sale_id"]])
        assert row["status"] == "발행완료"
        doc = db._one("SELECT * FROM sale_documents WHERE id=?", [row["document_id"]])
        assert doc["doc_type"] == "수정세금계산서" and int(doc["supply_amount"]) == -200_000
    finally:
        company.save({"company_name": "", "company_biz_no": ""}, "test")


# ── 2. 대손 ──────────────────────────────────────────────────────────────
def test_writeoff_needs_approval_and_clears_ar(app):
    cid = _cust("대손상사")
    sid = _sale(cid, qty=1, unit=20_000_000)            # 2,200만 > 1,000만 → 임원 결재
    ent.record_payment(sid, 2_000_000)
    rep = login(app, "김영업")
    post(rep, f"/sales/{sid}/writeoff", {"reason": "거래처 파산"})
    req = db._one("SELECT * FROM fin_requests WHERE sale_id=? AND kind='대손'", [sid])
    assert req["required_role"] == "EXEC" and int(req["amount"]) == 20_000_000
    mgr = login(app, "한팀장")
    decide_url = f"/approvals/finance/{req['id']}/decide"
    assert decide_url not in mgr.get("/approvals?tab=finance").get_data(as_text=True)    # 팀장은 결재 버튼 없음
    exec_ = login(app, "정임원")
    assert decide_url in exec_.get("/approvals?tab=finance").get_data(as_text=True)
    post(exec_, f"/approvals/finance/{req['id']}/decide", {"decision": "approve", "comment": "회수 불능 확인"})
    sale = db.get_sale(sid)
    assert sale["status"] == "입금완료" and _paid_matches(sid)
    assert db._one("SELECT id FROM payments WHERE sale_id=? AND source='대손'", [sid])
    assert sid not in set(ent.ar_aging()["id"]) if not ent.ar_aging().empty else True
    wo = db._one("SELECT * FROM erp_outbox WHERE doc_type='대손' AND ref_id=?", [req["id"]])
    doc = erp.build_document(wo, erp.settings())
    assert doc["doc_type"] == "대손" and doc["writeoff_amount"] == 20_000_000
    # 본인 요청 결재 금지
    sid2 = _sale(cid, qty=1, unit=100_000)
    rid = credit.request_writeoff(sid2, "소액", user("한팀장"))
    with pytest.raises(PermissionError):
        credit.decide(rid, True, "", user("한팀장"))


# ── 3. 선수금 · 일괄 입금 ────────────────────────────────────────────────
def test_lump_receipt_and_advance(app):
    cid = _cust("선수금상사")
    s1 = _sale(cid, qty=1, unit=1_000_000, due_date=TODAY)         # 110만
    s2 = _sale(cid, qty=1, unit=500_000, due_date=(date.today() + timedelta(days=10)).isoformat())   # 55만
    rep = login(app, "김영업")
    post(rep, f"/customers/{cid}/receipt", {"amount": "2000000", "pay_date": TODAY, "method": "계좌이체"})
    assert db.get_sale(s1)["status"] == "입금완료" and db.get_sale(s2)["status"] == "입금완료"
    assert adv.balance(cid) == 2_000_000 - 1_650_000
    s3 = _sale(cid, qty=1, unit=1_000_000)
    post(rep, f"/sales/{s3}/apply-advance", {})
    assert int(db.get_sale(s3)["paid_amount"]) == 350_000 and adv.balance(cid) == 0 and _paid_matches(s3)
    with pytest.raises(ValueError, match="잔액"):
        adv.add(cid, 1, "환불")
    assert "선수금 잔액" in rep.get("/sales?tab=ar").get_data(as_text=True)


# ── 4. 자동 거래정지 · 해제 결재 ─────────────────────────────────────────
def test_auto_block_and_unblock(app):
    db.set_context("system", None)
    company.save({"auto_block_overdue_days": 30}, "test")
    try:
        cid = _cust("연체정지상사")
        old = (date.today() - timedelta(days=80)).isoformat()
        with database.get_conn() as conn:
            conn.execute("UPDATE sales_period_closes SET closed_through=closed_through WHERE 1=0")
        sid = db.upsert_sale({"customer_id": cid, "item": "연체", "qty": 1, "unit_price": 100_000,
                              "owner_id": user("김영업")["id"], "sale_date": old, "due_date": old})
        result = credit.auto_block()
        assert cid in result["customers"]
        cust = db.get_customer(cid)
        assert cust["trade_blocked"] and cust["block_source"] == "자동" and "연체" in cust["block_reason"]
        rep = login(app, "김영업")
        res = post(rep, "/sales/add", {"customer_id": cid, "item": "막힘", "qty": "1", "unit_price": "1000",
                                       "status": "입금대기"})
        assert res.status_code == 400
        post(rep, f"/customers/{cid}/unblock", {"reason": "회수 약정"})
        req = db._one("SELECT * FROM fin_requests WHERE customer_id=? AND kind='거래정지해제'", [cid])
        post(login(app, "한팀장"), f"/approvals/finance/{req['id']}/decide", {"decision": "approve", "comment": "약정 확인"})
        cust = db.get_customer(cid)
        assert not cust["trade_blocked"] and cust["block_exempt_until"]
        assert cid not in credit.auto_block()["customers"]                # 유예기간
        assert sid
    finally:
        company.save({"auto_block_overdue_days": 0}, "test")


# ── 5. 거래처 담당자 ────────────────────────────────────────────────────
def test_multiple_contacts(app):
    cid = _cust("담당자상사", manager="김대표", phone="010-1111-0000")
    assert ct.list_for(cid)[0]["name"] == "김대표"
    rep = login(app, "김영업")
    post(rep, f"/customers/{cid}/contacts", {"name": "이회계", "dept": "재무팀", "role": "회계·세금계산서",
                                             "email": "acc@x.kr", "is_primary": "1"})
    assert db.get_customer(cid)["manager"] == "이회계"                    # 대표 담당자 → 거래처 고객담당자
    names = [c["name"] for c in ct.list_for(cid)]
    assert names[0] == "이회계" and "김대표" in names
    page = rep.get(f"/customers?tab=edit&id={cid}").get_data(as_text=True)
    assert "재무팀" in page and "거래처 담당자" in page
    found = privacy.search("acc@x.kr")
    assert len(found["contacts"]) == 1
    tid = int(found["contacts"]["id"].iloc[0])
    privacy.erase("acc@x.kr", "본인", "관리자", [], [], [tid])
    assert all(c["name"] != "이회계" for c in ct.list_for(cid))
    assert login(app, "박고객").post(f"/customers/{cid}/contacts").status_code in (400, 403)


# ── 6. 수주 · 분할 납품 ─────────────────────────────────────────────────
def test_order_partial_delivery(app):
    cid = _cust("수주상사")
    db.set_context("system", None)
    oid = so.create({"customer_id": cid, "owner_id": user("김영업")["id"], "delivery_date": TODAY},
                    [{"item_name": "장비", "qty": 10, "unit_price": 300_000, "tax_type": "과세"},
                     {"item_name": "설치", "qty": 1, "unit_price": 1_000_000, "tax_type": "과세"}])
    o = so.get(oid)
    eq, inst = o["items"]
    rep = login(app, "김영업")
    post(rep, f"/orders/{oid}/deliver", {f"qty_{eq['id']}": "4", "sale_date": TODAY})
    o = so.get(oid)
    assert o["items"][0]["remain"] == 6 and len(o["sales"]) == 1 and o["status"] == "진행"
    with pytest.raises(ValueError, match="잔량"):
        so.deliver(oid, {eq["id"]: 7})
    sale_id = o["sales"][0]["id"]
    db.set_context("system", None)
    db.cancel_sale(sale_id, "납품 취소")
    assert so.get(oid)["items"][0]["remain"] == 10                       # 취소하면 잔량으로 복귀
    so.deliver(oid, {eq["id"]: 10, inst["id"]: 1})
    assert so.get(oid)["status"] == "완료"
    assert "수주" in rep.get(f"/orders?oid={oid}").get_data(as_text=True)
    assert re.search(r"SO-\d{4}-\d{4}", so.get(oid)["order_no"])

"""자재관리와 비교해 옮긴 기능 (2026-10-06): 매출 취소·입금 반제 요청 결재 · 한꺼번에 승인 · 홈택스 매출 대사 ·
품목·특가·거래처 담당자 엑셀 · 품목 ERP 잠금·동시 수정 · 거래처별 채권 ERP 대사 · ERP 마스터 가져오기 ·
사용자별 데이터 범위 · 서비스 워커 · 상태 표시 이름."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest
from conftest import biz, login, post, user

from core import catalog
from core import company
from core import credit
from core import dataio
from core import enterprise as ent
from core import erp
from core import hometax
from core import sales_db as db

TODAY = date.today().isoformat()


def _cust(name: str, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"], **extra})


def _sale(cid: int, unit: int = 100_000, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_sale({"customer_id": cid, "item": "패리티", "qty": 1, "unit_price": unit,
                           "owner_id": user("김영업")["id"], "sale_date": TODAY, **extra})


# ── 매출 취소 · 입금 반제 요청 ────────────────────────────────────────────
def test_cancel_request_for_erp_sent_sale(app):
    cid = _cust("취소요청상사")
    sid = _sale(cid)
    with db.get_conn() as conn:                               # ERP 로 전송된 매출, 등록자는 김영업
        conn.execute("UPDATE sales SET erp_status='전송완료', erp_doc_no='E1', created_by_id=? WHERE id=?",
                     (user("김영업")["id"], sid))
    assert not credit.can_cancel_directly(db.get_sale(sid), user("김영업"))
    rid = credit.request_cancel(sid, "계약 해지", user("김영업"))
    approvers = {int(u["id"]) for u in credit._approvers(db._one("SELECT * FROM fin_requests WHERE id=?", [rid]))}
    assert int(user("김영업")["id"]) not in approvers
    credit.decide(rid, True, "", user("한팀장"))
    s = db.get_sale(sid)
    assert s["status"] == "취소" and s["erp_status"] == "취소대기"


def test_direct_reversal_rules(app):
    sid = _sale(_cust("반제규칙상사"))
    db.set_context("김영업", None, user("김영업")["id"])
    ent.record_payment(sid, 50_000)
    pay = ent.list_payments(sid)[-1]
    with pytest.raises(PermissionError):
        ent.reverse_payment(pay["id"], "실수", actor=user("김영업"))     # 영업사원
    db.set_context("system", None)
    ent.reverse_payment(pay["id"], "실수", actor=user("한팀장"))          # 넣지 않은 팀장은 바로 가능
    assert int(db.get_sale(sid)["paid_amount"]) == 0


# ── 한꺼번에 승인 ────────────────────────────────────────────────────────
def test_decide_many_reports_failures_and_continues(app):
    db.set_context("system", None)
    ids = []
    for i in range(2):
        cid = _cust(f"일괄승인상사{i}")
        did = db.upsert_deal({"customer_id": cid, "title": f"일괄 {i}", "owner_id": user("김영업")["id"],
                              "stage": db.OPEN_STAGES[0], "list_amount": 1_000_000, "amount": 950_000,
                              "discount_rate": 5, "expected_close": TODAY})
        ids.append(ent.request_approval(did, user("김영업"), "할인"))
    done, problems = ent.decide_many(ids + [999999], user("한팀장"))
    assert done == 2 and len(problems) == 1
    assert {db._one("SELECT status FROM approvals WHERE id=?", [i])["status"] for i in ids} == {"승인"}


# ── 홈택스 매출 대사 ─────────────────────────────────────────────────────
def test_hometax_sales_reconciliation(app):
    cid = _cust("홈택스상사", biz_no=biz("456789012"))
    sid = _sale(cid, 1_000_000)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO sale_documents (sale_id, doc_type, approval_no, issue_date, supply_amount, tax_amount, "
                     "total_amount, file_path, file_name, mime, file_size, sha256, uploaded_at) "
                     "VALUES (?, '전자세금계산서', ?, ?, 1000000, 100000, 1100000, 'x', 'x.xml', 'text/xml', 1, '0', ?)",
                     (sid, "2" * 24, TODAY, db._now()))
    other = _sale(cid, 500_000)                                 # 홈택스에만 있는 건의 후보 매출 (합계 550,000)
    frame = pd.DataFrame([["조회기간", f"{TODAY} ~ {TODAY}", "", "", ""],
                          ["작성일자", "승인번호", "공급받는자사업자등록번호", "공급가액", "세액"],
                          [TODAY.replace("-", ""), "2" * 24, biz("456789012"), "1,000,000", "100,000"],
                          [TODAY.replace("-", ""), "3" * 24, biz("456789012"), "500000", "50000"]])
    buf = io.BytesIO()
    frame.to_excel(buf, header=False, index=False)
    df, period = hometax.read(buf.getvalue(), "hometax.xlsx")
    result = hometax.reconcile(df, period).set_index("승인번호")
    assert result.loc["2" * 24, "결과"] == "일치"
    assert result.loc["3" * 24, "결과"] == "증빙 없음" and f"#{other}" in result.loc["3" * 24, "참고"]
    with pytest.raises(ValueError, match="승인번호"):
        hometax.read(b"a,b\n1,2", "x.csv")


# ── 품목·특가·거래처 담당자 엑셀 ──────────────────────────────────────────
def test_master_uploads(app):
    admin = user("시스템관리자")
    prod = pd.DataFrame([{"품목코드": "MP-01", "품목명": "마스터품목", "정가": 100_000, "과세구분": "과세"}])
    assert dataio.import_rows("품목", prod, admin, dry_run=False)["ok"] == 1
    upd = pd.DataFrame([{"품목코드": "MP-01", "품목명": "마스터품목(개정)"}])           # 정가 칸 없음 → 기존 값 유지
    assert dataio.import_rows("품목", upd, admin, dry_run=False, on_duplicate="덮어쓰기")["ok"] == 1
    p = db._one("SELECT * FROM products WHERE code='MP-01'")
    assert p["name"] == "마스터품목(개정)" and int(p["list_price"]) == 100_000
    _cust("마스터특가상사")
    price = pd.DataFrame([{"거래처명": "마스터특가상사", "품목코드": "MP-01", "특가": 90_000, "시작일": TODAY}])
    assert dataio.import_rows("특가", price, admin, dry_run=False)["ok"] == 1
    with pytest.raises(ValueError, match="팀장"):
        dataio.import_rows("특가", price, user("김영업"), dry_run=True)
    people = pd.DataFrame([{"거래처명": "마스터특가상사", "이름": "구매담당", "연락처": "010-1111-2222", "대표": "Y"}])
    assert dataio.import_rows("거래처담당자", people, admin, dry_run=False)["ok"] == 1
    frames = dataio.collect(["품목", "특가", "거래처담당자"], include_pii=True)
    assert list(frames["품목"].columns)[:3] == ["품목코드", "품목명", "규격"]       # 올리기 양식과 같은 열
    assert "마스터특가상사" in set(frames["특가"]["거래처명"])


def test_product_erp_lock_and_conflict(app):
    db.set_context("system", None)
    erp.receive_products([{"code": "ERP-LOCK-1", "name": "ERP품목", "list_price": 5_000, "tax_type": "과세"}])
    p = db._one("SELECT * FROM products WHERE code='ERP-LOCK-1'")
    assert p["erp_synced_at"]
    with pytest.raises(ValueError, match="ERP"):
        catalog.upsert_product({**p, "list_price": 1})
    catalog.upsert_product({**p, "memo": "메모는 가능"})
    stale = p["row_version"]
    with pytest.raises(db.ConflictError):
        catalog.upsert_product({**p, "memo": "늦은 저장", "row_version": stale})


# ── 거래처별 채권 ERP 대사 · 마스터 가져오기 ─────────────────────────────
def test_ar_balance_reconciliation(app):
    cid = _cust("잔액대사상사", erp_code="ARB-1")
    sent = _sale(cid, 100_000)
    _sale(cid, 50_000)                                          # 아직 ERP 로 안 보낸 매출 (55,000)
    with db.get_conn() as conn:
        conn.execute("UPDATE sales SET erp_status='전송완료' WHERE id=?", (sent,))
    df = pd.DataFrame([{"ERP코드": "ARB-1", "채권잔액": 110_000}, {"ERP코드": "NOPE", "채권잔액": 1}])
    out = erp.reconcile_ar_balances(df).set_index("ERP코드")
    assert out.loc["ARB-1", "결과"] == "일치" and int(out.loc["ARB-1", "ERP미전송"]) == 55_000
    assert out.loc["NOPE", "결과"] == "미일치"


def test_master_pull(app, monkeypatch):
    calls = []

    def fake_call(self, method, url, payload=None, extra=None):
        calls.append(url)
        if url.endswith("/customers") or "/customers?" in url:
            return {"items": [{"erp_code": "PULL-1", "name": "가져온거래처", "owner_emp_no": user("김영업")["emp_no"]}]}
        return {"items": [{"code": "PULL-P1", "name": "가져온품목", "list_price": 1_000}]}

    monkeypatch.setattr(erp.RestAdapter, "call", fake_call)
    db.set_context("system", None)
    r = erp.master_pull({**erp.settings(), "master_url": "https://erp.example/api/master"}, full=True)
    assert r["products"]["받음"] == 1 and db._one("SELECT erp_synced_at FROM products WHERE code='PULL-P1'")["erp_synced_at"]
    erp.master_pull({**erp.settings(), "master_url": "https://erp.example/api/master"})
    assert "changedSince=" in calls[-1]                          # 두 번째부터는 바뀐 것만


# ── 사용자별 데이터 범위 ─────────────────────────────────────────────────
def test_extra_user_scope(app):
    rep, other = user("김영업"), user("이영업") or user("박고객")
    before = set(ent.visible_owners(rep))
    sid = ent.add_user_scope(int(rep["id"]), "owner", int(other["id"]), "휴직자 거래처 임시 관리", user("시스템관리자"))
    assert int(other["id"]) in set(ent.visible_owners(rep)) - before
    with pytest.raises(ValueError, match="이미"):
        ent.add_user_scope(int(rep["id"]), "owner", int(other["id"]), "또", user("시스템관리자"))
    ent.remove_user_scope(sid, user("시스템관리자"))
    assert set(ent.visible_owners(rep)) == before
    with pytest.raises(ValueError, match="기한"):
        ent.add_user_scope(int(rep["id"]), "owner", int(other["id"]), "지난 기한", user("시스템관리자"), "2000-01-01")


# ── 서비스 워커 · 상태 표시 이름 ─────────────────────────────────────────
def test_service_worker_and_offline_page(app):
    c = app.test_client()
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["Service-Worker-Allowed"] == "/" and b"OFFLINE" in sw.data
    off = c.get("/offline")
    assert off.status_code == 200 and "연결할 수 없습니다" in off.get_data(as_text=True)


def test_status_display_labels(app):
    company.save({"status_labels": "입금대기=미수\n발송=제출"}, "test")
    company.refresh(force=True)
    try:
        assert company.label("입금대기") == "미수" and company.label("부분입금") == "부분입금"
        rep = login(app, "김영업")
        assert "미수" in rep.get("/sales").get_data(as_text=True)
        with pytest.raises(ValueError, match="바꿀 수 있는 상태"):
            company.validate({"status_labels": "없는상태=x"})
    finally:
        company.save({"status_labels": ""}, "test")
        company.refresh(force=True)

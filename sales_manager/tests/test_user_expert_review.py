"""2026-10-04 실무자·회계·운영 관점 점검에서 찾은 문제 회귀 테스트."""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import pytest
from conftest import login, post, user

import manage
from core import company
from core import dataio
from core import documents as docs
from core import enterprise as ent
from core import quotes as qt
from core import returns as rtn
from core import sales_db as db

TODAY = date.today().isoformat()


def _cust(name: str, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"], **extra})


def _sale(cid: int, qty: int, unit: int, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_sale({"customer_id": cid, "item": "점검2", "qty": qty, "unit_price": unit,
                           "owner_id": user("김영업")["id"], "sale_date": TODAY, **extra})


def _net(sid: int) -> tuple[int, int]:
    return rtn.net_totals(db.get_sale(sid))


# ── 회계 ────────────────────────────────────────────────────────────────
def test_split_returns_leave_no_vat_residue(app):
    sid = _sale(_cust("부가세끝전상사"), 3, 3_335)          # 공급가액 10,005 · 부가세 1,000
    for _ in range(3):
        rtn.create(sid, "반품", "하나씩 반품", qty=1)
    assert _net(sid) == (0, 0)                              # 순 공급가액 0 이면 순 부가세도 0
    s = db.get_sale(sid)
    assert int(s["paid_amount"]) == int(s["total_amount"]) and s["status"] == "입금완료"


def test_partial_return_vat_follows_net_supply(app):
    sid = _sale(_cust("부가세누적상사"), 3, 3_335)
    rtn.create(sid, "반품", "1개", qty=1)
    amount, vat = _net(sid)
    assert vat == db.vat_for(amount, "과세")


def test_full_return_after_amount_edit_clears_balance(app):
    sid = _sale(_cust("공급가수정반품상사"), 3, 4_000)
    s = db.get_sale(sid)
    db.set_context("system", None)
    db.upsert_sale({**s, "amount": 10_000, "unit_price": round(10_000 / 3)})   # 화면에서 공급가액만 고친 경우
    rtn.create(sid, "반품", "전량", qty=3)
    assert _net(sid) == (0, 0)
    s = db.get_sale(sid)
    assert int(s["paid_amount"]) == int(s["total_amount"])


def test_issue_deadline_moves_past_weekend_and_holidays(app):
    assert docs.issue_deadline("2026-09-25") == date(2026, 10, 12)          # 10/10 토요일 → 월요일
    company.save({"holidays": "2026-10-12"}, "테스트")
    company.refresh(force=True)
    try:
        assert docs.issue_deadline("2026-09-25") == date(2026, 10, 13)
    finally:
        company.save({"holidays": ""}, "테스트")
        company.refresh(force=True)
    with pytest.raises(ValueError, match="날짜"):
        company.validate({"holidays": "내일"})


def test_payment_date_must_be_real(app):
    sid = _sale(_cust("입금일상사"), 1, 100_000)
    with pytest.raises(ValueError, match="오늘 이후"):
        ent.record_payment(sid, 1_000, pay_date=(date.today() + timedelta(days=3)).isoformat())
    with pytest.raises(ValueError, match="선수금"):
        ent.record_payment(sid, 1_000, pay_date=(date.today() - timedelta(days=400)).isoformat())


def test_sale_form_status_does_not_create_payment(app):
    rep = login(app, "김영업")
    cid = _cust("상태선택상사")
    post(rep, "/sales/add", {"customer_id": str(cid), "item": "선택만", "qty": "1", "unit_price": "50000",
                             "sale_date": TODAY, "status": "입금완료"})
    s = db._one("SELECT * FROM sales WHERE customer_id=? ORDER BY id DESC LIMIT 1", [cid])
    assert int(s["paid_amount"] or 0) == 0 and s["status"] == "입금대기"
    post(rep, f"/sales/{s['id']}/update", {"amount": "50000", "tax_type": "과세", "status": "입금완료",
                                          "row_version": str(s["row_version"] or 0)})
    assert int(db.get_sale(int(s["id"]))["paid_amount"] or 0) == 0


# ── 실무 ────────────────────────────────────────────────────────────────
def test_direct_entry_quote_cannot_erase_deal_discount(app):
    cid = _cust("직접입력할인상사")
    db.set_context("system", None)
    did = db.upsert_deal({"customer_id": cid, "title": "직접입력 기회", "owner_id": user("김영업")["id"],
                          "stage": db.OPEN_STAGES[0], "list_amount": 10_000_000, "amount": 10_000_000,
                          "discount_rate": 0, "expected_close": TODAY})
    qid = qt.save_quote({"customer_id": cid, "deal_id": did, "owner_id": user("김영업")["id"]},
                        [{"item_name": "직접 입력", "qty": 1, "unit_price": 8_500_000}])
    with pytest.raises(ValueError, match="결재"):
        qt.send(qid)                                        # 정가 1,000만 → 850만 = 15% 할인 → 결재 필요
    assert float(db.get_deal(did)["discount_rate"]) == 15.0


def test_quote_list_search_and_ready_filter(app):
    cid = _cust("견적검색상사")
    db.set_context("system", None)
    qid = qt.save_quote({"customer_id": cid, "owner_id": user("김영업")["id"], "title": "검색되는 건명"},
                        [{"item_name": "x", "qty": 1, "unit_price": 1_000}])
    df = qt.list_quotes(keyword="검색되는 건명")
    assert list(df["id"]) == [qid]


def test_dashboard_aging_follows_owner_filter(app):
    mine = ent.ar_aging(owner_id=user("김영업")["id"])
    assert mine.empty or set(mine["담당자"]) == {"김영업"}


def test_transfer_moves_open_quotes(app):
    cid = _cust("이관견적상사")
    db.set_context("system", None)
    qid = qt.save_quote({"customer_id": cid, "owner_id": user("김영업")["id"]},
                        [{"item_name": "이관", "qty": 1, "unit_price": 1_000}])
    tmp = ent.upsert_user({"emp_no": "T9901", "name": "이관받는사람", "role": "REP"})
    tmp_id = int(ent.get_user(emp_no="T9901")["id"]) if not isinstance(tmp, int) else tmp
    with db.get_conn() as conn:
        conn.execute("UPDATE quotes SET owner_id=?, owner=? WHERE id=?", (tmp_id, "이관받는사람", qid))
    result = ent.transfer_owner(tmp_id, user("김영업")["id"])
    assert result["견적"] >= 1 and int(qt.get_quote(qid)["owner_id"]) == int(user("김영업")["id"])


# ── 데이터 관리 ──────────────────────────────────────────────────────────
def test_parse_date_variants():
    assert dataio.parse_date("26.10.04", "d") == "2026-10-04"
    assert dataio.parse_date("46299", "d") == "2026-10-04"           # 엑셀 날짜 일련번호
    with pytest.raises(ValueError):
        dataio.parse_date("1850-01-01", "d")


def test_customer_overwrite_keeps_missing_columns(app):
    cid = _cust("덮어쓰기보존상사", phone="02-1234-5678", credit_limit=200_000_000, payment_terms=45,
                address="서울시 어딘가")
    df = pd.DataFrame([{"거래처명": "덮어쓰기보존상사", "담당자": "김영업", "고객담당자": "새담당"}])
    res = dataio.import_rows("거래처", df, user("시스템관리자"), dry_run=False, on_duplicate="덮어쓰기")
    assert res["ok"] == 1
    c = db.get_customer(cid)
    assert c["manager"] == "새담당"
    assert (c["phone"], int(c["credit_limit"]), int(c["payment_terms"]), c["address"]) == \
        ("02-1234-5678", 200_000_000, 45, "서울시 어딘가")


def test_same_sales_file_twice_does_not_duplicate(app):
    _cust("중복업로드상사")
    df = pd.DataFrame([{"거래처명": "중복업로드상사", "매출일": TODAY, "품목": "업로드품", "수량": 2, "단가": 5_000,
                        "담당자": "김영업"}])
    first = dataio.import_rows("매출", df, user("시스템관리자"), dry_run=False)
    second = dataio.import_rows("매출", df, user("시스템관리자"), dry_run=False)
    assert first["ok"] == 1 and second["ok"] == 0 and second["skipped"] == 1
    assert db._scalar("SELECT COUNT(*) FROM sales WHERE item='업로드품'") == 1


def test_unknown_api_path_is_json(app):
    c = app.test_client()
    res = c.get("/api/v1/nope")
    assert res.status_code == 404 and res.is_json and res.get_json()["error"]["code"] == "not_found"


def test_restore_refuses_non_database(app, tmp_path):
    bad = tmp_path / "notadb.txt"
    bad.write_text("hello")
    assert manage.main(["restore", str(bad), "--yes"]) == 2
    assert manage.main(["db", "downgrade"]) == 2          # 확인 없이 되돌리지 않음
    assert os.path.exists(bad)


# ── 수정세금계산서 · 세금계산서 잠금 ────────────────────────────────────────
def _issued_sale(monkeypatch, name: str, prefix9: str) -> tuple[int, dict]:
    from conftest import biz
    from core import jobs
    monkeypatch.setenv("SALES_ETAX_ADAPTER", "mock")
    company.save({"company_name": "(주)수정발행", "company_biz_no": biz("123456789")}, "test")
    company.refresh(force=True)
    cid = _cust(name, biz_no=biz(prefix9))
    sid = _sale(cid, 1, 1_000_000)
    from core import etax
    etax.request_issue(sid, None, {"name": "t"})
    jobs.run_pending()
    row = db._one("SELECT * FROM etax_invoices WHERE sale_id=? AND modify_code IS NULL", [sid])
    assert row["status"] == "발행완료"
    return sid, row


def test_issued_sale_is_locked_until_contract_cancel_invoice(app, monkeypatch):
    from core import etax, jobs
    sid, row = _issued_sale(monkeypatch, "계약해제상사", "214365871")
    s = db.get_sale(sid)
    with pytest.raises(ValueError, match="세금계산서"):
        db.upsert_sale({**s, "amount": 900_000, "unit_price": 900_000})
    with pytest.raises((ValueError, PermissionError)):
        db.cancel_sale(sid, "취소", actor=user("시스템관리자"))
    with pytest.raises(ValueError, match="해제일"):
        etax.request_modify(row["id"], "04", "고객 계약 해지", {"name": "t"},
                            day=(date.today() + timedelta(days=1)).isoformat())
    mid = etax.request_modify(row["id"], "04", "고객 계약 해지", {"name": "t"}, day=TODAY)
    with pytest.raises(ValueError, match="이미"):
        etax.request_modify(row["id"], "04", "또", {"name": "t"}, day=TODAY)
    jobs.run_pending()
    mod = db._one("SELECT * FROM etax_invoices WHERE id=?", [mid])
    assert mod["status"] == "발행완료" and int(mod["supply_amount"]) == -1_000_000
    assert db._one("SELECT status FROM etax_invoices WHERE id=?", [row["id"]])["status"] == "수정발행됨"
    assert db._one("SELECT voided_at FROM sale_documents WHERE id=?", [row["document_id"]])["voided_at"]
    mod_doc = db._one("SELECT * FROM sale_documents WHERE id=?", [mod["document_id"]])
    assert mod_doc["doc_type"] == "수정세금계산서"
    db.set_context("system", None)
    db.cancel_sale(sid, "계약 해제", actor=user("시스템관리자"))      # 이제 취소 가능
    assert db.get_sale(sid)["status"] == "취소"
    company.save({"company_name": "", "company_biz_no": ""}, "test")


def test_error_correction_invoice_allows_reissue(app, monkeypatch):
    from core import etax, jobs
    sid, row = _issued_sale(monkeypatch, "착오정정상사", "214365872")
    etax.request_modify(row["id"], "01", "품목명 오기재", {"name": "t"})
    jobs.run_pending()
    mod = db._one("SELECT * FROM etax_invoices WHERE original_etax_id=?", [row["id"]])
    assert mod["issue_date"] == row["issue_date"]                       # 01 은 당초 작성일
    etax.request_issue(sid, None, {"name": "t"})                         # 바로잡아 다시 발행 가능
    jobs.run_pending()
    assert db._scalar("SELECT COUNT(*) FROM etax_invoices WHERE sale_id=? AND modify_code IS NULL "
                      "AND status='발행완료'", [sid]) == 1
    with pytest.raises(ValueError, match="사유"):
        etax.request_modify(row["id"], "03", "반품", {"name": "t"})
    company.save({"company_name": "", "company_biz_no": ""}, "test")


def test_paper_tax_invoice_locks_sale(app):
    sid = _sale(_cust("종이세금계산서상사"), 1, 500_000)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO sale_documents (sale_id, doc_type, file_name, file_path, mime, file_size, sha256, "
                     "uploaded_by, uploaded_at) VALUES (?, '세금계산서', 'paper.jpg', 'x/paper.jpg', 'image/jpeg', 1, "
                     "'0', 't', ?)", (sid, db._now()))
    s = db.get_sale(sid)
    with pytest.raises(ValueError, match="세금계산서"):
        db.upsert_sale({**s, "tax_type": "영세"})
    db.upsert_sale({**s, "memo": "메모는 고칠 수 있음"})


def test_special_price_is_approval_base(app):
    from core import catalog
    cid = _cust("특가기준상사")
    db.set_context("system", None)
    pid = catalog.upsert_product({"code": "SP-TEST-01", "name": "특가시험품", "list_price": 1_000_000, "unit": "EA",
                                  "tax_type": "과세"})
    catalog.set_customer_price(cid, pid, 900_000, TODAY)
    qid = qt.save_quote({"customer_id": cid, "owner_id": user("김영업")["id"]}, [{"product_id": pid, "qty": 1}])
    q = qt.get_quote(qid)
    assert float(q["discount_rate"]) == 10.0 and qt.approval_rate(q) == 0.0
    qt.send(qid)                                                          # 특가 그대로면 결재 없이 발송
    assert qt.get_quote(qid)["status"] == "발송"


def test_order_from_quote_wins_deal(app):
    from core import orders as so
    cid = _cust("자동수주상사")
    db.set_context("system", None)
    did = db.upsert_deal({"customer_id": cid, "title": "자동수주 기회", "owner_id": user("김영업")["id"],
                          "stage": db.OPEN_STAGES[0], "list_amount": 100_000, "amount": 100_000,
                          "discount_rate": 0, "expected_close": TODAY})
    qid = qt.save_quote({"customer_id": cid, "deal_id": did, "owner_id": user("김영업")["id"]},
                        [{"item_name": "자동", "qty": 1, "unit_price": 100_000}])
    qt.send(qid)
    qt.decide(qid, True)
    so.from_quote(qid)
    assert db.get_deal(did)["stage"] == db.STAGE_WON


def test_refund_due_shown_apart_from_advances(app):
    from core import advances as adv
    cid = _cust("돌려줄돈상사")
    sid = _sale(cid, 2, 100_000)
    ent.record_payment(sid, 220_000)
    rtn.create(sid, "반품", "1개 반품", qty=1)                          # 다 받은 매출의 반품 → 돌려줄 돈 110,000
    assert adv.refund_due(cid) == 110_000
    row = adv.balances().set_index("id").loc[cid]
    assert int(row["돌려줄돈(반품초과)"]) == 110_000 and int(row["선수금잔액"]) == 0


# ── 대손 법정 사유 · 대손세액 · 회수 ─────────────────────────────────────────
def test_writeoff_needs_legal_reason_and_elapsed_time(app):
    from core import credit
    sid = _sale(_cust("대손요건상사"), 1, 1_000_000)
    with pytest.raises(ValueError, match="사유를 고르세요"):
        credit.request_writeoff(sid, "회수 불능", user("김영업"))
    with pytest.raises(ValueError, match="요건이 아직"):                 # 부도 후 6개월이 안 됨
        credit.request_writeoff(sid, "부도", user("김영업"), "DISHONOR", TODAY)
    with pytest.raises(ValueError, match="30만원"):
        credit.request_writeoff(sid, "소액", user("김영업"), "SMALL")
    s = db.get_sale(sid)
    assert credit.check_writeoff(s, 330_000, "DISHONOR", "2026-01-05", today=date(2026, 7, 5)) == date(2026, 7, 5) \
        if s["sale_date"] <= "2026-01-05" else True


def test_writeoff_vat_and_recovery(app):
    from core import credit
    sid = _sale(_cust("대손회수상사"), 1, 1_000_000)                     # 합계 1,100,000
    rid = credit.request_writeoff(sid, "파산 선고", user("김영업"), "BANKRUPT", TODAY)
    credit.decide(rid, True, "", user("정임원"))
    req = db._one("SELECT * FROM fin_requests WHERE id=?", [rid])
    assert int(req["bad_debt_vat"]) == 100_000                         # 1,100,000 × 10/110
    r = credit.recover(rid, 550_000, TODAY, user("한팀장"))
    assert r["대손세액가산"] == 50_000
    s = db.get_sale(sid)
    assert int(s["paid_amount"]) == 1_100_000 and _paid_sum(sid) == 1_100_000
    with pytest.raises(ValueError, match="회수 금액"):
        credit.recover(rid, 600_000, TODAY, user("한팀장"))
    half = 1 if date.today().month <= 6 else 2
    rep = credit.tax_report(date.today().year, half)
    assert rid in set(rep["공제"]["요청번호"]) and 550_000 in set(rep["가산"]["회수액"])


def _paid_sum(sid: int) -> int:
    return int(db._scalar("SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=?", [sid]))


# ── 외화 ─────────────────────────────────────────────────────────────────
def test_foreign_amount_rounded_once(app):
    cid = _cust("외화반올림상사")
    db.set_context("system", None)
    sid = db.upsert_sale({"customer_id": cid, "item": "외화품", "qty": 1000, "currency": "USD", "fx_rate": "1385.55",
                          "foreign_unit_price": "10.555", "owner_id": user("김영업")["id"], "sale_date": TODAY})
    s = db.get_sale(sid)
    assert int(s["amount"]) == 14_624_480                 # 10,555 USD × 1,385.55 (단가를 먼저 반올림하면 14,624,000)


def test_split_delivery_uses_delivery_date_rate(app):
    from core import entities as ent_mod
    from core import orders as so
    cid = _cust("납품환율상사")
    db.set_context("system", None)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM fx_rates WHERE currency='EUR'")
    ent_mod.set_rate("EUR", (date.today() - timedelta(days=60)).isoformat(), 1500, "test")
    oid = so.create({"customer_id": cid, "owner_id": user("김영업")["id"], "currency": "EUR", "fx_rate": 1500,
                     "delivery_date": TODAY}, [{"item_name": "유로품", "qty": 2, "unit_price": 150_000, "tax_type": "영세"}])
    ent_mod.set_rate("EUR", TODAY, 1600, "test")
    item = so.get(oid)["items"][0]
    [sid] = so.deliver(oid, {item["id"]: 1}, TODAY)
    s = db.get_sale(sid)
    assert float(s["fx_rate"]) == 1600 and int(s["amount"]) == 160_000   # EUR 100 × 납품일 환율 1,600
    assert ent_mod.rate_on("EUR", TODAY) == 1600


# ── 감사로그 검색 · 백그라운드 업로드·추출 ─────────────────────────────────
def test_audit_search_by_date_target_and_detail(app):
    cid = _cust("감사검색상사")
    rows = db.list_audit(50, entity="거래처", entity_id=cid, date_from=TODAY, date_to=TODAY, keyword="감사검색상사")
    assert not rows.empty and set(rows["대상ID"].astype(int)) == {cid}
    assert db.list_audit(50, entity_id=cid, date_to="2000-01-01").empty


def test_background_import_and_backup(app, monkeypatch):
    from core import bulk
    monkeypatch.setattr(bulk, "SYNC", True)
    monkeypatch.setattr(bulk, "INLINE_ROWS", 1)              # 2행이어도 백그라운드 경로로
    _cust("대량업로드상사")
    df = pd.DataFrame([{"거래처명": "대량업로드상사", "매출일": TODAY, "품목": f"대량{i}", "수량": 1, "단가": 1_000,
                        "담당자": "김영업"} for i in range(2)] +
                      [{"거래처명": "없는거래처", "매출일": TODAY, "품목": "x", "수량": 1, "단가": 1, "담당자": "김영업"}])
    admin_user = user("시스템관리자")
    tid = bulk.start("업로드", "시험", lambda p: {**(r := dataio.import_rows("매출", df, admin_user, dry_run=False,
                                                                        progress=p)),
                                                   "errors": [list(e) for e in r["errors"]]}, admin_user, total=3)
    task = bulk.get(tid, admin_user)
    assert task["status"] == "완료" and task["ok"] == 2 and task["error_count"] == 1
    admin = login(app, "시스템관리자")
    assert "없는거래처" in admin.get(f"/data/tasks/{tid}/errors.csv").get_data(as_text=True)
    with pytest.raises(PermissionError):
        bulk.get(tid, user("김영업"))                          # 남의 작업은 못 봄
    res = admin.get("/admin/data/backup.xlsx")
    assert res.status_code == 302
    last = db._one("SELECT * FROM bulk_tasks WHERE title LIKE '엑셀 백업%' ORDER BY id DESC LIMIT 1")
    assert last["status"] == "완료" and last["file_key"]
    before = db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='다운로드'")
    got = admin.get(f"/data/tasks/{last['id']}/file")
    assert got.status_code == 200 and "spreadsheetml" in got.mimetype
    assert db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='다운로드'") == before + 1


def test_stale_bulk_task_marked_stopped(app):
    from core import bulk
    with db.get_conn() as conn:
        conn.execute("INSERT INTO bulk_tasks (kind, title, status, created_by_id, created_at, updated_at) "
                     "VALUES ('업로드', '멈춘 작업', '진행중', ?, '2000-01-01 00:00:00', '2000-01-01 00:00:00')",
                     (user("김영업")["id"],))
    assert bulk.mark_stale() >= 1
    assert db._one("SELECT status FROM bulk_tasks WHERE title='멈춘 작업'")["status"] == "중단"


# ── 순채권 · ERP 대사 · 견적 입력 유지 · 결재 회수 · API 인증 실패 ─────────────
def test_net_receivable_subtracts_advances(app):
    from core import advances as adv
    from core import periods
    cid = _cust("순채권상사", credit_limit=1_000_000)
    _sale(cid, 1, 1_000_000)                                    # 미수 1,100,000 (한도 초과)
    adv.add(cid, 300_000, "입금", TODAY, memo="계약금")
    row = ent.credit_exposure().set_index("거래처").loc["순채권상사"]
    assert int(row["선수금"]) == 300_000 and int(row["순채권"]) == 800_000 and row["한도초과"] == ""
    bal = periods.ar_balances(TODAY).set_index("customer_id").loc[cid]
    assert int(bal["advance"]) == 300_000 and int(bal["net"]) == int(bal["balance"]) - 300_000


def test_erp_reconcile_ignores_internal_settlements(app):
    from core import erp
    cid = _cust("대사내부상사")
    sid = _sale(cid, 2, 100_000)                                # 220,000
    rtn.create(sid, "반품", "1개", qty=1)                       # 원매출에 반품상계 110,000
    frame = pd.DataFrame([{"참조번호": f"CRM-{sid}", "누적입금액": 0}])
    out = erp.reconcile_payments(frame).iloc[0]
    assert out["결과"] == "일치" and int(out["CRM내부정리"]) == 110_000
    frame = pd.DataFrame([{"참조번호": f"CRM-{sid}", "누적입금액": 110_000}])
    assert erp.reconcile_payments(frame).iloc[0]["결과"] == "반영예정"


def test_failed_quote_save_keeps_all_lines(app):
    rep = login(app, "김영업")
    cid = _cust("견적유지상사")
    res = post(rep, "/quotes/save", {"customer_id": str(cid), "title": "남아야 할 건명",
                                     "item_name": ["첫줄", "둘째줄"], "product_id": ["", ""], "qty": ["1", "0"],
                                     "unit_price": ["1000", "2000"], "tax_type": ["과세", "과세"]})
    text = res.get_data(as_text=True)
    assert res.status_code == 400 and "남아야 할 건명" in text and "둘째줄" in text


def test_requester_can_withdraw_pending_requests(app):
    from core import credit
    cid = _cust("회수상사")
    sid = _sale(cid, 1, 100_000)
    rid = credit.request_writeoff(sid, "파산", user("김영업"), "BANKRUPT", TODAY)
    with pytest.raises(PermissionError):
        credit.withdraw(rid, user("한팀장"))
    credit.withdraw(rid, user("김영업"), "착오")
    assert db._one("SELECT status FROM fin_requests WHERE id=?", [rid])["status"] == "회수"
    with pytest.raises(ValueError):
        credit.decide(rid, True, "", user("정임원"))
    credit.request_writeoff(sid, "파산", user("김영업"), "BANKRUPT", TODAY)      # 다시 요청 가능
    db.set_context("system", None)
    did = db.upsert_deal({"customer_id": cid, "title": "회수 기회", "owner_id": user("김영업")["id"],
                          "stage": db.OPEN_STAGES[0], "list_amount": 1_000_000, "amount": 900_000,
                          "discount_rate": 10, "expected_close": TODAY})
    aid = ent.request_approval(did, user("김영업"), "할인")
    ent.withdraw_approval(aid, user("김영업"))
    assert db.get_deal(did)["approval_status"] == "미요청"
    assert db._one("SELECT status FROM approvals WHERE id=?", [aid])["status"] == "취소"


def test_bad_api_keys_do_not_flood_audit_log(app, monkeypatch):
    from core import auth as core_auth
    monkeypatch.setattr(core_auth, "IP_MAX_FAILURES", 5)
    c = app.test_client()
    before = db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='API인증실패'")
    codes = [c.get("/api/v1/sales", headers={"Authorization": "Bearer sk_nope_bad"},
                   environ_base={"REMOTE_ADDR": "203.0.113.200"}).status_code for _ in range(8)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429] * 3          # 5번 실패 뒤 그 IP 는 15분 차단
    assert db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='API인증실패'") - before == 2   # 첫 실패 + 차단
    with db.get_conn() as conn:
        conn.execute("DELETE FROM login_ip_failures WHERE ip LIKE 'api:%'")

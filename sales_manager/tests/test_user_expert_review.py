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

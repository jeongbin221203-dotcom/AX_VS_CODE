"""2026-10-04 점검에서 찾은 결함 회귀 테스트: 견적 상태 경쟁 · 반제 제한 · 정정 후 반품 단가 · 매출 수정 시 입금 보존 ·
너무 큰 숫자 · 로그인 후 이동 주소 · ERP 참조 · 개인정보 검색 LIKE."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import login, post, user

from core import enterprise as ent
from core import erp
from core import privacy
from core import quotes as qt
from core import returns as rtn
from core import sales_db as db
from views import auth as auth_views

TODAY = date.today().isoformat()


def _cust(name: str) -> int:
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"]})


def _sale(cid: int, qty: int = 10, unit: int = 100_000, **extra) -> int:
    db.set_context("system", None)
    return db.upsert_sale({"customer_id": cid, "item": "점검품목", "qty": qty, "unit_price": unit,
                           "owner_id": user("김영업")["id"], "sale_date": TODAY, **extra})


def _quote(cid: int) -> int:
    db.set_context("system", None)
    return qt.save_quote({"customer_id": cid, "owner_id": user("김영업")["id"]}, [{"item_name": "점검 견적", "qty": 2, "unit_price": 50_000}])


def test_quote_state_changes_cannot_race(app):
    cid = _cust("견적경쟁상사")
    qid = _quote(cid)
    stale = qt.get_quote(qid)                          # 두 사람이 같은 화면을 열어 둔 상태
    qt.send(qid)
    with pytest.raises(ValueError):
        qt.send(qid)                                   # 두 번 발송 안 됨
    qt.decide(qid, True)
    with pytest.raises(ValueError):
        qt.decide(qid, False, "늦은 거절")             # 수락된 견적을 뒤늦게 거절 못 함
    with pytest.raises(ValueError):
        qt.revise(qid)                                 # 수락된 견적은 개정 못 함
    qt.convert_to_sales(qid, TODAY)
    with pytest.raises(ValueError):
        qt.claim(stale)                                # 옛 판 번호로는 다시 선점 못 함
    assert db._scalar("SELECT COUNT(*) FROM sales WHERE quote_id=?", [qid]) == 1


def test_quote_zero_price_line_rejected(app):
    cid = _cust("무상견적상사")
    with pytest.raises(ValueError, match="1원 이상"):
        qt.save_quote({"customer_id": cid, "owner_id": user("김영업")["id"]}, [{"item_name": "무상", "qty": 1, "unit_price": 0}])


def test_internal_payments_cannot_be_reversed_and_only_once(app):
    cid = _cust("반제제한상사")
    sid = _sale(cid)
    rtn.create(sid, "반품", "불량", qty=2)              # 원매출에 '반품상계' 입금이 생김
    offset = db._one("SELECT id FROM payments WHERE sale_id=? AND source='반품상계'", [sid])
    with pytest.raises(ValueError, match="반제할 수 없습니다"):
        ent.reverse_payment(int(offset["id"]), "실수")
    pid = ent.record_payment(sid, 100_000)
    pay_id = db._scalar("SELECT MAX(id) FROM payments WHERE sale_id=? AND amount=100000", [sid])
    ent.reverse_payment(int(pay_id), "잘못 입력")
    with pytest.raises(ValueError, match="이미 반제"):
        ent.reverse_payment(int(pay_id), "또")
    assert pid is not None


def test_return_after_price_correction_uses_current_price(app):
    cid = _cust("정정후반품상사")
    sid = _sale(cid)                                   # 10개 × 10만
    rtn.create(sid, "정정", "단가 인하", new_unit_price=90_000)
    assert rtn.current_unit_price(db.get_sale(sid)) == 90_000
    r = rtn.create(sid, "반품", "불량", qty=1)
    ret = db.get_sale(r["sale_id"])
    assert int(ret["amount"]) == -90_000               # 처음 단가(10만)가 아니라 정정된 단가로 반품
    with pytest.raises(ValueError):
        rtn.create(sid, "정정", "같은 단가", new_unit_price=90_000)


def test_editing_sale_keeps_payments(app):
    cid = _cust("입금보존상사")
    sid = _sale(cid, qty=1, unit=1_000_000)            # 합계 110만
    ent.record_payment(sid, 500_000)
    s = db.get_sale(sid)
    db.set_context("system", None)
    db.upsert_sale({**s, "memo": "메모만 수정", "status": "입금대기"})
    s = db.get_sale(sid)
    assert int(s["paid_amount"]) == 500_000 and s["status"] == "부분입금"
    with pytest.raises(ValueError):
        db.upsert_sale({**s, "qty": 1, "unit_price": 100_000, "amount": 100_000})   # 이미 받은 돈보다 작게 못 줄임


def test_huge_number_is_a_message_not_500(app):
    rep = login(app, "김영업")
    cid = _cust("큰숫자상사")
    res = post(rep, "/sales/add", {"customer_id": str(cid), "item": "x", "qty": "9" * 40, "unit_price": "1",
                                    "sale_date": TODAY}, headers={"Referer": "http://localhost/sales"})
    assert res.status_code in (302, 400)


@pytest.mark.parametrize("nxt,ok", [("/deals", True), ("//evil.com", False), ("/\t/evil.com", False),
                                    ("/\\evil.com", False), ("https://evil.com", False), ("/ /evil.com", False)])
def test_next_url_only_same_site(app, nxt, ok):
    with app.test_request_context("/login"):
        assert (auth_views._next_url(nxt) == nxt) is ok


def test_erp_reference_matching(app):
    cid = _cust("ERP참조상사")
    sid = _sale(cid)
    assert erp._find_sale(f"CRM-{sid}", "")["id"] == sid
    assert erp._find_sale(f"CRM-WO-{sid}", "") is None   # 대손 결재 번호는 매출 번호가 아님
    db.set_context("system", None)
    with db.get_conn() as conn:
        conn.execute("UPDATE sales SET erp_doc_no=? WHERE id=?", ("ERP-77001", sid))
    assert erp._find_sale("ERP-77001", "")["id"] == sid  # 참조에 ERP 전표번호를 적은 경우


def test_privacy_search_treats_wildcards_literally(app):
    _cust("개인정보와일드상사")
    found = privacy.search("%%")
    assert found["customers"].empty and found["activities"].empty and found["contacts"].empty

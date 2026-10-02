"""데이터가 바뀌거나 저장이 날아가는 경우 — 동시 입금 · 동시 수정 · 두 번 제출 · ERP 중복 전송 · 인증 장애."""
from __future__ import annotations

import re
import threading

import pytest
from conftest import login, post, user

from core import auth as core_auth
from core import enterprise as ent
from core import erp
from core import offline
from core import sales_db as db


def _sale(name: str, unit: int = 1_000_000) -> int:
    db.set_context("system", None)
    owner = user("김영업")["id"]
    cid = db.upsert_customer({"name": name, "owner_id": owner, "erp_code": "S900"})
    return db.upsert_sale({"customer_id": cid, "item": "장비", "item_code": "HW-01", "qty": 1, "unit_price": unit,
                           "owner_id": owner, "sale_date": "2026-09-15"})


def test_concurrent_payments_are_not_lost(app):
    sid = _sale("동시입금상사")                        # 합계 1,100,000
    ok, errors = [], []

    def pay():
        db.set_context("system", None)
        try:
            ok.append(ent.record_payment(sid, 10_000))
        except db.ConflictError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=pay) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    paid = int(db.get_sale(sid)["paid_amount"])
    assert paid == 10_000 * len(ok)                      # 성공한 입금은 하나도 사라지지 않는다
    assert len(ok) + len(errors) == 8 and len(ok) >= 1


def test_erp_reconcile_does_not_double_apply(app):
    sid = _sale("대사중입금상사")
    db.set_context("system", None)
    ent.record_payment(sid, 100_000)
    # ERP 대사가 '입금 0' 을 기준으로 차액을 계산한 뒤, 그 사이 수기 입금이 들어온 상황
    with pytest.raises(db.ConflictError):
        ent.record_payment(sid, 500_000, source="ERP", expected_before=0)
    assert int(db.get_sale(sid)["paid_amount"]) == 100_000


def test_sale_edit_conflict_keeps_newer_data(app):
    sid = _sale("동시수정상사")
    rep = login(app, "김영업")
    page = rep.get(f"/sales?sid={sid}").get_data(as_text=True)
    version = re.search(r'name="row_version" value="(\d+)"', page).group(1)
    db.set_context("system", None)
    ent.record_payment(sid, 300_000)                     # 화면을 연 뒤 다른 사람이 입금
    res = post(rep, f"/sales/{sid}/update", {"status": "입금대기", "amount": "1000000", "row_version": version},
               follow_redirects=True)
    assert "그 사이" in res.get_data(as_text=True)
    sale = db.get_sale(sid)
    assert sale["status"] == "부분입금" and int(sale["paid_amount"]) == 300_000


def test_double_submit_is_processed_once(app):
    rep = login(app, "김영업")
    cid = db._one("SELECT id FROM customers WHERE name='동시수정상사'")["id"]
    form = {"customer_id": cid, "item": "두번클릭", "qty": "1", "unit_price": "50000", "status": "입금대기",
            "_submit_id": "11111111-2222-3333-4444-555555555555"}
    first = post(rep, "/sales/add", form)
    second = post(rep, "/sales/add", form, headers={"Referer": "http://localhost/sales"}, follow_redirects=True)
    assert first.status_code == 302 and "이미 처리된 요청" in second.get_data(as_text=True)
    assert len(db._df("SELECT id FROM sales WHERE item='두번클릭'")) == 1
    # 실패한 요청(검증 오류)은 같은 번호로 고쳐서 다시 보낼 수 있다
    bad = {**form, "item": "", "_submit_id": "99999999-2222-3333-4444-555555555555"}
    assert post(rep, "/sales/add", bad).status_code == 400
    assert post(rep, "/sales/add", {**bad, "item": "고쳐서다시"}).status_code == 302
    assert len(db._df("SELECT id FROM sales WHERE item='고쳐서다시'")) == 1


def test_erp_outbox_claimed_once(app, monkeypatch):
    with db.get_conn() as conn:
        conn.execute("UPDATE erp_outbox SET status='취소' WHERE status IN ('대기','실패')")
    sid = _sale("ERP중복상사")
    sent = []

    class SlowAdapter:
        name = "slow"

        def send(self, oid, doc):
            sent.append(oid)
            return f"DOC-{oid}"

    # 관리자 버튼과 배치가 동시에 같은 대기열을 처리
    results = []
    threads = [threading.Thread(target=lambda: results.append(erp.process_outbox(adapter=SlowAdapter())))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sent) == 1 and db.get_sale(sid)["erp_status"] == "전송완료"
    # 전송 도중 서버가 꺼져 '전송중' 으로 남은 건은 자동 재전송하지 않고 '실패'(확인 필요)로 돌린다
    with db.get_conn() as conn:
        conn.execute("UPDATE erp_outbox SET status='전송중', sent_at='2020-01-01 00:00:00' WHERE ref_id=?", (sid,))
    assert erp.recover_stuck() == 1
    row = db._one("SELECT status, attempts FROM erp_outbox WHERE ref_id=?", [sid])
    assert row["status"] == "실패" and int(row["attempts"]) >= erp.MAX_ATTEMPTS


def test_cancel_blocked_while_sending(app):
    sid = _sale("전송중취소상사")
    with db.get_conn() as conn:
        conn.execute("UPDATE erp_outbox SET status='전송중', sent_at=? WHERE ref_id=?", (db._now(), sid))
    db.set_context("system", None)
    with pytest.raises(ValueError, match="전송하는 중"):
        db.cancel_sale(sid, "중복")
    assert db.get_sale(sid)["status"] != db.SALE_CANCELLED


def test_breakglass_login_when_idp_down(app, monkeypatch):
    admin_user = user("시스템관리자")
    monkeypatch.setattr(core_auth, "AUTH_MODE", "password")
    core_auth.set_password(admin_user["id"], "Break!Glass2026x")
    monkeypatch.setattr(core_auth, "AUTH_MODE", "oidc")
    monkeypatch.setenv("SALES_BREAKGLASS_USERS", admin_user["emp_no"])
    client = app.test_client()
    assert "비상 계정 로그인" in client.get("/login").get_data(as_text=True)
    # 비상 계정이 아닌 사람은 비밀번호가 맞아도 들어올 수 없다
    res = post(client, "/login/breakglass", {"emp_no": user("김영업")["emp_no"], "password": "x"}, follow_redirects=True)
    assert "올바르지 않습니다" in res.get_data(as_text=True)
    res = post(client, "/login/breakglass", {"emp_no": admin_user["emp_no"], "password": "Break!Glass2026x"})
    assert res.status_code == 302
    assert client.get("/admin/settings").status_code == 200
    assert db._one("SELECT id FROM audit_log WHERE action='비상로그인'")
    monkeypatch.delenv("SALES_BREAKGLASS_USERS")
    assert not core_auth.breakglass_enabled()
    assert "비상 계정 로그인" not in app.test_client().get("/login").get_data(as_text=True)


def test_offline_dependency_report(app, monkeypatch):
    monkeypatch.setenv("SALES_ERP_REST_URL", "http://127.0.0.1:9/slips")
    monkeypatch.setenv("SALES_SMTP_HOST", "smtp.gmail.com")
    rows = {r["연결"]: r for r in offline.dependencies()}
    assert rows["ERP (REST)"]["위치"] == "사내망"
    assert rows["메일 (SMTP)"]["위치"] in ("외부 인터넷", "확인 불가 (이름 해석 실패)")
    admin = login(app, "시스템관리자")
    page = admin.get("/admin/settings").get_data(as_text=True)
    assert "인터넷이 끊겨도" in page and "ERP (REST)" in page
    # 화면은 외부 주소를 하나도 불러오지 않는다 (사내망만으로 열림)
    html = admin.get("/").get_data(as_text=True)
    assert not re.search(r'(src|href)="https?://', html)

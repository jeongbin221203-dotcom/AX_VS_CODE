"""2026-10-04 남은 결함 5종 회귀 테스트: 영업기회 없는 견적 할인 결재 · 전자세금계산서 전송중 멈춤·중복·늦은 회신 ·
여러 단계 처리의 원자성(분할 납품·일괄 입금·대손 결재) · (SAP 시간 초과 이중 전표는 test_core) · 예약 작업 이중 실행."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from conftest import user

from core import advances as adv
from core import credit
from core import database
from core import enterprise as ent
from core import etax
from core import jobs
from core import orders as so
from core import quotes as qt
from core import sales_db as db

TODAY = date.today().isoformat()


def _cust(name: str) -> int:
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"]})


def _sale(cid: int, unit: int = 1_000_000) -> int:
    db.set_context("system", None)
    return db.upsert_sale({"customer_id": cid, "item": "원자성", "qty": 1, "unit_price": unit,
                           "owner_id": user("김영업")["id"], "sale_date": TODAY})


def _paid(sid: int) -> int:
    return int(db.get_sale(sid)["paid_amount"] or 0)


# ── 1. 영업기회 없는 견적의 할인 ─────────────────────────────────────────
def test_discounted_quote_without_deal_needs_approval(app):
    cid = _cust("무기회할인상사")
    owner = user("김영업")["id"]
    db.set_context("system", None)
    discounted = qt.save_quote({"customer_id": cid, "owner_id": owner},
                               [{"item_name": "할인품", "qty": 1, "list_price": 100_000, "unit_price": 80_000}])
    with pytest.raises(ValueError, match="영업기회"):
        qt.send(discounted)                            # 20% 할인인데 결재 경로가 없으면 발송 안 됨
    assert qt.get_quote(discounted)["status"] == "작성중"
    plain = qt.save_quote({"customer_id": cid, "owner_id": owner},
                          [{"item_name": "정가품", "qty": 1, "unit_price": 100_000}])
    qt.send(plain)                                     # 할인 없으면 그대로 발송
    assert qt.get_quote(plain)["status"] == "발송"


# ── 2. 전자세금계산서 ───────────────────────────────────────────────────
def _etax_row(sid: int, status: str, **cols) -> int:
    fields = {"sale_id": sid, "status": status, "issue_date": TODAY, "requested_by": "테스트", "requested_at": db._now(), **cols}
    with db.get_conn() as conn:
        cur = conn.execute(f"INSERT INTO etax_invoices ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                           list(fields.values()))
        return int(cur.lastrowid)


def test_etax_one_active_issue_per_sale(app):
    sid = _sale(_cust("세금계산서중복상사"))
    _etax_row(sid, "발행요청")
    with pytest.raises(Exception):
        _etax_row(sid, "전송중")                       # DB 가 같은 매출의 두 번째 진행 중 발행을 거부
    _etax_row(sid, "실패")                              # 실패한 기록은 여러 개여도 됨


def test_etax_stuck_sending_is_requeued_but_waiting_ack_is_not(app):
    old = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    stuck = _etax_row(_sale(_cust("전송멈춤상사")), "전송중", claimed_at=old)
    waiting = _etax_row(_sale(_cust("회신대기상사")), "전송중", claimed_at=old, sent_at=old)   # 파일 방식: ASP 로 넘김
    fresh = _etax_row(_sale(_cust("방금전송상사")), "전송중", claimed_at=db._now())
    assert etax.recover_stuck() >= 1
    status = lambda eid: db._one("SELECT status FROM etax_invoices WHERE id=?", [eid])["status"]   # noqa: E731
    assert status(stuck) == "발행요청"
    assert status(waiting) == "전송중" and status(fresh) == "전송중"
    assert db._one("SELECT id FROM jobs WHERE kind='etax.issue' AND payload LIKE ? AND status='대기'",
                   [f'%"id": {stuck}%'])


def test_etax_late_approval_after_reissue_is_flagged(app):
    sid = _sale(_cust("늦은승인상사"))
    old = _etax_row(sid, "실패", error="ASP 응답 시간 초과")
    _etax_row(sid, "발행요청")                         # 실패로 보고 다시 요청
    number = "2026100441000000" + "12345678"
    res = etax.complete(old, number)                   # 옛 요청이 뒤늦게 승인됨 → 이중 발행
    assert res.get("duplicate") is True
    row = db._one("SELECT * FROM etax_invoices WHERE id=?", [old])
    assert row["approval_no"] == number and "이중 발행" in row["error"]
    assert db._one("SELECT id FROM audit_log WHERE action='세금계산서확인필요' AND entity_id=?", [sid])


def test_etax_rejection_does_not_undo_issued(app):
    sid = _sale(_cust("발행후거부상사"))
    eid = _etax_row(sid, "발행완료", approval_no="2" * 24, document_id=1)
    etax.complete(eid, "", ok=False, message="늦게 온 거부")
    assert db._one("SELECT status FROM etax_invoices WHERE id=?", [eid])["status"] == "발행완료"


# ── 3. 여러 단계 처리는 전부 되거나 전부 안 되거나 ─────────────────────────
def test_transaction_rolls_back_every_step(app):
    sid = _sale(_cust("트랜잭션상사"))
    with pytest.raises(RuntimeError):
        with db.transaction():
            ent.record_payment(sid, 100_000)
            assert _paid(sid) == 100_000                   # 같은 트랜잭션 안에서는 보임
            raise RuntimeError("두 번째 단계 실패")
    assert _paid(sid) == 0
    assert db._scalar("SELECT COUNT(*) FROM payments WHERE sale_id=?", [sid]) == 0


def test_inner_caught_error_keeps_outer_transaction_usable(app):
    sid = _sale(_cust("저장점상사"))
    with db.transaction():
        try:
            with db.get_conn() as conn:
                conn.execute("INSERT INTO payments (id, sale_id, pay_date, amount, created_at) VALUES (1, 1, '2026-01-01', 1, 'x')")
                conn.execute("INSERT INTO payments (id, sale_id, pay_date, amount, created_at) VALUES (1, 1, '2026-01-01', 1, 'x')")
        except Exception:   # noqa: BLE001 - 잡아서 넘긴 오류 (PostgreSQL 에서도 트랜잭션이 깨지지 않아야 함)
            pass
        ent.record_payment(sid, 50_000)
    assert _paid(sid) == 50_000


def test_lump_receipt_is_all_or_nothing(app, monkeypatch):
    cid = _cust("일괄입금원자상사")
    s1, s2 = _sale(cid), _sale(cid)
    real, calls = ent.record_payment, []

    def flaky(sale_id, *a, **kw):
        calls.append(sale_id)
        if len(calls) == 2:
            raise RuntimeError("두 번째 매출 입금 중 오류")
        return real(sale_id, *a, **kw)

    monkeypatch.setattr(ent, "record_payment", flaky)
    with pytest.raises(RuntimeError):
        adv.receive(cid, 3_000_000, TODAY)
    assert _paid(s1) == 0 and _paid(s2) == 0 and adv.balance(cid) == 0


def test_split_delivery_is_all_or_nothing(app, monkeypatch):
    cid = _cust("납품원자상사")
    db.set_context("system", None)
    oid = so.create({"customer_id": cid, "owner_id": user("김영업")["id"], "delivery_date": TODAY},
                    [{"item_name": "본체", "qty": 5, "unit_price": 100_000, "tax_type": "과세"},
                     {"item_name": "부속", "qty": 5, "unit_price": 10_000, "tax_type": "과세"}])
    a, b = so.get(oid)["items"]
    real, calls = db.upsert_sale, []

    def flaky(data, *x, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("두 번째 품목 매출 등록 실패")
        return real(data, *x, **kw)

    monkeypatch.setattr(db, "upsert_sale", flaky)
    with pytest.raises(RuntimeError):
        so.deliver(oid, {a["id"]: 2, b["id"]: 2})
    o = so.get(oid)
    assert [i["remain"] for i in o["items"]] == [5, 5] and not o["sales"]


def test_writeoff_approval_rolls_back_when_nothing_left(app):
    cid = _cust("대손원자상사")
    sid = _sale(cid, unit=100_000)
    rid = credit.request_writeoff(sid, "회수 불가", user("김영업"))
    ent.record_payment(sid, 110_000)                   # 결재 전에 전액 입금
    with pytest.raises(ValueError, match="미수금이 없습니다"):
        credit.decide(rid, True, "", user("시스템관리자"))
    assert db._one("SELECT status FROM fin_requests WHERE id=?", [rid])["status"] == "대기"   # 결재 상태도 되돌아감
    credit.decide(rid, False, "입금됨", user("시스템관리자"))
    with pytest.raises(ValueError):
        credit.decide(rid, False, "또", user("시스템관리자"))


# ── 5. 예약 작업 이중 실행 ──────────────────────────────────────────────
def test_job_that_lost_its_lock_does_not_overwrite(app, monkeypatch):
    @jobs.handler("test.slow")
    def _slow(payload):
        # 실행 도중 다른 서버가 '죽은 작업'으로 보고 가져간 상황
        with db.get_conn() as conn:
            conn.execute("UPDATE jobs SET locked_by='other:1', locked_at=? WHERE kind='test.slow' AND status='실행중'",
                         (db._now(),))
        return {"done": True}

    jobs.enqueue("test.slow", {}, dedupe_key=f"slow-{datetime.now().timestamp()}")
    while jobs.run_one("me:1"):
        if db._one("SELECT id FROM jobs WHERE kind='test.slow' AND status <> '대기'", []):
            break
    row = db._one("SELECT * FROM jobs WHERE kind='test.slow' ORDER BY id DESC LIMIT 1", [])
    assert row["status"] == "실행중" and row["locked_by"] == "other:1"   # 가져간 서버의 실행 상태를 덮어쓰지 않음
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET status='완료', locked_by=NULL WHERE kind='test.slow'")


def test_job_lease_is_renewed_while_running(app, monkeypatch):
    monkeypatch.setattr(jobs, "LEASE_SECONDS", 0.2)
    seen = {}

    @jobs.handler("test.lease")
    def _lease(payload):
        first = db._one("SELECT locked_at FROM jobs WHERE kind='test.lease' AND status='실행중'", [])["locked_at"]
        with db.get_conn() as conn:                    # 오래된 시각으로 만들어 두고
            conn.execute("UPDATE jobs SET locked_at='2000-01-01 00:00:00' WHERE kind='test.lease' AND status='실행중'")
        import time
        time.sleep(0.7)                                # 갱신될 시간
        seen["after"] = db._one("SELECT locked_at FROM jobs WHERE kind='test.lease' AND status='실행중'", [])["locked_at"]
        seen["first"] = first
        return {}

    jobs.enqueue("test.lease", {}, dedupe_key=f"lease-{datetime.now().timestamp()}")
    for _ in range(50):
        if not jobs.run_one("me:2") or "after" in seen:
            break
    assert seen["after"] > "2000-01-01 00:00:00"          # 실행 중에 잠금 시각이 갱신됨 → 다른 서버가 가져가지 않음
    assert json.loads(db._one("SELECT result FROM jobs WHERE kind='test.lease' ORDER BY id DESC LIMIT 1", [])["result"]) == {}
    assert database.scalar("SELECT COUNT(*) FROM jobs WHERE kind='test.lease' AND status='완료'") >= 1

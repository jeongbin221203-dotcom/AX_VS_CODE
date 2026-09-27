"""제출 전 검토에서 찾은 문제의 회귀 테스트 (업무 로직 · 권한 범위 · 인증).

SQLite(기본)와 PostgreSQL(MM_DATABASE_URL=...mm_test) 모두에서 돌린다.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_rev_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import config  # noqa: E402
from core import auth, db, mfa, org, periods, purchasing, sap, services, valuation  # noqa: E402
from core.utils import prev_month  # noqa: E402
from test_advanced import CLERK, M1, M2, fresh, idp, mid, sso_login, stock, wh  # noqa: E402,F401
from test_app import PW, app, client, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def new_material(code: str, price: float = 100) -> int:
    assert services.create_material({"code": code, "name": code, "unit": "EA", "unit_price": price}).ok
    return mid(code)


def balanced(row) -> bool:
    """기초 + 입고 − 출고원가 + 조정 + 이동 = 기말."""
    calc = row["open_value"] + row["receipts"] - row["issues"] + row["adjustments"] + row["transfers"]
    return abs(calc - row["close_value"]) < 0.01


# ── 업무 로직 ────────────────────────────────────────────────
def test_adjustment_approval_uses_master_price(fresh):
    m = mid("PKG-001")                                        # 단가 18,000, 재고 35
    r = services.register_transaction(m, "ADJ", 0, TODAY, 0, actor=CLERK)
    assert r.ok and r.pending, "화면 단가 0으로 결재를 건너뛸 수 없다 (35 × 18,000 = 63만 원)"
    assert stock("PKG-001") == 35


def test_po_item_leading_zero_cannot_over_receive(fresh):
    m = mid("PKG-002")
    pr = purchasing.create_pr(wh(), [(m, 10, 1000)], TODAY, "소량", CLERK).id
    assert purchasing.decide_pr(pr, True, "", M1).ok
    po = purchasing.create_po(pr, "거래처", {}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    if db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po.id,)) == "PENDING_APPROVAL":
        assert purchasing.approve_po(po.id, M1).ok
    assert services.register_transaction(m, "IN", 10, TODAY, 1000, po_no=po_no, po_item="010", actor=CLERK).ok
    assert db.scalar("SELECT po_item FROM transactions WHERE po_no = ?", (po_no,)) == "10"
    assert not services.register_transaction(m, "IN", 1, TODAY, 1000, po_no=po_no, po_item="10").ok, "초과 입고"


def test_backdated_issue_checks_every_later_day(fresh):
    m = new_material("BK-1")
    assert services.register_transaction(m, "IN", 10, days_ago(5), 100).ok
    assert services.register_transaction(m, "OUT", 10, days_ago(3), 0).ok
    assert services.register_transaction(m, "IN", 10, TODAY, 100).ok
    r = services.register_transaction(m, "OUT", 5, days_ago(4), 0)
    assert not r.ok and "재고 부족" in r.message, "그 뒤 날짜의 재고가 음수가 된다"
    assert not services.transfer(m, wh(), wh(), 1, TODAY).ok                       # 같은 창고
    assert services.register_transaction(m, "OUT", 5, TODAY, 0).ok


def test_backdated_adjustment_uses_book_qty_of_that_day(fresh):
    m = new_material("BK-2")
    assert services.register_transaction(m, "IN", 10, days_ago(5), 100).ok
    assert services.register_transaction(m, "IN", 10, TODAY, 100).ok
    r = services.register_transaction(m, "ADJ", 8, days_ago(4), 100)     # 그날 장부 10 → 실사 8
    assert r.ok and r.qty == -2 and stock("BK-2") == 18


def test_reversal_of_pre_sap_transaction_does_not_block_close(fresh):
    m = new_material("SAP-0")
    tx = services.register_transaction(m, "IN", 5, TODAY, 100).tx_id    # SAP 연동 전
    config.SAP_MODE = "mock"
    r = services.reverse_transaction(tx, "오입력", actor=M1)
    assert r.ok
    assert db.scalar("SELECT COUNT(*) FROM sap_outbox") == 0, "SAP에 없는 거래의 취소는 보낼 것이 없다"
    with db.get_conn() as conn:
        assert sap.unsent_until(conn, TODAY) == 0


def test_mavg_residual_is_booked_to_adjustments(fresh):
    m = new_material("MV-1")
    a = services.register_transaction(m, "IN", 10, TODAY, 100).tx_id
    b = services.register_transaction(m, "IN", 10, TODAY, 300).tx_id
    services.register_transaction(m, "OUT", 10, TODAY, 0)               # 평균 200 → 10개 2,000 남음
    assert services.reverse_transaction(b, "단가 착오", actor=M1).ok      # 300짜리 입고 취소 → 수량 0
    row = valuation.report("2000-01-01", TODAY, "MAVG")[0].set_index("code").loc["MV-1"]
    assert row["close_qty"] == 0 and row["close_value"] == 0
    assert balanced(row) and round(row["adjustments"]) == 1000
    assert a


def test_reversal_of_closed_month_transaction_is_valued(fresh):
    m = new_material("CL-1", price=100)
    old = (date.today().replace(day=1) - timedelta(days=40)).isoformat()     # 두 달 전
    tx = services.register_transaction(m, "IN", 10, old, 250).tx_id
    this = TODAY[:7]
    ym = periods.next_closable()
    while ym < this:
        assert periods.close_month(ym, None).ok, ym
        ym = periods.next_closable()
    assert periods.closed_through() == prev_month(this)
    assert services.reverse_transaction(tx, "반품", actor=M1).ok
    for method in ("MAVG", "FIFO"):
        df, warnings = valuation.report(f"{this}-01", TODAY, method)
        row = df.set_index("code").loc["CL-1"]
        assert round(row["open_value"]) == 2500 and round(row["receipts"]) == -2500, method
        assert row["issues"] == 0 and row["close_value"] == 0 and balanced(row), method
        assert any("마감 이전" in w for w in warnings)


# ── 권한 범위 ────────────────────────────────────────────────
def test_scoped_manager_cannot_touch_other_warehouse_purchases(fresh):
    pr = purchasing.create_pr(wh(), [(mid("PKG-002"), 5, 1000)], TODAY, "범위", CLERK).id
    assert not purchasing.cancel_pr(pr, M1, wh_ids={-1}).ok
    assert purchasing.decide_pr(pr, True, "", M1).ok
    po = purchasing.create_po(pr, "거래처", {}, "", "", M2)
    assert not purchasing.cancel_po(po.id, M1, wh_ids={-1}).ok
    assert not purchasing.set_sap_po_no(po.id, "4500000001", M1, wh_ids={-1}).ok
    assert periods.snapshot_df("2000-01", wh_ids=set()).empty


def test_transfer_reversal_needs_sender_scope(fresh):
    assert org.create_plant("P2", "2공장", "2000", None).ok
    assert org.create_warehouse(int(db.scalar("SELECT id FROM plants WHERE code='P2'")), "WH2", "2창고", "", None).ok
    r = services.transfer(mid("PKG-003"), wh("WH1"), wh("WH2"), 10, TODAY, actor=CLERK)
    in_leg = int(db.scalar("SELECT id FROM transactions WHERE transfer_no <> '' AND tx_type = 'IN'"))
    assert not services.reverse_transaction(in_leg, "취소", actor=M1, wh_ids={wh("WH2")}).ok
    assert services.reverse_transaction(r.tx_id, "취소", actor=M1, wh_ids={wh("WH1")}).ok


# ── 인증 ─────────────────────────────────────────────────────
def test_new_user_has_no_warehouse_scope_by_default(fresh, monkeypatch):
    monkeypatch.setattr(config, "NEW_USER_ALL_WAREHOUSES", False)
    u = auth.create_user("newbie", "신입", "CLERK", PW, None, must_change_pw=False).user
    assert org.allowed_warehouses(u) == set(), "관리자가 범위를 주기 전에는 아무 창고도 못 본다"


def test_logout_ends_session_on_server(app):
    c = login(app.test_client(), "clerk")
    cookie = c.get_cookie(config.SESSION_COOKIE_NAME).value
    post(c, "/logout")
    thief = app.test_client()
    thief.set_cookie(config.SESSION_COOKIE_NAME, cookie)
    assert thief.get("/stock/").status_code == 302, "로그아웃한 세션 쿠키는 다시 쓸 수 없다"


def test_get_request_does_not_log_out(app):
    c = login(app.test_client(), "clerk")
    c.get("/login/mfa")
    assert c.get("/stock/").status_code == 200


def test_admin_suspension_survives_sso_login(app, idp):
    claims = {"sub": "u-9", "preferred_username": "park", "name": "박", "groups": ["grp-mm-clerk"]}
    sso_login(app.test_client(), idp, claims)
    uid = int(db.scalar("SELECT id FROM users WHERE sso_subject = 'u-9'"))
    assert auth.update_user(uid, "박", "CLERK", False, None).ok
    c = app.test_client()
    sso_login(c, idp, claims)
    assert db.scalar("SELECT active FROM users WHERE id = ?", (uid,)) == 0, "관리자가 중지한 계정은 SSO로 안 살아난다"
    assert c.get("/stock/").status_code == 302

    sso_login(app.test_client(), idp, {**claims, "groups": ["other"]})           # 그룹에서 빠져 중지된 경우는
    assert auth.update_user(uid, "박", "CLERK", True, None).ok
    sso_login(app.test_client(), idp, {**claims, "groups": ["other"]})
    sso_login(app.test_client(), idp, claims)                                   # 그룹에 다시 들면 살아난다
    assert db.scalar("SELECT active FROM users WHERE id = ?", (uid,)) == 1


def test_recovery_codes_are_strong_and_salted(fresh):
    u = auth.create_user("otp2", "오티피", "CLERK", PW, None, must_change_pw=False).user
    secret = mfa.new_secret()
    ok, _, codes = mfa.enable(u["id"], secret, mfa.now_code(secret), CLERK)
    assert ok and all(len(c.replace("-", "")) == 16 for c in codes), "64비트"
    stored = db.scalar("SELECT recovery_codes FROM users WHERE id = ?", (u["id"],))
    assert stored.count("$") == 10, "코드마다 소금"
    assert mfa.verify(u["id"], codes[3].lower())[0]
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'MFA_RECOVERY'") == 1

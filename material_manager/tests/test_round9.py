"""감사로그 앵커(서버 밖 보관) · 결재 요청 회수 · 데이터 범위 기한·사유."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r9_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from core import approvals, audit, auth, db, jobs, org, seed, services, workflow  # noqa: E402
from test_advanced import M1, M2, fresh, mid, wh  # noqa: E402,F401
from test_app import app, login, post  # noqa: E402,F401


def _log(n: int) -> None:
    for i in range(n):
        audit.log({"id": None, "name": "t", "role": "ADMIN", "ip": "1.2.3.4"}, "SEED", "x", i, {"i": i})


# ── 감사로그 앵커 (서버 밖 보관) ─────────────────────────────
def test_anchor_roundtrip_and_tamper_detection(app):  # noqa: F811
    _log(5)
    text = audit.anchor_text()
    parsed = audit.parse_anchor(text)
    assert parsed and parsed[0] >= 5 and len(parsed[1]) == 64
    assert audit.parse_anchor("ANCHOR seq=7 hash=" + "a" * 64) == (7, "a" * 64)
    assert audit.parse_anchor("엉뚱한 글") is None
    ok, msg = audit.check_anchor(*parsed)
    assert ok and "정상" in msg
    _log(3)                                                    # 앵커 뒤에 기록이 늘어도 앵커는 여전히 맞다
    assert audit.check_anchor(*parsed)[0]
    assert not audit.check_anchor(parsed[0], "0" * 64)[0]       # 해시가 다르면 거부
    assert not audit.check_anchor(99999, parsed[1])[0]          # 없는 번호
    if not db.is_pg():                                          # 트리거를 끄고 고쳐도 앵커·체인이 찾아낸다
        db.execute("DROP TRIGGER audit_seal_only")
        first = int(db.scalar("SELECT MIN(seq) FROM audit_log"))
        db.execute("UPDATE audit_log SET detail = '위조' WHERE seq = ?", (first,))
        ok, msg = audit.check_anchor(*parsed)
        assert not ok and ("바뀌" in msg or "끊어" in msg or "다릅니다" in msg)


def test_anchor_download_check_and_age(app):  # noqa: F811
    client = login(app.test_client(), "admin")
    assert audit.anchor_age_days() is None
    res = client.get("/admin/audit/anchor.txt")
    assert res.status_code == 200 and b"ANCHOR seq=" in res.data
    assert audit.anchor_age_days() == 0
    body = res.get_data(as_text=True)
    page = post(client, "/admin/audit/anchor-check", {"anchor": body}, follow_redirects=True).get_data(as_text=True)
    assert "정상입니다" in page
    page = post(client, "/admin/audit/anchor-check", {"anchor": "ANCHOR seq=1 hash=" + "f" * 64},
                follow_redirects=True).get_data(as_text=True)
    assert "다릅니다" in page
    page = post(client, "/admin/audit/anchor-check", {"anchor": "아무 글"}, follow_redirects=True).get_data(as_text=True)
    assert "읽지 못했습니다" in page
    from core import doctor
    checks = {c.name: c for c in doctor.run()}
    assert checks["감사로그 무결성"].status == "ok"
    ran, msg = jobs.run("audit_anchor", force=True)
    assert ran and "앵커" in msg


def test_doctor_warns_when_anchor_missing(fresh):  # noqa: F811
    from core import doctor
    checks = {c.name: c for c in doctor.run()}
    assert checks["감사로그 무결성"].status == "warn" and "앵커" in checks["감사로그 무결성"].message


# ── 결재 요청 회수 ───────────────────────────────────────────
def _adj_request(requester):
    with db.transaction() as conn:
        return approvals.create(conn, "ADJ", mid("PKG-001"), wh(), "2026-01-01", 5.0, 5000.0, requester, {"reason": "시험"})


def test_withdraw_only_by_requester_and_once(fresh):  # noqa: F811
    me = {"id": 41, "name": "요청자", "role": "CLERK", "ip": ""}
    other = {"id": 42, "name": "남", "role": "MANAGER", "ip": ""}
    rid = _adj_request(me)
    assert not approvals.withdraw(rid, other).ok                      # 남은 못 회수
    assert approvals.withdraw(rid, me).ok
    assert db.scalar("SELECT status FROM approval_requests WHERE id = ?", (rid,)) == "WITHDRAWN"
    assert not approvals.withdraw(rid, me).ok                         # 두 번은 안 됨
    assert not approvals.decide(rid, True, "", other).ok              # 회수한 요청은 결재도 안 됨
    assert int(db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'APPROVAL_WITHDRAW'")) == 1


def test_withdraw_loses_against_decision_and_cancel_can_be_requested_again(fresh):  # noqa: F811
    me = {"id": 43, "name": "요청자", "role": "CLERK", "ip": ""}
    rid = _adj_request(me)
    assert approvals.decide(rid, False, "반려", M1).ok                 # 결재자가 먼저 처리
    r = approvals.withdraw(rid, me)
    assert not r.ok and "이미" in r.message
    tx = services.register_transaction(mid("PKG-001"), "IN", 3, date.today().isoformat(), 100, warehouse_id=wh(), actor=M2)
    assert tx.ok
    first = approvals.request_cancel(tx.tx_id, "잘못", me)
    assert first.ok
    assert not approvals.request_cancel(tx.tx_id, "또", me).ok         # 처리 중이라 중복 불가
    assert approvals.withdraw(int(first.tx_id), me).ok
    assert approvals.request_cancel(tx.tx_id, "다시", me).ok           # 회수했으니 다시 요청 가능


def test_withdraw_screen_flow(app):  # noqa: F811
    seed.seed()
    clerk_id = int(db.scalar("SELECT id FROM users WHERE username = 'clerk'"))
    rid = _adj_request({"id": clerk_id, "name": "clerk님", "role": "CLERK", "ip": ""})
    client = login(app.test_client(), "clerk")
    assert [w["id"] for w in workflow.withdrawable(clerk_id)] == [rid]
    assert "회수할 수 있는 요청" in client.get("/approvals/?tab=requests").get_data(as_text=True)
    res = post(client, "/approvals/withdraw", {"kind": "ADJ", "id": str(rid)}, follow_redirects=True)
    assert "회수했습니다" in res.get_data(as_text=True)
    assert workflow.withdrawable(clerk_id) == []
    assert "회수" in client.get("/approvals/?tab=requests").get_data(as_text=True)
    bad = post(client, "/approvals/withdraw", {"kind": "XX", "id": "1"}, follow_redirects=True)
    assert "회수할 수 없는" in bad.get_data(as_text=True)
    big = post(client, "/approvals/withdraw", {"kind": "ADJ", "id": "9" * 30}, follow_redirects=True)
    assert big.status_code == 200 and "요청이 없거나" in big.get_data(as_text=True)


# ── 데이터 범위 기한·사유 ────────────────────────────────────
def _scoped_user() -> int:
    r = auth.create_user("scoped.user", "범위사용자", "CLERK", "Passw0rd!", audit.SYSTEM, must_change_pw=False)
    uid = r.user["id"]
    db.execute("UPDATE users SET all_warehouses = 0 WHERE id = ?", (uid,))
    return uid


def test_temp_scope_expires_and_validations(fresh):  # noqa: F811
    uid = _scoped_user()
    admin = {"id": 999, "name": "관리자", "role": "ADMIN", "ip": ""}
    w = wh()

    def user():
        return auth.get_user(uid)

    assert org.allowed_warehouses(user()) == set()                           # 기본 범위 없음
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert not org.add_temp_scope(uid, [], [w], tomorrow, "", admin).ok         # 사유 필수
    assert not org.add_temp_scope(uid, [], [w], "2000-01-01", "지난 날", admin).ok    # 과거 기한
    assert not org.add_temp_scope(uid, [], [w], (date.today() + timedelta(days=9999)).isoformat(), "너무 김", admin).ok
    assert not org.add_temp_scope(uid, [], [], tomorrow, "비어 있음", admin).ok
    assert not org.add_temp_scope(uid, [], [w], tomorrow, "본인", {"id": uid, "name": "본인", "role": "ADMIN", "ip": ""}).ok
    assert org.add_temp_scope(uid, [], [w], tomorrow, "휴가 대행", admin).ok
    assert org.allowed_warehouses(user()) == {w}
    db.execute("UPDATE user_scopes SET valid_to = ? WHERE user_id = ?", ((date.today() - timedelta(days=1)).isoformat(), uid))
    assert org.allowed_warehouses(user()) == set()                           # 기한이 지나면 자동으로 빠진다
    assert org.temp_scopes(uid)[0]["expired"] is True
    db.execute("UPDATE user_scopes SET valid_to = ? WHERE user_id = ?", (date.today().isoformat(), uid))
    assert org.allowed_warehouses(user()) == {w}                             # 기한 당일은 포함
    assert org.user_scope(uid) == (set(), set())                             # 기본 범위 화면에 임시 범위가 섞이지 않는다
    assert org.set_user_scope(uid, False, [], [w], admin).ok
    assert org.user_scope(uid) == (set(), {w}) and len(org.temp_scopes(uid)) == 1   # 기본 범위 저장은 임시 범위를 건드리지 않음
    assert org.set_user_scope(uid, True, [], [], admin).ok
    assert org.temp_scopes(uid) == []                                        # 전체 창고로 바꾸면 임시 범위 정리
    assert not org.add_temp_scope(uid, [], [w], tomorrow, "전체인데", admin).ok     # 이미 전체 권한


def test_temp_scope_revoke_and_screen(app):  # noqa: F811
    seed.seed()
    uid = _scoped_user()
    client = login(app.test_client(), "admin")
    w = wh()
    assert "임시 범위 추가" in client.get("/admin/users").get_data(as_text=True)
    until = (date.today() + timedelta(days=3)).isoformat()
    res = post(client, f"/admin/users/{uid}/scope-temp", {"warehouse": str(w), "valid_to": until, "reason": "휴가 대행"},
               follow_redirects=True)
    assert "임시 범위를 추가했습니다" in res.get_data(as_text=True)
    page = client.get("/admin/users").get_data(as_text=True)
    assert "휴가 대행" in page and "적용 중" in page
    sid = org.temp_scopes(uid)[0]["id"]
    res = post(client, f"/admin/scope-temp/{sid}/revoke", follow_redirects=True)
    assert "회수했습니다" in res.get_data(as_text=True)
    assert org.temp_scopes(uid) == []
    assert int(db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'SCOPE_TEMP'")) == 2


def test_temp_scope_used_for_notification_recipients(fresh):  # noqa: F811
    from core import notify
    uid = _scoped_user()
    db.execute("UPDATE users SET role = 'MANAGER' WHERE id = ?", (uid,))
    w = wh()
    with db.get_conn() as conn:
        assert uid not in {r["id"] for r in notify.recipients(conn, "MANAGER", w, [])}
    until = (date.today() + timedelta(days=2)).isoformat()
    assert org.add_temp_scope(uid, [], [w], until, "대행", {"id": 999, "name": "관리자", "role": "ADMIN", "ip": ""}).ok
    with db.get_conn() as conn:
        assert uid in {r["id"] for r in notify.recipients(conn, "MANAGER", w, [])}      # 임시 범위로도 결재 알림을 받는다
    db.execute("UPDATE user_scopes SET valid_to = ? WHERE user_id = ?", ((date.today() - timedelta(days=1)).isoformat(), uid))
    with db.get_conn() as conn:
        assert uid not in {r["id"] for r in notify.recipients(conn, "MANAGER", w, [])}  # 기한이 지나면 안 받는다


# ── 점검 에이전트가 찾은 결함의 회귀 테스트 ───────────────────────
def test_history_background_export_works_and_viewer_can_see_own_task(app, monkeypatch):  # noqa: F811
    import config
    from core import tasks
    seed.seed()
    monkeypatch.setattr(config, "BG_EXPORT_ROWS", -1)                   # 무조건 백그라운드
    monkeypatch.setattr(config, "BG_INLINE", True)
    client = login(app.test_client(), "viewer")                          # 조회 역할도 자기 작업 화면을 볼 수 있어야 한다
    res = client.get("/history/?start=2000-01-01&end=2099-01-01&type=IN&type=OUT&type=ADJ&export=xlsx")
    assert res.status_code == 302 and "/tasks/" in res.headers["Location"]
    tid = int(res.headers["Location"].rsplit("/", 1)[-1])
    task = tasks.get(tid)
    assert task["status"] == "DONE", task["message"]                      # audit 가져오기 빠짐(NameError)으로 항상 실패하던 것
    assert client.get(f"/tasks/{tid}").status_code == 200
    assert client.get(f"/tasks/{tid}.json").get_json()["status"] == "DONE"
    dl = client.get(f"/tasks/{tid}/download")
    assert dl.status_code == 200 and dl.data[:2] == b"PK"
    assert client.get("/tasks/").status_code == 200


def test_mark_stale_skips_running_here_and_reads_before_writing(fresh):  # noqa: F811
    from core import tasks
    db.execute("INSERT INTO bg_tasks (kind, title, status, created_at, heartbeat_at) VALUES ('t', 'a', 'RUNNING', "
               "'2000-01-01 00:00:00', '2000-01-01 00:00:00')")
    tid = int(db.scalar("SELECT id FROM bg_tasks WHERE title = 'a'"))
    tasks._threads[tid] = object()                                       # 이 서버에서 아직 도는 작업
    try:
        assert tasks.mark_stale() == 0
        assert db.scalar("SELECT status FROM bg_tasks WHERE id = ?", (tid,)) == "RUNNING"
    finally:
        tasks._threads.pop(tid, None)
    assert tasks.mark_stale() == 1
    assert tasks.mark_stale() == 0                                       # 없으면 아무것도 안 쓴다


def test_cancel_pr_conditional_and_marks_notification_read(fresh):  # noqa: F811
    from core import purchasing
    clerk = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}
    pr = purchasing.create_pr(wh(), [(mid("PKG-001"), 2, 100)], date.today().isoformat(), "보충", clerk)
    assert purchasing.cancel_pr(pr.id, clerk).ok
    assert not purchasing.cancel_pr(pr.id, clerk).ok                      # 이미 취소된 요청
    assert db.scalar("SELECT status FROM purchase_requests WHERE id = ?", (pr.id,)) == "CANCELLED"
    unread = int(db.scalar("SELECT COUNT(*) FROM notifications WHERE ref = ? AND read_at IS NULL", (f"pr:{pr.id}",)) or 0)
    assert unread == 0


def test_history_hides_withdrawn_and_temp_scope_rejects_unknown_ids(fresh):  # noqa: F811
    me = {"id": 44, "name": "요청자", "role": "CLERK", "ip": ""}
    rid = _adj_request(me)
    assert approvals.withdraw(rid, me).ok
    assert workflow.history(me).empty or "WITHDRAWN" not in set(workflow.history(me)["decision"])
    uid = _scoped_user()
    until = (date.today() + timedelta(days=2)).isoformat()
    admin = {"id": 999, "name": "관리자", "role": "ADMIN", "ip": ""}
    r = org.add_temp_scope(uid, [99999], [], until, "없는 플랜트", admin)
    assert not r.ok and "없는" in r.message                                # 500 이 아니라 안내
    assert not org.add_temp_scope(uid, [], [99999], until, "없는 창고", admin).ok
    assert not org.set_user_scope(uid, False, [], [99999], admin).ok


def test_api_requests_without_key_are_not_counted(fresh, monkeypatch):  # noqa: F811
    import config
    from core import api_keys
    monkeypatch.setattr(config, "API_FAIL_MAX", 3)
    for _ in range(6):
        assert api_keys.check("", "7.7.7.7", "stock:read")[1] == 401      # 키를 안 보낸 요청은 실패로 세지 않는다
    assert not api_keys.ip_blocked("7.7.7.7")
    for _ in range(3):
        api_keys.check("mmk_wrongwrongwrong", "7.7.7.7", "stock:read")     # 틀린 키만 센다
    assert api_keys.ip_blocked("7.7.7.7")

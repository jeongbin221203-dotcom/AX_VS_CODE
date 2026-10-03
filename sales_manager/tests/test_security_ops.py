"""보안·운영 보강 (자재관리와 비교해 추가): 세션 무효 · IP 차단 · 실제 접속 IP · 운영 점검 · 백업 검증 ·
점검 모드 · SSO 장애 모드 · 거래처 이름 맞추기 · 확인 창 · S3 임시 보관 · 메신저 ID · 감사로그 표준출력."""
from __future__ import annotations

import io
import json
import re

import pytest
from conftest import csrf, login, post, user

import config
from core import auth as core_auth
from core import company
from core import customer_names as cn
from core import database
from core import enterprise as ent
from core import sales_db as db


def _logged_in(client) -> bool:
    res = client.get("/customers")
    return res.status_code == 200


def test_password_change_and_logout_end_other_sessions(app):
    a, b = login(app, "김영업"), login(app, "김영업")              # 같은 사람이 PC 두 대에서
    assert _logged_in(a) and _logged_in(b)
    uid = user("김영업")["id"]
    core_auth.set_password(uid, "NewPass!2026x", must_change=False)   # 비밀번호가 바뀌면
    assert not _logged_in(a) and not _logged_in(b)               # 복사된 쿠키·다른 PC 모두 끊김
    a, b = login(app, "김영업"), login(app, "김영업")
    post(a, "/logout")                                           # 한 곳에서 로그아웃하면
    assert not _logged_in(b)                                     # 다른 세션도 끊김
    c = login(app, "김영업")
    admin = login(app, "시스템관리자")
    post(admin, f"/admin/users/{uid}/sessions/end")              # 관리자 '모든 세션 끊기'
    assert not _logged_in(c) and _logged_in(admin)
    assert database.scalar("SELECT COUNT(*) FROM audit_log WHERE action='세션종료'") >= 1


def test_ip_block_after_many_failures(app):
    ip = "203.0.113.77"
    c = app.test_client()
    tok = csrf(c)
    for _ in range(core_auth.IP_MAX_FAILURES):
        c.post("/login", data={"_csrf": tok, "user_id": "999999"}, environ_base={"REMOTE_ADDR": ip})
    good = user("김영업")["id"]
    res = c.post("/login", data={"_csrf": tok, "user_id": good}, environ_base={"REMOTE_ADDR": ip})
    assert res.status_code == 200 and "로그인 실패가 많아" in res.get_data(as_text=True)   # 맞는 계정이어도 막힘
    other = app.test_client()
    res = other.post("/login", data={"_csrf": csrf(other), "user_id": good}, environ_base={"REMOTE_ADDR": "198.51.100.1"})
    assert res.status_code == 302                                # 다른 IP 는 그대로
    with db.get_conn() as conn:
        conn.execute("DELETE FROM login_ip_failures")


def test_client_ip_header_is_recorded(app, monkeypatch):
    monkeypatch.setenv("SALES_CLIENT_IP_HEADER", "True-Client-IP")
    c = app.test_client()
    tok = csrf(c)
    c.post("/login", data={"_csrf": tok, "user_id": user("김영업")["id"]}, headers={"True-Client-IP": "121.130.1.2"})
    row = database.rows("SELECT detail FROM audit_log WHERE action='로그인' ORDER BY id DESC LIMIT 1")[0]
    assert "121.130.1.2" in row["detail"]


def test_doctor_page_and_checks(app):
    from core import doctor
    with app.test_request_context():
        checks = {c.name: c for c in doctor.run()}
    assert checks["연결"].status == "ok" and checks["구조(리비전)"].status == "ok" and checks["저장소"].status == "ok"
    assert checks["SQLite 안전 설정"].status == ("off" if database.is_pg() else "ok")   # WAL + synchronous=FULL
    html = login(app, "시스템관리자").get("/admin/doctor").get_data(as_text=True)
    assert "🩺 운영 점검" in html and "배치 워커" in html and "점검(읽기 전용) 모드" in html
    assert login(app, "김영업").get("/admin/doctor").status_code == 403
    if not database.is_pg():
        with database.get_conn() as conn:
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_backup_is_verified(app, tmp_path):
    db.set_context("system", None)
    path = db.backup_database(tmp_path / "bk", keep=3)
    if not database.is_pg():                                    # PostgreSQL 은 pg_restore --list 로 검증 (.dump)
        import sqlite3
        conn = sqlite3.connect(path)
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        conn.close()
    detail = json.loads(database.rows("SELECT detail FROM audit_log WHERE action='DB백업' ORDER BY id DESC LIMIT 1")[0]["detail"])
    assert detail["검증"] == "통과"


def test_maintenance_toggle_blocks_writes_but_admin_can_turn_off(app):
    admin, rep = login(app, "시스템관리자"), login(app, "김영업")
    post(admin, "/admin/maintenance", {"state": "on", "reason": "DB 교체"})
    company.refresh(force=True)
    try:
        assert "시스템 점검 중" in rep.get("/customers").get_data(as_text=True)
        assert post(rep, "/customers/save", {"name": "점검중거래처"}).status_code == 503
        res = post(admin, "/admin/maintenance", {"state": "off"})            # 점검 중에도 끌 수 있다
        assert res.status_code == 302
        company.refresh(force=True)
        assert "시스템 점검 중" not in rep.get("/customers").get_data(as_text=True)
    finally:
        company.set_state("maintenance", {"on": False}, "test")


def test_sso_outage_mode_allows_password_login_for_limited_time(app, monkeypatch):
    monkeypatch.setattr(core_auth, "AUTH_MODE", "sso")
    monkeypatch.delenv("SALES_BREAKGLASS_USERS", raising=False)
    uid = user("한팀장")["id"]
    core_auth.set_password(uid, "Outage!2026pw", must_change=False)
    assert not core_auth.breakglass_enabled()
    assert core_auth.authenticate_breakglass(user("한팀장")["emp_no"], "Outage!2026pw")[0] is None
    with pytest.raises(ValueError):
        core_auth.set_sso_outage(48, "test")                     # 최대 24시간
    until = core_auth.set_sso_outage(2, "test", "IdP 장애")
    try:
        assert until and core_auth.breakglass_enabled()
        assert core_auth.authenticate_breakglass(user("한팀장")["emp_no"], "Outage!2026pw")[0]["id"] == uid
    finally:
        core_auth.set_sso_outage(0, "test")
    assert core_auth.sso_outage_until() is None and not core_auth.breakglass_enabled()


def test_customer_name_matching_aliases_and_unknown(app):
    db.set_context("system", None)
    cid = db.upsert_customer({"name": "(주)한빛정밀", "owner": "김영업", "biz_no": ""}, confirm_similar=False)
    idx = cn.Index()
    assert idx.resolve("한빛정밀 주식회사") == (cid, "비슷한 이름")         # 법인 표시·띄어쓰기 무시
    assert idx.resolve("한빛 정밀 부산지점")[0] is None
    admin = login(app, "시스템관리자")
    csv = "거래처명,활동일,유형,활동내용,담당자\n한빛정밀 부산지점,2026-10-01,방문,첫 방문,김영업\n".encode("utf-8-sig")
    res = post(admin, "/data/import/check", {"entity": "영업활동", "file": (io.BytesIO(csv), "a.csv")},
               content_type="multipart/form-data")
    text = res.get_data(as_text=True)
    assert "등록되지 않은 거래처" in text and "비슷한 거래처" in text and "(주)한빛정밀" in text
    unknown = cn.list_unknown()
    row = next(u for u in unknown if u["name"] == "한빛정밀 부산지점")
    assert row["suggest"][0] == (cid, "(주)한빛정밀")
    assert "🏷️ 이름 정리" in admin.get("/customers?tab=names").get_data(as_text=True)
    post(admin, "/customers/names/link", {"unknown_id": row["id"], "customer_id": cid})
    assert cn.Index().resolve("한빛정밀 부산지점") == (cid, "다른 이름")     # 다음 업로드부터 맞춰짐
    assert not any(u["name"] == "한빛정밀 부산지점" for u in cn.list_unknown())
    db.upsert_customer({"id": cid, "name": "(주)한빛테크", "owner": "김영업"}, confirm_similar=False)
    assert cn.Index().resolve("한빛정밀") == (cid, "다른 이름")             # 예전 이름은 다른 이름으로
    other = db.upsert_customer({"name": "다른회사상사", "owner": "김영업"}, confirm_similar=False)
    with pytest.raises(ValueError, match="이미"):
        cn.add_alias(cid, "(주)다른회사상사", "t")                # 다른 거래처 이름은 다른 이름으로 못 씀
    with pytest.raises(ValueError):
        cn.add_alias(10**7, "없는거래처", "t")
    assert cn.Index().resolve("다른회사상사") == (other, "이름")
    assert login(app, "김영업").get("/customers?tab=names").status_code == 200
    assert "🏷️ 이름 정리" not in login(app, "김영업").get("/customers").get_data(as_text=True)


def test_dangerous_buttons_ask_first(app):
    html = login(app, "시스템관리자").get("/admin/data").get_data(as_text=True)
    forms = re.findall(r"<form\b[^>]*>", html)
    danger = [f for f in forms if "data-confirm" in f]
    assert danger, "위험한 버튼이 있는 폼에 확인 창"


def test_storage_spool_when_s3_is_down(tmp_path):
    from core.storage import LocalStorage, SpoolingStorage

    class Flaky(LocalStorage):
        name = "s3"
        down = True

        def put(self, key, data, content_type="application/octet-stream"):
            if self.down:
                raise ConnectionError("S3 응답 없음")
            return super().put(key, data, content_type)

    inner = Flaky(tmp_path / "s3")
    st = SpoolingStorage(inner, tmp_path / "spool")
    st.put("documents/a.png", b"img", "image/png")              # 장애: 임시 보관, 업무는 계속
    assert st.get("documents/a.png") == b"img" and st.spooled_count() == 1 and not inner.exists("documents/a.png")
    assert st.flush() == {"uploaded": 0, "left": 1}
    inner.down = False
    assert st.flush() == {"uploaded": 1, "left": 0}
    assert inner.get("documents/a.png") == b"img" and st.spooled_count() == 0


def test_messenger_id_saved_and_used(app, monkeypatch):
    admin = login(app, "시스템관리자")
    u = user("이수주")
    post(admin, "/admin/users/save", {"id": u["id"], "emp_no": u["emp_no"], "name": u["name"], "role": u["role"],
                                      "org_id": u["org_id"] or "", "email": "lee@corp.kr", "active": "1",
                                      "messenger_id": "lee.works"})
    assert user("이수주")["messenger_id"] == "lee.works"
    seen = []
    from core import messenger
    monkeypatch.setattr(messenger, "deliver", lambda rows, base: seen.extend(rows) or {"sent": 0})
    from core import notify
    notify.deliver(notify.notify([u["id"]], "요약", "테스트", "", "/", deliver=False))
    assert seen and seen[0]["messenger_id"] == "lee.works"


def test_audit_stdout_for_demo_logs(app, monkeypatch, capsys):
    monkeypatch.setenv("SALES_AUDIT_STDOUT", "1")
    db.audit("시험", "시스템", None, {"a": 1})
    out = capsys.readouterr().out
    assert "AUDIT {" in out and '"action": "시험"' in out


def test_review_fixes(app, monkeypatch):
    """코드 검토에서 찾은 것: 지운 거래처의 다른 이름 · 기본 웹훅 재시도 중복 · 링크 인자 500 · 사무실 IP 예외 · 없는 단계 · 수주 전 견적."""
    db.set_context("system", None)
    # 지운 거래처에 남은 다른 이름이 같은 이름 재사용을 막지 않는다
    cid = db.upsert_customer({"name": "삭제될상사", "owner": "김영업"}, confirm_similar=False)
    db.upsert_customer({"id": cid, "name": "삭제될상사2", "owner": "김영업"}, confirm_similar=False)
    db.delete_customer(cid)
    other = db.upsert_customer({"name": "남는상사", "owner": "김영업"}, confirm_similar=False)
    assert cn.add_alias(other, "삭제될상사", "t") is True
    # 기본 웹훅: 재시도해도 한 번만
    calls = []
    from core import notify
    monkeypatch.setenv("SALES_NOTIFY_WEBHOOK_URL", "https://hook.example/x")
    monkeypatch.setattr(notify, "post_webhook", lambda text: calls.append(text))
    nids = notify.notify([user("김영업")["id"]], "요약", "웹훅 한 번", "", "/", deliver=False)
    notify.deliver(nids)
    with db.get_conn() as conn:
        conn.execute("UPDATE notifications SET email_status='대기' WHERE id=?", (nids[0],))
    notify.deliver(nids)
    assert len(calls) == 1
    # 목록 링크에 이상한 인자가 와도 500 이 아니다
    admin = login(app, "시스템관리자")
    for bad in ("/deals?endpoint=x", "/deals?_scheme=x", "/customers?_external=1"):
        assert admin.get(bad).status_code == 200, bad
    # 사무실 IP 는 IP 차단에서 뺀다
    monkeypatch.setenv("SALES_IP_ALLOWLIST", "10.0.0.0/8")
    for _ in range(core_auth.IP_MAX_FAILURES + 1):
        core_auth.record_ip_failure("10.1.2.3")
    assert not core_auth.ip_blocked("10.1.2.3")
    # 없는 단계 이름은 데이터 점검에 잡힌다
    from core import quality
    deal = int(database.scalar("SELECT MIN(id) FROM deals"))
    old = db.get_deal(deal)["stage"]
    with db.get_conn() as conn:
        conn.execute("UPDATE deals SET stage='옛단계' WHERE id=?", (deal,))
    try:
        assert quality.unknown_stage()["count"] >= 1
    finally:
        with db.get_conn() as conn:
            conn.execute("UPDATE deals SET stage=? WHERE id=?", (old, deal))
    # 수주 전 견적 필터
    html = admin.get("/quotes?ready=1").get_data(as_text=True)
    assert "수주 전" in html

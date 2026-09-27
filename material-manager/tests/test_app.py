"""Flask 화면 구동 확인 (임시 DB 사용 — data/materials.db 는 건드리지 않는다).

실행: python -m pytest tests -q
"""
from __future__ import annotations

import base64
import io
import os
import re
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

TMP = Path(tempfile.mkdtemp(prefix="mm_app_test_"))
os.environ["MM_DB_PATH"] = str(TMP / "test.db")
os.environ["MM_PW_ITERATIONS"] = "1000"          # 테스트 속도용. 운영 기본값은 600,000
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from app import create_app  # noqa: E402
from core import audit, auth, db, seed  # noqa: E402

TODAY = date.today().isoformat()
PW = "Passw0rd!"
USERS = {"VIEWER": "viewer", "CLERK": "clerk", "MANAGER": "manager", "ADMIN": "admin"}
PAGES = ["/", "/materials/", "/materials/?tab=new", "/materials/?tab=edit",
         "/transactions/", "/transactions/?type=ADJ", "/stock/", "/history/", "/data/",
         "/documents/", "/documents/?tab=new", "/periods/", "/sap/", "/admin/users", "/admin/audit",
         "/password"]


@pytest.fixture()
def app():
    """테스트마다 빈 DB에서 시작한다 (test_core 와 같은 임시 DB를 공유하므로 매번 초기화)."""
    config.SAP_MODE = "off"
    config.MFA_REQUIRED_ROLES = set()          # 2단계 인증 필수는 test_enterprise의 전용 테스트에서 확인
    db.reset_database()
    application = create_app({"TESTING": True})
    for role, username in USERS.items():
        auth.create_user(username, f"{username}님", role, PW, audit.SYSTEM, must_change_pw=False)
    return application


@pytest.fixture()
def client(app):
    """시스템관리자로 로그인한 클라이언트."""
    return login(app.test_client(), "admin")


def csrf(client) -> str:
    for url in ("/password", "/login"):
        m = re.search(r'name="_csrf" value="([0-9a-f]+)"', client.get(url).get_data(as_text=True))
        if m:
            return m.group(1)
    raise AssertionError("CSRF 토큰 없음")


def post(client, url: str, data: dict | None = None, **kw):
    return client.post(url, data={**(data or {}), "_csrf": csrf(client)}, **kw)


def login(client, username: str, password: str = PW):
    res = post(client, "/login", {"username": username, "password": password})
    assert res.status_code == 302, res.get_data(as_text=True)[:500]
    return client


def mid_of(code: str) -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()["id"]


def stock_of(code: str) -> float:
    from core import repository as repo
    with db.get_conn() as conn:
        return repo.current_stock(conn, mid_of(code))


def wh1() -> int:
    return int(db.scalar("SELECT MIN(id) FROM warehouses"))


def actions() -> list[str]:
    return db.query_df("SELECT action FROM audit_log ORDER BY id")["action"].tolist()


# ── 로그인 · 권한 ────────────────────────────────────────────
def test_first_run_setup_then_login():
    config.MFA_REQUIRED_ROLES = set()
    db.reset_database()
    application = create_app({"TESTING": True})
    code = application.config["SETUP_CODE"]
    c = application.test_client()
    assert c.get("/").headers["Location"].endswith("/setup")
    html = c.get("/setup").get_data(as_text=True)
    token = re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)
    form = {"_csrf": token, "username": "boss", "name": "대표", "password": "Admin1234", "password2": "Admin1234"}
    res = c.post("/setup", data={**form, "setup_code": "wrong"})
    assert "설정 코드" in res.get_data(as_text=True) and auth.count_users() == 0, "코드 없이는 관리자를 만들 수 없다"
    res = c.post("/setup", data={**form, "setup_code": code})
    assert res.status_code == 302
    assert c.get("/admin/users").status_code == 200, "최초 등록자는 시스템관리자"
    assert c.get("/setup").status_code == 404, "사용자가 생긴 뒤에는 최초 설정을 열 수 없다"


def test_login_required_and_lockout(app):
    c = app.test_client()
    res = c.get("/stock/")
    assert res.status_code == 302 and "/login" in res.headers["Location"]
    for _ in range(config.LOGIN_MAX_FAILS):
        post(c, "/login", {"username": "clerk", "password": "wrong-pass1"})
    res = post(c, "/login", {"username": "clerk", "password": PW})
    assert "잠겼습니다" in res.get_data(as_text=True), "연속 실패 후 올바른 비밀번호도 거부"
    assert actions().count("LOGIN_FAIL") == config.LOGIN_MAX_FAILS + 1


def test_post_without_csrf_rejected(client):
    assert client.post("/data/seed").status_code == 400


def test_role_permissions(app):
    seed.seed()
    mid = mid_of("PKG-001")
    tx = {"tx_type": "IN", "material_id": mid, "qty": "1", "unit_price": "1", "tx_date": TODAY, "warehouse_id": wh1()}

    viewer = login(app.test_client(), "viewer")
    assert viewer.get("/stock/").status_code == 200
    assert viewer.get("/transactions/").status_code == 403
    assert post(viewer, "/transactions/", tx).status_code == 403
    assert viewer.get("/data/").status_code == 403
    assert "입출고 등록" not in viewer.get("/").get_data(as_text=True), "권한 없는 메뉴는 숨긴다"

    clerk = login(app.test_client(), "clerk")
    assert post(clerk, "/transactions/", tx).status_code == 302
    assert post(clerk, f"/materials/{mid}/active", {"active": "0"}).status_code == 403
    assert post(clerk, "/history/reverse", {"tx_id": 1, "reason": "x"}).status_code == 403

    manager = login(app.test_client(), "manager")
    assert manager.get("/periods/").status_code == 200
    assert manager.get("/admin/users").status_code == 403
    assert post(manager, "/periods/reopen", {"reason": "x", "confirm": "1"}).status_code == 403

    created_by = db.query_df("SELECT created_by FROM transactions ORDER BY id DESC LIMIT 1").iloc[0, 0]
    assert created_by == "clerk님", "담당자는 로그인 사용자로 기록된다"


def test_user_admin_and_forced_password_change(client, app):
    post(client, "/admin/users", {"username": "newbie", "name": "신입", "role": "CLERK", "password": "Temp1234"})
    c = login(app.test_client(), "newbie", "Temp1234")
    res = c.get("/stock/")
    assert res.headers["Location"].endswith("/password"), "임시 비밀번호는 먼저 바꿔야 한다"
    post(c, "/password", {"current": "Temp1234", "new": "Mine12345", "new2": "Mine12345"})
    assert c.get("/stock/").status_code == 200

    uid = db.query_df("SELECT id FROM users WHERE username = 'newbie'").iloc[0, 0]
    post(client, f"/admin/users/{uid}", {"name": "신입", "role": "CLERK", "active": "0"})
    assert c.get("/stock/").status_code == 302, "중지된 계정은 즉시 로그아웃"

    admin_id = db.query_df("SELECT id FROM users WHERE username = 'admin'").iloc[0, 0]
    res = post(client, f"/admin/users/{admin_id}", {"name": "admin님", "role": "CLERK", "active": "1"},
               follow_redirects=True)
    assert "다른 시스템관리자" in res.get_data(as_text=True), "본인 역할은 본인이 못 바꾼다(직무 분리)"
    assert "USER_CREATE" in actions() and "PASSWORD_CHANGE" in actions()


# ── 화면 ─────────────────────────────────────────────────────
def test_pages_render_empty_and_seeded(client):
    for url in PAGES:
        assert client.get(url).status_code == 200, url
    seed.seed()
    for url in PAGES + ["/stock/?q=팔레트&shortage=1", "/history/?start=2000-01-01&end=2100-01-01&type=IN",
                        "/admin/audit?action=LOGIN"]:
        assert client.get(url).status_code == 200, url
    assert "안전재고 미달" in client.get("/").get_data(as_text=True)


def test_material_create_edit_deactivate(client):
    data = {"code": "tst-1", "name": "테스트 자재", "unit": "EA", "safety_stock": "5", "unit_price": "1000",
            "sap_matnr": "tst1"}
    res = post(client, "/materials/new", data, follow_redirects=True)
    assert "[TST-1] 테스트 자재 등록 완료" in res.get_data(as_text=True)
    assert "이미 존재합니다" in post(client, "/materials/new", data).get_data(as_text=True)

    mid = mid_of("TST-1")
    post(client, f"/materials/{mid}/edit", {**data, "name": "바뀐 이름"})
    assert "바뀐 이름" in client.get(f"/materials/?tab=edit&id={mid}").get_data(as_text=True)
    post(client, f"/materials/{mid}/active", {"active": "0"})
    assert "TST-1" not in client.get("/materials/").get_data(as_text=True)
    assert "TST-1" in client.get("/materials/?inactive=1").get_data(as_text=True)

    update = db.query_df("SELECT detail FROM audit_log WHERE action = 'MATERIAL_UPDATE'").iloc[0, 0]
    assert "바뀐 이름" in update and "테스트 자재" in update, "변경 전·후 값이 감사로그에 남는다"


def test_transactions_and_over_outbound_blocked(client):
    seed.seed()
    mid = mid_of("PKG-001")                     # 샘플 재고 120 - 45 - 40 = 35
    base = {"material_id": mid, "tx_date": TODAY, "unit_price": "18000", "warehouse_id": wh1()}
    res = post(client, "/transactions/", {**base, "tx_type": "IN", "qty": "10"}, follow_redirects=True)
    assert "현재고 45.00" in res.get_data(as_text=True)
    assert "재고 부족" in post(client, "/transactions/", {**base, "tx_type": "OUT", "qty": "999"}).get_data(as_text=True)
    assert stock_of("PKG-001") == 45
    post(client, "/transactions/", {**base, "tx_type": "ADJ", "qty": "40"})
    assert stock_of("PKG-001") == 40
    res = post(client, "/transactions/", {**base, "tx_type": "IN", "qty": "1", "tx_date": "2999-01-01"})
    assert "미래 일자" in res.get_data(as_text=True)


def test_history_reverse(client):
    seed.seed()
    with db.get_conn() as conn:
        tx_id = conn.execute("SELECT MAX(id) AS i FROM transactions WHERE tx_type = 'OUT'").fetchone()["i"]
    res = post(client, "/history/reverse", {"tx_id": tx_id, "reason": "", "next": "/history/"},
               follow_redirects=True)
    assert "취소 사유" in res.get_data(as_text=True)

    res = post(client, "/history/reverse", {"tx_id": tx_id, "reason": "수량 오입력", "next": "https://evil.example"})
    assert res.headers["Location"].endswith("/history/")
    html = client.get("/history/").get_data(as_text=True)
    assert f"취소됨(#" in html and f"취소거래(#{tx_id})" in html
    with db.get_conn() as conn:
        assert conn.execute("SELECT 1 FROM transactions WHERE id = ?", (tx_id,)).fetchone(), "원거래는 남는다"


def test_period_close_blocks_old_dates(client):
    seed.seed()
    ym = db.query_df("SELECT MIN(tx_date) AS d FROM transactions").iloc[0, 0][:7]
    if ym >= TODAY[:7]:
        pytest.skip("샘플 거래가 모두 이번 달이라 마감 대상 월이 없음")
    before = stock_of("PKG-001")
    res = post(client, "/periods/close", {"ym": ym}, follow_redirects=True)
    assert "마감했습니다" in res.get_data(as_text=True)
    assert stock_of("PKG-001") == before, "스냅샷 방식으로 계산해도 재고는 같다"
    res = post(client, "/transactions/", {"tx_type": "IN", "material_id": mid_of("PKG-001"), "qty": "1", "warehouse_id": wh1(),
                                          "unit_price": "1", "tx_date": f"{ym}-01"})
    assert "마감되어" in res.get_data(as_text=True)
    res = post(client, "/periods/reopen", {"reason": "수정 필요", "confirm": "1"}, follow_redirects=True)
    assert "해제했습니다" in res.get_data(as_text=True)
    assert "PERIOD_CLOSE" in actions() and "PERIOD_REOPEN" in actions()


def test_sap_mock_flow(client):
    config.SAP_MODE = "mock"
    try:
        seed.seed()
        mid = mid_of("PKG-001")
        tx = {"tx_type": "IN", "material_id": mid, "qty": "5", "unit_price": "1", "tx_date": TODAY, "warehouse_id": wh1()}
        res = post(client, "/transactions/", tx)
        assert "SAP 매핑" in res.get_data(as_text=True), "매핑 없는 자재는 연동 중 등록 불가"

        with db.transaction() as conn:
            conn.execute("UPDATE materials SET sap_matnr = 'PKG001'")
            conn.execute("UPDATE plants SET sap_plant = '1000'")
            conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
        post(client, "/transactions/", {**tx, "po_no": "4500000001", "po_item": "10"})
        res = post(client, "/sap/run", follow_redirects=True)
        assert "완료 1" in res.get_data(as_text=True)
        row = db.query_df("SELECT o.status, o.sap_doc_no, t.movement_type FROM sap_outbox o "
                          "JOIN transactions t ON t.id = o.tx_id").iloc[0]
        assert row["status"] == "SENT" and row["sap_doc_no"].startswith("49") and row["movement_type"] == "101"
        assert "SAP" in client.get("/sap/").get_data(as_text=True)
    finally:
        config.SAP_MODE = "off"


def test_excel_downloads(client):
    seed.seed()
    for url in ("/stock/?export=xlsx", "/history/?export=xlsx", "/data/template.xlsx",
                "/data/backup.xlsx", "/admin/audit?export=xlsx"):
        res = client.get(url)
        assert res.status_code == 200 and res.mimetype.endswith("sheet"), url
    if db.is_pg():
        assert client.get("/data/backup.db").status_code == 404, "PostgreSQL은 pg_dump로 백업"
        assert actions().count("BACKUP") == 1
    else:
        assert client.get("/data/backup.db").status_code == 200
        assert actions().count("BACKUP") == 2


# ── 증빙 ─────────────────────────────────────────────────────
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
DOC = {"doc_type": "E_TAX_INVOICE", "doc_issue_date": TODAY,
       "approval_no": "202609274100000012345678", "supplier_biz_no": "1248100998",
       "supplier_name": "대한팔레트", "supply_amount": "1800000", "tax_amount": "180000"}


def tx_count() -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]


def test_inbound_with_tax_invoice_image(client):
    seed.seed()
    mid = mid_of("PKG-001")
    base = {"tx_type": "IN", "material_id": mid, "qty": "100", "unit_price": "18000", "tx_date": TODAY,
            "warehouse_id": wh1()}

    before = tx_count()
    res = post(client, "/transactions/", {**base, **DOC, "doc_file": (io.BytesIO(b"<html>"), "tax.png")},
               content_type="multipart/form-data")
    assert "거래는 등록되지 않았습니다" in res.get_data(as_text=True)
    assert tx_count() == before, "증빙이 잘못되면 거래도 등록하지 않는다"

    res = post(client, "/transactions/", {**base, **DOC, "doc_file": (io.BytesIO(PNG), "세금계산서.png")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "전자세금계산서 증빙 등록" in res.get_data(as_text=True)
    doc = db.query_df("SELECT id, tx_id, created_by FROM documents").iloc[0]
    assert int(doc["tx_id"]) == db.query_df("SELECT MAX(id) AS i FROM transactions").iloc[0]["i"]
    assert doc["created_by"] == "admin님"

    detail = client.get(f"/documents/{int(doc['id'])}").get_data(as_text=True)
    assert "124-81-00998" in detail and "₩ 1,980,000" in detail
    file = client.get(f"/documents/{int(doc['id'])}/file")
    assert file.data == PNG and file.mimetype == "image/png"
    assert file.headers["X-Content-Type-Options"] == "nosniff"
    zipped = client.get("/data/backup-attachments.zip")
    assert zipped.status_code == 200 and zipfile.ZipFile(io.BytesIO(zipped.data)).namelist()


def test_document_upload_link_and_delete(client, app):
    seed.seed()
    res = post(client, "/documents/", {**DOC, "doc_type": "STATEMENT", "approval_no": "",
                                        "supplier_biz_no": "", "doc_file": (io.BytesIO(PNG), "명세서.png")},
               content_type="multipart/form-data")
    assert res.status_code == 302, res.get_data(as_text=True)[:1500]
    doc_id = int(res.headers["Location"].rsplit("/", 1)[1])
    assert "미연결" in client.get("/documents/?unlinked=1").get_data(as_text=True)

    with db.get_conn() as conn:
        tx_id = conn.execute("SELECT MIN(id) AS i FROM transactions").fetchone()["i"]
    post(client, f"/documents/{doc_id}/link", {"tx_id": tx_id})
    assert f"#{tx_id} ·" in client.get(f"/documents/{doc_id}").get_data(as_text=True)

    res = post(client, f"/documents/{doc_id}/delete", {"confirm": "1"}, follow_redirects=True)
    assert "다른 관리자" in res.get_data(as_text=True), "본인이 올린 증빙은 본인이 못 지운다(직무 분리)"
    manager = login(app.test_client(), "manager")
    post(manager, f"/documents/{doc_id}/delete", {"confirm": "1"})
    assert client.get(f"/documents/{doc_id}").status_code == 404
    assert {"DOC_CREATE", "DOC_LINK", "DOC_DELETE"} <= set(actions())


def test_upload_preview_then_apply(client):
    df = pd.DataFrame({"자재코드": ["new-1", "", "new-2", "new-2"], "자재명": ["가", "빈코드", "나", "나2"],
                       "규격": ["1100x1100", None, None, None], "SAP자재번호": [100200, None, "ab-1", "ab-2"]})
    res = post(client, "/data/upload",
               {"file": (io.BytesIO(df.to_csv(index=False).encode("utf-8")), "up.csv")},
               content_type="multipart/form-data")
    html = res.get_data(as_text=True)
    assert "2건 반영" in html
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)

    post(client, "/data/upload/apply", {"token": token})
    materials = db.query_df("SELECT code, name, spec, sap_matnr FROM materials ORDER BY code")
    assert materials.values.tolist() == [["NEW-1", "가", "1100x1100", "100200"], ["NEW-2", "나2", "", "AB-2"]]
    assert post(client, "/data/upload/apply", {"token": token}).status_code == 400, "토큰은 한 번만 쓴다"
    assert "MATERIAL_IMPORT" in actions()


# ── 보안 ─────────────────────────────────────────────────────
def test_security_headers(client):
    res = client.get("/stock/")
    csp = res.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert res.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["Cache-Control"] == "no-store"
    assert "onchange=" not in client.get("/materials/?tab=edit").get_data(as_text=True), "인라인 스크립트 없음"
    assert "cdn." not in res.get_data(as_text=True), "외부 스크립트 없음"
    cookie = res.request.environ.get("HTTP_COOKIE", "")
    assert "mm_session=" in cookie
    assert config.DEBUG is False, "디버그는 기본 꺼짐"


def test_open_redirect_blocked(app):
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "/\t/evil.com"):
        c = app.test_client()
        res = post(c, "/login", {"username": "clerk", "password": PW, "next": bad})
        assert res.headers["Location"] in ("/", "http://localhost/"), (bad, res.headers["Location"])
    c = app.test_client()
    res = post(c, "/login", {"username": "clerk", "password": PW, "next": "/stock/?q=a"})
    assert res.headers["Location"].endswith("/stock/?q=a")


def test_excel_formula_injection_neutralized(client):
    import openpyxl
    post(client, "/materials/new", {"code": "X-1", "name": '=HYPERLINK("http://evil","click")', "unit": "EA"})
    ws = openpyxl.load_workbook(io.BytesIO(client.get("/stock/?export=xlsx").data)).active
    cells = [c for row in ws.iter_rows() for c in row]
    assert not any(c.data_type == "f" for c in cells), "사용자 입력이 수식으로 저장되면 안 된다"


def test_old_session_invalid_after_password_reset(app, client):
    victim = login(app.test_client(), "clerk")
    assert victim.get("/stock/").status_code == 200
    uid = db.query_df("SELECT id FROM users WHERE username = 'clerk'").iloc[0, 0]
    post(client, f"/admin/users/{uid}/password", {"password": "Reset1234"})
    assert victim.get("/stock/").status_code == 302, "비밀번호 초기화 전 세션(탈취 쿠키 포함)은 무효"


def test_idle_timeout(app):
    c = login(app.test_client(), "clerk")
    with c.session_transaction() as s:
        s["seen"] = s["seen"] - config.IDLE_MINUTES * 60 - 5
    res = c.get("/stock/")
    assert res.status_code == 302 and "/login" in res.headers["Location"]


def test_ip_throttle(app):
    c = app.test_client()
    for i in range(config.LOGIN_IP_MAX_FAILS):
        post(c, "/login", {"username": f"nobody{i}", "password": "x"})
    res = post(c, "/login", {"username": "viewer", "password": PW})
    assert "이 PC에서의 로그인" in res.get_data(as_text=True), "계정을 바꿔 가며 시도해도 IP 단위로 막는다"


def test_document_image_sandboxed(client):
    seed.seed()
    res = post(client, "/documents/", {**DOC, "doc_type": "STATEMENT", "approval_no": "", "supplier_biz_no": "",
                                        "doc_file": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
    doc_id = int(res.headers["Location"].rsplit("/", 1)[1])
    file = client.get(f"/documents/{doc_id}/file")
    assert "sandbox" in file.headers["Content-Security-Policy"] and "no-store" in file.headers["Cache-Control"]


def test_upload_zip_bomb_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", b"0" * (30 * 1024 * 1024))   # 30MB → 수십 KB로 압축
    res = post(client, "/data/upload", {"file": (io.BytesIO(buf.getvalue()), "bomb.xlsx")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "비정상적으로" in res.get_data(as_text=True)

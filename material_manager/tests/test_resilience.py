"""장애·실수에 대한 보호: 두 번 제출 · 엑셀 업로드 덮어쓰기 · 동시 수정 · 자동 백업 · S3 장애 임시 보관 ·
SSO 장애 모드 · 재고 대사 범위 · ERP 시각(UTC)·취소 재시도.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_res_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402
from core import backup, db, erp, once, org, reconcile, seed, services, sso, storage  # noqa: E402
from test_advanced import M1, fresh, mid, wh  # noqa: E402,F401
from test_app import PW, app, client, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()
NOW_ISO = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tokens(html: str) -> dict:
    """화면의 첫 POST 폼의 _csrf · _once."""
    return {"_csrf": re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1),
            "_once": re.search(r'name="_once" value="([0-9a-f]+)"', html).group(1)}


def tx_count() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM transactions"))


# ── 두 번 제출 ───────────────────────────────────────────────
def test_double_submit_registers_once(client):
    seed.seed()
    m, w = mid("PKG-001"), wh("WH1")
    page = client.get(f"/transactions/?type=IN&material={m}&wh={w}").get_data(as_text=True)
    form = {**tokens(page), "tx_type": "IN", "material_id": m, "warehouse_id": w, "qty": "7", "unit_price": "100",
            "tx_date": TODAY}
    before = tx_count()
    first = client.post("/transactions/", data=form)
    assert first.status_code == 302
    second = client.post("/transactions/", data=form)            # 같은 화면에서 다시 누름 · 네트워크 재전송
    assert second.status_code == 302 and second.headers["Location"] == first.headers["Location"]
    assert tx_count() == before + 1, "같은 제출은 한 번만 등록"
    assert "이미 처리" in client.get(second.headers["Location"]).get_data(as_text=True)


def test_rejected_submit_can_be_fixed_and_resent(client):
    seed.seed()
    m, w = mid("PKG-001"), wh("WH1")
    page = client.get(f"/transactions/?type=OUT&material={m}&wh={w}").get_data(as_text=True)
    form = {**tokens(page), "tx_type": "OUT", "material_id": m, "warehouse_id": w, "qty": "999999",
            "unit_price": "0", "tx_date": TODAY}
    before = tx_count()
    assert client.post("/transactions/", data=form).status_code == 200, "재고 부족 → 입력 화면을 다시 보여 줌"
    form["qty"] = "1"                                             # 같은 표로 고쳐서 다시 보내도 처리된다
    assert client.post("/transactions/", data=form).status_code == 302
    assert tx_count() == before + 1


def test_unfinished_token_is_not_reprocessed(client):
    """서버가 처리 중 죽어 '처리 중'으로 남은 표 → 다시 처리하지 않고 확인을 요청한다."""
    seed.seed()
    m, w = mid("PKG-001"), wh("WH1")
    page = client.get(f"/transactions/?type=IN&material={m}&wh={w}").get_data(as_text=True)
    form = {**tokens(page), "tx_type": "IN", "material_id": m, "warehouse_id": w, "qty": "3", "unit_price": "0",
            "tx_date": TODAY}
    assert once.claim(form["_once"], None)[0]
    before = tx_count()
    res = client.post("/transactions/", data=form, headers={"Referer": "http://localhost/transactions/"})
    assert res.status_code == 302 and tx_count() == before
    once.finish(form["_once"], done=False)
    assert once.cleanup() == 0


# ── 엑셀 업로드 ──────────────────────────────────────────────
def upload(client, df: pd.DataFrame):
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    res = post(client, "/data/upload", {"file": (io.BytesIO(buf.getvalue()), "m.xlsx")},
               content_type="multipart/form-data")
    token = re.search(r'name="token" value="([0-9a-f]+)"', res.get_data(as_text=True)).group(1)
    return post(client, "/data/upload/apply", {"token": token})


def test_upload_keeps_existing_values_for_blank_or_missing_columns(client):
    seed.seed()
    assert services.update_material(mid("PKG-001"), {
        "name": "수출용 목재 팔레트", "spec": "1100x1100 훈증", "unit": "EA", "category": "포장재", "safety_stock": 50,
        "unit_price": 18000, "location": "A-01", "supplier": "대한팔레트", "sap_matnr": "100200"}).ok
    # 자재명만 바꾸려고 코드·이름·보관위치(빈 칸)만 담은 파일
    upload(client, pd.DataFrame({"자재코드": ["PKG-001", "NEW-9"], "자재명": ["목재 팔레트(수출)", "새 자재"],
                                 "보관위치": [None, "B-1"], "단가": ["", "abc"]}))
    row = db.query_df("SELECT * FROM materials WHERE code = 'PKG-001'").iloc[0]
    assert row["name"] == "목재 팔레트(수출)"
    assert (row["unit_price"], row["safety_stock"], row["sap_matnr"], row["location"], row["unit"]) == \
        (18000, 50, "100200", "A-01", "EA"), "빈 칸·없는 열은 기존 값 유지"
    new = db.query_df("SELECT * FROM materials WHERE code = 'NEW-9'").iloc[0]
    assert (new["unit_price"], new["unit"], new["location"]) == (0, "EA", "B-1"), "새 자재는 기본값"


def test_upload_does_not_change_sap_owned_fields(client):
    seed.seed()
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_synced_at = '2026-01-01 00:00:00', sap_matnr = 'S4-1' WHERE code = 'PKG-002'")
    res = upload(client, pd.DataFrame({"자재코드": ["PKG-002"], "자재명": ["다른 이름"], "단가": [1], "보관위치": ["Z-9"]}))
    assert res.status_code == 302
    row = db.query_df("SELECT name, unit_price, location FROM materials WHERE code = 'PKG-002'").iloc[0]
    assert (row["name"], row["unit_price"], row["location"]) == ("스트레치 필름", 9500, "Z-9")


# ── 자재 동시 수정 ───────────────────────────────────────────
def test_material_edit_conflict_is_not_overwritten(client):
    seed.seed()
    m = mid("PKG-003")
    page = client.get(f"/materials/?tab=edit&id={m}").get_data(as_text=True)
    form = {**tokens(page.split('action="/materials/new"')[-1]),
            "updated_at": re.search(r'name="updated_at" value="([^"]+)"', page).group(1),
            "name": "내가 고친 이름", "unit": "EA", "category": "포장재", "safety_stock": "1", "unit_price": "1"}
    with db.transaction() as conn:                                  # 그 사이 다른 사람이 저장
        conn.execute("UPDATE materials SET name = '다른 사람이 고친 이름', updated_at = '2099-01-01 00:00:00' WHERE id = ?", (m,))
    res = client.post(f"/materials/{m}/edit", data=form)
    assert res.status_code == 200 and "먼저 바꿨습니다" in res.get_data(as_text=True)
    assert db.scalar("SELECT name FROM materials WHERE id = ?", (m,)) == "다른 사람이 고친 이름"


# ── 자동 백업 ────────────────────────────────────────────────
def test_backup_creates_checked_copy_and_rotates(fresh, monkeypatch, tmp_path):
    if db.is_pg():
        pytest.skip("PostgreSQL 백업은 test_postgres_backup_with_pg_dump")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "bk")
    monkeypatch.setattr(config, "BACKUP_KEEP", 2)
    made = [backup.run().split(" ")[0] for _ in range(3)]
    kept = backup.files()
    assert [f.name for f in kept] == made[1:], "최근 2개만 남긴다"
    with sqlite3.connect(kept[-1]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == int(db.scalar("SELECT COUNT(*) FROM materials"))
    assert not list((tmp_path / "bk").glob("*.part"))


# ── S3 장애 → 임시 보관 ──────────────────────────────────────
class DownS3:
    """연결이 안 되는 S3. up=True로 바꾸면 되살아난다."""

    def __init__(self):
        self.up, self.data = False, {}

    def _net(self):
        if not self.up:
            raise ConnectionError("EndpointConnectionError")

    def put(self, key, data):
        self._net(); self.data[key] = data

    def get(self, key):
        self._net(); return self.data.get(key)

    def exists(self, key):
        self._net(); return key in self.data

    def delete(self, key):
        self._net(); self.data.pop(key, None)

    def keys(self, prefix):
        self._net(); return sorted(k for k in self.data if k.startswith(prefix))

    def check(self):
        self._net()


def test_s3_outage_spools_locally_and_flushes(app, tmp_path):
    remote = DownS3()
    store = storage.SpoolingStorage(remote, tmp_path / "spool")
    store.put("attachments/a.png", b"PNG")
    assert store.get("attachments/a.png") == b"PNG" and store.exists("attachments/a.png")
    assert store.keys("attachments/") == ["attachments/a.png"]
    assert store.check() == "degraded" and store.pending() == 1
    with pytest.raises(RuntimeError):
        store.flush()
    remote.data["attachments/old.png"] = b"OLD"
    store.delete("attachments/old.png")                            # S3가 꺼져 있어도 지운 것으로 보인다
    assert "attachments/old.png" not in store.keys("attachments/")
    remote.up = True
    assert store.flush() == "올림 1 · 지움 1"
    assert remote.data == {"attachments/a.png": b"PNG"} and store.pending() == 0 and store.check() == "ok"


def test_health_stays_up_when_storage_degraded(app, monkeypatch, tmp_path):
    fake = storage.SpoolingStorage(DownS3(), tmp_path / "spool")
    monkeypatch.setattr(storage, "get", lambda: fake)
    res = app.test_client().get("/health")
    assert res.status_code == 200 and res.get_json()["storage"] == "degraded"


# ── SSO 장애 ─────────────────────────────────────────────────
def test_sso_outage_mode_allows_password_login(app, monkeypatch):
    monkeypatch.setattr(config, "SSO_ENABLED", True)
    monkeypatch.setattr(config, "SSO_ISSUER", "https://idp.test")
    monkeypatch.setattr(config, "SSO_CLIENT_ID", "mm")
    monkeypatch.setattr(config, "SSO_ONLY", True)

    def unreachable():
        raise OSError("IdP에 연결할 수 없음")
    monkeypatch.setattr(sso, "start", unreachable)
    c = app.test_client()
    res = post(c, "/sso/login", {"next": "/stock/"})
    assert res.status_code == 302 and "/login" in res.headers["Location"]
    assert "연결할 수 없습니다" in c.get(res.headers["Location"]).get_data(as_text=True)

    assert post(app.test_client(), "/login", {"username": "clerk", "password": PW}).status_code == 200, "SSO 전용"
    admin = login(app.test_client(), "admin")
    assert "장애 모드 켜기" in admin.get("/admin/users").get_data(as_text=True)
    assert post(admin, "/admin/sso-outage", {"hours": "4"}).status_code == 302 and sso.outage_active()
    assert "지금 끄기" in admin.get("/admin/users").get_data(as_text=True)
    assert "장애 모드입니다" in app.test_client().get("/login").get_data(as_text=True)
    assert post(app.test_client(), "/login", {"username": "clerk", "password": PW}).status_code == 302
    assert sso.set_outage(0, None) == "" and not sso.outage_active()
    actions = db.query_df("SELECT action FROM audit_log")["action"].tolist()
    assert actions.count("SSO_OUTAGE") == 2


# ── 재고 대사: 창고 범위 ─────────────────────────────────────
def test_reconcile_scoped_user_sees_only_own_storage_locations(fresh):
    assert org.create_plant("P2", "평택", "2000", None).ok
    pid = int(db.scalar("SELECT id FROM plants WHERE code = 'P2'"))
    assert org.create_warehouse(pid, "WH2", "평택 창고", "0002", None).ok
    with db.transaction() as conn:
        conn.execute("UPDATE plants SET sap_plant = '1000' WHERE code = 'P1'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001' WHERE code = 'WH1'")
    sap_stock = pd.DataFrame({"sap_matnr": ["A", "B", "C"], "plant": ["1000", "2000", "1000"],
                              "sloc": ["0001", "0002", "0009"], "sap_qty": [1.0, 2.0, 3.0]})
    mine = reconcile.restrict(sap_stock, {wh("WH1")})
    assert mine["sap_matnr"].tolist() == ["A"], "다른 플랜트·같은 플랜트의 다른 저장위치는 빼고"
    assert len(reconcile.restrict(sap_stock, None)) == 3

    config.SAP_MODE = "mock"
    with db.transaction() as conn:
        conn.execute("UPDATE plants SET sap_plant = ''")
    config.SAP_MODE = "http"
    df, msg = reconcile.fetch_sap_stock({wh("WH1")})
    assert df.empty and "플랜트가 지정되어 있지 않아" in msg, "빈 목록으로 회사 전체 재고를 받지 않는다"


# ── ERP ──────────────────────────────────────────────────────
def test_odata_since_is_sent_in_utc():
    local = "2026-10-02 10:00:00"
    expected = datetime.strptime(local, "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert erp._utc(local) == expected


def test_cancel_retry_reuses_existing_reversal(monkeypatch):
    calls = []
    conn = erp.SapODataConnector.__new__(erp.SapODataConnector)

    def fake_call(method, path, body=None, query=None):
        calls.append((method, path))
        if path.endswith("/A_MaterialDocumentItem"):
            assert "ReversedMaterialDocument eq '4900000001'" in query["$filter"]
            return {"d": {"results": [{"MaterialDocument": "4900000077", "MaterialDocumentYear": "2026"}]}}
        raise AssertionError("이미 취소된 문서를 다시 취소하면 안 된다")
    conn._call = fake_call
    payload = {"action": "CANCEL", "idempotencyKey": "MM-TX-9", "postingDate": TODAY,
               "originalDocument": "4900000001", "originalYear": "2026"}
    assert conn.send(payload, attempt=2) == erp.Posted("4900000077", "2026")
    assert calls == [("GET", erp.SapODataConnector.MD + "/A_MaterialDocumentItem")]


def test_int_env_falls_back(monkeypatch):
    monkeypatch.setenv("MM_X_TEST", "abc")
    assert config._int_env("MM_X_TEST", 15) == 15
    monkeypatch.setenv("MM_X_TEST", "7")
    assert config._int_env("MM_X_TEST", 15) == 7


# ── 오프라인 대기열 (/transactions/queue) ─────────────────────
def queue_post(client, data: dict, token: str):
    from test_app import csrf as csrf_of
    return client.post("/transactions/queue", data={**data, "_csrf": csrf_of(client), "_once": token},
                       headers={"X-MM-Queue": "1"})


def test_offline_queue_applies_once_and_rechecks_stock(client):
    seed.seed()
    m, w = mid("PKG-001"), wh("WH1")
    before = tx_count()
    entry = {"kind": "TX", "tx_type": "IN", "material_id": m, "warehouse_id": w, "qty": "4", "unit_price": "10",
             "tx_date": TODAY, "captured_at": NOW_ISO}
    tok = once.new_token()
    r = queue_post(client, entry, tok).get_json()
    assert r["ok"] and r["tx_id"]
    again = queue_post(client, entry, tok).get_json()             # 응답을 못 받아 다시 보냄
    assert again["ok"] and again["duplicate"] and tx_count() == before + 1, "같은 입력은 한 번만"
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'OFFLINE_SYNC'") == 1

    out_tok = once.new_token()
    out = {**entry, "tx_type": "OUT", "qty": "999999"}
    r = queue_post(client, out, out_tok).get_json()
    assert not r["ok"] and "재고 부족" in r["message"], "끊긴 동안 받은 출고도 서버가 재고를 다시 확인"
    out["qty"] = "1"                                              # 거부되면 표가 풀려 고쳐 보낼 수 있다
    assert queue_post(client, out, out_tok).get_json()["ok"]

    trf = {"kind": "TRF", "material_id": m, "warehouse_id": w, "to_warehouse_id": w, "qty": "1", "tx_date": TODAY}
    assert "같습니다" in queue_post(client, trf, once.new_token()).get_json()["message"]


def test_offline_queue_requires_login(app):
    res = app.test_client().post("/transactions/queue", data={"_once": once.new_token()}, headers={"X-MM-Queue": "1"})
    assert res.status_code == 302 and "/login" in res.headers["Location"], "세션이 끝났으면 다시 로그인한 뒤 보낸다"


def test_service_worker_is_served(app):
    res = app.test_client().get("/sw.js")
    assert res.status_code == 200 and "javascript" in res.content_type and "mm-offline" in res.get_data(as_text=True)


# ── 동시 수정 (플랜트 · 창고 · 사용자 · 범위 · 발주) ─────────
def test_org_and_user_edits_reject_stale_screens(client):
    from core import auth, purchasing, version
    page = client.get("/admin/org").get_data(as_text=True)
    ver = re.search(r'name="_ver" value="([0-9a-f]+)"', page).group(1)
    pid = int(db.scalar("SELECT id FROM plants ORDER BY id LIMIT 1"))
    with db.transaction() as conn:                                # 다른 관리자가 먼저 이름을 바꿈
        conn.execute("UPDATE plants SET name = '먼저 바꾼 이름' WHERE id = ?", (pid,))
    res = post(client, f"/admin/org/plant/{pid}", {"name": "나중 이름", "sap_plant": "", "active": "1", "_ver": ver},
               follow_redirects=True)
    assert "먼저 바꿨습니다" in res.get_data(as_text=True)
    assert db.scalar("SELECT name FROM plants WHERE id = ?", (pid,)) == "먼저 바꾼 이름"

    w = wh("WH1")
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM warehouses WHERE id = ?", (w,)).fetchone()
    good = version.of_row("warehouses", row)
    assert org.update_warehouse(w, "새 이름", "", True, None, expected=good).ok
    assert not org.update_warehouse(w, "또 이름", "", True, None, expected=good).ok, "같은 판으로 두 번 저장 불가"

    uid = int(db.scalar("SELECT id FROM users WHERE username = 'clerk'"))
    users_page = client.get("/admin/users").get_data(as_text=True)
    assert 'name="_ver"' in users_page
    with db.get_conn() as conn:
        u = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        scope_ver = version.scope_now(conn, uid)
    user_ver = version.of_row("users", u)
    assert auth.update_user(uid, "담당자2", "CLERK", True, None, expected=user_ver).ok
    assert not auth.update_user(uid, "담당자3", "VIEWER", True, None, expected=user_ver).ok
    assert org.set_user_scope(uid, False, [], [w], None, expected=scope_ver).ok
    assert not org.set_user_scope(uid, True, [], [], None, expected=scope_ver).ok

    with db.transaction() as conn:
        po_id = conn.execute("INSERT INTO purchase_orders (po_no, supplier, warehouse_id, status, created_by, created_at) "
                             "VALUES ('PO-T1', '공급처', ?, 'OPEN', 'x', ?)", (w, TODAY)).lastrowid
        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
    po_ver = version.of_row("purchase_orders", po)
    assert purchasing.set_sap_po_no(po_id, "4500000001", M1, None, expected=po_ver).ok
    assert not purchasing.set_sap_po_no(po_id, "4500000002", M1, None, expected=po_ver).ok


# ── 운영 점검 · 백업 위치 ────────────────────────────────────
def test_doctor_reports_each_dependency(client, monkeypatch, tmp_path):
    from core import doctor
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "bk")
    checks = {c.name: c for c in doctor.run()}
    assert checks["DB"].status == "ok" and checks["파일 저장소"].status == "ok"
    assert checks["ERP·SAP"].status == "off" and checks["사내 로그인(SSO)"].status == "off"
    assert checks["백업"].status == "warn" and "아직 백업 파일이 없습니다" in checks["백업"].message
    monkeypatch.setattr(config, "SAP_MODE", "rest")
    monkeypatch.setattr(config, "ERP_REST_URL", "http://erp.example.com")
    assert {c.name: c for c in doctor.run()}["ERP·SAP"].status == "fail", "평문 ERP 주소는 실패로 알린다"
    monkeypatch.setattr(config, "SAP_MODE", "off")
    res = post(client, "/admin/doctor")
    assert res.status_code == 200 and "운영 점검" in res.get_data(as_text=True)
    runner = client.application.test_cli_runner()
    out = runner.invoke(args=["doctor"])
    assert out.exit_code == 0 and "파일 저장소" in out.output


def test_backup_warns_when_on_same_disk(fresh, monkeypatch):
    if db.is_pg():
        pytest.skip("SQLite 파일 위치 확인")
    monkeypatch.setattr(config, "BACKUP_DIR", config.DB_PATH.parent / "bk-same")
    assert "같은 디스크" in backup.same_disk_warning()


def test_postgres_backup_with_pg_dump(fresh, monkeypatch, tmp_path):
    if not db.is_pg() or not config.PG_DUMP:
        pytest.skip("PostgreSQL + pg_dump(MM_PG_DUMP)에서만")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "pg")
    assert backup.enabled()
    msg = backup.run()
    assert "검사 통과" in msg and backup.files() and backup.files()[0].suffix == ".dump"


# ── 대시보드 (영업관리 대시보드와 맞춘 항목) ─────────────────
def test_dashboard_sections_and_check_tabs(client):
    from core import insights
    seed.seed()
    m = mid("PKG-003")
    with db.transaction() as conn:                                  # 오래 출고가 없는 자재 하나
        conn.execute("UPDATE materials SET safety_stock = 0 WHERE id = ?", (m,))
    html = client.get("/").get_data(as_text=True)
    for text in ("기준월", "월별 입고·출고 금액", "창고별 재고금액", "출고 금액 상위 자재", "즉시 확인이 필요한 항목",
                 "소진 예상", "미사용 재고", "수치 보기"):
        assert text in html, text
    for tab in ("shortage", "expiry", "cover", "dead", "approvals", "incoming"):
        assert client.get(f"/?tab={tab}").status_code == 200, tab
    prev = insights.prev_ym(TODAY[:7])
    assert "입고금액 (오늘까지)" in html and "실사조정 금액" in html and "최근 30일 입고·출고·조정 금액" in html
    prev_html = client.get(f"/?ym={prev}").get_data(as_text=True)
    assert "(오늘까지)" not in prev_html and "실사조정 금액" in prev_html
    amounts = insights.monthly_amounts(12)
    assert len(amounts) == 12 and amounts["입고금액"].sum() > 0
    stock = __import__("core.repository", fromlist=["x"]).stock_df()
    cover = insights.stock_cover(stock, horizon=100000)
    assert not cover.empty and (cover["남은 일수"] >= 0).all()
    assert not insights.top_issues("").empty
    assert insights.warehouse_values()["재고금액"].sum() > 0


def test_dead_stock_and_incoming_po(fresh):
    from datetime import timedelta
    from core import insights, repository as repo
    old = (date.today() - timedelta(days=120)).isoformat()
    assert services.create_material({"code": "OLD-1", "name": "오래된 자재", "unit_price": 1000}).ok
    assert services.register_transaction(mid("OLD-1"), "IN", 5, old, 1000, actor=M1).ok
    dead = insights.dead_stock(repo.stock_df())
    assert "OLD-1" in dead["자재코드"].tolist() and dead.iloc[0]["재고금액"] >= 5000

    w = wh("WH1")
    past = (date.today() - timedelta(days=2)).isoformat()
    with db.transaction() as conn:
        pr = conn.execute("INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, status, requested_by, "
                          "requested_at, updated_at) VALUES ('PR-T', ?, ?, 'ORDERED', 'x', ?, ?)",
                          (w, past, TODAY, TODAY)).lastrowid
        po = conn.execute("INSERT INTO purchase_orders (po_no, pr_id, supplier, warehouse_id, status, created_by, created_at) "
                          "VALUES ('PO-T', ?, '공급처', ?, 'OPEN', 'x', ?)", (pr, w, TODAY)).lastrowid
        conn.execute("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, 10, ?, 8, 100)",
                     (po, mid("OLD-1")))
    inc = insights.incoming_po()
    assert inc.iloc[0]["상태"] == "지연" and inc.iloc[0]["잔량"] == 8



def test_month_to_date_compares_same_days_and_counts_adjustments(fresh):
    from datetime import timedelta
    from core import insights
    today = date.today()
    prev_month_late = (today.replace(day=1) - timedelta(days=1))           # 전월 말일
    m = mid("PKG-001")
    with db.transaction() as conn:                                         # 전월 말일 입고 (같은 기간 밖)
        conn.execute("INSERT INTO transactions (material_id, tx_type, qty, unit_price, tx_date, created_at, warehouse_id) "
                     "VALUES (?, 'IN', 10, 100, ?, ?, ?)", (m, prev_month_late.isoformat(), TODAY, wh("WH1")))
    cur, before, same = insights.month_to_date(TODAY[:7])
    assert same
    if today.day < prev_month_late.day:
        assert before["입고금액"] == insights.period_amounts(prev_month_late.replace(day=1).isoformat(),
                                                           prev_month_late.replace(day=today.day).isoformat())["입고금액"]
    adj = insights.period_amounts("2000-01-01", TODAY)
    assert adj["ADJ"] == 1 and adj["조정금액"] == -3 * 1500, "샘플 실사조정(안전장갑 -3) 반영"
    daily = insights.daily_amounts(30)
    assert len(daily) == 30 and daily["조정금액"].sum() == -4500


def test_history_seed_fills_every_dashboard_tab(client):
    from core import insights, repository as repo
    seed.seed(history=True)
    stock = repo.stock_df()
    assert len(insights.monthly_amounts(12).query("입고금액 > 0")) >= 11, "12개월 추이"
    assert not insights.dead_stock(stock).empty and not insights.stock_cover(stock).empty
    assert insights.incoming_po().iloc[0]["상태"] == "지연"
    lots = repo.stock_by_lot()
    assert (lots["days_left"] < 0).any() and ((lots["days_left"] >= 0) & (lots["days_left"] <= 30)).any()
    assert len(insights.warehouse_values()) == 2
    assert int(db.scalar("SELECT COUNT(*) FROM approval_requests WHERE status = 'PENDING'")) == 1
    neg = db.query_df("SELECT material_id, warehouse_id, lot_no, SUM(CASE WHEN tx_type='OUT' THEN -qty ELSE qty END) q "
                      "FROM transactions GROUP BY 1,2,3 HAVING SUM(CASE WHEN tx_type='OUT' THEN -qty ELSE qty END) < 0")
    assert neg.empty, "음수 재고 없음"
    html = client.get("/").get_data(as_text=True)
    assert "총 재고금액 (기준단가)" in html and "재고평가(이동평균)" in html
    for tab in ("expiry", "cover", "dead", "approvals", "incoming"):
        assert "해당 항목이 없습니다" not in client.get(f"/?tab={tab}").get_data(as_text=True), tab

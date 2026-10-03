"""대기업 운영 기능: 직무 분리·결재 · 창고/이동/범위 · 수불부 · 재고 대사 · 페이지 · 저장소(S3) · 배치 잠금.

SQLite(기본)와 PostgreSQL(MM_DATABASE_URL=...mm_test) 모두에서 돌린다.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_ent_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402
from core import (approvals, db, documents, jobs, org, periods, reconcile, repository as repo, sap,  # noqa: E402
                  seed, services, storage)
from test_app import DOC, PNG, PW, app, client, login, post  # noqa: E402,F401 (fixture 재사용)

TODAY = date.today().isoformat()
A = {"id": 101, "name": "담당자A", "role": "CLERK", "ip": "10.0.0.1"}
B = {"id": 102, "name": "관리자B", "role": "MANAGER", "ip": "10.0.0.2"}


@pytest.fixture()
def fresh():
    config.SAP_MODE = "off"
    db.reset_database()
    seed.seed()
    yield
    config.SAP_MODE = "off"


def mid(code: str) -> int:
    return int(db.scalar("SELECT id FROM materials WHERE code = ?", (code,)))


def wh(code: str) -> int:
    return int(db.scalar("SELECT id FROM warehouses WHERE code = ?", (code,)))


def stock(code: str, wh_code: str | None = None) -> float:
    with db.get_conn() as conn:
        return repo.current_stock(conn, mid(code), None if wh_code is None else wh(wh_code))


def second_plant() -> int:
    assert org.create_plant("P2", "평택 공장", "2000", None).ok
    pid = int(db.scalar("SELECT id FROM plants WHERE code = 'P2'"))
    assert org.create_warehouse(pid, "WH2", "평택 창고", "0002", None).ok
    return wh("WH2")


# ── 직무 분리 · 결재 ─────────────────────────────────────────
def test_self_reversal_blocked(fresh):
    tx = services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 1, actor=A)
    r = services.reverse_transaction(tx.tx_id, "오입력", actor=A)
    assert not r.ok and "다른 관리자" in r.message
    assert services.reverse_transaction(tx.tx_id, "오입력", actor=B).ok


def test_large_adjustment_needs_approval(fresh):
    before = stock("PKG-001")                                   # 35, 단가 18,000
    r = services.register_transaction(mid("PKG-001"), "ADJ", 5, TODAY, 18000, actor=A)   # 차이 -30 → 540,000원
    assert r.ok and r.pending, r.message
    assert stock("PKG-001") == before, "결재 전에는 재고가 바뀌지 않는다"
    req_id = int(db.scalar("SELECT MAX(id) FROM approval_requests"))

    assert not approvals.decide(req_id, True, "", A).ok, "본인 요청은 본인이 승인 불가"
    assert not approvals.decide(req_id, False, "", B).ok, "반려는 사유 필수"
    ok = approvals.decide(req_id, True, "실사표 확인", B)
    assert ok.ok, ok.message
    assert stock("PKG-001") == 5
    row = db.query_df("SELECT created_by, approved_by, qty FROM transactions WHERE id = ?", (ok.tx_id,)).iloc[0]
    assert (row["created_by"], row["approved_by"], row["qty"]) == ("담당자A", "관리자B", -30)
    assert not approvals.decide(req_id, True, "", B).ok, "이미 처리된 결재"

    small = services.register_transaction(mid("LBL-001"), "ADJ", 1099, TODAY, 80, actor=A)  # 80원
    assert small.ok and not small.pending, "기준 미만은 바로 반영"


# ── 창고 · 이동 · 수불부 ─────────────────────────────────────
def test_transfer_reverse_and_ledger(fresh):
    wh2 = second_plant()
    r = services.transfer(mid("PKG-001"), wh("WH1"), wh2, 10, TODAY, actor=A)
    assert r.ok and "301" in r.message, r.message                 # 다른 플랜트 → 301
    assert (stock("PKG-001", "WH1"), stock("PKG-001", "WH2"), stock("PKG-001")) == (25, 10, 35)
    assert not services.transfer(mid("PKG-001"), wh("WH1"), wh2, 999, TODAY, actor=A).ok, "재고 부족"

    ym = TODAY[:7]
    led = repo.ledger_df(f"{ym}-01", TODAY)
    row = led[led["code"] == "PKG-001"].iloc[0]
    assert row["trf_in"] == 10 and row["trf_out"] == 10
    assert row["closing"] == stock("PKG-001"), "수불부 기말 = 현재고"
    only_wh2 = repo.ledger_df(f"{ym}-01", TODAY, {wh2})
    assert only_wh2[only_wh2["code"] == "PKG-001"].iloc[0]["closing"] == 10

    in_leg = int(db.scalar("SELECT id FROM transactions WHERE transfer_no <> '' AND tx_type = 'IN'"))
    rev = services.reverse_transaction(in_leg, "잘못 보냄", actor=B)
    assert rev.ok, rev.message
    assert (stock("PKG-001", "WH1"), stock("PKG-001", "WH2")) == (35, 0), "이동은 두 행이 함께 취소"


def test_transfer_sap_payload(fresh):
    config.SAP_MODE = "mock"
    wh2 = second_plant()
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = 'PKG001'")
        conn.execute("UPDATE plants SET sap_plant = '1000' WHERE code = 'P1'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001' WHERE code = 'WH1'")
    r = services.transfer(mid("PKG-001"), wh("WH1"), wh2, 4, TODAY, actor=A)
    assert r.ok, r.message
    assert int(db.scalar("SELECT COUNT(*) FROM sap_outbox")) == 1, "이동은 출고 쪽 한 건만 전송"
    assert sap.process_outbox()["sent"] == 1
    payload = db.scalar("SELECT payload FROM sap_outbox")
    assert '"receivingPlant": "2000"' in payload and '"movementType": "301"' in payload


def test_scope_limits_data(fresh):
    wh2 = second_plant()
    services.transfer(mid("PKG-001"), wh("WH1"), wh2, 10, TODAY, actor=A)
    user = {"id": 200, "role": "CLERK", "all_warehouses": 0}
    with db.transaction() as conn:
        conn.execute("INSERT INTO users (id, username, name, role, password_hash, all_warehouses, created_at, updated_at) "
                     "VALUES (200, 'scoped', '범위', 'CLERK', 'x', 0, '', '')")
        conn.execute("INSERT INTO user_scopes (user_id, plant_id) VALUES (200, ?)",
                     (int(db.scalar("SELECT id FROM plants WHERE code = 'P2'")),))
    allowed = org.allowed_warehouses(user)
    assert allowed == {wh2}
    assert org.allowed_warehouses({**user, "role": "ADMIN"}) is None, "시스템관리자는 전체"
    r = services.register_transaction(mid("PKG-001"), "OUT", 1, TODAY, 1, warehouse_id=wh("WH1"), wh_ids=allowed)
    assert not r.ok and "권한" in r.message
    df, total, _ = repo.history_page("2000-01-01", TODAY, ["IN", "OUT", "ADJ"], wh_ids=allowed)
    assert total == 1 and set(df["wh_code"]) == {"WH2"}
    assert repo.stock_df(wh_ids=allowed).set_index("code").loc["PKG-001", "stock"] == 10


# ── 수불부 · 페이지 · 대사 ───────────────────────────────────
def test_ledger_matches_after_close(fresh):
    first = db.scalar("SELECT MIN(tx_date) FROM transactions")[:7]
    if first >= TODAY[:7]:
        pytest.skip("마감할 달이 없음")
    before = repo.ledger_df(f"{TODAY[:7]}-01", TODAY)
    assert periods.close_month(first, None).ok
    after = repo.ledger_df(f"{TODAY[:7]}-01", TODAY)
    pd.testing.assert_frame_equal(before.reset_index(drop=True), after.reset_index(drop=True),
                                  check_dtype=False)


def test_history_pagination(fresh):
    with db.transaction() as conn:
        for i in range(230):
            repo.insert_transaction(conn, {"material_id": mid("LBL-001"), "tx_type": "IN", "qty": 1,
                                           "unit_price": 80, "tx_date": TODAY, "ref_no": f"P{i}", "partner": "",
                                           "note": "", "created_by": "t"})
    df, total, sums = repo.history_page("2000-01-01", TODAY, ["IN", "OUT", "ADJ"], page=3, size=100)
    assert total == 230 + 17 and len(df) == 47
    assert sums["in_qty"] == 230 + 2990, "합계는 페이지와 상관없이 조건 전체"


def test_reconcile_separates_timing_and_real_diff(fresh):
    config.SAP_MODE = "mock"
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = REPLACE(code, '-', '')")
        conn.execute("UPDATE plants SET sap_plant = '1000'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
    services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 1, actor=A)
    sap.process_outbox()
    services.register_transaction(mid("PKG-001"), "IN", 3, TODAY, 1, actor=A)      # 아직 전송 전
    sap_stock, err = reconcile.fetch_sap_stock()
    assert not err
    result = reconcile.compare(sap_stock).set_index("code")
    assert result.loc["PKG-001", "unsent"] == 3 and result.loc["PKG-001", "verdict"].startswith("일치")

    uploaded = sap_stock.copy()
    uploaded.loc[uploaded["sap_matnr"] == "PKG002", "sap_qty"] -= 2                 # SAP에서 누가 2개를 뺐다
    result = reconcile.compare(uploaded).set_index("code")
    assert result.loc["PKG-002", "verdict"] == "차이 — 조사 필요" and result.loc["PKG-002", "diff"] == -2
    df, problem = reconcile.normalize_sap_stock(pd.DataFrame({"자재": ["a"], "플랜트": [1000], "저장 위치": ["1"],
                                                              "가용재고": [3]}))
    assert not problem and df.iloc[0]["plant"] == "1000", "SAP 한글 헤더 별칭 인식"


# ── 저장소 · 배치 ────────────────────────────────────────────
def test_documents_on_s3(fresh, monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with moto.mock_aws():
        boto3.client("s3", region_name="ap-northeast-2").create_bucket(
            Bucket="mm-docs", CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        monkeypatch.setattr(config, "STORAGE", "s3")
        monkeypatch.setattr(config, "S3_BUCKET", "mm-docs")
        meta = {"doc_type": "STATEMENT", "issue_date": TODAY}
        saved = documents.save(documents.prepare(PNG, "a.png", meta), actor=A)
        assert saved.ok, saved.message
        doc = repo.get_document(saved.doc_id)
        assert storage.get().name == "s3" and documents.read(doc, A) == PNG
        assert storage.get().keys("attachments/") == [documents.key(doc)]
        storage.get().check()
        assert documents.delete(saved.doc_id, B).ok and not documents.exists(doc)
    monkeypatch.setattr(config, "STORAGE", "local")


def test_job_lease_one_server_at_a_time(fresh):
    ran, _ = jobs.run("cleanup_uploads", holder="server-A")
    assert ran
    assert jobs.run("cleanup_uploads", holder="server-B") == (False, "다른 서버가 실행 중이거나 아직 실행 주기가 아닙니다.")
    future = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    with db.transaction() as conn:                                  # A가 작업 중인 상태
        conn.execute("UPDATE job_locks SET holder = 'server-A', lease_until = ? WHERE name = 'cleanup_uploads'",
                     (future,))
    assert not jobs.run("cleanup_uploads", force=True, holder="server-B")[0], "임대 중에는 강제 실행도 불가"
    with db.transaction() as conn:                                  # A가 죽고 임대가 끝남
        conn.execute("UPDATE job_locks SET lease_until = '2000-01-01 00:00:00' WHERE name = 'cleanup_uploads'")
    assert jobs.run("cleanup_uploads", force=True, holder="server-B")[0], "임대가 끝나면 다른 서버가 이어받음"
    runs = jobs.runs_df()
    assert list(runs["holder"][:2]) == ["server-B", "server-A"] and set(runs["status"]) == {"OK"}


# ── 화면 ─────────────────────────────────────────────────────
def test_new_pages_render(client):
    seed.seed()
    for url in ("/reports/ledger", f"/reports/ledger?ym={TODAY[:7]}", "/reports/reconcile", "/approvals/",
                "/approvals/?status=ALL", "/admin/org", "/admin/jobs", "/stock/?view=wh", "/transactions/?type=TRF",
                "/history/?page=2", "/admin/audit?page=2"):
        assert client.get(url).status_code == 200, url


def test_health_endpoint(app):
    res = app.test_client().get("/health")
    assert res.status_code == 200 and res.json == {"status": "ok", "db": "ok", "storage": "ok"}


def test_view_and_export_are_logged(client):
    seed.seed()
    res = post(client, "/documents/", {**DOC, "doc_type": "STATEMENT", "approval_no": "", "supplier_biz_no": "",
                                        "doc_file": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
    doc_id = int(res.headers["Location"].rsplit("/", 1)[1])
    client.get(f"/documents/{doc_id}/file")
    client.get(f"/documents/{doc_id}/file?download=1")
    client.get("/stock/?export=xlsx")
    client.get(f"/reports/ledger?ym={TODAY[:7]}&export=xlsx")
    acts = db.query_df("SELECT action FROM audit_log")["action"].tolist()
    assert {"DOC_VIEW", "DOC_DOWNLOAD"} <= set(acts) and acts.count("EXPORT") == 2


def test_scope_enforced_in_ui(app, client):
    seed.seed()
    wh2 = second_plant()
    services.transfer(mid("PKG-001"), wh("WH1"), wh2, 10, TODAY, actor=A)
    wh1_tx = int(db.scalar("SELECT MIN(id) FROM transactions"))
    res = post(client, "/documents/", {**DOC, "doc_type": "STATEMENT", "approval_no": "", "supplier_biz_no": "",
                                        "tx_id": wh1_tx, "doc_file": (io.BytesIO(PNG), "a.png")},
               content_type="multipart/form-data")
    doc_id = int(res.headers["Location"].rsplit("/", 1)[1])
    uid = int(db.scalar("SELECT id FROM users WHERE username = 'viewer'"))
    post(client, f"/admin/users/{uid}/scope", {"warehouse": wh2})
    viewer = login(app.test_client(), "viewer")
    html = viewer.get("/history/?start=2000-01-01&end=2100-01-01&type=IN&type=OUT&type=ADJ").get_data(as_text=True)
    assert "WH2" in html and "SAMPLE-PKG-003" not in html, "다른 창고 거래는 안 보인다"
    assert viewer.get(f"/documents/{doc_id}").status_code == 404, "다른 창고 증빙은 없는 것처럼"
    assert viewer.get(f"/documents/{doc_id}/file").status_code == 404


def test_approval_and_transfer_ui(app, client):
    seed.seed()
    clerk = login(app.test_client(), "clerk")
    base = {"material_id": mid("PKG-001"), "warehouse_id": wh("WH1"), "tx_date": TODAY}
    res = post(clerk, "/transactions/", {**base, "tx_type": "ADJ", "qty": "5", "unit_price": "18000"},
               follow_redirects=True)
    assert "결재 요청" in res.get_data(as_text=True)
    req_id = int(db.scalar("SELECT MAX(id) FROM approval_requests"))
    manager = login(app.test_client(), "manager")
    assert "결재 대기 1건" in manager.get("/").get_data(as_text=True)
    res = post(manager, f"/approvals/{req_id}", {"decision": "approve", "comment": "확인"}, follow_redirects=True)
    assert "승인" in res.get_data(as_text=True) and stock("PKG-001") == 5

    wh2 = second_plant()
    res = post(clerk, "/transactions/transfer", {**base, "to_warehouse_id": wh2, "qty": "2"}, follow_redirects=True)
    assert "이동" in res.get_data(as_text=True) and stock("PKG-001", "WH2") == 2

"""업무 로직 단위 검증 — 마이그레이션 · 감사로그 무결성 · ERP 연동 · 증빙 검증 · 운영 설정."""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pandas as pd
import pytest
from conftest import IS_PG, TMP, biz, login, post, sqlite_only, user

import config
from core import database
from core import documents as docs
from core import enterprise as ent
from core import erp
from core import sales_db as db

FIXTURES = Path(__file__).parent / "fixtures"


# ============================================================================
# 마이그레이션: 전환 전 DB 가 데이터 손실 없이 올라오는가
# ============================================================================
@sqlite_only
def test_legacy_db_migrates_without_loss(app, monkeypatch, tmp_path):
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript((FIXTURES / "legacy_schema.sql").read_text(encoding="utf-8"))
    now = "2026-01-01 00:00:00"
    conn.executescript(f"""
        INSERT INTO orgs (id, name, org_type, created_at) VALUES (1, '영업팀', '팀', '{now}');
        INSERT INTO users (id, emp_no, name, role, org_id, active, created_at) VALUES
            (1, '100', '홍길동', 'REP', 1, 1, '{now}'), (2, '200', '팀장', 'MANAGER', 1, 1, '{now}'),
            (3, '300', '동명', 'REP', 1, 1, '{now}'), (4, '301', '동명', 'REP', 1, 1, '{now}');
        INSERT INTO customers (id, name, owner, created_at, updated_at) VALUES
            (1, '가상사', '홍길동', '{now}', '{now}'), (2, '나상사', '동명', '{now}', '{now}'),
            (3, '다상사', '퇴사자', '{now}', '{now}');
        INSERT INTO deals (customer_id, title, owner, stage, amount, created_at, updated_at)
            VALUES (1, '딜', '홍길동', '리드', 100, '{now}', '{now}');
        INSERT INTO sales (customer_id, sale_date, item, amount, owner, created_at)
            VALUES (1, '2026-01-02', '품목', 100, '홍길동', '{now}');
        INSERT INTO targets (yyyymm, owner, target_amount) VALUES ('2026-01', '홍길동', 500), ('2026-01', '퇴사자', 300);
        INSERT INTO audit_log (ts, actor, action, entity) VALUES ('{now}', '홍길동', '등록', '거래처');
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "DB_PATH", str(legacy))
    db.init_db()
    db.init_db()                                                     # 두 번 돌려도 안전

    c = sqlite3.connect(legacy)
    owners = dict(c.execute("SELECT name, owner_id FROM customers").fetchall())
    assert owners == {"가상사": 1, "나상사": None, "다상사": None}     # 동명이인·퇴사자는 자동 연결 안 함
    assert c.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 2
    assert c.execute("SELECT owner_key FROM targets WHERE owner='홍길동'").fetchone()[0] == "1"
    assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError, match="삭제할 수 없습니다"):
        c.execute("DELETE FROM audit_log")
    c.close()

    db.set_context("팀장", ent.visible_owners(ent.get_user(user_id=2)), 2)
    assert set(db.list_customers()["거래처명"]) == {"가상사"}       # 연결된 기록만 팀 범위에 들어온다
    assert ent.unlinked_counts()["customers"] == 2
    db.set_context("system", None)
    ent.upsert_user({"emp_no": "400", "name": "퇴사자", "role": "REP", "org_id": 1, "active": 0})
    assert db.get_customer(3)["owner_id"] is not None                # 같은 이름 사용자 등록 → 자동 연결


# ============================================================================
# 감사로그 해시 체인
# ============================================================================
def test_audit_chain_detects_tampering(app, isolated_db):
    db.set_context("tester", None, 1)
    for i in range(5):
        db.audit("등록", "거래처", i, {"n": i})
    assert db.verify_audit_chain()["verified"] == 5 and db.verify_audit_chain()["broken_id"] is None

    # DB 에 직접 접속한 공격자가 보호 트리거를 끄고 기록을 고친다고 가정
    off_update = ("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_no_update" if IS_PG
                  else "DROP TRIGGER trg_audit_no_update")
    off_delete = ("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_no_delete" if IS_PG
                  else "DROP TRIGGER trg_audit_no_delete")
    with database.get_conn() as c:
        c.execute(off_update)
        c.execute("UPDATE audit_log SET detail='{\"n\": 99}' WHERE id=3")
    result = db.verify_audit_chain()
    assert result["broken_id"] == 3 and "수정" in result["reason"]

    with database.get_conn() as c:
        c.execute(off_delete)
        c.execute("UPDATE audit_log SET detail='{\"n\": 2}' WHERE id=3")
        c.execute("DELETE FROM audit_log WHERE id=2")
    result = db.verify_audit_chain()
    assert result["broken_id"] == 3 and "삭제" in result["reason"]


def test_audit_log_cannot_be_deleted(app, isolated_db):
    db.set_context("tester", None, 1)
    db.audit("등록", "거래처", 1, None)
    with pytest.raises(Exception, match="삭제할 수 없습니다"):
        with database.get_conn() as c:
            c.execute("DELETE FROM audit_log")


# ============================================================================
# 증빙 검증 규칙
# ============================================================================
def test_document_rules():
    assert docs.valid_biz_no(biz("220810000")) and not docs.valid_biz_no("2208100000"[:9] + "9")
    sale = {"amount": 11_000_000, "sale_date": "2026-01-15"}
    customer = {"biz_no": biz("220810000")}
    meta = {"doc_type": "세금계산서", "issue_date": "2026-03-02", "supplier_biz_no": biz("123456789"),
            "buyer_biz_no": biz("220810000"), "supply_amount": 10_000_000, "tax_amount": 1_000_000,
            "total_amount": None, "approval_no": None}
    errors, warnings = docs.validate(dict(meta), sale, customer)
    assert not errors and any("발급 기한(2026-02-10)" in w for w in warnings)
    errors, _ = docs.validate({**meta, "doc_type": "전자세금계산서"}, sale, customer)
    assert any("24자리" in e for e in errors)
    errors, _ = docs.validate({**meta, "total_amount": 99}, sale, customer)
    assert any("합계" in e for e in errors)
    with pytest.raises(ValueError, match="DTD"):
        docs.detect_type(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')


# ============================================================================
# ERP 연동
# ============================================================================
def _new_sale(item_code="HW-01", erp_code="C100"):
    db.set_context("system", None)
    owner = user("김영업")["id"]
    cid = db.upsert_customer({"name": f"ERP상사{erp_code}{item_code}", "owner_id": owner, "erp_code": erp_code})
    sid = db.upsert_sale({"customer_id": cid, "item": "장비", "item_code": item_code, "qty": 2,
                          "unit_price": 500_000, "owner_id": owner, "sale_date": "2026-09-01"})
    return cid, sid


def test_erp_file_adapter_and_failures(app, monkeypatch):
    with db.get_conn() as conn:                                     # 앞 테스트에서 쌓인 대기 건 정리
        conn.execute("UPDATE erp_outbox SET status='취소' WHERE status IN ('대기','실패')")
    _, ok_sid = _new_sale()
    _, bad_sid = _new_sale(erp_code="")                              # ERP 코드 없는 거래처
    result = erp.process_outbox()
    assert result["sent"] == 1 and result["failed"] == 1
    files = list((TMP / "erp_out").glob("SALE_*.json"))
    doc = json.loads(files[-1].read_text(encoding="utf-8"))
    assert doc["crm_ref"] == f"CRM-{ok_sid}" and doc["customer_code"] == "C100" and doc["amount"] == 1_000_000
    assert db.get_sale(ok_sid)["erp_status"] == "전송완료"
    failed = db._one("SELECT * FROM erp_outbox WHERE ref_id=?", [bad_sid])
    assert failed["status"] == "실패" and "ERP 코드" in failed["last_error"]

    # 전송된 매출은 금액을 못 바꾸고, 취소하면 취소 전표가 나간다
    db.set_context("system", None)
    sale = db.get_sale(ok_sid)
    with pytest.raises(ValueError, match="ERP로 전송된"):
        db.upsert_sale({**sale, "id": ok_sid, "amount": 1})
    db.cancel_sale(ok_sid, "계약 해지")
    assert erp.process_outbox()["sent"] == 1
    assert db.get_sale(ok_sid)["erp_status"] == "취소완료"
    assert list((TMP / "erp_out").glob("CANCEL_*.json"))


class _FakeSap(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *args):   # noqa: D401 - 테스트 출력 억제
        pass

    def _reply(self, code, body=None, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        data = json.dumps(body).encode() if body is not None else b""
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _FakeSap.calls.append(("GET", self.path, dict(self.headers)))
        if self.headers.get("x-csrf-token") == "Fetch":
            return self._reply(200, {"d": {"results": []}},
                               {"x-csrf-token": "TOKEN123", "Set-Cookie": "SAP_SESSIONID=abc; Path=/"})
        return self._reply(400)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")

    def do_POST(self):
        body = self._body()
        _FakeSap.calls.append(("POST", self.path, dict(self.headers), body))
        if self.headers.get("x-csrf-token") != "TOKEN123" or "SAP_SESSIONID=abc" not in self.headers.get("Cookie", ""):
            return self._reply(403, {"error": {"message": {"value": "CSRF token validation failed"}}})
        if not body.get("SoldToParty"):
            return self._reply(400, {"error": {"message": {"value": "Sold-to party missing"}}})
        return self._reply(201, {"d": {"SalesOrder": "5000001"}})

    def do_PATCH(self):
        _FakeSap.calls.append(("PATCH", self.path, dict(self.headers), self._body()))
        return self._reply(204)


def test_sap_odata_adapter_against_mock(app, monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeSap)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("SALES_ERP_ADAPTER", "sap_odata")
        monkeypatch.setenv("SALES_SAP_BASE_URL", f"http://127.0.0.1:{server.server_port}/sap/opu/odata/sap/API_SALES_ORDER_SRV")
        monkeypatch.setenv("SALES_SAP_USER", "CRM_RFC")
        monkeypatch.setenv("SALES_SAP_PASSWORD", "secret")
        monkeypatch.setenv("SALES_SAP_CLIENT", "100")
        with db.get_conn() as conn:
            conn.execute("UPDATE erp_outbox SET status='취소' WHERE status IN ('대기','실패')")
        _, sid = _new_sale(item_code="MAT-9", erp_code="C777")
        assert erp.process_outbox()["sent"] == 1
        assert db.get_sale(sid)["erp_doc_no"] == "5000001"
        post_call = next(c for c in _FakeSap.calls if c[0] == "POST")
        body = post_call[3]
        assert body["SoldToParty"] == "C777" and body["PurchaseOrderByCustomer"] == f"CRM-{sid}"
        assert body["to_Item"][0]["Material"] == "MAT-9"
        assert body["to_Item"][0]["to_PricingElement"][0]["ConditionRateValue"] == "500000"
        headers = {k.lower(): v for k, v in post_call[2].items()}
        assert headers["authorization"].startswith("Basic ") and headers["sap-client"] == "100"

        db.set_context("system", None)
        db.cancel_sale(sid, "취소")
        erp.process_outbox()
        patch_call = next(c for c in _FakeSap.calls if c[0] == "PATCH")
        assert "SalesOrder='5000001'" in patch_call[1] and patch_call[3]["SalesDocumentRjcnReason"] == "Z1"
    finally:
        server.shutdown()


def test_payment_reconciliation_is_idempotent(app):
    db.set_context("system", None)
    _, sid = _new_sale(item_code="PAY-1", erp_code="C555")
    frame = pd.DataFrame([{"참조번호": f"CRM-{sid}", "누적입금액": 400_000, "매출액": 1_000_000},
                          {"참조번호": "CRM-999999", "누적입금액": 1}])
    preview = erp.reconcile_payments(frame, apply=False)
    assert list(preview["결과"]) == ["반영예정", "미일치"] and db.get_sale(sid)["paid_amount"] == 0
    erp.reconcile_payments(frame, apply=True)
    erp.reconcile_payments(frame, apply=True)                         # 같은 파일을 다시 올려도
    assert db.get_sale(sid)["paid_amount"] == 400_000                  # 이중 입금되지 않는다
    frame.loc[0, "누적입금액"] = 1_100_000                            # 공급가 1,000,000 + 부가세 100,000
    erp.reconcile_payments(frame, apply=True)
    assert db.get_sale(sid)["status"] == "입금완료"


def test_erp_screen_flow(app):
    admin = login(app, "시스템관리자")
    assert "전송 대기" in admin.get("/admin/erp").get_data(as_text=True)
    res = post(admin, "/admin/erp/send", follow_redirects=True)
    assert "ERP 전송" in res.get_data(as_text=True)


# ============================================================================
# 운영 설정 · 백업 · 초기화
# ============================================================================
def test_production_refuses_unsafe_settings(monkeypatch):
    monkeypatch.setattr(config, "PRODUCTION", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.delenv("SALES_SECRET_KEY", raising=False)
    problems = config.production_problems("simple")
    assert len(problems) == 3
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setenv("SALES_SECRET_KEY", "x" * 32)
    assert config.production_problems("sso") == []


def test_backup_and_reset_keep_audit(app, isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "bk")
    db.set_context("system", None)
    ent.seed_org_demo()
    admin = login(app, "시스템관리자")
    before_audit = db._scalar("SELECT COUNT(*) FROM audit_log")
    res = post(admin, "/admin/data/action", {"action": "reset", "confirm": "1"})
    assert res.status_code == 302 and "/setup" in res.headers["Location"]
    backups = [*(tmp_path / "bk").glob("sales_*.db"), *(tmp_path / "bk").glob("sales_*.dump")]
    assert backups and backups[0].stat().st_size > 0                  # 지우기 전에 백업
    assert db._scalar("SELECT COUNT(*) FROM users") == 0
    assert db._scalar("SELECT COUNT(*) FROM audit_log") > before_audit  # 감사로그는 보존
    client = app.test_client()
    res = post(client, "/setup", {"action": "seed"})
    assert res.status_code == 302 and len(ent.list_users()) == 8


def test_migration_roundtrip_and_restore(app, isolated_db, monkeypatch, tmp_path):
    """스키마를 처음 버전까지 내렸다 다시 올려도 데이터가 남고, 백업 → 복구가 실제로 되돌린다."""
    from alembic import command

    import manage
    from core import database
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "bk")
    db.set_context("system", None)
    ent.seed_org_demo()
    kim = ent.get_user(name="김영업")["id"]
    cid = db.upsert_customer({"name": "왕복상사", "owner_id": kim})
    db.upsert_sale({"customer_id": cid, "item": "왕복", "qty": 1, "unit_price": 1000, "amount": 1000,
                    "owner_id": kim, "tax_type": "과세"})

    command.downgrade(database.alembic_config(), "0001_baseline")
    assert database.current_revision() == "0001_baseline"
    database.migrate()
    assert database.current_revision() == database.head_revision()
    sale = db._one("SELECT * FROM sales WHERE item='왕복'")
    assert (sale["amount"], sale["vat_amount"], sale["total_amount"]) == (1000, 100, 1100)   # 다시 계산됨

    assert manage.main(["backup", "--dir", str(tmp_path / "manual")]) == 0
    dump = next(iter([*(tmp_path / "manual").glob("sales_*.db"), *(tmp_path / "manual").glob("sales_*.dump")]))
    with db.get_conn() as conn:
        conn.execute("UPDATE customers SET name='사고로바뀐이름' WHERE id=?", (cid,))
    assert manage.main(["restore", str(dump)]) == 2                            # --yes 없으면 거부
    assert manage.main(["restore", str(dump), "--yes"]) == 0
    database.close_pool()
    assert db.get_customer(cid)["name"] == "왕복상사"
    assert database.current_revision() == database.head_revision()


# ============================================================================
# 공용 파일 저장소 (S3 호환) — moto 모의 S3
# ============================================================================
def test_s3_storage_roundtrip():
    import boto3
    from moto import mock_aws

    from core.storage import S3Storage
    with mock_aws():
        boto3.client("s3", region_name="ap-northeast-2").create_bucket(
            Bucket="sales-files", CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        st = S3Storage("sales-files", "prod", region="ap-northeast-2", sse="AES256")
        key = st.put("documents/2026/09/a.png", b"PNGDATA", "image/png")
        assert key == "documents/2026/09/a.png" and st.get(key) == b"PNGDATA" and st.exists(key)
        head = boto3.client("s3", region_name="ap-northeast-2").head_object(Bucket="sales-files", Key="prod/" + key)
        assert head["ServerSideEncryption"] == "AES256" and head["ContentType"] == "image/png"
        st.delete(key)
        assert not st.exists(key)
        with pytest.raises(FileNotFoundError):
            st.get(key)
        with pytest.raises(ValueError):
            st.put("../../etc/passwd", b"x")


def test_documents_on_s3_end_to_end(app, monkeypatch):
    """서버 여러 대 구성: 증빙을 S3 에 올리고, 다른 요청(다른 서버라고 가정)에서 읽는다."""
    import io as _io

    import boto3
    from moto import mock_aws
    from PIL import Image

    from core import storage
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="sales-docs")
        monkeypatch.setenv("SALES_STORAGE", "s3")
        monkeypatch.setenv("SALES_S3_BUCKET", "sales-docs")
        monkeypatch.setenv("SALES_S3_REGION", "us-east-1")
        storage.reset_storage()
        try:
            rep = login(app, "김영업")
            sid = db._one("SELECT id FROM sales WHERE owner='김영업' AND status <> '취소' ORDER BY id")["id"]
            buf = _io.BytesIO()
            Image.new("RGB", (40, 40), "white").save(buf, "PNG")
            post(rep, f"/sales/{sid}/documents", {"doc_type": "거래명세서", "file": (_io.BytesIO(buf.getvalue()), "s3.png")},
                 content_type="multipart/form-data")
            doc = db._one("SELECT * FROM sale_documents WHERE file_name='s3.png'")
            assert doc and doc["file_path"].startswith("documents/")
            objs = boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="sales-docs")["Contents"]
            assert any(o["Key"].endswith(doc["file_path"]) for o in objs)
            res = login(app, "김영업").get(f"/documents/{doc['id']}/file")
            assert res.status_code == 200 and res.data == buf.getvalue()
        finally:
            storage.reset_storage()

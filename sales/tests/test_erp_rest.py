"""SAP 외 ERP 연동 — 범용 REST 어댑터(모의 ERP 서버) · 연결 테스트 · ERP → CRM 실시간 수신 API."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import login, post, user

from core import erp
from core import sales_db as db

TOKEN, SECRET = "erp-token", "hmac-secret"


class _FakeErp(BaseHTTPRequestHandler):
    """더존·영림원 같은 사내 ERP 앞단 API 흉내: Bearer 인증 + HMAC 서명 확인 + 멱등키로 중복 전표 방지."""
    slips: dict = {}          # 멱등키 → 전표번호
    received: list = []
    cancelled: list = []

    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, body: bytes) -> bool:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return False
        stamp = self.headers.get("X-Timestamp", "")
        expect = "sha256=" + hmac.new(SECRET.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.headers.get("X-Signature", ""), expect)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._reply(401, {"error": "unauthorized"})
        return self._reply(200, {"status": "UP"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self._authorized(body):
            return self._reply(401, {"error": "bad signature"})
        doc = json.loads(body)
        if self.path.startswith("/slips/") and self.path.endswith("/cancel"):
            _FakeErp.cancelled.append(self.path.split("/")[2])
            return self._reply(200, {"result": "OK"})
        if not doc.get("CUST_CD"):
            return self._reply(422, {"error": "CUST_CD required"})
        key = self.headers.get("Idempotency-Key")
        if key not in _FakeErp.slips:
            _FakeErp.slips[key] = f"SL{len(_FakeErp.slips) + 1:06d}"
            _FakeErp.received.append(doc)
        return self._reply(201, {"data": {"slipNo": _FakeErp.slips[key]}})


@pytest.fixture
def fake_erp(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeErp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    for k, v in {"SALES_ERP_ADAPTER": "rest", "SALES_ERP_REST_URL": base + "/slips",
                 "SALES_ERP_REST_HEALTH_URL": base + "/health", "SALES_ERP_REST_AUTH": "bearer",
                 "SALES_ERP_REST_TOKEN": TOKEN, "SALES_ERP_REST_HMAC_SECRET": SECRET,
                 "SALES_ERP_REST_DOCNO_PATH": "data.slipNo",
                 "SALES_ERP_REST_FIELD_MAP": json.dumps({"customer_code": "CUST_CD", "amount": "SUPPLY_AMT",
                                                         "vat_amount": "VAT_AMT", "owner": ""})}.items():
        monkeypatch.setenv(k, v)
    yield base
    server.shutdown()


def _sale(name: str, erp_code: str = "D200") -> int:
    db.set_context("system", None)
    owner = user("김영업")["id"]
    cid = db.upsert_customer({"name": name, "owner_id": owner, "erp_code": erp_code})
    return db.upsert_sale({"customer_id": cid, "item": "라이선스", "item_code": "SW-ERP-01", "qty": 1,
                           "unit_price": 2_000_000, "owner_id": owner, "sale_date": "2026-09-10"})


def test_rest_adapter_send_cancel_and_connection(app, fake_erp):
    with db.get_conn() as conn:
        conn.execute("UPDATE erp_outbox SET status='취소' WHERE status IN ('대기','실패')")
    sid = _sale("REST상사")
    ok, msg = erp.test_connection()
    assert ok, msg
    result = erp.process_outbox()
    assert result == {"sent": 1, "failed": 0, "skipped": 0, "adapter": "rest"}
    sale = db.get_sale(sid)
    assert sale["erp_status"] == "전송완료" and sale["erp_doc_no"] == "SL000001"
    doc = _FakeErp.received[-1]
    assert doc["CUST_CD"] == "D200" and doc["SUPPLY_AMT"] == 2_000_000 and doc["VAT_AMT"] == 200_000
    assert "owner" not in doc and doc["crm_ref"] == f"CRM-{sid}"          # 빈 이름으로 매핑한 필드는 빼고 보낸다

    # 같은 대기열 건을 다시 보내도(재시도) ERP 가 멱등키로 같은 전표를 돌려준다
    adapter = erp.get_adapter()
    again = adapter.send(int(db._one("SELECT id FROM erp_outbox WHERE ref_id=?", [sid])["id"]),
                         erp.build_document(db._one("SELECT * FROM erp_outbox WHERE ref_id=?", [sid]), erp.settings()))
    assert again == "SL000001" and len(_FakeErp.slips) == 1

    db.cancel_sale(sid, "주문 취소")
    assert erp.process_outbox()["sent"] == 1
    assert _FakeErp.cancelled[-1] == "SL000001" and db.get_sale(sid)["erp_status"] == "취소완료"


def test_rest_adapter_rejects_bad_credentials(app, fake_erp, monkeypatch):
    monkeypatch.setenv("SALES_ERP_REST_TOKEN", "wrong")
    ok, msg = erp.test_connection()
    assert not ok and "401" in msg
    with db.get_conn() as conn:
        conn.execute("UPDATE erp_outbox SET status='취소' WHERE status IN ('대기','실패')")
    sid = _sale("REST인증오류상사")
    assert erp.process_outbox()["failed"] == 1
    row = db._one("SELECT * FROM erp_outbox WHERE ref_id=?", [sid])
    assert row["status"] == "실패" and "401" in row["last_error"]

    monkeypatch.setenv("SALES_ERP_REST_FIELD_MAP", "{not json")
    ok, msg = erp.test_connection()
    assert not ok and "JSON" in msg


def test_erp_connection_button(app, fake_erp):
    admin = login(app, "시스템관리자")
    res = post(admin, "/admin/erp/test", follow_redirects=True)
    assert "ERP 연결 정상" in res.get_data(as_text=True)
    page = admin.get("/admin/erp?tab=inbound").get_data(as_text=True)
    assert "/api/v1/erp/payments" in page


def _erp_key(app, acting: str = "시스템관리자") -> str:
    admin = login(app, "시스템관리자")
    res = post(admin, "/admin/api/create", {"name": f"ERP수신-{acting}", "user_id": user(acting)["id"],
                                            "scopes": ["erp:write"]})
    return re.search(r"(sk_[0-9a-f]{8}_[A-Za-z0-9_\-]+)", res.get_data(as_text=True)).group(1)


def test_erp_inbound_api(app):
    key = _erp_key(app)
    h = {"Authorization": f"Bearer {key}"}
    client = app.test_client()
    sid = _sale("수신상사", erp_code="R300")          # 공급가액 200만 + 부가세 20만

    # 전표번호 회신 (파일·EAI 비동기)
    res = client.post("/api/v1/erp/acks", json={"items": [{"ref": f"CRM-{sid}", "erp_doc_no": "90007777"},
                                                          {"ref": "CRM-999999", "erp_doc_no": "X"}]}, headers=h)
    body = res.get_json()
    assert res.status_code == 200 and body["summary"] == {"반영": 1, "오류": 1}
    assert db.get_sale(sid)["erp_doc_no"] == "90007777" and db.get_sale(sid)["erp_status"] == "전송완료"

    # 누적 입금액 — 같은 값을 두 번 보내도 한 번만 반영, 과입금은 확인필요
    pay = {"items": [{"erp_doc_no": "90007777", "ref": "", "paid_total": 1_100_000}]}
    first = client.post("/api/v1/erp/payments", json=pay, headers=h).get_json()
    again = client.post("/api/v1/erp/payments", json=pay, headers=h).get_json()
    assert first["results"][0]["result"] == "반영" and again["results"][0]["result"] == "일치"
    assert int(db.get_sale(sid)["paid_amount"]) == 1_100_000
    over = client.post("/api/v1/erp/payments", json={"items": [{"ref": f"CRM-{sid}", "paid_total": 9_000_000}]},
                       headers=h).get_json()
    assert over["results"][0]["result"] == "확인필요"

    # 여신한도 · 품목 마스터
    credit = client.post("/api/v1/erp/credit", json={"items": [{"erp_code": "R300", "credit_limit": 50_000_000},
                                                               {"erp_code": "없음", "credit_limit": 1}]},
                         headers=h).get_json()
    assert credit["summary"] == {"변경": 1, "미일치": 1}
    assert int(db._one("SELECT credit_limit FROM customers WHERE erp_code='R300'")["credit_limit"]) == 50_000_000
    prod = client.post("/api/v1/erp/products", json={"items": [
        {"code": "ERP-MAT-01", "name": "ERP 수신 자재", "unit": "EA", "list_price": 700000, "tax_type": "과세"},
        {"code": "ERP-MAT-01", "name": "ERP 수신 자재(개정)", "list_price": 750000},       # 같은 코드 → 수정
        {"code": "BAD", "name": "", "list_price": 1}]}, headers=h).get_json()
    assert prod["summary"] == {"등록": 1, "수정": 1, "오류": 1}
    assert int(db._one("SELECT list_price FROM products WHERE code='ERP-MAT-01'")["list_price"]) == 750_000
    assert db._one("SELECT id FROM audit_log WHERE action='ERP여신'")

    # 형식 오류 · 권한
    assert client.post("/api/v1/erp/payments", json={"x": 1}, headers=h).status_code == 400
    assert client.post("/api/v1/erp/payments", json={"items": [{}] * 1001}, headers=h).status_code == 413
    rep_key = _erp_key(app, "김영업")
    res = client.post("/api/v1/erp/products", json={"items": [{"code": "Z", "name": "z"}]},
                      headers={"Authorization": f"Bearer {rep_key}"})
    assert res.status_code == 403
    assert "/erp/payments" in client.get("/api/v1/openapi.json").get_json()["paths"]

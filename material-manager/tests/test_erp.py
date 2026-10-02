"""ERP·SAP 연결 방식: S/4HANA OData · RFC(BAPI) · 기타 ERP REST(매핑 파일) · 파일 연계 · Flask CLI.

실제 SAP·ERP 대신 같은 PC의 가짜 서버와 가짜 pyrfc 모듈로 요청 형식과 흐름을 확인한다.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import types
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_erp_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
from core import db, erp, master_sync, reconcile, sap, services  # noqa: E402
from test_advanced import M1, fresh, mid  # noqa: E402,F401
from test_app import app, client, csrf, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()


def map_all() -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = UPPER(REPLACE(code, '-', ''))")
        conn.execute("UPDATE plants SET sap_plant = '1000'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001'")


def outbox(tx_id: int) -> dict:
    with db.get_conn() as conn:
        return dict(conn.execute("SELECT * FROM sap_outbox WHERE tx_id = ?", (tx_id,)).fetchone())


class FakeServer:
    """handler(method, path, query, headers, body) → (status, body, headers)."""

    def __init__(self, handler):
        outer = self
        self.requests = []

        class H(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                u = urllib.parse.urlsplit(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                body = json.loads(raw) if raw and raw[:1] in (b"{", b"[") else raw.decode()
                outer.requests.append((self.command, u.path, q, dict(self.headers.items()), body))
                status, out, hdrs = handler(self.command, u.path, q, self.headers, body)
                data = json.dumps(out, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (hdrs or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _do

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


# ── SAP S/4HANA OData ────────────────────────────────────────
@pytest.fixture()
def s4(monkeypatch):
    state = {"token": "T1", "expire_once": True, "docs": {}}

    def handler(method, path, q, headers, body):
        if q.get("sap-client") != "100":
            return 400, {"error": {"message": {"value": "sap-client 없음"}}}, {}
        if headers.get("Authorization") != "Basic " + base64.b64encode(b"RFCUSER:pw").decode():
            return 401, {}, {}
        if headers.get("X-CSRF-Token") == "Fetch":
            state["token"] = "T2" if state.get("fetched") else "T1"
            state["fetched"] = True
            return 200, {}, {"x-csrf-token": state["token"], "Set-Cookie": "SAP_SESSIONID=abc; Path=/"}
        if method == "POST":
            if headers.get("X-CSRF-Token") != state["token"] or "SAP_SESSIONID=abc" not in (headers.get("Cookie") or ""):
                return 403, {}, {"x-csrf-token": "Required"}
            if state["expire_once"]:                       # 토큰 만료 흉내 → 다시 받아 재요청해야 한다
                state["expire_once"] = False
                state["token"] = "EXPIRED"
                return 403, {}, {"x-csrf-token": "Required"}
        if path.endswith("/A_MaterialDocumentHeader") and method == "POST":
            item = body["to_MaterialDocumentItem"]["results"][0]
            if item["Material"].startswith("FAIL"):
                return 400, {"error": {"code": "M7/021", "message": {"value": "자재가 플랜트에 없습니다"}}}, {}
            no = f"49{len(state['docs']) + 1:08d}"
            state["docs"][body["MaterialDocumentHeaderText"]] = no
            return 201, {"d": {"MaterialDocument": no, "MaterialDocumentYear": TODAY[:4]}}, {}
        if path.endswith("/A_MaterialDocumentHeader"):
            key = q["$filter"].split("'")[1]
            rows = [{"MaterialDocument": state["docs"][key], "MaterialDocumentYear": TODAY[:4]}] if key in state["docs"] else []
            return 200, {"d": {"results": rows}}, {}
        if path.endswith("/Cancel"):
            return 200, {"d": {"MaterialDocument": "4999999999", "MaterialDocumentYear": TODAY[:4]}}, {}
        if path.endswith("/A_Product"):
            if q.get("$skiptoken") == "2":
                return 200, {"d": {"results": [{"Product": "S4-200", "BaseUnit": "L", "ProductGroup": "",
                                                "IsMarkedForDeletion": False, "IsBatchManagementRequired": True,
                                                "to_Description": {"results": [{"Language": "EN", "ProductDescription": "Oil"}]}}]}}, {}
            return 200, {"d": {"results": [{"Product": "S4-100", "BaseUnit": "EA", "ProductGroup": "PKG",
                                            "IsMarkedForDeletion": False, "IsBatchManagementRequired": False,
                                            "to_Description": {"results": [
                                                {"Language": "EN", "ProductDescription": "Pallet"},
                                                {"Language": "KO", "ProductDescription": "팔레트"}]}}],
                               "__next": "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product?$skiptoken=2"}}, {}
        if path.endswith("/A_CostCenter"):
            return 200, {"d": {"results": [{"CostCenter": "4100", "CostCenterName": "포장팀", "ValidityEndDate": "/Date(253402214400000)/"},
                                           {"CostCenter": "4900", "CostCenterName": "폐쇄", "ValidityEndDate": "/Date(946684800000)/"}]}}, {}
        if path.endswith("/A_MatlStkInAcctMod"):
            return 200, {"d": {"results": [
                {"Material": "PKG001", "Plant": "1000", "StorageLocation": "0001", "MatlWrhsStkQtyInMatlBaseUnit": "30"},
                {"Material": "PKG001", "Plant": "1000", "StorageLocation": "0001", "MatlWrhsStkQtyInMatlBaseUnit": "5"}]}}, {}
        return 404, {}, {}

    srv = FakeServer(handler)
    for k, v in {"SAP_MODE": "sap_odata", "SAP_ODATA_URL": srv.url, "SAP_CLIENT": "100", "SAP_USER": "RFCUSER",
                 "SAP_PASSWORD": "pw", "SAP_OAUTH_TOKEN_URL": ""}.items():
        monkeypatch.setattr(config, k, v)
    yield srv, state
    srv.close()


def test_s4_odata_post_cancel_and_idempotent_retry(fresh, s4):
    srv, state = s4
    map_all()
    tx = services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 18000, ref_no="GR-1")
    assert sap.process_outbox()["sent"] == 1
    row = outbox(tx.tx_id)
    assert row["status"] == "SENT" and row["sap_doc_no"] == "4900000001"
    body = [r[4] for r in srv.requests if r[0] == "POST" and r[1].endswith("A_MaterialDocumentHeader")][-1]
    item = body["to_MaterialDocumentItem"]["results"][0]
    assert body["GoodsMovementCode"] == "05" and body["MaterialDocumentHeaderText"] == f"MM-TX-{tx.tx_id}"
    assert body["PostingDate"].startswith("/Date(") and body["ReferenceDocument"] == "GR-1"
    assert item == {"Material": "PKG001", "Plant": "1000", "StorageLocation": "0001", "GoodsMovementType": "501",
                    "EntryUnit": "EA", "QuantityInEntryUnit": "5.000"}
    assert state["token"] == "T2", "만료된 CSRF 토큰은 다시 받아 재요청"

    # 응답을 못 받아 다시 보내는 경우: 헤더 텍스트(멱등키)로 찾아 같은 번호를 쓰고 새로 전기하지 않는다
    payload = json.loads(row["payload"])
    posts_before = sum(1 for r in srv.requests if r[0] == "POST")
    assert erp.connector().send(payload, attempt=2).doc_no == "4900000001"
    assert sum(1 for r in srv.requests if r[0] == "POST") == posts_before

    assert services.reverse_transaction(tx.tx_id, "반품", actor=M1).ok
    assert sap.process_outbox()["sent"] == 1
    cancel = [r for r in srv.requests if r[1].endswith("/Cancel")][-1]
    assert cancel[2]["MaterialDocument"] == "'4900000001'" and cancel[2]["PostingDate"] == f"datetime'{TODAY}T00:00:00'"


def test_s4_odata_business_error_fails_and_ping(fresh, s4):
    srv, _ = s4
    map_all()
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = 'FAIL1' WHERE code = 'PKG-002'")
    tx = services.register_transaction(mid("PKG-002"), "IN", 1, TODAY, 1)
    assert sap.process_outbox()["failed"] == 1
    assert "자재가 플랜트에 없습니다" in outbox(tx.tx_id)["last_error"]
    assert erp.ping().ok


def test_s4_odata_master_and_stock(fresh, s4, monkeypatch):
    monkeypatch.setattr(config, "SAP_MASTER_SYNC", True)
    map_all()
    price_before = db.scalar("SELECT unit_price FROM materials WHERE code = 'PKG-001'")
    counts = master_sync.sync()
    assert counts["created"] == 2 and counts["cost_centers"] == 2, "다음 페이지(__next)까지 읽는다"
    assert db.scalar("SELECT name FROM materials WHERE sap_matnr = 'S4-100'") == "팔레트", "한국어 설명 우선"
    assert db.scalar("SELECT lot_managed FROM materials WHERE sap_matnr = 'S4-200'") == 1
    assert db.scalar("SELECT active FROM cost_centers WHERE code = '4900'") == 0, "유효기간 끝난 원가센터"
    assert db.scalar("SELECT unit_price FROM materials WHERE code = 'PKG-001'") == price_before
    df, msg = reconcile.fetch_sap_stock()
    assert not msg and float(df[df["sap_matnr"] == "PKG001"]["sap_qty"].sum()) == 35, (df, msg)


# ── SAP RFC (BAPI) ───────────────────────────────────────────
class FakeRfc:
    calls: list = []
    docs: dict = {}

    class CommunicationError(Exception):
        pass

    class Connection:
        def __init__(self, **params):
            FakeRfc.calls.append(("CONNECT", params))

        def call(self, fm, **kw):
            FakeRfc.calls.append((fm, kw))
            if fm == "BAPI_GOODSMVT_CREATE":
                item = kw["GOODSMVT_ITEM"][0]
                if item.get("MATERIAL", "").startswith("ERR"):
                    return {"MATERIALDOCUMENT": "", "RETURN": [{"TYPE": "E", "ID": "M7", "NUMBER": "021", "MESSAGE": "자재 없음"}]}
                no = f"50{len(FakeRfc.docs) + 1:08d}"
                FakeRfc.docs[kw["GOODSMVT_HEADER"]["HEADER_TXT"]] = no
                return {"MATERIALDOCUMENT": no, "MATDOCUMENTYEAR": TODAY[:4], "RETURN": []}
            if fm == "BAPI_GOODSMVT_CANCEL":
                return {"GOODSMVT_HEADRET": {"MAT_DOC": "5099999999", "DOC_YEAR": TODAY[:4]}, "RETURN": []}
            if fm == "RFC_READ_TABLE" and kw["QUERY_TABLE"] == "MKPF":
                key = kw["OPTIONS"][0]["TEXT"].split("'")[1]
                return {"DATA": [{"WA": f"{FakeRfc.docs[key]}|{TODAY[:4]}"}] if key in FakeRfc.docs else []}
            if fm == "RFC_READ_TABLE" and kw["QUERY_TABLE"] == "MARD":
                return {"DATA": [{"WA": "000000000000012345|1000|0001|   12.000"}]}
            return {}

        def ping(self):
            return None

        def close(self):
            FakeRfc.calls.append(("CLOSE", {}))


@pytest.fixture()
def rfc(monkeypatch):
    FakeRfc.calls, FakeRfc.docs = [], {}
    monkeypatch.setitem(sys.modules, "pyrfc", types.SimpleNamespace(Connection=FakeRfc.Connection,
                                                                    CommunicationError=FakeRfc.CommunicationError))
    for k, v in {"SAP_MODE": "sap_rfc", "SAP_RFC_ASHOST": "sap.test", "SAP_RFC_SYSNR": "00", "SAP_CLIENT": "100",
                 "SAP_USER": "RFCUSER", "SAP_PASSWORD": "pw"}.items():
        monkeypatch.setattr(config, k, v)
    return FakeRfc


def test_sap_rfc_bapi_post_commit_cancel(fresh, rfc):
    map_all()
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = '12345' WHERE code = 'PKG-001'")
    out = services.register_transaction(mid("PKG-001"), "OUT", 2, TODAY, 0, cost_center="4100")
    assert sap.process_outbox()["sent"] == 1
    create = next(kw for fm, kw in rfc.calls if fm == "BAPI_GOODSMVT_CREATE")
    item = create["GOODSMVT_ITEM"][0]
    assert create["GOODSMVT_CODE"] == {"GM_CODE": "03"} and create["GOODSMVT_HEADER"]["HEADER_TXT"] == f"MM-TX-{out.tx_id}"
    assert item["MATERIAL"] == "000000000000012345" and item["COSTCENTER"] == "0000004100", "숫자 번호는 ALPHA 변환"
    assert ("BAPI_TRANSACTION_COMMIT", {"WAIT": "X"}) in rfc.calls
    assert rfc.calls[-1][0] == "CLOSE"
    connect = next(kw for fm, kw in rfc.calls if fm == "CONNECT")
    assert connect["ashost"] == "sap.test" and connect["client"] == "100"

    payload = json.loads(outbox(out.tx_id)["payload"])
    n = sum(1 for fm, _ in rfc.calls if fm == "BAPI_GOODSMVT_CREATE")
    assert erp.connector().send(payload, attempt=2).doc_no == outbox(out.tx_id)["sap_doc_no"], "MKPF에서 찾음"
    assert sum(1 for fm, _ in rfc.calls if fm == "BAPI_GOODSMVT_CREATE") == n

    assert services.reverse_transaction(out.tx_id, "오입력", actor=M1).ok
    assert sap.process_outbox()["sent"] == 1
    assert any(fm == "BAPI_GOODSMVT_CANCEL" for fm, _ in rfc.calls)
    assert erp.connector().fetch_stock(["1000"])[0] == {"material": "12345", "plant": "1000",
                                                        "storageLocation": "0001", "quantity": 12.0}


def test_sap_rfc_error_rolls_back_and_missing_library(fresh, rfc, monkeypatch):
    map_all()
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = 'ERR1' WHERE code = 'PKG-002'")
    tx = services.register_transaction(mid("PKG-002"), "IN", 1, TODAY, 1)
    assert sap.process_outbox()["failed"] == 1
    assert "M7 021" in outbox(tx.tx_id)["last_error"]
    assert any(fm == "BAPI_TRANSACTION_ROLLBACK" for fm, _ in rfc.calls)
    monkeypatch.setitem(sys.modules, "pyrfc", None)
    result = erp.ping()
    assert not result.ok and "pyrfc" in result.message


# ── 기타 ERP REST (매핑 파일) ─────────────────────────────────
@pytest.fixture()
def rest_erp(monkeypatch):
    def handler(method, path, q, headers, body):
        if headers.get("Authorization") != "Bearer erp-token" or headers.get("X-Company-Code") != "1000":
            return 401, {"message": "인증 실패"}, {}
        if path == "/api/v1/inventory/movements" and method == "POST":
            if body["itemCode"] == "BAD":
                return 200, {"success": False, "message": "품목이 없습니다"}, {}
            return 200, {"success": True, "data": {"docNo": f"IV-{body['extKey']}"}}, {}
        if path.endswith("/cancel"):
            return 200, {"success": True, "data": {"docNo": "IV-CANCEL"}}, {}
        if path == "/api/v1/items":
            return 200, {"data": [{"itemCode": "ERP-9", "itemName": "완충재", "unit": "EA", "itemGroup": "포장재",
                                   "stdCost": 700, "useYn": "Y", "lotYn": "N"}]}, {}
        if path == "/api/v1/departments":
            return 200, {"data": [{"deptCode": "D10", "deptName": "생산팀", "useYn": "Y"}]}, {}
        return 404, {"message": "없음"}, {}

    srv = FakeServer(handler)
    for k, v in {"SAP_MODE": "rest", "ERP_REST_URL": srv.url, "ERP_REST_MAP": "erp_maps/generic_example.json",
                 "ERP_REST_AUTH": "bearer:erp-token"}.items():
        monkeypatch.setattr(config, k, v)
    yield srv
    srv.close()


def test_generic_rest_erp_with_mapping_file(fresh, rest_erp, monkeypatch):
    map_all()
    tx = services.register_transaction(mid("PKG-001"), "IN", 3, TODAY, 18000, ref_no="R-7")
    assert sap.process_outbox()["sent"] == 1
    _, path, _, headers, body = [r for r in rest_erp.requests if r[0] == "POST"][-1]
    assert headers["X-Request-Id"] == f"MM-TX-{tx.tx_id}"
    assert body["ioType"] == "I" and body["moveCode"] == "GR" and body["qty"] == 3
    assert body["docDate"] == TODAY.replace("-", "") and body["remark"] == "MM R-7" and body["itemCode"] == "PKG001"
    assert outbox(tx.tx_id)["sap_doc_no"] == f"IV-MM-TX-{tx.tx_id}"

    assert services.reverse_transaction(tx.tx_id, "취소", actor=M1).ok
    assert sap.process_outbox()["sent"] == 1
    assert [r for r in rest_erp.requests if r[0] == "POST"][-1][1] == f"/api/v1/inventory/movements/IV-MM-TX-{tx.tx_id}/cancel"

    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = 'BAD' WHERE code = 'PKG-002'")
    bad = services.register_transaction(mid("PKG-002"), "IN", 1, TODAY, 1)
    assert sap.process_outbox()["failed"] == 1 and "품목이 없습니다" in outbox(bad.tx_id)["last_error"]

    monkeypatch.setattr(config, "SAP_MASTER_SYNC", True)
    counts = master_sync.sync()
    assert counts["created"] == 1 and counts["cost_centers"] == 1
    assert db.scalar("SELECT unit_price FROM materials WHERE sap_matnr = 'ERP-9'") == 700


def test_rest_template_rendering():
    ctx = {"quantity": 2.5, "movementType": "999", "postingDate": "2026-09-27", "reference": "a/b"}
    assert erp.render({"q": "{quantity}", "m": {"$map": "movementType", "values": {"101": "X"}, "default": "ETC"},
                       "d": {"$date": "postingDate", "format": "%Y%m%d"}, "t": "R {reference}"}, ctx) == \
        {"q": 2.5, "m": "ETC", "d": "20260927", "t": "R a/b"}
    assert erp.render("/x/{reference}", ctx, for_url=True) == "/x/a%2Fb", "경로 값은 URL 인코딩"


# ── 파일 연계 ────────────────────────────────────────────────
def test_file_interface_outbound_ack_and_master(fresh, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAP_MODE", "file")
    monkeypatch.setattr(config, "ERP_FILE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ERP_FILE_FORMAT", "csv")
    map_all()
    a = services.register_transaction(mid("PKG-001"), "IN", 4, TODAY, 18000)
    b = services.register_transaction(mid("PKG-002"), "IN", 1, TODAY, 100)
    assert sap.process_outbox()["sent"] == 2
    text = (tmp_path / "outbound" / f"MM-TX-{a.tx_id}.csv").read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("idempotencyKey,action,postingDate") and "PKG001" in text
    assert outbox(a.tx_id)["sap_doc_no"] == f"FILE:MM-TX-{a.tx_id}"

    inbox = tmp_path / "inbound"
    inbox.mkdir()
    (inbox / f"MM-TX-{a.tx_id}.ack.json").write_text(json.dumps({"document": "DZ-2026-0001", "year": "2026"}))
    (inbox / f"MM-TX-{b.tx_id}.ack.json").write_text(json.dumps({"error": "품목코드 없음"}))
    counts = sap.process_outbox()
    assert counts["acked"] == 1 and counts["rejected"] == 1
    assert outbox(a.tx_id)["sap_doc_no"] == "DZ-2026-0001"
    assert outbox(b.tx_id)["status"] == "FAILED" and "품목코드 없음" in outbox(b.tx_id)["last_error"]
    assert len(list((inbox / "processed").iterdir())) == 2

    (inbox / "master_materials.csv").write_text("material,description,unit,deleted\nF-1,파일 자재,EA,N\n",
                                                encoding="utf-8-sig")
    monkeypatch.setattr(config, "SAP_MASTER_SYNC", True)
    assert master_sync.sync()["created"] == 1
    assert erp.ping().ok


# ── Flask CLI · 화면 ─────────────────────────────────────────
def test_flask_cli_commands(app, monkeypatch):
    runner = app.test_cli_runner()
    res = runner.invoke(args=["batch", "list"])
    assert res.exit_code == 0 and "sap_sync" in res.output
    monkeypatch.setattr(config, "SAP_MODE", "mock")
    assert "mock" in runner.invoke(args=["erp", "status"]).output
    res = runner.invoke(args=["erp", "test"])
    assert res.exit_code == 0 and "연결됨" in res.output
    assert runner.invoke(args=["erp", "send"]).exit_code == 0
    assert runner.invoke(args=["batch", "run", "cleanup_uploads"]).exit_code == 0
    monkeypatch.setattr(config, "SAP_MODE", "rest")
    monkeypatch.setattr(config, "ERP_REST_URL", "http://erp.example.com")
    res = runner.invoke(args=["erp", "test"])
    assert res.exit_code == 1 and "https" in res.output, "평문 주소는 거부"


def test_erp_screen_and_connection_test(client, monkeypatch):
    monkeypatch.setattr(config, "SAP_MODE", "mock")
    html = client.get("/sap/").get_data(as_text=True)
    assert "연결 설정" in html and "sap_odata" in html
    res = post(client, "/sap/test", follow_redirects=True)
    assert "연결 확인" in res.get_data(as_text=True)
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'ERP_TEST'") == 1

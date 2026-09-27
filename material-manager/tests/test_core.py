"""업무 로직 단위 테스트.

core 계층은 화면에 의존하지 않으므로 Flask 없이도 실행된다.

    python tests/test_core.py        # 단독 실행
    pytest tests/test_core.py        # pytest 사용 시
"""

import base64
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# config 임포트 전에 테스트 전용 DB 경로를 지정한다.
_TMP = Path(tempfile.mkdtemp(prefix="mm_test_")) / "test.db"
os.environ["MM_DB_PATH"] = str(_TMP)
os.environ["MM_PW_ITERATIONS"] = "1000"          # 테스트 속도용. 운영 기본값은 600,000

import pandas as pd  # noqa: E402

import config  # noqa: E402
from core import audit, auth, db, documents, periods, repository as repo, sap, seed, services, storage  # noqa: E402

TODAY = date.today().isoformat()


def setup_db() -> None:
    """테스트마다 빈 DB (SQLite 임시 파일 또는 MM_DATABASE_URL의 PostgreSQL 테스트 DB)."""
    config.SAP_MODE = "off"
    db.reset_database()


def mid_of(code: str) -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()["id"]


def test_stock_calculation() -> None:
    setup_db()
    seed.seed()
    expected = {"PKG-001": 35, "PKG-002": 18, "PKG-003": 170,
                "LSH-001": 50, "LSH-002": 30, "LBL-001": 1100, "SFT-001": 87}
    got = dict(zip(repo.stock_df()["code"], repo.stock_df()["stock"]))
    assert got == {k: float(v) for k, v in expected.items()}, got


def test_outbound_blocked_over_stock() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    r = services.register_transaction(mid, "OUT", 999, TODAY, 18000)
    assert not r.ok and "재고 부족" in r.message, r.message
    ok = services.register_transaction(mid, "OUT", 5, TODAY, 18000)
    assert ok.ok and ok.stock_after == 30, ok


def test_adjustment_records_difference() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    r = services.register_transaction(mid, "ADJ", 30, TODAY, 18000)
    assert r.ok and r.qty == -5 and r.stock_after == 30, r
    same = services.register_transaction(mid, "ADJ", 30, TODAY, 18000)
    assert not same.ok, "동일 수량 조정은 거부되어야 한다"


def test_safety_stock_warning() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("LSH-001")  # 재고 50, 안전재고 40
    r = services.register_transaction(mid, "OUT", 15, TODAY, 15000)
    assert r.ok and r.stock_after == 35 and r.warning, r


def test_reversal_instead_of_delete() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    with db.get_conn() as conn:
        in_id = conn.execute(
            "SELECT id FROM transactions WHERE material_id = ? AND tx_type = 'IN'", (mid,)
        ).fetchone()["id"]
        out_id = conn.execute(
            "SELECT id FROM transactions WHERE material_id = ? AND tx_type = 'OUT'", (mid,)
        ).fetchone()["id"]
    assert not services.reverse_transaction(in_id, "오입력").ok, "입고 취소 시 음수 재고 → 차단"
    assert not services.reverse_transaction(out_id, "").ok, "사유 없으면 거부"
    r = services.reverse_transaction(out_id, "출고 수량 오입력")
    assert r.ok and r.stock_after == 35 + 45, r
    assert not services.reverse_transaction(out_id, "다시").ok, "이미 취소된 건은 거부"
    assert not services.reverse_transaction(r.tx_id, "취소의 취소").ok, "취소 거래는 취소 불가"
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE id IN (?, ?)", (out_id, r.tx_id)).fetchone()[0] == 2


def test_transactions_and_audit_are_immutable() -> None:
    setup_db()
    seed.seed()
    services.register_transaction(mid_of("PKG-001"), "IN", 1, TODAY, 1, created_by="테스터")
    for sql in ("DELETE FROM transactions", "UPDATE transactions SET qty = 0",
                "DELETE FROM audit_log", "UPDATE audit_log SET user_name = 'x'"):
        try:
            with db.transaction() as conn:
                conn.execute(sql)
        except db.DBError:
            continue
        raise AssertionError(f"차단되지 않음: {sql}")
    row = db.query_df("SELECT user_name, action FROM audit_log").iloc[-1]
    assert row["user_name"] == "테스터" and row["action"] == "TX_CREATE"


def test_period_close_snapshot_and_lock() -> None:
    setup_db()
    seed.seed()
    first_ym = db.query_df("SELECT MIN(tx_date) AS d FROM transactions").iloc[0, 0][:7]
    if first_ym >= TODAY[:7]:
        return                                      # 샘플이 모두 이번 달이면 마감할 달이 없다
    before = dict(zip(repo.stock_df()["code"], repo.stock_df()["stock"]))
    assert not periods.close_month(TODAY[:7], None).ok, "진행 중인 달은 마감 불가"
    r = periods.close_month(first_ym, None)
    assert r.ok, r.message
    after = dict(zip(repo.stock_df()["code"], repo.stock_df()["stock"]))
    assert before == after, "스냅샷 + 이후 거래 = 전체 합계"
    blocked = services.register_transaction(mid_of("PKG-001"), "IN", 1, f"{first_ym}-15", 1)
    assert not blocked.ok and "마감" in blocked.message
    assert periods.reopen("재작업", None).ok
    assert services.register_transaction(mid_of("PKG-001"), "IN", 1, f"{first_ym}-15", 1).ok


def test_auth_password_and_last_admin() -> None:
    setup_db()
    assert not auth.create_user("a", "짧은아이디", "ADMIN", "Passw0rd", None).ok
    assert not auth.create_user("boss", "대표", "ADMIN", "password", None).ok, "숫자 없는 비밀번호 거부"
    admin = auth.create_user("boss", "대표", "ADMIN", "Passw0rd", None).user
    assert auth.verify_password("Passw0rd", admin["password_hash"])
    assert not auth.authenticate("boss", "wrong1234").ok
    assert auth.authenticate("BOSS", "Passw0rd").ok, "아이디는 대소문자 구분 없음"
    assert not auth.update_user(admin["id"], "대표", "CLERK", True, None).ok, "마지막 관리자 강등 불가"


def test_sap_outbox_retry_and_reversal() -> None:
    setup_db()
    seed.seed()
    config.SAP_MODE = "mock"
    try:
        mid = mid_of("PKG-001")
        assert not services.register_transaction(mid, "IN", 5, TODAY, 1).ok, "매핑 없으면 거부"
        with db.transaction() as conn:
            conn.execute("UPDATE materials SET sap_matnr = 'FAIL-1' WHERE id = ?", (mid,))
            conn.execute("UPDATE plants SET sap_plant = '1000'")
            conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
        assert not services.register_transaction(mid, "OUT", 1, TODAY, 1).ok, "출고는 원가센터 필수"

        tx = services.register_transaction(mid, "IN", 5, TODAY, 1)
        assert sap.process_outbox()["failed"] == 1, "업무 오류는 바로 FAILED"
        with db.transaction() as conn:
            conn.execute("UPDATE materials SET sap_matnr = 'PKG001' WHERE id = ?", (mid,))
        oid = int(db.query_df("SELECT id FROM sap_outbox WHERE tx_id = ?", (tx.tx_id,)).iloc[0, 0])
        assert sap.retry(oid, None)
        assert sap.process_outbox()["sent"] == 1
        sent = db.query_df("SELECT sap_doc_no, payload FROM sap_outbox WHERE id = ?", (oid,)).iloc[0]
        assert sent["sap_doc_no"].startswith("49") and "MM-TX-" in sent["payload"]

        rev = services.reverse_transaction(tx.tx_id, "입고 취소")
        assert rev.ok and "SAP 취소 전송 대기" in rev.message
        assert sap.process_outbox()["sent"] == 1
        cancel = db.query_df("SELECT payload FROM sap_outbox WHERE tx_id = ?", (rev.tx_id,)).iloc[0, 0]
        assert '"action": "CANCEL"' in cancel and sent["sap_doc_no"] in cancel and '"501"' not in cancel

        out = services.register_transaction(mid, "OUT", 1, TODAY, 1, cost_center="CC100")
        early = services.reverse_transaction(out.tx_id, "바로 취소")
        assert "둘 다 전송 안 함" in early.message, "전송 전 취소는 SAP에 보내지 않는다"
        assert sap.process_outbox() == {"sent": 0, "error": 0, "failed": 0, "waiting": 0}
    finally:
        config.SAP_MODE = "off"


def test_upload_normalization() -> None:
    setup_db()
    raw = pd.DataFrame({
        "자재코드": [" pkg-001 ", "NEW-001", "new-001", None, "BAD-001", "  "],
        "자재명": ["팔레트", "신규A", "신규A(중복)", "코드없음", None, "공백코드"],
        "단위": [None, "BOX", "  ", None, None, None],
        "안전재고": ["10", None, "abc", 5, 5, 5],
        "단가": [1000, None, "2000", -3, 1, 1],
    })
    r = services.normalize_upload(raw)
    assert r.dropped == 3, r.dropped          # 코드 없음 / 자재명 없음 / 공백 코드
    assert r.duplicated == 1, r.duplicated    # NEW-001 중복
    assert list(r.df["code"]) == ["PKG-001", "NEW-001"], list(r.df["code"])
    assert list(r.df["unit"]) == ["EA", "EA"], list(r.df["unit"])
    assert (r.df["unit_price"] >= 0).all() and (r.df["safety_stock"] >= 0).all()

    repo.upsert_materials(list(r.df.itertuples(index=False, name=None)))
    saved = repo.list_materials()
    assert len(saved) == 2 and saved["code"].notna().all()


def test_upload_missing_required_column() -> None:
    r = services.normalize_upload(pd.DataFrame({"이름": ["A"]}))
    assert r.errors and "자재코드" in r.errors[0], r.errors


def test_excel_export() -> None:
    setup_db()
    seed.seed()
    data = services.stock_display(repo.stock_df())
    blob = __import__("core.utils", fromlist=["to_excel_bytes"]).to_excel_bytes({"재고현황": data})
    assert blob[:2] == b"PK" and len(blob) > 3000


def test_empty_db_paths() -> None:
    setup_db()
    df = repo.stock_df()
    assert df.empty and "shortage" in df.columns
    assert services.material_options() == {}
    assert repo.count_materials() == 0


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
E_TAX = {"doc_type": "E_TAX_INVOICE", "issue_date": TODAY, "approval_no": "20260927-41000000-12345678",
         "supplier_biz_no": "124-81-00998", "supplier_name": "대한팔레트",
         "supply_amount": "1,000,000", "tax_amount": "100000"}


def test_biz_no_and_file_sniff() -> None:
    assert documents.valid_biz_no("1248100998")
    assert not documents.valid_biz_no("1248100990"), "검증번호 불일치"
    assert documents.sniff(PNG) == (".png", "image/png")
    assert documents.sniff(b"%PDF-1.7 ...")[1] == "application/pdf"
    assert documents.sniff(b"<svg onload=alert(1)>") is None, "SVG·HTML 거부"


def test_document_validation() -> None:
    setup_db()
    bad = documents.prepare(PNG, "a.png", {**E_TAX, "approval_no": "123", "supplier_biz_no": "124-81-00990"})
    assert not bad.ok and len(bad.errors) == 2, bad.errors
    fake = documents.prepare(b"<html>", "tax.png", E_TAX)
    assert not fake.ok and "JPG" in fake.errors[0], "확장자만 png인 HTML 거부"
    warn = documents.prepare(PNG, "a.png", {**E_TAX, "tax_amount": "50000"})
    assert warn.ok and "10%" in warn.warnings[0], warn.warnings
    paper = documents.prepare(PNG, "a.png", {"doc_type": "TAX_INVOICE", "issue_date": TODAY})
    assert not paper.ok and any("사업자등록번호" in e for e in paper.errors), paper.errors


def test_document_save_duplicate_and_tx_delete() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    tx = services.register_transaction(mid, "IN", 10, TODAY, 18000)
    saved = documents.save(documents.prepare(PNG, "세금계산서.png", {**E_TAX, "tx_id": str(tx.tx_id)}))
    assert saved.ok, saved.message
    doc = repo.get_document(saved.doc_id)
    assert storage.get().get(documents.key(doc)) == PNG
    assert doc["supply_amount"] == 1_000_000 and doc["supplier_biz_no"] == "1248100998"

    again = documents.prepare(PNG, "다른이름.png", {**E_TAX, "doc_type": "STATEMENT", "approval_no": ""})
    assert not again.ok and "이미 등록" in again.errors[0], "같은 파일 중복 거부"

    assert services.reverse_transaction(tx.tx_id, "취소").ok
    assert int(repo.get_document(saved.doc_id)["tx_id"]) == tx.tx_id, "거래를 취소해도 증빙 연결은 남는다"

    assert documents.delete(saved.doc_id).ok
    assert not documents.exists(doc), "증빙 삭제 시 파일도 삭제"


def test_sap_http_client() -> None:
    """사내 연계서버 흉내: 첫 요청은 500(재시도), 다음은 성공. 400은 바로 FAILED."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("Idempotency-Key"), self.headers.get("Authorization")))
            code = 500 if len(seen) == 1 else (400 if body.get("costCenter") == "BAD" else 200)
            payload = {"materialDocument": "5000000001", "year": "2026"} if code == 200 else {"error": "x"}
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    setup_db()
    seed.seed()
    config.SAP_MODE, config.SAP_ENDPOINT, config.SAP_TOKEN = "http", f"http://127.0.0.1:{server.server_port}", "tkn"
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE materials SET sap_matnr = 'M1'")
            conn.execute("UPDATE plants SET sap_plant = '1000'")
            conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
        tx = services.register_transaction(mid_of("PKG-001"), "IN", 1, TODAY, 1)
        assert sap.process_outbox()["error"] == 1, "500은 자동 재시도 대상"
        with db.transaction() as conn:
            conn.execute("UPDATE sap_outbox SET next_try_at = '' WHERE tx_id = ?", (tx.tx_id,))
        assert sap.process_outbox()["sent"] == 1
        assert seen[-1] == ("/goods-movements", f"MM-TX-{tx.tx_id}", "Bearer tkn"), seen
        services.register_transaction(mid_of("PKG-001"), "OUT", 1, TODAY, 1, cost_center="BAD")
        assert sap.process_outbox()["failed"] == 1, "400은 사람이 고쳐야 한다"
    finally:
        server.shutdown()
        config.SAP_MODE, config.SAP_ENDPOINT, config.SAP_TOKEN = "off", "", ""


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

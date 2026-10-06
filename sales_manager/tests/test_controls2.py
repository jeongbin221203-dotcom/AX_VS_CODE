"""자재관리와 비교해 옮긴 통제 2차 (2026-10-06): 초기 설정 코드 · 증빙 무효 직무 분리·열람 감사·응답 헤더 · 월 마감 점검·잠금 ·
변경 이력 패널 · 증빙 파일 백업·복원 · 데이터 점검 항목 · 목표 동시 수정."""
from __future__ import annotations

import io
import json
from datetime import date, timedelta

import pytest
from conftest import login, post, user

import config
from core import auth as core_auth
from core import backup_files
from core import documents as docs
from core import periods
from core import quality
from core import sales_db as db


TODAY = date.today().isoformat()


def _cust(name: str) -> int:
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"]})


def _sale(cid: int, unit: int = 100_000) -> int:
    db.set_context("system", None)
    return db.upsert_sale({"customer_id": cid, "item": "통제2", "qty": 1, "unit_price": unit,
                           "owner_id": user("김영업")["id"], "sale_date": TODAY})


def _png() -> bytes:
    import struct
    import zlib
    raw = b"\x00\xff\x00\x00"
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _doc(sid: int, uploader: dict) -> int:
    doc_id, _w = docs.add_document(sid, {"doc_type": "거래명세서"}, _png(), "t.png", uploader)
    return doc_id


def test_setup_code_helpers(monkeypatch):
    core_auth.retire_setup_code()
    monkeypatch.setenv("SALES_SETUP_CODE", "abc123")
    assert core_auth.setup_code() == "abc123"
    assert core_auth.check_setup_code("ABC123") and not core_auth.check_setup_code("") and not core_auth.check_setup_code("nope")
    core_auth.retire_setup_code()


def test_void_needs_manager_and_not_the_uploader(app):
    sid = _sale(_cust("무효직무상사"))
    uploader = user("김영업")
    did = _doc(sid, uploader)
    with pytest.raises(PermissionError, match="팀장 이상"):
        docs.void_document(did, "실수", uploader)                      # 영업사원
    mgr = user("한팀장")
    did2 = _doc(sid, mgr)
    with pytest.raises(PermissionError, match="본인이 올린"):
        docs.void_document(did2, "실수", mgr)                          # 올린 팀장 본인
    docs.void_document(did, "다른 팀장 처리", mgr)
    assert db._one("SELECT voided_at FROM sale_documents WHERE id=?", [did])["voided_at"]


def test_inline_view_audited_once_and_headers(app):
    sid = _sale(_cust("열람감사상사"))
    did = _doc(sid, user("김영업"))
    rep = login(app, "김영업")
    first = rep.get(f"/documents/{did}/file")
    assert first.status_code == 200
    assert first.headers["Content-Security-Policy"].endswith("sandbox") and "no-store" in first.headers["Cache-Control"]
    rep.get(f"/documents/{did}/file")
    assert db._scalar("SELECT COUNT(*) FROM audit_log WHERE action='증빙열람' AND entity_id=?", [did]) == 1   # 30분 안 중복은 한 번
    assert "camera=()" in rep.get("/").headers["Permissions-Policy"]


def test_month_close_checks_block_and_lock(app):
    ym = periods.next_closable()
    if ym >= date.today().strftime("%Y-%m"):
        pytest.skip("마감할 수 있는 지난 달이 없음")
    checks = {c["key"]: c for c in periods.close_checks(ym)}
    assert {"pending_requests", "etax_in_flight", "tax_docs_missing", "paid_mismatch"} <= set(checks)
    assert checks["pending_requests"]["level"] == "block" and checks["tax_docs_missing"]["level"] == "warn"


def test_entity_history_shows_field_changes(app):
    cid = _cust("이력상사")
    db.set_context("김영업", None, user("김영업")["id"])
    c = db.get_customer(cid)
    db.upsert_customer({**c, "credit_limit": 5_000_000, "row_version": c.get("row_version")})
    rows = db.entity_history("거래처", cid)
    changed = [ch for r in rows for ch in r["changes"]]
    assert any(ch["field"] == "여신한도" and ch["after"] == "5,000,000" for ch in changed)
    rep = login(app, "김영업")
    assert "변경 이력" in rep.get(f"/customers?tab=edit&id={cid}").get_data(as_text=True)


def test_document_files_backup_and_restore(app, tmp_path):
    sid = _sale(_cust("파일백업상사"))
    did = _doc(sid, user("김영업"))
    res = backup_files.mirror_files(tmp_path)
    assert res["복사"] >= 1 and res["불일치"] == 0
    assert backup_files.mirror_files(tmp_path)["복사"] == 0              # 두 번째는 새 파일만
    assert backup_files.missing_in_backup(tmp_path) == 0
    key = docs.storage_key(db._one("SELECT file_path FROM sale_documents WHERE id=?", [did])["file_path"])
    from core.storage import get_storage
    get_storage().delete(key)                                            # 저장소에서 파일을 잃은 상황
    assert backup_files.restore_files(tmp_path)["복원"] >= 1
    assert docs.get_document(did, with_data=True)["intact"]


def test_new_quality_checks_run(app):
    results = {r["key"]: r for r in quality.run_all()}
    for key in ("approval_overdue", "approver_no_contact", "product_price_zero", "tax_docs_missing",
                "delivery_over_order", "inactive_product_in_open"):
        assert key in results and results[key]["count"] >= 0


def test_target_save_detects_concurrent_change(app):
    owner = user("김영업")["id"]
    ym = date.today().strftime("%Y-%m")
    db.set_context("system", None)
    db.upsert_target(ym, owner, 10_000_000)
    mgr = login(app, "한팀장")
    post(mgr, "/targets/save", {"owner_id": [str(owner)], "amount": ["20000000"], "expected": ["10000000"]})
    assert int(db._scalar("SELECT target_amount FROM targets WHERE yyyymm=? AND owner_key=?", [ym, str(owner)])) == 20_000_000
    post(mgr, "/targets/save", {"owner_id": [str(owner)], "amount": ["30000000"], "expected": ["10000000"]})   # 낡은 화면
    assert int(db._scalar("SELECT target_amount FROM targets WHERE yyyymm=? AND owner_key=?", [ym, str(owner)])) == 20_000_000

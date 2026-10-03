"""거래명세서 입출고: 줄 읽기(엑셀 별칭·합계 줄) · 자재 찾기 · 금액 확인 · 한 트랜잭션 등록(한 줄 거부 시 전부 취소) ·
중복 명세서 · 화면 흐름(미리보기 → 자재 고르기 → 등록 → 상세) · 명세서 파일 첨부."""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_stmt_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from core import db, excel_forms, seed, services, statements  # noqa: E402
from test_advanced import M1, fresh, mid, wh  # noqa: E402,F401
from test_app import PNG, app, client, csrf, post  # noqa: E402,F401

TODAY = date.today().isoformat()


def stock(code: str) -> float:
    with db.get_conn() as conn:
        from core import repository as repo
        return repo.current_stock(conn, mid(code), wh("WH1"))


def header(kind="IN", no="TS-0001", partner="대한팔레트"):
    return {"kind": kind, "warehouse_id": str(wh("WH1")), "tx_date": TODAY, "partner": partner,
            "partner_biz_no": "", "statement_no": no, "cost_center": "", "note": ""}


def frame(rows):
    return pd.DataFrame(rows, columns=["자재코드", "품명", "규격", "수량", "단가", "공급가액", "세액"])


def test_excel_aliases_header_row_and_total_rows(fresh):
    df = pd.DataFrame([["거래명세서", None, None, None, None, None],          # 제목 줄
                       ["품번", "품목명", "Qty", "단위가격", "금액", "부가세"],   # 머리글 (2행, 별칭)
                       ["PKG-001", "수출용 목재 팔레트", 10, 18000, 180000, 18000],
                       [None, "스트레치 필름", "2", "9,500", "19,000", "1,900"],
                       [None, "합계", None, None, 199000, 19900]])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, header=False)
    with db.transaction() as conn:
        excel_forms._save(conn, "statement_lines", {"aliases": {}, "sheet": "", "header_row": 2}, None, None)
    raw, problem = excel_forms.read_import("statement_lines", buf.getvalue(), "s.xlsx", 100)
    assert not problem
    lines, errors = statements.lines_from_frame(raw)
    assert not errors and len(lines) == 2, [ln.name for ln in lines]
    statements.check("IN", lines)
    assert [ln.material_id for ln in lines] == [mid("PKG-001"), mid("PKG-002")], "코드 · 품명으로 자재 찾기"
    assert all(not ln.errors and not ln.warnings for ln in lines), [ln.errors + ln.warnings for ln in lines]


def test_amount_checks(fresh):
    lines, _ = statements.lines_from_frame(frame([
        ["PKG-001", "", "", 10, 18000, 190000, 18000],         # 공급가액 틀림
        ["PKG-002", "", "", 4, None, 38000, 5000],             # 단가 없음 → 계산, 세액 다름(경고)
        ["NOPE-9", "없는 자재", "", 1, 100, None, None],        # 자재 없음
        ["PKG-003", "", "", 0, 100, None, None]]))             # 수량 0
    statements.check("IN", lines)
    assert "≠ 수량×단가" in lines[0].errors[0]
    assert not lines[1].errors and lines[1].unit_price == 9500 and any("10%" in w for w in lines[1].warnings)
    assert "찾지 못했습니다" in lines[2].errors[0]
    assert any("0보다" in e for e in lines[3].errors)


def test_register_all_or_nothing_and_duplicate(fresh):
    before = (stock("PKG-001"), stock("PKG-002"), int(db.scalar("SELECT COUNT(*) FROM transactions")))
    lines, _ = statements.lines_from_frame(frame([["PKG-001", "", "", 5, 18000, None, None],
                                                  ["PKG-002", "", "", 99999, 9500, None, None]]))   # 재고보다 많은 출고
    statements.check("OUT", lines)
    r = statements.register(header("OUT", "OUT-1", "샘플고객"), lines, actor=M1)
    assert not r.ok and r.failed_line == 2 and "아무것도 등록하지" in r.message
    assert (stock("PKG-001"), stock("PKG-002"), int(db.scalar("SELECT COUNT(*) FROM transactions"))) == before
    assert int(db.scalar("SELECT COUNT(*) FROM statements")) == 0, "명세서 머리도 남지 않는다"

    lines, _ = statements.lines_from_frame(frame([["PKG-001", "", "", 20, 18000, 360000, 36000],
                                                  ["PKG-002", "", "", 10, 9500, 95000, 9500]]))
    statements.check("IN", lines)
    r = statements.register(header(), lines, actor=M1)
    assert r.ok, r.message
    assert stock("PKG-001") == before[0] + 20 and stock("PKG-002") == before[1] + 10
    st = statements.get(r.statement_id)
    assert (st["line_count"], st["supply_amount"], st["tax_amount"]) == (2, 455000, 45500)
    assert len(statements.lines_df(r.statement_id)) == 2
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'STATEMENT_CREATE'") == 1
    again = statements.register(header(partner=" 대한팔레트 "), lines, actor=M1)
    assert not again.ok and "이미 등록한" in again.message, "같은 거래처·번호는 한 번만 (앞뒤 공백 무시)"


def test_name_match_needs_unique_material(fresh):
    assert services.create_material({"code": "PKG-101", "name": "수출용 목재 팔레트", "spec": "1200x1000"}).ok
    lines, _ = statements.lines_from_frame(frame([[None, "수출용 목재 팔레트", None, 1, 100, None, None],
                                                  [None, "수출용 목재 팔레트", "1200x1000", 1, 100, None, None]]))
    statements.check("IN", lines)
    assert "2개" in lines[0].errors[0], "이름만으로는 고를 수 없다"
    assert lines[1].material_id == mid("PKG-101"), "규격까지 같으면 하나"
    statements.check("IN", lines, {1: mid("PKG-001")})
    assert lines[0].material_id == mid("PKG-001") and not lines[0].errors, "화면에서 고른 자재"


def test_lot_material_needs_lot_on_receipt(fresh):
    assert services.create_material({"code": "CHM-1", "name": "방청제", "lot_managed": 1, "expiry_managed": 1}).ok
    lines, _ = statements.lines_from_frame(pd.DataFrame([
        {"자재코드": "CHM-1", "수량": 3, "단가": 100},
        {"자재코드": "CHM-1", "수량": 2, "단가": 100, "로트": "l-9", "유효기한": "2099-12-31"}]))
    statements.check("IN", lines)
    assert "로트" in lines[0].errors[0] and not lines[1].errors and lines[1].lot_no == "L-9"


def test_screen_flow_preview_choose_register(client):
    seed.seed()
    page = client.get("/statements/").get_data(as_text=True)
    assert "명세서 머리" in page and 'name="code_1"' in page
    form = {"_csrf": csrf(client), "kind": "IN", "warehouse_id": str(wh("WH1")), "tx_date": TODAY,
            "partner": "대한팔레트", "statement_no": "DH-2026-77", "code_1": "PKG-001", "qty_1": "12", "unit_price_1": "18000",
            "name_2": "알 수 없는 품목", "qty_2": "3", "unit_price_2": "9500",
            "doc_file": (io.BytesIO(PNG), "statement.png")}
    res = client.post("/statements/preview", data=form, content_type="multipart/form-data")
    html = res.get_data(as_text=True)
    assert res.status_code == 200 and "찾지 못했습니다" in html and "statement.png" in html
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    before = int(db.scalar("SELECT COUNT(*) FROM transactions"))

    res = post(client, "/statements/apply", {"token": token})                      # 자재를 안 고름 → 다시 확인
    assert res.status_code == 200 and int(db.scalar("SELECT COUNT(*) FROM transactions")) == before
    res = post(client, "/statements/apply", {"token": token, "material_1": "PKG-001", "material_2": "NOPE"})
    assert "사용 중인 자재가 아닙니다" in res.get_data(as_text=True), "없는 코드를 적으면 다시 확인"
    res = post(client, "/statements/apply", {"token": token, "material_1": "PKG-001", "material_2": "pkg-002"})
    assert res.status_code == 302 and "/statements/" in res.headers["Location"]
    assert int(db.scalar("SELECT COUNT(*) FROM transactions")) == before + 2
    sid = int(db.scalar("SELECT MAX(id) FROM statements"))
    assert db.scalar("SELECT doc_id FROM statements WHERE id = ?", (sid,)), "명세서 파일 첨부"
    assert db.scalar("SELECT doc_type FROM documents ORDER BY id DESC LIMIT 1") == "STATEMENT"
    detail = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "DH-2026-77" in detail and "PKG-002" in detail
    assert post(client, "/statements/apply", {"token": token}).status_code == 400, "같은 미리보기로 두 번 등록 불가"
    form2 = {"_csrf": csrf(client), "kind": "IN", "warehouse_id": str(wh("WH1")), "tx_date": TODAY, "partner": "무번호상사",
             "code_1": "PKG-003", "qty_1": "5", "unit_price_1": "1200"}
    html = client.post("/statements/preview", data=form2, content_type="multipart/form-data").get_data(as_text=True)
    tok = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    assert post(client, "/statements/apply", {"token": tok, "material_1": "PKG-003"}).status_code == 302
    html = client.post("/statements/preview", data={**form2, "_csrf": csrf(client)},
                       content_type="multipart/form-data").get_data(as_text=True)
    assert "이미 있습니다" in html, "번호 없는 명세서 중복 경고"
    assert "DH-2026-77" in client.get("/statements/?tab=list").get_data(as_text=True)
    assert client.get("/statements/template.xlsx").status_code == 200


def test_scoped_user_cannot_use_other_warehouse(app):
    from core import auth, org
    from test_app import login
    seed.seed()
    assert org.create_plant("P2", "평택", "", None).ok
    pid = int(db.scalar("SELECT id FROM plants WHERE code = 'P2'"))
    assert org.create_warehouse(pid, "WH2", "평택 창고", "", None).ok
    uid = int(db.scalar("SELECT id FROM users WHERE username = 'clerk'"))
    assert org.set_user_scope(uid, False, [], [wh("WH2")], None).ok
    c = login(app.test_client(), "clerk")
    res = c.post("/statements/preview", data={"_csrf": csrf(c), "kind": "IN", "warehouse_id": str(wh("WH1")),
                                               "tx_date": TODAY, "partner": "X", "code_1": "PKG-001", "qty_1": "1",
                                               "unit_price_1": "1"}, content_type="multipart/form-data")
    assert "권한이 있는 창고만" in res.get_data(as_text=True)


# ── 명세서 전체 취소 ─────────────────────────────────────────
def _registered(kind="IN", no="C-1", rows=None, who=M1):
    lines, _ = statements.lines_from_frame(frame(rows or [["PKG-001", "", "", 10, 18000, None, None],
                                                          ["PKG-002", "", "", 4, 9500, None, None]]))
    statements.check(kind, lines)
    r = statements.register(header(kind, no), lines, actor=who)
    assert r.ok, r.message
    return r.statement_id


def test_cancel_whole_statement_all_or_nothing(fresh):
    from test_advanced import M2
    before = (stock("PKG-001"), stock("PKG-002"))
    sid = _registered()
    r = statements.cancel(sid, "오발행", actor=M1)
    assert not r.ok and "다른 관리자" in r.message, "본인이 등록한 명세서는 다른 관리자가 취소"
    assert services.register_transaction(mid("PKG-002"), "OUT", before[1] + 3, TODAY, 9500, actor=M2).ok   # 받은 것보다 더 출고
    r = statements.cancel(sid, "오발행", actor=M2)
    assert not r.ok and "아무것도 취소하지" in r.message, "한 줄이라도 재고가 모자라면 전체 거부"
    assert stock("PKG-001") == before[0] + 10, "앞 줄도 되돌리지 않음"
    assert services.register_transaction(mid("PKG-002"), "IN", 10, TODAY, 9500, actor=M2).ok
    r = statements.cancel(sid, "오발행", actor=M2)
    assert r.ok, r.message
    assert stock("PKG-001") == before[0]
    st = statements.get(sid)
    assert st["cancelled_at"] and st["cancel_reason"] == "오발행"
    assert not statements.cancel(sid, "다시", actor=M2).ok, "두 번 취소 불가"
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'STATEMENT_CANCEL'") == 1


def test_cancel_skips_lines_already_reversed(fresh):
    from test_advanced import M2
    sid = _registered(no="C-2")
    first = int(db.scalar("SELECT MIN(id) FROM transactions WHERE statement_id = ?", (sid,)))
    assert services.reverse_transaction(first, "한 줄만", actor=M2).ok
    r = statements.cancel(sid, "나머지", actor=M2)
    assert r.ok and "취소 거래 1건" in r.message, r.message


# ── PDF · 스캔에서 읽기 ──────────────────────────────────────
def _pdf(table: bool, scanned: bool = False) -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    rows = [["품번", "품명", "수량", "단가", "공급가액", "세액"],
            ["PKG-001", "목재 팔레트", "10", "18,000", "180,000", "18,000"],
            ["PKG-002", "스트레치 필름", "2", "9,500", "19,000", "1,900"],
            ["", "합계", "", "", "199,000", "19,900"]]
    x0, y0, widths, h = 40, 80, [70, 140, 50, 70, 80, 70], 24
    if scanned:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), 0)
        pix.clear_with(255)
        page.insert_image(fitz.Rect(40, 40, 240, 140), pixmap=pix)
        return doc.tobytes()
    page.insert_text((x0, 60), "거 래 명 세 서   No. DH-77", fontname="korea", fontsize=12)
    for r, row in enumerate(rows):
        x = x0
        for c, w in enumerate(widths):
            if table:
                page.draw_rect(fitz.Rect(x, y0 + r * h, x + w, y0 + (r + 1) * h), color=(0, 0, 0), width=0.7)
            page.insert_text((x + 3, y0 + r * h + 16), row[c], fontname="korea", fontsize=9)
            x += w
    return doc.tobytes()


def test_pdf_table_and_text_are_read(fresh):
    from core import statement_reader
    for table, method in ((True, "pdf-table"), (False, "pdf-text")):
        lines, errors, how = statement_reader.read(_pdf(table), "명세서.pdf")
        assert not errors and how == method, (errors, how)
        assert [(ln.code, ln.qty, ln.unit_price, ln.supply) for ln in lines] == \
            [("PKG-001", 10, 18000, 180000), ("PKG-002", 2, 9500, 19000)], [ln.to_dict() for ln in lines]
        statements.check("IN", lines)
        assert all(not ln.errors for ln in lines) and "확인하세요" in lines[0].warnings[0]


def test_scan_needs_ocr_and_ocr_text_is_parsed(fresh, monkeypatch):
    from core import statement_reader
    monkeypatch.setattr(statement_reader, "ocr_available", lambda: False)
    lines, errors, _ = statement_reader.read(_pdf(False, scanned=True), "scan.pdf")
    assert not lines and "Tesseract" in errors[0], "OCR이 없으면 읽지 않고 안내"
    lines, errors, _ = statement_reader.read(PNG, "scan.png")
    assert not lines and "Tesseract" in errors[0]

    monkeypatch.setattr(statement_reader, "ocr_available", lambda: True)
    monkeypatch.setattr(statement_reader, "_ocr_text", lambda images: (
        "거래명세서 No.DH-78\n품번 품명 수량 단가 공급가액 세액\n1 PKG-001 목재 팔레트 10 18,000 180,000 18,000\n"
        "2 스트레치 필름 2 9,500 19,000 1,900\n합 계 199,000 19,900\n"))
    lines, errors, how = statement_reader.read(PNG, "scan.png")
    assert how == "ocr" and not errors and len(lines) == 2
    assert (lines[0].code, lines[0].qty, lines[1].name, lines[1].unit_price) == ("PKG-001", 10, "스트레치 필름", 9500)
    statements.check("IN", lines)
    assert lines[1].material_id == mid("PKG-002") and "OCR" in lines[1].warnings[0]


def test_screen_reads_pdf_and_allows_two_previews(client):
    seed.seed()
    base = {"kind": "IN", "warehouse_id": str(wh("WH1")), "tx_date": TODAY, "partner": "대한팔레트"}
    res = client.post("/statements/preview", data={**base, "_csrf": csrf(client), "statement_no": "P-1",
                                                     "doc_file": (io.BytesIO(_pdf(True)), "명세서.pdf")},
                      content_type="multipart/form-data")
    html = res.get_data(as_text=True)
    assert "PDF의 표에서 품목을 읽었습니다" in html and "PKG-002" in html, "품목 없이 명세서 파일만 올려도 읽음"
    tok1 = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    res = client.post("/statements/preview", data={**base, "_csrf": csrf(client), "statement_no": "P-2",
                                                     "code_1": "PKG-003", "qty_1": "1", "unit_price_1": "1200"},
                      content_type="multipart/form-data")
    tok2 = re.search(r'name="token" value="([0-9a-f]+)"', res.get_data(as_text=True)).group(1)
    assert post(client, "/statements/apply", {"token": tok1, "material_1": "PKG-001", "material_2": "PKG-002"}).status_code == 302
    assert post(client, "/statements/apply", {"token": tok2, "material_1": "PKG-003"}).status_code == 302, "먼저 연 미리보기도 유효"
    assert db.scalar("SELECT COUNT(*) FROM statements") == 2
    sid = int(db.scalar("SELECT id FROM statements WHERE statement_no = 'P-1'"))
    assert db.scalar("SELECT doc_id FROM statements WHERE id = ?", (sid,)), "읽은 PDF는 증빙으로도 첨부"
    page = client.get(f"/statements/{sid}").get_data(as_text=True)
    assert "명세서 전체 취소" in page


# ── 제조 샘플 ────────────────────────────────────────────────
def test_manufacturing_sample(client):
    from core import insights, repository as repo, seed_mfg
    seed.seed()
    res = post(client, "/data/seed-mfg")
    assert res.status_code == 302
    assert seed_mfg.exists() and int(db.scalar("SELECT COUNT(*) FROM materials WHERE code LIKE 'RM-%'")) == 4
    assert post(client, "/data/seed-mfg").status_code == 302 and \
        int(db.scalar("SELECT COUNT(*) FROM plants WHERE code = 'P-CW'")) == 1, "한 번만"
    neg = db.query_df("SELECT material_id, warehouse_id, lot_no FROM transactions GROUP BY 1,2,3 "
                      "HAVING SUM(CASE WHEN tx_type='OUT' THEN -qty ELSE qty END) < -1e-9")
    assert neg.empty, "음수 재고 없음"
    assert len(insights.monthly_amounts(12).query("출고금액 > 0")) == 12, "12개월 추이"
    lots = repo.stock_by_lot()
    assert ((lots["days_left"] < 0) & (lots["stock"] > 0)).any() and ((lots["days_left"].between(0, 30)) & (lots["stock"] > 0)).any()
    assert db.scalar("SELECT COUNT(*) FROM statements") == 3 and insights.incoming_po().iloc[0]["상태"] == "지연"
    assert "제조 샘플은 이미" in client.get("/data/").get_data(as_text=True) or "이미 추가되어" in client.get("/data/").get_data(as_text=True)
    assert client.get("/").status_code == 200

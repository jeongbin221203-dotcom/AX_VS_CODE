"""회사 엑셀 양식 — 회사 파일 그대로 업로드 · 회사 서식 파일로 내려받기."""
from __future__ import annotations

import io
import re

from conftest import login, post, user
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Font, PatternFill, Side

from core import excel_forms as xf
from core import sales_db as db


def _company_sales_file() -> bytes:
    """ERP 에서 뽑은 듯한 매출집계표: 제목·결재란 → 4행 머리글 → 병합된 상호 → 합계 행."""
    wb = Workbook()
    ws = wb.active
    ws.title = "매출집계"
    ws["A1"] = "2026년 9월 매출집계표"
    ws["H1"], ws["I1"], ws["J1"] = "담당", "팀장", "본부장"
    ws.append([])
    ws.append(["No", "전표일자", "상호", "품번", "품명", "수량", "판매단가", "공급가액", "과세코드", "영업담당", "비고"])
    kim = user("김영업")["emp_no"]
    ws.append([1, "2026.09.03", "양식상사", "SW-ERP-01", "ERP 라이선스", 2, 1_000_000, 2_000_000, "01", kim, ""])
    ws.append([2, "2026-09-05", None, "SV-EDU-01", "사용자 교육", 1, 900_000, 900_000, "03", kim, "병합 셀"])
    ws.append([None, None, "합계", None, None, 3, None, 2_900_000, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_company_import_form_wizard_and_upload(app):
    rep = login(app, "김영업")
    post(rep, "/customers/save", {"name": "양식상사", "grade": "A", "industry": "제조"})
    admin = login(app, "시스템관리자")
    assert rep.get("/data/forms").status_code == 403                        # 양식 등록은 관리자만

    data = _company_sales_file()
    res = post(admin, "/data/forms/inspect", {"direction": "import", "entity_import": "매출",
                                              "entity_export": "매출", "name": "본사 매출집계표",
                                              "file": (io.BytesIO(data), "매출집계.xlsx")},
               content_type="multipart/form-data")
    page = res.get_data(as_text=True)
    assert res.status_code == 200 and "머리글 3행" in page                  # 제목·결재란을 건너뛰고 찾음
    info = xf.inspect_file(data, "매출집계.xlsx")
    suggested = {m["column"]: m["field"] for m in xf.suggest_mapping(info["headers"], xf.system_fields("import", "매출"))}
    assert suggested["상호"] == "거래처명" and suggested["품명"] == "품목" and suggested["공급가액"] == "금액"
    assert suggested["영업담당"] == "담당자" and suggested["비고"] == "메모" and suggested["No"] == ""

    token = re.search(r'name="sample_token" value="([0-9a-f]+)"', page).group(1)
    mapping = {"전표일자": "매출일", "상호": "거래처명", "품번": "품목코드", "품명": "품목", "수량": "수량",
               "판매단가": "단가", "공급가액": "금액", "과세코드": "과세구분", "영업담당": "담당자", "비고": "메모"}
    res = post(admin, "/data/forms/save", {
        "name": "본사 매출집계표", "direction": "import", "entity": "매출", "sheet_name": "매출집계",
        "header_row": "3", "map_column": list(mapping), "map_field": list(mapping.values()),
        "value_map": "과세구분: 01=과세, 02=영세, 03=면세", "fill_down": ["거래처명"],
        "stop_words": "합계, 소계", "sample_token": token, "use_sample": "1"})
    assert res.status_code == 302
    form = next(f for f in xf.list_forms("import", "매출") if f["name"] == "본사 매출집계표")
    assert form["template_key"] and form["value_map"]["과세구분"]["03"] == "면세"

    # 필수 항목(담당자) 누락 매핑은 저장되지 않는다
    bad = post(admin, "/data/forms/save", {"name": "잘못된양식", "direction": "import", "entity": "매출",
                                           "header_row": "3", "map_column": ["상호"], "map_field": ["거래처명"]},
               follow_redirects=True)
    assert "필수 항목이 매핑되지 않았습니다" in bad.get_data(as_text=True)

    # 담당자가 회사 파일을 그대로 올린다
    assert "본사 매출집계표 (회사 양식)" in rep.get("/data/?entity=매출").get_data(as_text=True)
    blank = rep.get(f"/data/template/form/{form['id']}.xlsx")
    assert blank.status_code == 200 and load_workbook(io.BytesIO(blank.data)).active["A1"].value == "2026년 9월 매출집계표"
    res = post(rep, "/data/import/check", {"entity": "매출", "form_id": str(form["id"]),
                                           "file": (io.BytesIO(data), "9월매출.xlsx")},
               content_type="multipart/form-data")
    page = res.get_data(as_text=True)
    assert res.status_code == 200 and "본사 매출집계표" in page and "등록 가능" in page
    post(rep, "/data/import/run", {"confirm": "1"})
    rows = db._df("SELECT s.item, s.sale_date, s.amount, s.tax_type, s.vat_amount, s.customer_id FROM sales s "
                  "JOIN customers c ON c.id = s.customer_id WHERE c.name='양식상사' ORDER BY s.sale_date")
    assert list(rows["tax_type"]) == ["과세", "면세"] and list(rows["vat_amount"]) == [200_000, 0]
    assert rows["customer_id"].nunique() == 1                               # 병합된 상호를 이어 씀
    assert list(rows["sale_date"]) == ["2026-09-03", "2026-09-05"]          # 합계 행은 들어오지 않음


def test_company_import_form_reports_original_row_numbers(app):
    admin = login(app, "시스템관리자")
    form = next(f for f in xf.list_forms("import", "매출") if f["name"] == "본사 매출집계표")
    wb = load_workbook(io.BytesIO(_company_sales_file()))
    wb.active["F4"] = "abc"                                                  # 4행 수량 오류
    buf = io.BytesIO()
    wb.save(buf)
    res = post(admin, "/data/import/check", {"entity": "매출", "form_id": str(form["id"]),
                                             "file": (io.BytesIO(buf.getvalue()), "오류.xlsx")},
               content_type="multipart/form-data")
    csv = admin.get("/data/import/errors.csv").get_data().decode("utf-8-sig")
    assert res.status_code == 200 and "\n4," in csv                          # 원래 엑셀 행 번호


def _company_report_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "보고"
    ws["A1"] = "영업 매출 보고 ({{기간}})"
    ws["A1"].font = Font(bold=True, size=16)
    ws["E1"] = "작성: {{추출자}}"
    ws.append([])
    ws.append(["일자", "고객사", "품명", "공급가액", "부가세", "합계금액"])
    thin = Side(style="thin")
    for col in range(1, 7):                                                  # 4행: 데이터 행 서식
        cell = ws.cell(row=4, column=col)
        cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        cell.fill = PatternFill("solid", fgColor="FFF2CC")
    ws["A5"] = "합계"
    ws["D5"] = "{{합계:공급가액}}"
    ws["F5"] = "{{합계:합계금액}}"
    ws["A7"] = "※ 본 자료는 사내용입니다. 건수 {{건수}}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_company_export_template_filled(app):
    admin = login(app, "시스템관리자")
    tpl = _company_report_template()
    res = post(admin, "/data/forms/inspect", {"direction": "export", "entity_import": "매출",
                                              "entity_export": "매출", "name": "월간 매출 보고",
                                              "file": (io.BytesIO(tpl), "보고양식.xlsx")},
               content_type="multipart/form-data")
    page = res.get_data(as_text=True)
    token = re.search(r'name="sample_token" value="([0-9a-f]+)"', page).group(1)
    mapping = {"일자": "매출일", "고객사": "거래처", "품명": "품목", "공급가액": "공급가액", "부가세": "부가세",
               "합계금액": "합계"}
    assert post(admin, "/data/forms/save", {
        "name": "월간 매출 보고", "direction": "export", "entity": "매출", "sheet_name": "보고", "header_row": "3",
        "data_start_row": "4", "map_column": list(mapping), "map_field": list(mapping.values()),
        "sample_token": token, "use_sample": "1"}).status_code == 302
    form = next(f for f in xf.list_forms("export") if f["name"] == "월간 매출 보고")

    rep = login(app, "김영업")
    res = rep.get(f"/data/export?download=form&form_id={form['id']}&from=2026-09-01&to=2026-09-30")
    assert res.status_code == 200
    ws = load_workbook(io.BytesIO(res.data))["보고"]
    # 표준 추출과 같은 행 (취소 건도 '취소' 상태로 함께 나간다)
    expected = db._df("SELECT amount, total_amount FROM sales WHERE owner_id=? AND sale_date BETWEEN ? AND ?",
                      [user("김영업")["id"], "2026-09-01", "2026-09-30"])
    n = len(expected)
    assert n >= 2
    assert ws["A1"].value == "영업 매출 보고 (2026-09-01 ~ 2026-09-30)" and ws["E1"].value == "작성: 김영업"
    assert ws["A4"].value and ws.cell(row=3 + n, column=2).value                # 4행부터 n행
    assert ws.cell(row=3 + n, column=4).fill.fgColor.rgb.endswith("FFF2CC")     # 데이터 행 서식 복사
    assert ws.cell(row=4 + n, column=1).value == "합계"                          # 합계 줄이 아래로 밀림
    assert ws.cell(row=4 + n, column=4).value == int(expected["amount"].sum())
    assert ws.cell(row=4 + n, column=6).value == int(expected["total_amount"].sum())
    assert ws.cell(row=6 + n, column=1).value.endswith(f"건수 {n:,}")
    assert db._one("SELECT id FROM audit_log WHERE action='다운로드' ORDER BY id DESC")                # 다운로드 기록

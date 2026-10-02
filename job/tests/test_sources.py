"""출처 응답 해석 (저장해 둔 응답으로, 네트워크 없이)."""
import json

import pytest

from core.sources import SourceError, fileimport, saramin, wanted, work24


def test_saramin(fixture_text):
    items, total = saramin.parse(json.loads(fixture_text("saramin.json")))
    assert total == 2
    a, b = items
    assert (a["company"], a["sido"], a["sigungu"]) == ("테스트소프트", "서울", "강남구")
    assert (a["career_type"], a["career_min"]) == ("경력", 3)
    assert (a["salary_min"], a["salary_max"], a["education"]) == (4000, 4500, "대졸")
    assert a["deadline"] and a["keywords"] == "Python, Django, AWS"
    assert b["career_type"] == "신입" and b["salary_negotiable"] == 1 and b["deadline"] is None   # 상시


def test_saramin_error():
    with pytest.raises(SourceError):
        saramin.parse({"code": 3, "message": "Invalid access-key"})


def test_work24(fixture_text):
    items, total = work24.parse(fixture_text("work24.xml"))
    (p,) = items
    assert total == 1
    assert (p["sido"], p["sigungu"]) == ("충북", "청주시 흥덕구")
    assert (p["salary_min"], p["salary_max"]) == (3000, 3600)          # 월 250~300 → 연 환산
    assert (p["career_type"], p["education"], p["employment_type"]) == ("무관", "고졸", "정규직")
    assert p["deadline"] == "2026-10-31"


def test_work24_error():
    with pytest.raises(SourceError):
        work24.parse("<error>인증키 오류</error>")


def test_wanted(fixture_text):
    a, b = wanted.parse(json.loads(fixture_text("wanted.json")))
    assert a["url"] == "https://www.wanted.co.kr/wd/300001"
    assert (a["sido"], a["career_type"], a["career_min"], a["career_max"]) == ("서울", "경력", 2, 5)
    assert b["career_type"] == "신입" and b["sido"] == "경기"
    with pytest.raises(SourceError):
        wanted.parse({"message": "blocked"})


def test_csv_import():
    data = ("회사,제목,지역,연봉,경력,마감일,링크,회사평균연봉\n"
            "가상A,회계 담당,서울 중구,\"3,200~3,600만원\",신입,2026-11-30,https://example.com/1,4800\n"
            ",제목만 있음,,,,,,\n").encode("cp949")
    rows = fileimport.read_table("jobs.csv", data)
    items, skipped = fileimport.to_postings(rows, "jobkorea")
    assert len(items) == 1 and len(skipped) == 1
    p = items[0]
    assert p["source"] == "jobkorea" and p["company_avg_salary"] == 4800 and p["salary_max"] == 3600


def test_csv_needs_company_and_title():
    with pytest.raises(SourceError):
        fileimport.to_postings([{"이름": "x"}])


def test_xlsx_import(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["출처", "회사", "제목", "지역", "연봉"])
    ws.append(["잡코리아", "가상B", "구매 담당", "인천 남동구", "월 280만원"])
    path = tmp_path / "a.xlsx"
    wb.save(path)
    items, _ = fileimport.to_postings(fileimport.read_table("a.xlsx", path.read_bytes()))
    assert items[0]["source"] == "jobkorea" and items[0]["salary_min"] == 3360 and items[0]["sido"] == "인천"

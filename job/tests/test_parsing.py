"""연봉·지역·경력·학력·날짜 해석."""
import pytest

from core import salary
from core.normalize import parse_career, parse_education, parse_region, to_date


@pytest.mark.parametrize("text, pay, expected", [
    ("2,600~2,800만원", None, (2600, 2800, False)),
    ("연봉 3,000만원 이상", None, (3000, None, False)),
    ("~4000만원", None, (None, 4000, False)),
    ("월 250만원", None, (3000, 3000, False)),
    ("250만원 ~ 300만원", "월급", (3000, 3600, False)),
    ("시급 10,030원", None, (2516, 2516, False)),
    ("12,000원", "시급", (3010, 3010, False)),
    ("4천5백만원", None, (4500, 4500, False)),
    ("1억 2천만원", None, (12000, 12000, False)),
    ("35000000원", "연봉", (3500, 3500, False)),
    ("회사내규에 따름", None, (None, None, True)),
    ("면접 후 결정", None, (None, None, True)),
    ("", None, (None, None, False)),
])
def test_salary_parse(text, pay, expected):
    assert salary.parse(text, pay) == expected


def test_salary_format():
    assert salary.format_range(3000, 3600) == "3,000만원 ~ 3,600만원"
    assert salary.format_range(3000, None) == "3,000만원 이상"
    assert salary.format_range(None, None, True) == "회사내규·협의"
    assert salary.format_manwon(12500) == "1억 2,500만원"
    assert salary.midpoint(3000, 3600) == 3300


@pytest.mark.parametrize("text, expected", [
    ("서울 &gt; 강남구,서울 &gt; 서초구", ("서울", "강남구")),
    ("경기도 성남시 분당구 판교로 1", ("경기", "성남시 분당구")),
    ("경기 광주시", ("경기", "광주시")),          # 광주광역시와 헷갈리지 않는다
    ("광주 광산구", ("광주", "광산구")),
    ("전북특별자치도 전주시 덕진구", ("전북", "전주시 덕진구")),
    ("재택근무", ("재택", None)),
    ("", (None, None)),
])
def test_region(text, expected):
    assert parse_region(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("신입", ("신입", None, None)),
    ("경력 3년↑", ("경력", 3, None)),
    ("경력(2~5년)", ("경력", 2, 5)),
    ("경력 2년~10년 차", ("경력", 2, 10)),
    ("신입/경력", ("신입·경력", None, None)),
    ("경력무관", ("무관", None, None)),
    ("관계없음", ("무관", None, None)),
])
def test_career(text, expected):
    assert parse_career(text) == expected


def test_education_and_date():
    assert parse_education("대학교졸업(4년)이상") == "대졸"
    assert parse_education("대학졸업(2,3년)이상") == "초대졸"
    assert parse_education("학력무관") == "무관"
    assert to_date("26-10-31") == "2026-10-31"
    assert to_date("2026.10.31") == "2026-10-31"
    assert to_date("20261031") == "2026-10-31"
    assert to_date("채용시까지") is None

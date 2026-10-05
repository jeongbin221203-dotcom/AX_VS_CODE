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


def test_negotiable_text_with_address_numbers_is_not_salary():
    """'회사 내규에 따름 근무지주소 … 서초대로 301, 16~18F' 의 주소 숫자를 금액으로 읽지 않는다."""
    assert salary.parse("회사 내규에 따름 근무지주소 대한민국 서울특별시 서초구 서초대로 301, 16~18F") == (None, None, True)
    assert salary.parse("면접 후 결정 (주 5일, 09~18시)") == (None, None, True)
    assert salary.parse("연봉 2800만원~3000만원(경력자 협의 가능)")[:2] == (2800, 3000)
    assert salary.parse("회사 내규에 따름 (3,000만원 이상)")[:2] == (3000, None)


def test_region_with_country_prefix():
    assert parse_region("대한민국 서울특별시 서초구 서초대로 301, 16~18F") == ("서울", "서초구")
    assert parse_region("한국 경기도 성남시 분당구") == ("경기", "성남시 분당구")


def test_negotiable_with_unitless_big_amount_keeps_amount():
    assert salary.parse("회사 내규에 따름(연봉 : 25,882,560)")[:2] == (2588, 2588)


@pytest.mark.parametrize("text, expected", [
    ("현장직(소각장) - 시급 11,200원, 평균연봉 4,250만원(상여 포함, 잔업제외 금액)", (2809, 4250, False)),   # 금액마다 단위
    ("· 시급 10,320원 / 식대 별도 지급 (월 220,000원)", (2588, 2588, False)),                       # 식대는 연봉 아님
    ("조건 : 월356만원 (연봉 환산 시 약 4,280만원 수준)", (4272, 4280, False)),
    ("시급 60,000 원", (None, None, False)),                                                     # 높은 시급은 환산 안 함
    ("월급 3,300~4,800만원", (3300, 4800, False)),                                              # 월 1,500만원↑ = 연봉을 월급으로 잘못 적음
    ("기본급 292만원 ~ 312만원 (지게차 가능시 10~20만원 추가", (3504, 3744, False)),                 # 첫 범위만
    ("급여 2,054,200원+휴대폰요금(67,500원 지원)", (2465, 2465, False)),                           # 통신비는 연봉 아님
    ("회사 내규에 따름 (※건당 1000만원 이상)", (None, None, True)),                                # 건당 = 성과급
    ("정규직 수습 3개월, 월 280만원", (3360, 3360, False)),                                         # '3개월'의 3 은 금액 아님
    ("3~4천만원", (3000, 4000, False)),
])
def test_salary_per_amount_units(text, expected):
    assert salary.parse(text) == expected


def test_commission_jobs():
    from core import fit
    p = {"title": "TMR 모집", "employment_type": "위촉직/개인사업자", "salary_min": 3000, "salary_max": 12000}
    assert salary.is_commission(p) and not salary.is_commission({"title": "사무", "employment_type": "정규직"})
    r = fit.evaluate(p, {"min_salary": 4500, "career_type": "모두"})
    assert r.parts["연봉"][0] == 12 and any("실적" in w for w in r.warnings)


def test_percent_and_counts_are_not_money():
    """'수습기간 급여 100% 지급'의 100 은 100만원이 아니다 (예전엔 연 1,200만원으로 읽음)."""
    assert salary.parse("입사일 ~ 3개월 간 수습기간(급여 100% 지급) 진행합니다.")[:2] == (None, None)
    assert salary.parse("*수습기간 주5일 급여100%")[:2] == (None, None)
    assert salary.parse("월급 110 만원 (주 20시간)")[:2] == (1320, 1320)


def test_allowance_included_is_base_pay():
    assert salary.parse("OT 수당 포함 월 평균 300만원 이상")[:2] == (3600, None)
    assert salary.parse("월 250만원 + 식대 10만원")[:2] == (3000, 3000)


def test_absurd_upper_bound_dropped():
    assert salary.parse("연봉 3,000~50,000만원")[:2] == (3000, None)
    assert salary.parse("연봉 2,800만원~3,000만원")[:2] == (2800, 3000)


def test_summary_keeps_thousands_comma():
    from core.sources.linkimport import _summary_fields
    assert _summary_fields("경력 : 경력, 학력 : 초대졸이상, 급여 : 3,200만원 이상, 마감일 : 2026.11.09")["salary"] == "3,200만원 이상"


def test_employment_type_cleanup():
    from core.normalize import parse_employment as f
    assert f("정규직 수습기간 3개월 자격요건 자격요건") == "정규직 (수습 3개월)"
    assert f("정규직 (수습 3개월) 직급/직책") == "정규직 (수습 3개월)"
    assert f("정규직, 계약직, 계약직") == "정규직, 계약직"
    assert f("계약직 근무기간 1년 정규직 전환 가능") == "계약직 (근무기간 1년 · 정규직 전환 가능)"
    assert f("무기계약직") == "무기계약직" and f("협의") == "협의"
    from core.exclude import _contract_only
    assert _contract_only({"employment_type": f("계약직 근무기간 1년 정규직 전환 가능"), "title": ""})
    assert not _contract_only({"employment_type": "정규직, 계약직", "title": ""})


def test_region_from_title_when_location_missing():
    from core import postings
    p = postings.build("jobkorea", "1", title="[서울 역삼역] ㈜삼구아이앤씨 오피스 건물 시설 기사 모집", company="삼구")
    assert p["sido"] == "서울"
    assert postings.build("jobkorea", "2", title="시루정보 Java 개발 (대전근무)", company="시루")["sido"] == "대전"
    assert postings.build("jobkorea", "3", title="[신입/경력] 업무총괄", company="x")["sido"] is None
    assert postings.build("jobkorea", "4", title="(주)광주에너지 채용", company="x")["sido"] is None   # 괄호 밖 회사 이름은 지역 아님
    # 근무지가 있으면 제목보다 근무지
    assert postings.build("jobkorea", "5", title="[서울] 채용", company="x", location="부산 해운대구")["sido"] == "부산"


def test_far_future_deadline_is_open_ended():
    assert to_date("9999-01-01") is None and to_date("2099-12-31") == "2099-12-31"


def test_saramin_highschool_wording():
    assert parse_education("고교졸업 이상") == "고졸"
    assert parse_education("대학교졸업(4년) 이상") == "대졸"
    assert parse_education("대학졸업(2,3년) 이상") == "초대졸"


def test_wide_gap_and_unit_mistakes():
    """화면에서 본 갭이 큰 연봉: 1억 끝값, 최저임금을 만원으로 잘못 적음, 원을 만원으로 적음."""
    assert salary.parse("연봉 25900000원~100000000원")[:2] == (2590, None)       # 잡코리아 입력 칸 끝값 1억
    assert salary.parse("연봉 5,000~10,000만원")[:2] == (5000, None)
    assert salary.parse("연봉 6,000~10,000만원")[:2] == (6000, 10000)            # 아래쪽이 5천 넘으면 그대로
    assert salary.parse("연봉 25,882만원 이상 (면접 후 결정)")[:2] == (2588, None)  # 최저임금 25,882,560원
    assert salary.parse("연봉 2억 5천만원")[:2] == (25000, 25000)                 # 진짜 2억 5천은 그대로
    assert salary.parse("월급 2,236,300만원")[:2] == (2684, 2684)                 # 2,236,300원


def test_wide_range_keeps_upper_bound_scoring():
    """원문 그대로의 넓은 범위('3,500~8,000')는 중간값으로 바꾸지 않는다 — 상한이 희망 연봉에 닿으면 20점."""
    from core import fit
    prof = {"min_salary": 8000}
    assert fit.evaluate({"salary_min": 3500, "salary_max": 8000, "title": "x"}, prof).parts["연봉"] == (20, "상한은 희망 연봉 이상")


def test_abroad_title_overrides_head_office_address():
    from core import postings
    p = postings.build("jobkorea", "9", title="[일본근무] 티맥스소프트 일본법인엔지니어", company="티맥스",
                       location="대한민국 경기도 성남시 분당구 정자일로 45")
    assert p["sido"] == "해외"
    assert postings.build("jobkorea", "8", title="[중국어 통역] 채용", company="x", location="서울 강남구")["sido"] == "서울"


def test_deadline_with_date_and_early_close_note():
    assert to_date("2026.10.31(토) 채용 시 마감") == "2026-10-31"
    assert to_date("채용시 마감") is None and to_date("상시채용") is None


def test_summary_keeps_comma_in_parentheses():
    from core.sources.linkimport import _summary_fields
    f = _summary_fields("(주)가나, 생산관리, 경력:경력무관, 학력:대학졸업(2,3년)이상, 연봉 3,200만원, 마감일:2026-11-30")
    assert f["education"] == "대학졸업(2,3년)이상" and f["salary"] == "연봉 3,200만원"


def test_body_salary_line_and_allowance_ranges():
    """'40만원 ~ 1억' 범위의 원인: 본문의 복리후생·인상·수당 문장을 연봉으로 읽던 것."""
    from core.sources.linkimport import _salary_from_body as pick
    assert pick(["반기별 스타상: 최대 상금 100만원과 연봉 600만원 인상의 기회", "급여 외 매월 최대 6만6천원 지원",
                 "• 급여 : 연봉 3,200만원 이상"]) == "연봉 3,200만원 이상"
    assert pick(["입사 후 교육 기간 동안 매일 10만원의 급여를 지급해요."]) is None
    assert salary.parse("급여 실수령액 270만원 + 식대20만원 + 인센티브(평균 15~20만원)")[:2] == (3240, 3240)
    assert salary.parse("건별 300,000원 (면접 후 결정)")[:2] == (None, None)
    assert salary.parse("급여 : 편당 40만 원")[:2] == (None, None)
    assert salary.parse("• 급여 : 3천 중후반선 (이후 안내)")[:2] == (3000, 3000)


def test_foreign_location_is_abroad():
    assert parse_region("미국전체") == ("해외", None) and parse_region("유럽 폴란드") == ("해외", None)
    assert parse_region("인천 남동구") == ("인천", "남동구")


def test_more_salary_wording():
    assert salary.parse("화.목 5시부터 8시30분 시급 25.000원")[:2] == (6270, 6270)     # 25.000 = 25,000, '5시부터' 는 이상 아님
    assert salary.parse("정규직 380부터")[:2] == (4560, None)
    assert salary.parse("o 연봉 : 경력에 따른 협의 ( 정규 근무시 실 수령액 280 만원 이상 ~)")[:2] == (3360, None)   # 실수령은 월
    assert salary.parse("- 연봉 : 기본 3,064만원 이상 + 인센티브 1년간 평균 250만원 지급")[:2] == (3064, None)
    from core.normalize import parse_employment
    assert parse_employment("파트 자격요건 1건 자격요건") == "파트타임"


def test_big_amount_not_hourly_and_tiny_dropped():
    assert salary.parse("기본급 2,156,880원 + 만근수당 170,000원 / 특근시급 15,480원")[:2] == (2588, 2588)
    assert salary.parse("시급 60,000 원")[:2] == (None, None)                  # 진짜 높은 시급은 그대로 원문 표시
    assert salary.parse("- 최저 NET 280 + 충격파인센 5%, 메뉴얼 7분당 1,000원")[:2] == (None, None)
    assert salary.parse("월급 50 만원 (주 7시간)")[:2] == (600, 600)


def test_slider_default_range_is_undisclosed():
    assert salary.parse("1000~10000만원")[:2] == (None, None)
    assert salary.parse("연봉 25900000원~100000000원")[:2] == (2590, None)

    assert salary.parse("시급 10500원~10500원")[:2] == (2633, 2633)

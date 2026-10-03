"""공고 링크로 가져오기 (사람인·잡코리아·링커리어·자소설닷컴·잡플래닛·원티드)."""
import json

import pytest

from core import postings
from core.sources import SourceError, linkimport

JSONLD_PAGE = """<html><head><title>[가상정밀] 품질관리 신입 채용 | 잡코리아</title>
<script type="application/ld+json">%s</script></head><body></body></html>""" % json.dumps({
    "@context": "https://schema.org", "@type": "JobPosting",
    "title": "품질관리 신입 채용", "hiringOrganization": {"@type": "Organization", "name": "가상정밀"},
    "jobLocation": {"@type": "Place", "address": {"addressRegion": "경기도", "addressLocality": "화성시"}},
    "baseSalary": {"@type": "MonetaryAmount", "currency": "KRW",
                   "value": {"@type": "QuantitativeValue", "minValue": 32000000, "maxValue": 36000000, "unitText": "YEAR"}},
    "experienceRequirements": {"@type": "OccupationalExperienceRequirements", "monthsOfExperience": 0},
    "educationRequirements": {"credentialCategory": "대학교졸업(4년)"},
    "employmentType": "FULL_TIME", "validThrough": "2026-11-15T23:59:59+09:00", "datePosted": "2026-09-25",
    "skills": ["ISO9001", "엑셀"], "description": "<p>품질 검사 및 성적서 관리</p>",
}, ensure_ascii=False)

OG_PAGE = """<html><head>
<meta property="og:title" content="[가상전자] 2026 하반기 신입 공채 - 링커리어">
<meta property="og:description" content="서류 접수 10/20까지 &amp; 인적성 검사">
</head></html>"""


@pytest.mark.parametrize("url, site", [
    ("https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "saramin"),
    ("https://www.jobkorea.co.kr/Recruit/GI_Read/1", "jobkorea"),
    ("https://m.jobkorea.co.kr/Recruit/GI_Read/1", "jobkorea"),
    ("https://linkareer.com/activity/1", "linkareer"),
    ("https://jasoseol.com/recruit/1", "jasoseol"),
    ("https://www.jobplanet.co.kr/job/search?posting_ids[]=1", "jobplanet"),
    ("https://www.wanted.co.kr/wd/1", "wanted"),
    ("https://career.rememberapp.co.kr/job/posting/322775", "remember"),
    ("https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo=K1", "work24"),
    ("https://evil-jobkorea.co.kr/x", None),          # 비슷한 이름의 다른 도메인
    ("https://jobkorea.co.kr.evil.com/x", None),
    ("http://127.0.0.1:5004/", None),
    ("file:///etc/passwd", None),
])
def test_site_of(url, site):
    assert linkimport.site_of(url) == site


def test_jsonld_page():
    p = linkimport.parse(JSONLD_PAGE, "https://www.jobkorea.co.kr/Recruit/GI_Read/1", "jobkorea")
    assert (p["source"], p["company"], p["title"]) == ("jobkorea", "가상정밀", "품질관리 신입 채용")
    assert (p["sido"], p["sigungu"]) == ("경기", "화성시")
    assert (p["salary_min"], p["salary_max"]) == (3200, 3600)
    assert (p["career_type"], p["education"], p["employment_type"]) == ("신입", "대졸", "정규직")
    assert p["deadline"] == "2026-11-15" and p["keywords"] == "ISO9001, 엑셀"
    assert "<p>" not in p["description"]


def test_og_only_page():
    p = linkimport.parse(OG_PAGE, "https://linkareer.com/activity/1", "linkareer")
    assert p["company"] == "가상전자"
    assert p["title"] == "2026 하반기 신입 공채"          # 앞의 [회사명]·사이트 이름 꼬리표 제거
    assert "10/20" in p["description"]


def test_page_without_title():
    with pytest.raises(SourceError):
        linkimport.parse("<html></html>", "https://jasoseol.com/recruit/1", "jasoseol")


def test_fetch_rejects_other_sites():
    with pytest.raises(SourceError):
        linkimport.fetch("https://example.com/job/1")


def test_links_route(client, monkeypatch):
    class Res:
        text = JSONLD_PAGE

    calls = []
    monkeypatch.setattr(linkimport, "http_get", lambda url, params, headers=None: calls.append(url) or Res())
    body = "https://www.jobkorea.co.kr/Recruit/GI_Read/1\nhttps://example.com/x\n"
    html = client.post("/collect/links", data={"links": body}).get_data(as_text=True)
    assert "가상정밀" in html and "지원하는 사이트 주소가 아닙니다" in html
    assert calls == ["https://www.jobkorea.co.kr/Recruit/GI_Read/1"]      # 허용하지 않은 주소는 요청하지 않음
    # 같은 링크를 다시 넣으면 중복되지 않고 갱신
    client.post("/collect/links", data={"links": body})
    assert len(postings.all_rows()) == 1
    assert postings.all_rows()[0]["source"] == "jobkorea"


def test_csv_site_aliases():
    from core.sources import fileimport
    rows = [{"출처": s, "회사": f"가상{i}", "제목": "공고"} for i, s in
            enumerate(["사람인", "잡코리아", "링커리어", "자소설닷컴", "잡플래닛", "원티드"])]
    items, _ = fileimport.to_postings(rows)
    assert [i["source"] for i in items] == ["saramin", "jobkorea", "linkareer", "jasoseol", "jobplanet", "wanted"]


SARAMIN_PAGE = """<html><head>
<meta property="og:title" content="[가상오토(주)] 가상오토 신입사원 채용(D-12) - 사람인">
<meta property="og:description" content="가상오토(주), 가상오토 신입사원 채용, 경력:신입, 학력:대학교졸업(4년)이상, 면접 후 결정, 마감일:2026-10-12, 홈페이지:https://example.com/">
</head></html>"""


def test_summary_page():
    """JSON-LD 없이 요약문만 있는 페이지(사람인 꼴): 괄호 든 회사명, 경력·학력·급여·마감."""
    p = linkimport.parse(SARAMIN_PAGE, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "saramin")
    assert (p["company"], p["title"]) == ("가상오토(주)", "가상오토 신입사원 채용")
    assert (p["career_type"], p["education"], p["deadline"]) == ("신입", "대졸", "2026-10-12")
    assert p["salary_negotiable"] == 1


@pytest.mark.parametrize("url", [
    "https://www.saramin.co.kr/zf_user/",
    "https://www.jobkorea.co.kr/?utm_source=google",
    "https://www.work24.go.kr/cm/main.do",
    "https://www.jobplanet.co.kr/welcome/index",
    "https://linkareer.com/",
    "https://jasoseol.com/",
    "https://career.rememberapp.co.kr/job/postings",
])
def test_home_pages_rejected(url, monkeypatch):
    monkeypatch.setattr(linkimport, "http_get", lambda *a, **k: pytest.fail("요청하면 안 됨"))
    with pytest.raises(SourceError, match="첫 화면"):
        linkimport.fetch(url)


def test_salary_unit_sanity():
    """연봉을 MONTH 로 잘못 적은 페이지(링커리어)는 연봉으로 본다."""
    from core import salary
    wrong = linkimport._salary({"value": {"minValue": 43000000, "maxValue": 45000000, "unitText": "MONTH"}})
    assert salary.parse(wrong)[:2] == (4300, 4500)
    right = linkimport._salary({"value": {"minValue": 2500000, "maxValue": 3000000, "unitText": "MONTH"}})
    assert salary.parse(right)[:2] == (3000, 3600)


def test_jobkorea_max_off_by_10000():
    """잡코리아 JSON-LD 상한은 1만원이 더해져 있다: 화면 '월급 500~1,000만원' = maxValue 10,010,000."""
    from core import salary
    t = linkimport._salary({"value": {"minValue": 5000000, "maxValue": 10010000, "unitText": "MONTH"}})
    assert salary.parse(t)[:2] == (6000, 12000)
    t = linkimport._salary({"value": {"minValue": 4000000, "maxValue": 9010000, "unitText": "MONTH"}})
    assert salary.parse(t)[:2] == (4800, 10800)


SARAMIN_VIEW = """<html><head><meta property="og:title" content="[가상물산(주)] 각 부문별 직원 채용(D-3) - 사람인"></head><body>
<div class="jv_cont jv_summary"><h2>핵심 정보</h2><dl><dt>경력</dt><dd>경력무관(신입포함)</dd><dt>학력</dt><dd>학력무관</dd>
<dt>근무형태</dt><dd>정규직</dd><dd>수습기간 3개월</dd><dt>급여</dt><dd>면접 후 결정</dd><dt>근무지역</dt><dd>경기 화성시</dd><a>지도보기</a></dl><p>조회수</p></div>
<div class="jv_cont jv_detail"><div class="user_content jobsViewDetail_1"><p>담당업무</p><p>ㆍ전화 CS 클레임 처리</p>
<p>자격요건</p><p>ㆍ학력사항 : 학력무관</p><p>근무조건</p><p>ㆍ급여조건 : 연봉2800만원~3000만원(경력자 협의 가능)</p></div></div>
<div class="jv_cont jv_howto"><dt>시작일</dt><dd>2026.09.30 00:00</dd><dt>마감일</dt><dd>2026.10.05 23:59</dd></div>
<div class="jv_cont jv_company"><h2>기업정보</h2><dt>대표자명</dt><dd>홍길동</dd><dt>기업형태</dt><dd>중소기업</dd><dt>업종</dt><dd>식품 제조업</dd>
<dt>사원수</dt><dd>8 명</dd><dd>(2026년 기준)</dd><dt>매출액</dt><dd>63억 3,331만원</dd><p>채용정보</p></div></body></html>"""


def test_saramin_full_page():
    import json
    p = linkimport.parse(SARAMIN_VIEW, "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=77", "saramin")
    assert (p["source_id"], p["company"], p["title"]) == ("77", "가상물산(주)", "각 부문별 직원 채용")
    assert (p["sido"], p["sigungu"], p["employment_type"]) == ("경기", "화성시", "정규직 (수습 3개월)")
    assert (p["posted_at"], p["deadline"]) == ("2026-09-30", "2026-10-05")
    assert (p["salary_min"], p["salary_max"]) == (2800, 3000)                  # 요약은 '면접 후 결정' → 본문 금액
    assert "ㆍ전화 CS 클레임 처리" in p["description"].split("\n")
    info = json.loads(p["company_info"])
    assert info["업종"] == "식품 제조업" and info["사원수"] == "8 명 (2026년 기준)" and "대표자명" not in info


def test_saramin_detail_url():
    assert linkimport.detail_url("https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=5&x=1", "saramin") == \
        "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5"
    assert linkimport.detail_url("https://linkareer.com/activity/9", "linkareer") == "https://linkareer.com/activity/9"


def test_keep_existing_and_detail_sections(app, client):
    from core import postings
    postings.upsert_many([postings.build("saramin", "77", title="옛 제목", company="가상", location="경기 화성시",
                                         job_category="생산")])
    item = linkimport.parse(SARAMIN_VIEW.replace("<dt>근무지역</dt><dd>경기 화성시</dd>", ""),
                            "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=77", "saramin")
    postings.upsert_many([postings.keep_existing(item)])
    row = postings.get(postings.find_id("saramin", "77"))
    assert row["title"] == "각 부문별 직원 채용" and row["sido"] == "경기" and row["job_category"] == "생산"
    html = client.get(f"/jobs/{row['id']}").get_data(as_text=True)
    assert "기업 정보" in html and "식품 제조업" in html and "상세 내용" in html and "전화 CS 클레임" in html


def test_jobkorea_table_overrides_structured_data():
    import json
    page = JSONLD_PAGE.replace("</body>", """<div><h3>모집요강</h3><dl><dt>모집분야</dt><dd>물류센터</dd><dt>모집인원</dt><dd>○○</dd><dd>명</dd>
<dt>고용형태</dt><dd>계약직</dd><dd>(정규직 전환 가능)</dd><dt>급여</dt><dd>연봉 5,020만원</dd><dt>근무시간</dt><dd>요일협의</dd></dl>
<h3>지원자격</h3><dt>경력</dt><dd>경력무관</dd><dt>학력</dt><dd>학력무관</dd><p>로그인</p>
<h3>접수기간 · 방법</h3><dt>시작일</dt><dd>2026.08.27(목)</dd><dt>마감일</dt><dd>상시채용</dd>
<h3>기업 정보</h3><dt>사원수</dt><dd>10,001명 이상</dd><dt>기업구분</dt><dd>대기업 (비상장)</dd><dt>산업(업종)</dt><dd>택배업</dd><a>지도보기</a></div></body>""")
    p = linkimport.parse(page, "https://www.jobkorea.co.kr/Recruit/GI_Read/1", "jobkorea")
    assert p["employment_type"] == "계약직 (정규직 전환 가능)"            # 구조화 데이터는 FULL_TIME 이었음
    assert (p["salary_min"], p["career_type"], p["posted_at"]) == (5020, "무관", "2026-08-27")
    assert "모집분야: 물류센터" in p["description"] and "모집인원: ○○명" in p["description"]
    info = json.loads(p["company_info"])
    assert info == {"기업형태": "대기업 (비상장)", "업종": "택배업", "사원수": "10,001명 이상"}


def _next_page(data):
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></html>'


REMEMBER_DATA = {"props": {"pageProps": {"dehydratedState": {"queries": [{"queryKey": ["/job_postings/9"], "state": {"data": {"data": {
    "id": 9, "title": "[글로벌 보험사] 해외영업", "jobPostingType": "internal_headhunter", "jobDescription": "- 신규 고객 발굴",
    "qualifications": "- 학사 이상", "preferredQualifications": "- 상경계열", "introduction": "글로벌 보험사입니다.",
    "recruitingProcess": "서류 → 면접", "minSalary": 5000, "maxSalary": 7000, "educationRequirement": "bachelor",
    "minExperience": 3, "maxExperience": None, "startsAt": "2026-10-01T00:00:00.000+09:00",
    "endsAt": "2026-10-12T23:59:59.000+09:00", "explicitDue": True, "companyDescription": "글로벌 보험사",
    "jobCategories": [{"level1": "영업", "level2": "해외B2B영업"}], "industries": [{"level1": "금융", "level2": "보험"}],
    "normalizedAddress": {"level1": "서울", "level2": "종로구"},
    "organization": {"name": "(주)가상써치", "headhunter": True, "applicationResponseMetrics": {"responseRate": 97}},
    "desiredProfileCondition": {"skills": [{"name": "B2B 영업"}]}}}}}]}}}}

LINKAREER_DATA = {"props": {"pageProps": {
    "data": {"activityData": {"activity": {
        "id": "77", "title": "[가상기획] 번역 사무 채용", "organizationName": "가상기획", "jobTypes": ["CONTRACT"],
        "educationTypes": [], "isSalaryDecidedByCompanyPolicy": True, "recruitStartAt": "1790780400000",
        "recruitCloseAt": "1791125999999", "regions": [{"name": "서울"}], "regionDistricts": [{"name": "마포구"}],
        "addresses": [{"address": "서울 마포구 상암동 1606", "detailAddress": "가상센터"}], "organizationType": "중견기업",
        "rootCategories": [{"name": "기획/경영"}], "categories": [{"name": "사무/문서관리"}],
        "applyTypes": [{"name": "이메일"}]}}},
    "__APOLLO_STATE__": {"Activity:77": {"detailText": {"__ref": "ActivityText:1"}},
                         "ActivityText:1": {"text": "<p>1.&nbsp;모집부문</p><p>담당업무 : 자막 번역</p><ul><li>꼼꼼한 분</li></ul>"}}}}}


def test_remember_next_data():
    import json as _j
    p = linkimport.parse(_next_page(REMEMBER_DATA), "https://career.rememberapp.co.kr/job/posting/9", "remember")
    assert (p["company"], p["sido"], p["sigungu"]) == ("글로벌 보험사", "서울", "종로구")      # 헤드헌터 공고는 실제 회사 설명
    assert (p["career_type"], p["career_min"], p["education"]) == ("경력", 3, "대졸")
    assert (p["salary_min"], p["salary_max"], p["deadline"], p["posted_at"]) == (5000, 7000, "2026-10-12", "2026-10-01")
    assert "[담당 업무]\n- 신규 고객 발굴" in p["description"] and "B2B 영업" in p["keywords"]
    assert p["_flags"] == ["헤드헌팅"] and _j.loads(p["company_info"])["올린 곳"] == "(주)가상써치 (헤드헌터)"


def test_linkareer_next_data():
    import json as _j
    p = linkimport.parse(_next_page(LINKAREER_DATA), "https://linkareer.com/activity/77", "linkareer")
    assert (p["company"], p["title"], p["employment_type"]) == ("가상기획", "번역 사무 채용", "계약직")
    assert (p["sido"], p["sigungu"], p["salary_negotiable"]) == ("서울", "마포구", 1)
    assert (p["posted_at"], p["deadline"]) == ("2026-10-01", "2026-10-04")
    assert "담당업무 : 자막 번역" in p["description"] and "· 꼼꼼한 분" in p["description"]
    assert _j.loads(p["company_info"]) == {"기업형태": "중견기업"}


def test_detail_read_marks_done_and_flags(app):
    """상세를 읽으면 기업정보가 비어도 '읽음'({}), 리멤버 헤드헌터는 헤드헌팅 표시."""
    from core import postings
    p = linkimport.parse(_next_page(REMEMBER_DATA), "https://career.rememberapp.co.kr/job/posting/9", "remember")
    postings.upsert_many([p])
    assert postings.flagged("remember", "헤드헌팅") == {"9"}
    empty = postings.build("linkareer", "5", title="t", company="c", company_info={})
    postings.upsert_many([empty])
    assert postings.get(postings.find_id("linkareer", "5"))["company_info"] == "{}"


def test_jobkorea_salary_not_merged_with_address():
    page = JSONLD_PAGE.replace("</body>", """<div><h3>모집요강</h3><dt>모집분야</dt><dd>Sales Leader</dd><dt>고용형태</dt><dd>정규직</dd>
<dt>급여</dt><dd>회사 내규에 따름</dd><dt>근무지주소</dt><dd>대한민국 서울특별시 서초구 서초대로 301, 16~18F</dd><a>지도보기</a>
<h3>지원자격</h3><dt>경력</dt><dd>경력</dd><dd>(5년이상)</dd></div></body>""").replace('"maxValue": 36000000', '"maxValue": 0')
    page = page.replace('"baseSalary"', '"x_baseSalary"')
    p = linkimport.parse(page, "https://www.jobkorea.co.kr/Recruit/GI_Read/2", "jobkorea")
    assert p["salary_raw"] == "회사 내규에 따름" and p["salary_min"] is None and p["salary_negotiable"] == 1
    assert "근무지주소: 대한민국 서울특별시 서초구" in p["description"]


def test_saramin_location_from_map_when_summary_has_none():
    """핵심 정보에 근무지역이 없으면 '근무지위치' 지도 주소(data-address)로 — 기업주소(본사)는 쓰지 않는다."""
    page = ('<html><head><meta property="og:title" content="(주)에코솔라파워 채용 - 태양광발전소 운영 담당자 | 사람인">'
            '</head><body><div class="jv_cont jv_summary"><dt>경력</dt><dd>경력무관</dd></div>'
            '<div class="jv_cont jv_location" data-company-name="(주)에코솔라파워" '
            'data-address="(57030) 전남 영광군 백수읍 백수로1길 20">근무지위치</div>'
            '<div class="jv_cont jv_company">기업주소 광주 북구 어딘가</div></body></html>')
    p = linkimport.parse(page, "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55190071", "saramin")
    assert (p["sido"], p["sigungu"]) == ("전남", "영광군")


def test_jobkorea_headhunting_page_is_flagged():
    """잡코리아 헤드헌팅 공고는 회사 이름 대신 '벤처기업 : 제목' — 회사 비공개 + 헤드헌팅 표시(제외 항목에 걸리게)."""
    page = "<html><head><title>벤처기업 : 가맹사업 총괄 임원 | 잡코리아 헤드헌팅</title></head><body></body></html>"
    p = linkimport.parse(page, "https://www.jobkorea.co.kr/Recruit/GI_Read/49725148", "jobkorea")
    assert (p["title"], p["company"], p["_flags"]) == ("가맹사업 총괄 임원", "비공개 (벤처기업)", ["헤드헌팅"])

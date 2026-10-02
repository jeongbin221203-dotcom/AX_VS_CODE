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

"""정기 크롤링: 목록 해석·새 공고만 읽기·사이트맵 전체 동기화·잠금·robots.txt (가짜 사이트로, 네트워크 없이)."""
import json
from datetime import datetime, timedelta

import pytest

from core import crawler, db, postings
from core.sources import linkimport


class Res:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class FakeFetcher(crawler.Fetcher):
    """주소 → (상태, 본문). robots.txt 는 따로 정하지 않으면 모두 허용."""

    def __init__(self, pages: dict):
        super().__init__(delay=0)
        self.pages = pages
        self.seen: list[str] = []

    def _get(self, url):
        self.requests += 1
        self.seen.append(url)
        if url.endswith("/robots.txt"):
            return Res(200, self.pages.get(url, "User-agent: *\nAllow: /\n"))
        status, text = self.pages.get(url, (404, ""))
        return Res(status, text)


def detail_page(title, company, region="서울특별시", locality="강남구", valid="2099-12-31"):
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
          "hiringOrganization": {"name": company}, "validThrough": valid,
          "jobLocation": {"address": {"addressRegion": region, "addressLocality": locality}}}
    return f'<html><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></html>'


SARAMIN_LIST = """
<div class="item_recruit" value="101"><a href="/zf_user/jobs/relay/view?rec_idx=101">자재관리 담당</a>
  <div class="job_condition"><span>경기 화성시</span><span>신입 · 경력</span><span>대졸↑</span><span>정규직</span></div></div>
<div class="item_recruit" value="102"><a href="/zf_user/jobs/relay/view?rec_idx=102">구매 담당</a>
  <div class="job_condition"><span>서울 금천구</span><span>경력 3년↑</span><span>초대졸↑</span><span>계약직</span></div></div>
"""
SARAMIN_OG = """<html><head><meta property="og:title" content="[가상물산(주)] 자재관리 담당(D-5) - 사람인">
<meta property="og:description" content="가상물산(주), 자재관리 담당, 경력:신입·경력, 학력:대학교졸업(4년)이상, 면접 후 결정, 마감일:2099-12-31"></head></html>"""


def test_list_items_with_hints():
    items = crawler.list_items("saramin", SARAMIN_LIST)
    assert [i["id"] for i in items] == ["101", "102"]
    assert items[0]["hint"] == {"location": "경기 화성시", "career": "신입 · 경력", "education": "대졸↑",
                                "employment": "정규직"}
    assert items[1]["hint"]["location"] == "서울 금천구"


def _settings(**kw):
    base = {"enabled": True, "keywords": ["자재관리"], "sites": ["saramin"], "sitemap_sites": [], "use_api": False,
            "max_refresh": 0}
    base.update(kw)
    crawler.save_settings(base)


def test_run_reads_only_new_postings_and_fills_location(app):
    _settings()
    search = crawler.LIST_SITES["saramin"]["search"].format(kw="%EC%9E%90%EC%9E%AC%EA%B4%80%EB%A6%AC", page=1)
    d = crawler.LIST_SITES["saramin"]["detail"]
    # 102 는 이미 저장돼 있음 → 상세를 다시 읽지 않는다
    postings.upsert_many([postings.build("saramin", "102", title="구매 담당", company="가상")])
    f = FakeFetcher({search: (200, SARAMIN_LIST), d.format(id=101): (200, SARAMIN_OG)})
    result = crawler.run_once(force=True, fetcher=f)
    assert result["new"] == 1
    assert d.format(id=102) not in f.seen
    p = postings.get(postings.find_id("saramin", "101"))
    assert (p["company"], p["title"]) == ("가상물산(주)", "자재관리 담당")
    assert (p["sido"], p["sigungu"]) == ("경기", "화성시")          # 상세에 없던 근무지를 목록에서 채움
    assert p["employment_type"] == "정규직" and p["education"] == "대졸"
    assert db.get_setting("crawl_last_run")


def test_robots_disallow_is_respected(app):
    _settings()
    f = FakeFetcher({"https://www.saramin.co.kr/robots.txt": "User-agent: *\nDisallow: /zf_user/search\n"})
    result = crawler.run_once(force=True, fetcher=f)
    assert result["new"] == 0 and any("robots" in e for e in result["errors"])
    assert not any("/zf_user/search" in u for u in f.seen)


def test_sitemap_full_sync(app):
    _settings(sites=[], keywords=[], sitemap_sites=["remember"], sitemap_max=3)
    cfg = crawler.SITEMAP_SITES["remember"]

    def sitemap(ids):
        return "<urlset>" + "".join(f"<url><loc>https://career.rememberapp.co.kr/job/posting/{i}</loc></url>"
                                    for i in ids) + "</urlset>"

    pages = {cfg["sitemap"]: (200, sitemap([1, 2, 3, 4, 5]))}
    for i in range(1, 7):
        pages[cfg["detail"].format(id=i)] = (200, detail_page(f"공고 {i}", f"회사{i}"))
    f = FakeFetcher(pages)
    r = crawler.run_once(force=True, fetcher=f)["sites"]["remember"]
    assert (r["total"], r["fetched"], r["inserted"], r["remaining"]) == (5, 3, 3, 2)
    got = {p["source_id"] for p in postings.all_rows()}
    assert got == {"5", "4", "3"}                                   # 최근(큰 번호)부터

    # 다음 실행: 5 가 사이트맵에서 빠지고 6 이 새로 생김
    pages[cfg["sitemap"]] = (200, sitemap([1, 2, 3, 4, 6]))
    f2 = FakeFetcher(pages)
    r = crawler.run_once(force=True, fetcher=f2)["sites"]["remember"]
    assert r["gone"] == 1
    assert cfg["detail"].format(id=5) not in f2.seen                  # 빠진 공고는 요청 없이 마감 처리
    assert postings.find_id("remember", "5") is None                    # 마감 처리 → 저장 안 했으니 삭제
    assert {p["source_id"] for p in postings.all_rows()} == {"1", "2", "3", "4", "6"}
    assert r["remaining"] == 0

    # 세 번째: 바뀐 게 없으면 사이트맵 한 장만 읽는다
    f3 = FakeFetcher(pages)
    crawler.run_once(force=True, fetcher=f3)
    assert [u for u in f3.seen if not u.endswith("robots.txt")] == [cfg["sitemap"]]
    prog = crawler.sitemap_progress()[0]
    assert (prog["total"], prog["fetched"], prog["remaining"]) == (5, 5, 0)


def test_sitemap_shrink_does_not_close_everything(app):
    _settings(sites=[], keywords=[], sitemap_sites=["remember"], sitemap_max=0)
    cfg = crawler.SITEMAP_SITES["remember"]
    many = "".join(f"<loc>https://career.rememberapp.co.kr/job/posting/{i}</loc>" for i in range(1, 101))
    crawler.run_once(force=True, fetcher=FakeFetcher({cfg["sitemap"]: (200, many)}))
    few = "<loc>https://career.rememberapp.co.kr/job/posting/1</loc>"
    r = crawler.run_once(force=True, fetcher=FakeFetcher({cfg["sitemap"]: (200, few)}))["sites"]["remember"]
    assert r["gone"] == 0
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM sitemap_ids WHERE gone = 1").fetchone()[0] == 0


def test_blocked_stops_run(app):
    _settings(sites=[], keywords=[], sitemap_sites=["remember"], sitemap_max=10)
    cfg = crawler.SITEMAP_SITES["remember"]
    ids = "".join(f"<loc>https://career.rememberapp.co.kr/job/posting/{i}</loc>" for i in (1, 2, 3))
    f = FakeFetcher({cfg["sitemap"]: (200, ids), cfg["detail"].format(id=3): (429, "")})
    result = crawler.run_once(force=True, fetcher=f)
    assert any("막았습니다" in e for e in result["errors"])
    assert cfg["detail"].format(id=2) not in f.seen                    # 막히면 바로 멈춘다


def test_due_lock_and_settings(app):
    crawler.save_settings({"enabled": False})
    assert not crawler.due()
    crawler.save_settings({"enabled": True, "interval_hours": 4})
    assert crawler.due()                                               # 한 번도 안 돌았으면 바로
    db.set_setting("crawl_last_run", (datetime.now() - timedelta(hours=1)).isoformat())
    assert not crawler.due()
    db.set_setting("crawl_last_run", (datetime.now() - timedelta(hours=5)).isoformat())
    assert crawler.due()

    assert crawler.acquire_lock() and not crawler.acquire_lock()       # 두 번째 프로세스는 못 잡음
    crawler.release_lock()
    assert crawler.acquire_lock()
    crawler.release_lock()

    s = crawler.save_settings({"interval_hours": 4, "sitemap_max": 99999, "sites": ["saramin", "evil"]})
    assert s["sites"] == ["saramin"]
    assert s["sitemap_max"] * 3 < 4 * 3600                              # 한 번 실행이 다음 실행 전에 끝남


def test_refresh_updates_and_closes(app):
    old = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    url_ok = "https://career.rememberapp.co.kr/job/posting/7"
    url_gone = "https://career.rememberapp.co.kr/job/posting/8"
    postings.upsert_many([
        postings.build("remember", "7", title="옛 제목", company="회사7", url=url_ok, location="부산 해운대구"),
        postings.build("remember", "8", title="사라질 공고", company="회사8", url=url_gone)])
    with db.connect() as con:
        con.execute("UPDATE postings SET updated_at = ?", (old,))
    f = FakeFetcher({url_ok: (200, detail_page("새 제목", "회사7", region="", locality=""))})
    updated, closed, _ = crawler.refresh(f, 10)
    assert (updated, closed) == (1, 1)
    p7 = postings.get(postings.find_id("remember", "7"))
    assert p7["title"] == "새 제목" and p7["sido"] == "부산"           # 새 페이지에 없는 근무지는 유지


WORK24_PAGE = """<html><body><div>채용정보</div><div>기업정보</div><div>가상기계 주식회사</div>
<h2>반도체 장비 CS엔지니어 채용</h2><span>조회수</span><span>0</span>
<dt>경력</dt><dd>관계없음</dd><dt>학력</dt><dd>대졸(2~3년)~대졸(4년)</dd>
<dt>임금</dt><dd>월급 250만원 ~ 320만원</dd><dt>지역</dt><dd>경기도   화성시  동탄구 메타폴리스로 54</dd>
<dt>고용형태</dt><dt>고용형태</dt><dd>기간의 정함이 없는 근로계약</dd>
<h3>직무내용</h3><p>반도체&nbsp;설비&nbsp;유지보수</p><button>더보기</button>
<dt>직종 키워드</dt><dd>산업용로봇설치원</dd><dt>접수 마감일</dt><dd>2026.10.31 (토) 17:00</dd></body></html>"""


def test_work24_detail_page():
    url = "https://www.work24.go.kr/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo=K1800&infoTypeCd=VALIDATION"
    p = linkimport.parse(WORK24_PAGE, url, "work24")
    assert (p["source_id"], p["company"], p["title"]) == ("K1800", "가상기계 주식회사", "반도체 장비 CS엔지니어 채용")
    assert (p["sido"], p["sigungu"], p["education"]) == ("경기", "화성시 동탄구", "초대졸")
    assert (p["salary_min"], p["salary_max"], p["employment_type"]) == (3000, 3840, "정규직")
    assert p["deadline"] == "2026-10-31" and p["career_type"] == "무관"
    assert "설비 유지보수" in p["description"]


def test_crawl_settings_page(client):
    res = client.post("/collect/crawl", data={"enabled": "1", "interval_hours": "4", "keywords": "자재관리, 구매",
                                              "sites": ["saramin", "jobkorea"], "sitemap_sites": ["remember"],
                                              "sitemap_max": "500", "pages": "1", "max_new": "30", "max_refresh": "30"})
    assert res.status_code == 302
    s = crawler.load_settings()
    assert s["enabled"] and s["keywords"] == ["자재관리", "구매"] and s["sitemap_sites"] == ["remember"]
    html = client.get("/collect").get_data(as_text=True)
    assert "자동 수집" in html and "4시간마다" in html


def test_all_jobs_mode_reads_latest_list(app):
    """검색어가 없으면 '모든 직무' — 검색어 없는 최근 등록순 목록을 읽는다."""
    _settings(keywords=[], sites=["saramin"], by_category=False)
    search = crawler.LIST_SITES["saramin"]["latest"].format(page=1)
    d = crawler.LIST_SITES["saramin"]["detail"]
    f = FakeFetcher({search: (200, SARAMIN_LIST), d.format(id=101): (200, SARAMIN_OG),
                     d.format(id=102): (200, SARAMIN_OG.replace("자재관리 담당", "구매 담당"))})
    result = crawler.run_once(force=True, fetcher=f)
    assert result["new"] == 2 and search in f.seen


SARAMIN_LATEST = """<a href="/zf_user/jobs/relay/view?rec_idx=201">CS 전문가</a><span>쿠팡(가상)</span>
<span>서울전체</span><span>2년</span><span>학력무관</span><span>~10.10(토)</span>
<a href="/zf_user/jobs/relay/view?rec_idx=202">시공 및 A/S</a><span>가상벤투스</span><span>경기 화성시</span><span>3년</span>"""


def test_latest_list_hints_and_backfill(app):
    items = {i["id"]: i["hint"] for i in crawler.list_items("saramin", SARAMIN_LATEST)}
    assert items["201"]["location"] == "서울" and items["201"]["career"] == "2년"
    assert items["202"]["location"] == "경기 화성시"
    postings.upsert_many([postings.build("saramin", "202", title="시공", company="가상")])
    crawler._fill_missing_from_list("saramin", items)
    p = postings.get(postings.find_id("saramin", "202"))
    assert (p["sido"], p["sigungu"]) == ("경기", "화성시")


def test_summary_salary_with_thousands_comma():
    page = ('<meta property="og:title" content="[상승(가상)] 수리 경력직원 - 사람인">'
            '<meta property="og:description" content="상승(가상), 수리 경력직원, 경력:경력 1년 이상, '
            '학력:학력무관, 연봉:4,200 만원, 마감일:상시채용, 홈페이지:">')
    p = linkimport.parse(page, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=9", "saramin")
    assert (p["salary_min"], p["salary_max"], p["deadline"]) == (4200, 4200, None)
    assert (p["career_type"], p["career_min"]) == ("경력", 1)



def test_pick_round_robin():
    groups = {"IT": {"1": {}, "2": {}, "3": {}}, "영업": {"4": {}, "1": {}}, "디자인": {"5": {}}}
    assert crawler.pick_round_robin(groups, lambda i: True, 4) == ["1", "4", "5", "2"]
    assert crawler.pick_round_robin(groups, lambda i: i != "1", 10) == ["2", "4", "5", "3"]


def test_by_category_mode(app, monkeypatch):
    """검색어가 없으면 직무별 목록을 모두 돌고, 공고에 직무 이름을 붙인다."""
    monkeypatch.setitem(crawler.LIST_SITES["saramin"], "categories", [("2", "IT개발·데이터"), ("8", "영업·판매·무역")])
    _settings(keywords=[], sites=["saramin"], by_category=True, pages=3, max_new=10)
    cat = crawler.LIST_SITES["saramin"]["category"]
    d = crawler.LIST_SITES["saramin"]["detail"]
    it_list = '<a href="/zf_user/jobs/relay/view?rec_idx=301">x</a><span>서울전체</span>'
    sales_p1 = '<a href="/zf_user/jobs/relay/view?rec_idx=401">y</a><span>부산전체</span>'
    sales_p2 = '<a href="/zf_user/jobs/relay/view?rec_idx=402">z</a>'
    og = lambda n: (f'<meta property="og:title" content="[가상{n}] 공고{n} - 사람인">'
                    f'<meta property="og:description" content="가상{n}, 공고{n}, 경력:신입">')
    f = FakeFetcher({
        cat.format(code="2", page=1): (200, it_list), cat.format(code="2", page=2): (200, it_list),
        cat.format(code="8", page=1): (200, sales_p1), cat.format(code="8", page=2): (200, sales_p2),
        cat.format(code="8", page=3): (200, sales_p2),
        d.format(id=301): (200, og(301)), d.format(id=401): (200, og(401)), d.format(id=402): (200, og(402)),
    })
    result = crawler.run_once(force=True, fetcher=f)
    assert result["new"] == 3
    assert cat.format(code="2", page=3) not in f.seen                 # 같은 내용이 나오면 다음 페이지를 안 읽음
    p = postings.get(postings.find_id("saramin", "301"))
    assert (p["job_category"], p["sido"]) == ("IT개발·데이터", "서울")
    assert postings.get(postings.find_id("saramin", "402"))["job_category"] == "영업·판매·무역"


def test_promoted_posting_gets_no_category(app, monkeypatch):
    """세 직무 이상 목록에 똑같이 나오는 광고 공고는 직무를 붙이지 않고, 잘못 붙은 값도 지운다."""
    cats = [("2", "IT개발·데이터"), ("8", "영업·판매·무역"), ("19", "교육")]
    monkeypatch.setitem(crawler.LIST_SITES["saramin"], "categories", cats)
    _settings(keywords=[], sites=["saramin"], by_category=True, pages=1, max_new=0)
    postings.upsert_many([postings.build("saramin", "999", title="광고", company="가상", job_category="IT개발·데이터")])
    cat = crawler.LIST_SITES["saramin"]["category"]
    ad = '<a href="/zf_user/jobs/relay/view?rec_idx=999">광고</a>'
    pages = {cat.format(code=c, page=1): (200, ad + f'<a href="/zf_user/jobs/relay/view?rec_idx={c}0">x</a>')
             for c, _ in cats}
    groups, _ = crawler.collect_lists(FakeFetcher(pages), "saramin", crawler.load_settings(), [])
    assert all("category" not in g["999"] for g in groups.values())
    assert groups["교육"]["190"]["category"] == "교육"
    assert postings.get(postings.find_id("saramin", "999"))["job_category"] is None


def test_run_purges_closed_unsaved(app):
    from datetime import date, timedelta
    _settings(sites=[], keywords=[], sitemap_sites=[])
    past = (date.today() - timedelta(days=1)).isoformat()
    postings.upsert_many([postings.build("saramin", "x", title="마감", company="가상", deadline=past)])
    result = crawler.run_once(force=True, fetcher=FakeFetcher({}))
    assert result["purged"] == 1 and postings.all_rows() == []


class FlakyFetcher(FakeFetcher):
    """처음 몇 번은 연결이 끊기는 사이트."""

    def __init__(self, pages, fail_times):
        super().__init__(pages)
        self.fail_times = fail_times
        self.RETRY_WAITS = (0, 0)

    def _get(self, url):
        import requests
        if not url.endswith("robots.txt") and self.fail_times > 0:
            self.fail_times -= 1
            self.requests += 1
            raise requests.ConnectionError("reset")
        return super()._get(url)


def test_retry_after_connection_reset(app):
    _settings(keywords=["자재관리"], sites=["saramin"])
    search = crawler.LIST_SITES["saramin"]["search"].format(kw="%EC%9E%90%EC%9E%AC%EA%B4%80%EB%A6%AC", page=1)
    d = crawler.LIST_SITES["saramin"]["detail"]
    f = FlakyFetcher({search: (200, SARAMIN_LIST), d.format(id=101): (200, SARAMIN_OG),
                      d.format(id=102): (200, SARAMIN_OG)}, fail_times=2)
    result = crawler.run_once(force=True, fetcher=f)
    assert result["new"] == 2 and not result["errors"]               # 두 번 끊겨도 다시 시도해 이어 감


def test_repeated_failures_stop_site(app):
    _settings(keywords=["자재관리"], sites=["saramin", "jobkorea"])
    f = FlakyFetcher({}, fail_times=3)
    result = crawler.run_once(force=True, fetcher=f)
    assert any("중단" in e for e in result["errors"])
    assert any("jobkorea" in u for u in f.seen)                       # 다음 사이트는 계속

"""화면 흐름: 샘플 → 목록·상세·통계 → 지원 기록 → 내보내기, CSRF."""
import io

import config
from core import collect, postings


def _load_sample(app):
    with app.app_context():
        collect.load_sample(config.SAMPLE_PATH)


def test_empty_pages(client):
    for url in ("/", "/jobs", "/stats", "/applications", "/collect", "/profile", "/jobs/new"):
        assert client.get(url).status_code == 200, url


def test_sample_flow(app, client):
    assert client.post("/collect/sample").status_code == 302
    html = client.get("/jobs").get_data(as_text=True)
    assert "(가상)" in html
    # 두 번 넣어도 중복되지 않는다
    client.post("/collect/sample")
    assert len(postings.all_rows()) == 40

    client.post("/profile", data={"regions": ["서울"], "min_salary": "3500", "career_type": "신입",
                                  "education": "대졸", "skills": "Python, SQL", "employment_types": ["정규직"]})
    pid = postings.all_rows()[0]["id"]
    assert client.get(f"/jobs/{pid}").status_code == 200
    stats = client.get("/stats").get_data(as_text=True)
    assert "지역별" in stats and "서울" in stats

    client.post(f"/jobs/{pid}/apply", data={"status": "지원 완료", "memo": "이력서 v2"})
    board = client.get("/applications").get_data(as_text=True)
    assert "이력서 v2" in board
    row = postings.get(pid)
    assert row["app_status"] == "지원 완료" and row["applied_at"]
    client.post(f"/jobs/{pid}/apply", data={"status": "면접", "remove": "1"})
    assert postings.get(pid)["app_status"] is None

    for q in ("?sido=서울", "?career=신입", "?eligible=1&sort=salary", "?min_fit=80", "?show_closed=1&sort=deadline"):
        assert client.get("/jobs" + q).status_code == 200, q
    csv_body = client.get("/jobs/export.csv").data.decode("utf-8-sig")
    assert csv_body.startswith("적합도,")


def test_filters(app, client):
    _load_sample(app)
    rows = postings.search({"regions": [], "skills": []}, {"sido": "서울"})
    assert rows and all(r["sido"] == "서울" for r in rows)
    rows = postings.search({}, {"career": "신입"})
    assert all(r["career_type"] in ("신입", "신입·경력", "무관") for r in rows)
    closed = postings.search({}, {"show_closed": "1"})
    assert len(closed) > len(postings.search({}, {}))


def test_manual_and_upload(client):
    res = client.post("/jobs/new", data={"site": "jobkorea", "company": "가상C", "title": "영업관리",
                                         "location": "대전 유성구", "salary_text": "3,000만원", "career": "신입"})
    assert res.status_code == 302 and "/jobs/" in res.headers["Location"]
    data = "회사,제목,지역\n가상D,품질관리,경남 창원시\n".encode("utf-8")
    res = client.post("/collect/upload", data={"file": (io.BytesIO(data), "a.csv"), "site": "jobkorea"},
                      content_type="multipart/form-data")
    assert res.status_code == 302
    assert {r["company"] for r in postings.all_rows()} == {"가상C", "가상D"}
    assert client.get("/collect/template.csv").status_code == 200


def test_collect_without_keys_reports_error(client, monkeypatch):
    monkeypatch.setattr(config, "SARAMIN_KEY", "")
    html = client.post("/collect/run", data={"sources": ["saramin"]}).get_data(as_text=True)
    assert "access-key" in html


def test_open_redirect_blocked(app, client):
    _load_sample(app)
    pid = postings.all_rows()[0]["id"]
    res = client.post(f"/jobs/{pid}/hide", data={"hidden": "1", "back": "//evil.example"})
    assert res.headers["Location"].endswith("/jobs")


def test_csrf_required(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "c.db"})
    c = app.test_client()
    assert c.post("/collect/sample").status_code == 400
    c.get("/collect")
    with c.session_transaction() as s:
        token = s["_csrf"]
    assert c.post("/collect/sample", data={"_csrf": token}).status_code == 302


def test_job_group_and_sub_filter(app, client):
    """사이트마다 다른 직무 이름을 하나의 직무로 묶고, 직무를 고르면 세부 직무를 고를 수 있다."""
    postings.upsert_many([
        postings.build("saramin", "1", title="백엔드 개발자 (Java/Spring)", company="가상A", job_category="IT개발·데이터"),
        postings.build("jobkorea", "2", title="데이터 분석가 SQL", company="가상B", job_category="AI·개발·데이터"),
        postings.build("linkareer", "3", title="React 프론트엔드 인턴", company="가상C", job_category="IT/개발"),
        postings.build("saramin", "4", title="B2B 법인영업", company="가상D", job_category="영업·판매·무역"),
        postings.build("remember", "5", title="Python 백엔드 엔지니어", company="가상E"),   # 직무 이름 없음 → 단어로 짐작
    ])
    html = client.get("/jobs").get_data(as_text=True)
    assert "IT·개발·데이터 (4)" in html and "영업·판매·무역 (1)" in html
    html = client.get("/jobs?category=IT·개발·데이터").get_data(as_text=True)
    assert all(c in html for c in ("가상A", "가상B", "가상C", "가상E")) and "가상D" not in html
    assert "세부 직무" in html and "백엔드" in html
    html = client.get("/jobs?category=IT·개발·데이터&sub=백엔드").get_data(as_text=True)
    assert "가상A" in html and "가상E" in html and "가상B" not in html and "가상C" not in html
    html = client.get("/jobs?category=IT·개발·데이터&sub=백엔드&sub=데이터").get_data(as_text=True)
    assert "가상B" in html and "가상C" not in html
    # 직무를 바꾸면 이전 세부 직무는 무시
    html = client.get("/jobs?category=영업·판매·무역&sub=백엔드").get_data(as_text=True)
    assert "가상D" in html


def test_save_and_purge_closed(app, client):
    """마감된 공고는 저장한 것·지원 기록이 있는 것만 남고 나머지는 지워진다."""
    from datetime import date, timedelta
    from core import applications
    past = (date.today() - timedelta(days=3)).isoformat()
    future = (date.today() + timedelta(days=10)).isoformat()
    postings.upsert_many([
        postings.build("saramin", "a", title="저장한 마감 공고", company="가상A", deadline=past),
        postings.build("saramin", "b", title="그냥 마감 공고", company="가상B", deadline=past),
        postings.build("saramin", "c", title="지원한 마감 공고", company="가상C", deadline=past),
        postings.build("saramin", "d", title="진행 중", company="가상D", deadline=future),
        postings.build("saramin", "e", title="상시", company="가상E"),
    ])
    ids = {r["source_id"]: r["id"] for r in postings.all_rows()}
    assert client.post(f"/jobs/{ids['a']}/save", data={"saved": "1"}).status_code == 302
    applications.upsert(ids["c"], "지원 완료")
    assert postings.purge_closed() == 1
    left = {r["source_id"] for r in postings.all_rows()}
    assert left == {"a", "c", "d", "e"}
    # 저장한 공고 화면: 마감된 것도 보인다
    html = client.get("/jobs?saved=1").get_data(as_text=True)
    assert "가상A" in html and "가상D" not in html and "★ 저장됨" in html
    assert "마감된 공고입니다" in client.get(f"/jobs/{ids['a']}").get_data(as_text=True)
    client.post(f"/jobs/{ids['a']}/save", data={"saved": "0"})
    assert postings.purge_closed() == 1


def test_sub_ignores_site_category_name():
    from core import jobgroups
    p = {"title": "QA 엔지니어", "job_category": "IT개발·데이터", "keywords": None, "description": None}
    assert jobgroups.subs_of(p, "IT·개발·데이터") == {"QA·테스트"}


def test_unsorted_sub():
    from core import jobgroups
    p = {"title": "2026년 신입사원 채용", "job_category": "IT개발·데이터"}
    assert jobgroups.subs_of(p, "IT·개발·데이터") == {jobgroups.UNSORTED}
    assert jobgroups.sub_names("IT·개발·데이터")[-1] == jobgroups.UNSORTED
    assert jobgroups.sub_names("기타") == []


def test_logo(client):
    html = client.get("/").get_data(as_text=True)
    assert 'rel="icon" type="image/svg+xml"' in html and 'class="brand-logo"' in html
    res = client.get("/favicon.ico")
    assert res.status_code == 200 and b"<svg" in res.data

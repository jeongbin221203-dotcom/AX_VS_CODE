"""내 조건(신입·경력 모두·희망 직무·연봉 단계), 연봉 구간 통계, 새로 들어온 맞는 공고."""
from datetime import date

from core import db, fit, postings, profile
from core.postings import build

TODAY = date(2026, 10, 2)


def p(**kw):
    base = dict(title="백엔드 개발자 (Java)", company="가상", location="서울 강남구", career="경력 5년 이상",
                job_category="IT개발·데이터", salary_text="4,000~5,000만원")
    base.update(kw)
    return build("saramin", "x", **base)


def test_career_all_never_blocks():
    prof = {"career_type": "모두", "regions": [], "skills": [], "interests": []}
    r = fit.evaluate(p(), prof, TODAY)
    assert r.parts["경력"][0] == 25 and r.eligible


def test_job_groups_and_subs():
    prof = {"career_type": "모두", "job_groups": ["IT·개발·데이터"], "job_subs": []}
    assert fit.evaluate(p(), prof, TODAY).parts["직무·기술"][0] == 25
    assert fit.evaluate(p(job_category="영업·판매·무역", title="법인영업"), prof, TODAY).parts["직무·기술"][0] == 0
    # 세부 직무까지 고르면 그 안에서만
    prof["job_subs"] = ["IT·개발·데이터/데이터"]
    r = fit.evaluate(p(), prof, TODAY)
    assert r.parts["직무·기술"][0] == 0 and "희망 직무가 아님" in r.warnings
    assert fit.evaluate(p(title="데이터 엔지니어"), prof, TODAY).parts["직무·기술"][0] == 25
    # 기술 키워드와 함께면 반반
    prof = {"career_type": "모두", "job_groups": ["IT·개발·데이터"], "skills": ["Java", "Kotlin", "Go"]}
    assert fit.evaluate(p(), prof, TODAY).parts["직무·기술"][0] == round(25 * (1 / 3 + 1) / 2)


def test_profile_form(client):
    res = client.post("/profile", data={"career_type": "모두", "min_salary": "4500", "education": "무관",
                                        "job_groups": ["IT·개발·데이터", "없는직무"],
                                        "job_subs": ["IT·개발·데이터/백엔드", "IT·개발·데이터/없음"]})
    assert res.status_code == 302
    prof = profile.load()
    assert prof["career_type"] == "모두" and prof["min_salary"] == 4500
    assert prof["job_groups"] == ["IT·개발·데이터"] and prof["job_subs"] == ["IT·개발·데이터/백엔드"]
    html = client.get("/profile").get_data(as_text=True)
    assert "8,000만원 이상" in html and "신입·경력 모두" in html and "기획·전략·경영" in html


def test_salary_bands(app):
    postings.upsert_many([build("saramin", str(i), title="t", company="c", salary_text=s) for i, s in
                          enumerate(["2,800만원", "3,000만원", "3,499만원", "3,500만원", "8,000만원", "1억", "회사내규"])])
    bands = {b["name"]: b["count"] for b in postings.salary_stats()["bands"]}
    assert bands["3,000만원 미만"] == 1 and bands["3,000~3,499만원"] == 2 and bands["3,500~3,999만원"] == 1
    assert bands["8,000만원 이상"] == 2 and sum(bands.values()) == 6


def test_new_matching_postings(app, client):
    db.set_setting("seen_at", "2000-01-01 00:00:00")
    client.post("/profile", data={"career_type": "모두", "education": "무관"})
    postings.upsert_many([build("saramin", "n1", title="새 공고", company="가상새회사", deadline="2099-12-31")])
    html = client.get("/").get_data(as_text=True)
    assert "새로 들어온 맞는 공고" in html and "가상새회사" in html
    assert "NEW" in client.get("/jobs?new=1").get_data(as_text=True)
    client.post("/seen")
    html = client.get("/").get_data(as_text=True)
    assert "새로 들어온 맞는 공고가 없습니다" in html
    assert "가상새회사" not in client.get("/jobs?new=1").get_data(as_text=True)

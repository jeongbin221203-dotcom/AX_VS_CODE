"""적합성 점수."""
from datetime import date

from core import fit
from core.postings import build

TODAY = date(2026, 9, 30)
PROFILE = {"regions": ["서울", "경기"], "remote_ok": True, "min_salary": 3500, "career_type": "신입", "years": 0,
           "education": "대졸", "skills": ["Python", "SQL"], "interests": ["데이터"], "exclude": ["교대"],
           "employment_types": ["정규직"]}


def posting(**kw):
    base = dict(title="데이터 분석 (Python, SQL)", company="가상회사", location="서울 강남구", career="신입",
                education="대졸", employment_type="정규직", salary_text="3,600~4,000만원", deadline="2026-10-31")
    base.update(kw)
    return build("sample", "x", **base)


def test_perfect_match():
    r = fit.evaluate(posting(), PROFILE, TODAY)
    assert r.score == 100 and r.grade == "적합" and r.eligible
    assert set(r.matched) == {"Python", "SQL", "데이터"}


def test_region_and_salary_lower_score():
    r = fit.evaluate(posting(location="부산 해운대구", salary_text="3,000만원"), PROFILE, TODAY)
    assert r.parts["지역"][0] == 0
    assert r.parts["연봉"][0] < 25
    assert r.eligible                         # 점수는 낮아도 지원은 가능


def test_remote_counts_as_region():
    assert fit.evaluate(posting(location="재택근무"), PROFILE, TODAY).parts["지역"][0] == 25


def test_unknown_salary_is_half():
    r = fit.evaluate(posting(salary_text="회사내규에 따름"), PROFILE, TODAY)
    assert r.parts["연봉"][0] == 12
    assert any("미공개" in w for w in r.warnings)


def test_experienced_only_blocks_newcomer():
    r = fit.evaluate(posting(career="경력 3년 이상"), PROFILE, TODAY)
    assert not r.eligible
    assert "경력 3년" in r.blockers[0]


def test_newcomer_or_experienced_ok():
    assert fit.evaluate(posting(career="신입/경력"), PROFILE, TODAY).parts["경력"][0] == 25


def test_experienced_profile():
    prof = dict(PROFILE, career_type="경력", years=4)
    assert fit.evaluate(posting(career="경력 3년 이상"), prof, TODAY).parts["경력"][0] == 25
    near = fit.evaluate(posting(career="경력 5년 이상"), prof, TODAY)
    assert near.eligible and near.parts["경력"][0] == 17     # 1년 부족은 경고만
    assert not fit.evaluate(posting(career="경력 7년 이상"), prof, TODAY).eligible


def test_education_exclude_and_closed():
    r = fit.evaluate(posting(education="석사 이상"), PROFILE, TODAY)
    assert not r.eligible and r.score == 85
    r = fit.evaluate(posting(title="생산직 3교대"), PROFILE, TODAY)
    assert not r.eligible and r.score <= 10
    r = fit.evaluate(posting(deadline="2026-09-01"), PROFILE, TODAY)
    assert any("마감" in b for b in r.blockers)


def test_empty_profile_is_full_score():
    empty = {"regions": [], "min_salary": 0, "career_type": "신입", "skills": [], "interests": [],
             "exclude": [], "employment_types": [], "education": "무관"}
    assert fit.evaluate(posting(location="제주"), empty, TODAY).score == 100

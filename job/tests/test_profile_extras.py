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


def test_exclude_presets_and_hiding(app, client):
    from core import exclude
    rows = [build("saramin", "1", title="서울/경기 노트북 출장 수리 엔지니어 모집", company="가상A"),
            build("saramin", "2", title="전동공구 수리 서비스 경력직원", company="가상B"),
            build("saramin", "3", title="[코스피 중견] 구매팀장", company="가상서치"),
            build("saramin", "4", title="백엔드 개발자", company="가상D"),
            build("saramin", "5", title="HTML 퍼블리셔", company="가상E")]
    assert exclude.matches(rows[0], ["수리기사"]) == ["수리기사"]
    assert exclude.matches(rows[1], ["수리기사"]) == ["수리기사"]
    assert exclude.matches(rows[3], ["수리기사", "헤드헌팅"]) == []
    assert exclude.matches(rows[4], ["텔레마케팅"]) == []                       # 'HTML' 안의 TM 은 아님
    assert exclude.matches({"title": "A/S 접수 사무", "description": "수리 엔지니어"}, ["수리기사"]) == []  # 설명은 안 봄
    postings.upsert_many(rows)
    postings.add_flags("saramin", ["3"], "헤드헌팅")
    client.post("/profile", data={"career_type": "모두", "education": "무관",
                                  "exclude_presets": ["헤드헌팅", "수리기사", "없는항목"], "exclude": "야간, 헤드헌팅"})
    assert profile.load()["exclude"] == ["헤드헌팅", "수리기사", "야간"]
    html = client.get("/jobs").get_data(as_text=True)
    assert "가상D" in html and "가상A" not in html and "가상B" not in html and "가상서치" not in html
    assert "(3건 숨김)" in html
    html = client.get("/jobs?show_excluded=1").get_data(as_text=True)
    assert "가상서치" in html and "헤드헌팅</span>" in html and "가상A" in html
    page = client.get("/profile").get_data(as_text=True)
    assert 'value="헤드헌팅" checked' in page and 'value="수리기사" checked' in page
    assert 'name="exclude" value="야간"' in page                                # 프리셋은 체크로, 나머지만 글칸에


def test_exclude_ignores_site_category_name():
    """사이트 직무 이름('고객상담·TM')만으로는 텔레마케팅으로 걸리지 않는다."""
    from core import exclude
    p = build("saramin", "1", title="건설 경력사원 공개채용", company="가상", job_category="고객상담·TM")
    assert exclude.matches(p, ["텔레마케팅"]) == []
    assert exclude.matches(build("saramin", "2", title="중고차 TM 상담원", company="가상"), ["텔레마케팅"]) == ["텔레마케팅"]


def test_exclude_preset_name_in_text_box(client):
    """글칸에 '파견·도급'을 적어도 '파견'·'도급' 으로 쪼개지 않고 항목으로 저장한다."""
    client.post("/profile", data={"career_type": "모두", "education": "무관", "exclude": "파견·도급, 강사, 컴퓨터 수리"})
    assert profile.load()["exclude"] == ["파견·도급", "강사", "컴퓨터 수리"]


def test_exclude_contract_only():
    from core import exclude
    def ex(emp, title="사무 담당"):
        return exclude.matches({"title": title, "employment_type": emp}, ["계약직"]) == ["계약직"]
    assert ex("계약직") and ex("계약직, 계약직") and ex("인턴, 계약직") and ex("계약직 (정규직 전환 가능)")
    assert ex("정규직·기간제") is False and ex("정규직, 계약직") is False and ex("계약직, 정규직") is False
    assert ex("정규직") is False and ex("정규직 수습기간 3개월") is False
    assert ex(None, "경리 계약직(육아휴직 대체근무자)") and not ex(None, "[정규직/계약직] 사무원")
    assert ex("정규직", "시장조사 (1년 계약직)") is False                    # 고용형태에 정규직이 있으면 그쪽을 믿음


def test_exclude_driving_not_plant_operation():
    from core import exclude
    hit = lambda t: exclude.matches({"title": t}, ["운전"]) == ["운전"]
    assert hit("[더셀피부과] 운전기사 모집") and hit("승용차 운전 기사 채용") and hit("임원 수행기사")
    assert not hit("[시운전 · O&M(운영) · 경상정비] 발전소 플랜트") and not hit("현장설비운전 채용")


def test_exclude_gym_and_academy_not_healthcare_or_grad_school():
    from core import exclude
    gym = lambda t, c="가상": exclude.matches({"title": t, "company": c}, ["헬스·피트니스"]) == ["헬스·피트니스"]
    assert gym("멋진 트레이너 모집", "헬스보이짐") and gym("동래PT샵1등 트레이너 구인") and gym("필라테스 강사")
    assert not gym("제약영업 채용", "(주)퍼슨헬스케어") and not gym("[AI 헬스케어] 마케터") and not gym("ePT Project Manager")
    aca = lambda t, c="가상": exclude.matches({"title": t, "company": c}, ["학원"]) == ["학원"]
    assert aca("영어 선생님 채용", "(주)DYB최선어학원") and aca("영어 보조교사", "고래영어교습소") and aca("수학학원 상담실장")
    assert not aca("대학원 석사 연구원 채용") and not aca("경영지원 사무", "가상산업")


def test_broad_group_words_when_no_sub_word():
    """세부 직무 단어가 없는 '이커머스 마케팅 담당자', '포워딩 영업 경력' 도 직무로 묶는다 (일반 공고는 그대로 미분류)."""
    from core import jobgroups
    assert jobgroups.groups_of({"title": "[노블러스] 이커머스 관리 및 마케팅 담당자"}) == {"마케팅·광고·홍보"}
    assert jobgroups.groups_of({"title": "포워딩 해상 신규 영업 경력 채용"}) == {"영업·판매·무역"}
    assert jobgroups.groups_of({"title": "각 부문별 신입/경력 채용"}) == set()
    assert jobgroups.groups_of({"title": "[교육3일 바로입사] 정규직 채용"}) == set()
    # 세부 직무 단어가 있으면 넓은 단어는 보지 않는다
    assert jobgroups.groups_of({"title": "해외영업 담당자"}) == {"영업·판매·무역"}

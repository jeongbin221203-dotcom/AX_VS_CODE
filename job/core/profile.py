"""내 구직 조건. settings 테이블에 JSON 한 줄로 보관한다."""
from __future__ import annotations

import json

from . import db, jobgroups
from .exclude import PRESETS
from .normalize import EDUCATION_LEVELS, split_keywords

DEFAULT = {
    "name": "",
    "regions": [],             # ['서울', '경기'] — 비우면 지역 무관
    "remote_ok": True,         # 재택 공고도 지역 조건 충족으로 본다
    "min_salary": 0,           # 희망 최저 연봉(만원), 0 = 조건 없음
    "career_type": "신입",      # 신입 / 경력 / 모두(조건 없음)
    "years": 0,                # 경력 년수
    "education": "대졸",
    "skills": [],              # 보유 기술·자격 (적합성 키워드)
    "interests": [],           # 희망 직무 키워드
    "job_groups": [],          # 희망 직무 (core.jobgroups 의 통합 직무) — 비우면 모든 직무
    "job_subs": [],            # 희망 세부 직무 '직무/세부 직무' — 그 직무 안에서만 좁힘
    "exclude": [],             # 이 단어가 들어간 공고는 제외 (예: 영업, 교대)
    "employment_types": ["정규직"],
}
EMPLOYMENT_TYPES = ["정규직", "계약직", "인턴", "파견직", "프리랜서", "아르바이트"]
SALARY_STEPS = list(range(3000, 8001, 500))   # 희망 최저 연봉 선택지 (만원), 0 = 상관없음


def load() -> dict:
    raw = db.get_setting("profile")
    data = dict(DEFAULT)
    if raw:
        try:
            data.update(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return data


def save(data: dict) -> dict:
    merged = dict(DEFAULT)
    merged.update({k: v for k, v in data.items() if k in DEFAULT})
    db.set_setting("profile", json.dumps(merged, ensure_ascii=False))
    return merged


def _exclude_from_form(form) -> list[str]:
    """체크한 제외 항목 + 글칸 단어. 글칸에 항목 이름('파견·도급')을 적어도 '·' 에서 쪼개지 않고 항목으로 본다."""
    words = [w for w in form.getlist("exclude_presets") if w in PRESETS]
    text = form.get("exclude", "")
    for name in PRESETS:
        if name in text:
            words.append(name)
            text = text.replace(name, ",")
    return list(dict.fromkeys(words + split_keywords(text)))[:40]


def from_form(form) -> dict:
    def num(name: str) -> int:
        try:
            return max(0, int(str(form.get(name, "0")).replace(",", "") or 0))
        except ValueError:
            return 0

    edu = form.get("education", "대졸")
    return {
        "name": form.get("name", "").strip()[:40],
        "regions": form.getlist("regions"),
        "remote_ok": form.get("remote_ok") == "1",
        "min_salary": num("min_salary"),
        "career_type": form.get("career_type") if form.get("career_type") in ("신입", "경력", "모두") else "신입",
        "years": num("years") if form.get("career_type") == "경력" else 0,
        "education": edu if edu in EDUCATION_LEVELS else "대졸",
        "skills": split_keywords(form.get("skills", ""))[:40],
        "interests": split_keywords(form.get("interests", ""))[:40],
        "exclude": _exclude_from_form(form),
        "employment_types": [t for t in form.getlist("employment_types") if t in EMPLOYMENT_TYPES],
        "job_groups": [g for g in form.getlist("job_groups") if g in jobgroups.NAMES],
        "job_subs": [s for s in form.getlist("job_subs")
                     if "/" in s and s.split("/", 1)[1] in jobgroups.sub_names(s.split("/", 1)[0])],
    }

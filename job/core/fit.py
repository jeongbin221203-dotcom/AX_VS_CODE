"""공고와 내 조건을 비교해 적합성 점수(100점)와 이유를 만든다.

지역 25 + 연봉 25 + 경력 25 + 직무·기술 25 에서 학력·고용형태가 맞지 않으면 감점한다.
'지원 불가' 사유(마감, 경력 부족, 학력 미달, 제외 단어)는 점수와 따로 표시한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .normalize import EDUCATION_LEVELS

W = 25


@dataclass
class FitResult:
    score: int
    grade: str
    parts: dict = field(default_factory=dict)        # 항목 → (점수, 설명)
    reasons: list = field(default_factory=list)      # 좋은 점
    warnings: list = field(default_factory=list)     # 확인할 점
    blockers: list = field(default_factory=list)     # 지원 불가 사유
    matched: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    excluded: list = field(default_factory=list)     # 걸린 제외 항목 (목록에서 숨김)

    @property
    def eligible(self) -> bool:
        return not self.blockers


SUSPICIOUS_ANNUAL = 20_000   # 연 2억 이상(만원)은 단위 오기일 수 있어 점수에서 미공개처럼

def grade_of(score: int) -> str:
    if score >= 80:
        return "적합"
    if score >= 60:
        return "보통"
    if score >= 40:
        return "낮음"
    return "부적합"


def _job_match(p: dict, prof: dict, r: FitResult) -> bool | None:
    """희망 직무를 고르지 않았으면 None. 고른 직무에 속하고, 그 직무의 세부 직무를 골랐다면 그것도 맞아야 True."""
    from . import jobgroups
    groups = prof.get("job_groups") or []
    if not groups:
        return None
    subs = prof.get("job_subs") or []                 # '직무/세부 직무'
    for g in jobgroups.groups_of(p) & set(groups):
        wanted = {s.split("/", 1)[1] for s in subs if s.startswith(g + "/")}
        if not wanted:
            r.reasons.append(f"희망 직무: {g}")
            return True
        hit = jobgroups.subs_of(p, g) & wanted
        if hit:
            r.reasons.append(f"희망 직무: {g} · {', '.join(sorted(hit))}")
            return True
    r.warnings.append("희망 직무가 아님")
    return False


def evaluate(p: dict, prof: dict, today: date | None = None) -> FitResult:
    today = today or date.today()
    r = FitResult(0, "")
    text = " ".join(str(p.get(k) or "") for k in
                    ("title", "company", "job_category", "keywords", "description")).lower()

    # ── 지역 ──
    regions = prof.get("regions") or []
    sido = p.get("sido")
    if not regions:
        r.parts["지역"] = (W, "지역 조건 없음")
    elif sido in regions:
        r.parts["지역"] = (W, f"희망 지역({sido})")
        r.reasons.append(f"희망 지역 {sido}{' ' + p['sigungu'] if p.get('sigungu') else ''}")
    elif sido == "재택" and prof.get("remote_ok"):
        r.parts["지역"] = (W, "재택 근무")
        r.reasons.append("재택 근무 가능")
    elif sido == "전국":
        r.parts["지역"] = (round(W * 0.6), "전국(근무지 확인 필요)")
        r.warnings.append("근무지가 '전국' — 실제 근무지 확인")
    elif not sido:
        r.parts["지역"] = (round(W * 0.5), "지역 정보 없음")
        r.warnings.append("근무 지역 정보 없음")
    else:
        r.parts["지역"] = (0, f"희망 지역 아님({sido})")
        r.warnings.append(f"희망 지역이 아님: {sido}")

    # ── 연봉 ──
    want = int(prof.get("min_salary") or 0)
    lo, hi = p.get("salary_min"), p.get("salary_max")
    top = hi or lo
    from .salary import is_commission
    if not want:
        r.parts["연봉"] = (W, "연봉 조건 없음")
    elif is_commission(p):
        r.parts["연봉"] = (round(W * 0.5), "성과급 직군 — 연봉 확인 필요")
        r.warnings.append("위촉·프리랜서 등 실적에 따른 수입 — 공고의 금액은 예시라 확인 필요")
    elif top is None:
        r.parts["연봉"] = (round(W * 0.5), "연봉 미공개·협의")
        r.warnings.append("연봉 미공개 — 면접 때 확인")
    elif top >= SUSPICIOUS_ANNUAL:
        # 원문 그대로지만 '연봉 31,500~41,200만원' 처럼 단위를 잘못 넣은 경우가 많다 — 값은 두고 점수는 미공개와 같게
        r.parts["연봉"] = (round(W * 0.5), "금액 확인 필요")
        r.warnings.append(f"원문 연봉이 {top:,}만원 — 단위를 잘못 적었을 수 있어 확인 필요")
    elif (lo or top) >= want:
        r.parts["연봉"] = (W, "희망 연봉 이상")
        r.reasons.append(f"연봉이 희망({want:,}만원) 이상")
    elif top >= want:
        r.parts["연봉"] = (round(W * 0.8), "상한은 희망 연봉 이상")
        r.reasons.append("연봉 상한이 희망 연봉에 닿음")
    else:
        ratio = top / want
        pts = 0 if ratio < 0.7 else round(W * (ratio - 0.7) / 0.3 * 0.7)
        r.parts["연봉"] = (pts, f"희망보다 {want - top:,}만원 낮음")
        r.warnings.append(f"연봉이 희망보다 {want - top:,}만원 낮음")

    # ── 경력 ──
    mine = prof.get("career_type", "신입")
    years = int(prof.get("years") or 0) if mine == "경력" else 0
    kind = p.get("career_type") or "무관"
    cmin, cmax = p.get("career_min"), p.get("career_max")
    if mine == "모두":
        r.parts["경력"] = (W, "경력 조건 없음 (신입·경력 모두)")
    elif kind in ("무관",):
        r.parts["경력"] = (W, "경력 무관")
    elif kind == "신입":
        if mine == "신입":
            r.parts["경력"] = (W, "신입 채용")
            r.reasons.append("신입 채용")
        else:
            r.parts["경력"] = (round(W * 0.4), "신입 공고(경력자)")
            r.warnings.append("신입 공고 — 경력 인정 여부 확인")
    else:  # 경력 / 신입·경력
        need = cmin or (1 if kind == "경력" else 0)
        if kind == "신입·경력" and mine == "신입":
            r.parts["경력"] = (W, "신입 지원 가능")
            r.reasons.append("신입도 지원 가능")
        elif years >= need:
            if cmax and years > cmax + 2:
                r.parts["경력"] = (round(W * 0.6), f"요구 경력({need}~{cmax}년)보다 많음")
                r.warnings.append(f"요구 경력 {need}~{cmax}년보다 경력이 많음")
            else:
                r.parts["경력"] = (W, f"경력 {need}년 이상 충족")
                r.reasons.append(f"경력 조건 충족({need}년 이상)")
        else:
            gap = need - years
            r.parts["경력"] = (max(0, W - gap * 8), f"경력 {gap}년 부족")
            if gap >= 2 or mine == "신입":
                r.blockers.append(f"경력 {need}년 이상 요구 (내 경력 {years}년)")
            else:
                r.warnings.append(f"경력 {gap}년 부족 — 우대 조건이면 지원 가능")

    # ── 직무·기술: 희망 직무(통합 직무·세부 직무)와 기술 키워드를 반반 ──
    wants = [k for k in (prof.get("skills") or []) + (prof.get("interests") or []) if k]
    job_ok = _job_match(p, prof, r)
    if not wants:
        if job_ok is None:
            r.parts["직무·기술"] = (W, "직무·키워드 조건 없음")
        else:
            r.parts["직무·기술"] = (W if job_ok else 0, "희망 직무" if job_ok else "희망 직무 아님")
    else:
        seen = set()
        for k in wants:
            key = k.lower()
            if key in seen:
                continue
            seen.add(key)
            (r.matched if key in text else r.missing).append(k)
        need = min(3, len(seen))
        ratio = min(1.0, len(r.matched) / need)
        if job_ok is not None:
            ratio = (ratio + (1.0 if job_ok else 0.0)) / 2
        pts = round(W * ratio)
        r.parts["직무·기술"] = (pts, f"키워드 {len(r.matched)}/{len(seen)}개 일치"
                                  + ("" if job_ok is None else (" · 희망 직무" if job_ok else " · 희망 직무 아님")))
        if r.matched:
            r.reasons.append("일치 키워드: " + ", ".join(r.matched[:6]))
        else:
            r.warnings.append("내 기술·희망 직무 키워드가 공고에 없음")

    score = sum(v[0] for v in r.parts.values())

    # ── 감점·지원 불가 ──
    edu_req = p.get("education") or "무관"
    my_edu = prof.get("education") or "무관"
    if edu_req != "무관" and my_edu != "무관" and edu_req in EDUCATION_LEVELS:
        if EDUCATION_LEVELS.index(my_edu) < EDUCATION_LEVELS.index(edu_req):
            score -= 15
            r.blockers.append(f"학력 {edu_req} 이상 요구 (내 학력 {my_edu})")

    types = prof.get("employment_types") or []
    emp = p.get("employment_type") or ""
    if types and emp and not any(t in emp for t in types):
        score -= 10
        r.warnings.append(f"고용형태가 희망과 다름: {emp}")

    from .exclude import matches
    r.excluded = matches(p, prof.get("exclude") or [])
    if r.excluded:
        score = min(score, 10)
        r.blockers.append("제외 항목: " + ", ".join(r.excluded))

    dl = p.get("deadline")
    if dl:
        left = (date.fromisoformat(dl) - today).days
        if left < 0:
            r.blockers.append(f"마감된 공고 ({dl})")
        elif left <= 3:
            r.warnings.append(f"마감 임박 D-{left}")

    r.score = max(0, min(100, score))
    r.grade = grade_of(r.score)
    return r

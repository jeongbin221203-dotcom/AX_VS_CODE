"""목표 점수·시험일·약한 파트로 오늘의 학습 계획을 만든다."""
from __future__ import annotations

from datetime import date, datetime

from . import scoring, stats, study
from .content import PART_INFO, Bank
from .guide import GUIDE
from .srs import queue

MIN_ATTEMPTS_FOR_WEAKNESS = 8
LEVEL_UP_RATE = 0.8        # 이 정답률을 넘으면 한 등급 위 문제를 권한다
LEVEL_UP_MIN_N = 20


def _int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def current_score(settings: dict) -> tuple[int | None, str]:
    """현재 점수: 가장 최근 진단·모의고사 추정치 → 없으면 설정의 직접 입력 점수."""
    est = stats.latest_estimate()
    manual = _int(settings.get("current_score"))
    if est and est["total_est"] is not None:
        if manual is not None and est["finished_at"] < settings.get("current_score_at", ""):
            return manual, "직접 입력"
        return est["total_est"], "추정 (" + ("진단" if est["mode"] == "diagnostic" else "모의고사") + ")"
    if manual is not None:
        return manual, "직접 입력"
    return None, ""


def recommended_level(part: int, grade_level: int, lvl_acc: dict) -> int:
    """기본은 현재 등급. 그 등급에서 충분히 맞히면 한 단계 위를 권한다."""
    a = lvl_acc.get((part, grade_level))
    if a and a["n"] >= LEVEL_UP_MIN_N and a["rate"] >= LEVEL_UP_RATE and grade_level < 5:
        return grade_level + 1
    return grade_level


def build(bank: Bank, settings: dict, today: date | None = None) -> dict:
    today = today or date.today()
    score, source = current_score(settings)
    grade = scoring.grade_for(score) if score is not None else None
    target = _int(settings.get("target_score"), 800)
    target_grade = scoring.grade_for(target)
    exam = None
    days_left = None
    if settings.get("exam_date"):
        try:
            exam = datetime.strptime(settings["exam_date"], "%Y-%m-%d").date()
            days_left = (exam - today).days
        except ValueError:
            exam = None
    gap = (target - score) if score is not None else None
    weekly_gain = None
    if gap is not None and gap > 0 and days_left and days_left > 0:
        weekly_gain = round(gap / max(days_left / 7, 1))

    lv = grade.level if grade else 1
    guide = GUIDE[lv]
    acc = stats.part_accuracy(last_n=60)
    lvl_acc = stats.level_accuracy()

    # 약한 파트: 기록이 충분하면 (등급 목표 정답률 - 실제 정답률)이 큰 순, 아니면 등급 기본 순서
    focus = list(guide["focus_parts"])
    measured = {p: a["rate"] for p, a in acc.items() if a["n"] >= MIN_ATTEMPTS_FOR_WEAKNESS}
    if measured:
        def shortfall(p):
            return guide["targets"][p] - measured[p]
        # 이 등급의 목표가 없는 파트(예: 1등급의 Part 3·7)는 약점 계산에서 뺀다 — 기초 파트를 밀어내지 않게
        weak = sorted((p for p in measured if p in guide["targets"]), key=shortfall, reverse=True)
        weak = [p for p in weak if shortfall(p) > 0]
        focus = weak[:2] + [p for p in focus if p not in weak[:2]]
    focus = focus[:3]

    daily_q = _int(settings.get("daily_questions"), 40)
    daily_new = _int(settings.get("daily_new_words"), guide["daily_new_words"])
    today_done = stats.today_counts()
    vq = queue(bank, None, daily_new, start_level=lv)
    open_notes = len(study.wrong_notes("open"))

    tasks = []
    tasks.append({"kind": "vocab", "title": "단어 복습 + 새 단어",
                  "detail": f"복습 {len(vq['due'])}개 · 새 단어 {len(vq['new'])}개",
                  "done": not vq["due"] and not vq["new"], "href": "/vocab/study"})
    share = [0.5, 0.3, 0.2]
    for i, p in enumerate(focus):
        n = max(5, round(daily_q * share[i]))
        if p in (3, 4):
            n = max(3, round(n / 3) * 3)
        rl = recommended_level(p, lv, lvl_acc)
        done = today_done["per_part"].get(p, 0)
        tasks.append({"kind": "part", "part": p, "level": rl,
                      "title": f"Part {p} {PART_INFO[p]['name']}",
                      "detail": f"{scoring.GRADE_BY_LEVEL[rl].name} 등급 {n}문항" + (f" · 오늘 {done}문항 풂" if done else ""),
                      "done": done >= n, "href": f"/practice/start?part={p}&level={rl}&n={n}"})
    if open_notes:
        tasks.append({"kind": "review", "title": "오답노트 복습", "detail": f"남은 오답 {open_notes}문항 중 10문항",
                      "done": today_done["reviewed"] >= min(10, open_notes), "href": "/review/start?n=10"})
    last = stats.latest_estimate()
    if not last:
        tasks.insert(0, {"kind": "diagnostic", "title": "진단 테스트", "detail": "약 20분 · 현재 등급을 먼저 확인",
                         "done": False, "href": "/diagnostic"})
    else:
        last_day = datetime.fromisoformat(last["finished_at"]).date()
        if (today - last_day).days >= 7:
            form = "half" if lv >= 3 else "mini"
            tasks.append({"kind": "mock", "title": scoring.MOCK_FORMS[form]["name"],
                          "detail": f"마지막 점수 확인 {(today - last_day).days}일 전", "done": False,
                          "href": f"/mock?form={form}"})

    return {
        "score": score, "score_source": source, "grade": grade,
        "target": target, "target_grade": target_grade, "gap": gap,
        "exam": exam, "days_left": days_left, "weekly_gain": weekly_gain,
        "guide": guide, "focus": focus, "tasks": tasks,
        "today": today_done, "daily_q": daily_q, "open_notes": open_notes,
        "vocab_due": len(vq["due"]), "vocab_new": len(vq["new"]),
    }

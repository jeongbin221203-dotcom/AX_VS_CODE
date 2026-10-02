"""상단 메뉴: 시험(토익·토플·…)을 누르면 그 시험의 메뉴가 펼쳐진다."""
from __future__ import annotations

from flask import request, url_for

from core.exams import EXAMS

# (이름, endpoint, url 인자, 이 항목으로 볼 endpoint 목록)
MENUS = {
    "toeic": [
        ("오늘", "main.dashboard", {}, ["main.dashboard"]),
        ("등급 가이드", "main.guide", {}, ["main.guide"]),
        ("파트 연습", "quiz.practice", {}, ["quiz.practice", "quiz.practice_start", "quiz.quiz"]),
        ("모의고사", "quiz.mock", {}, ["quiz.mock", "quiz.diagnostic", "quiz.result"]),
        ("오답노트", "quiz.review", {}, ["quiz.review"]),
        ("단어", "vocab.overview", {}, ["vocab."]),
        ("받아쓰기", "quiz.dictation", {}, ["quiz.dictation"]),
        ("통계", "main.stats_page", {}, ["main.stats_page", "main.history"]),
    ],
    "toefl": [
        ("토플 홈", "toefl.home", {}, ["toefl.home"]),
        ("Reading", "toefl.home", {"_anchor": "sec-R"}, ["toefl.practice:r_"]),
        ("Listening", "toefl.home", {"_anchor": "sec-L"}, ["toefl.practice:l_"]),
        ("Speaking", "toefl.home", {"_anchor": "sec-S"}, ["toefl.practice:s_"]),
        ("Writing", "toefl.home", {"_anchor": "sec-W"}, ["toefl.practice:w_"]),
        ("실전 모의고사", "toefl.mock", {}, ["toefl.mock"]),
        ("학술 어휘", "tvocab.overview", {}, ["tvocab."]),
    ],
    "toeic-speaking": [
        ("홈", "speaking.tsp_home", {}, ["speaking.tsp_home"]),
        ("유형별 연습", "speaking.tsp_home", {"_anchor": "tasks"}, ["speaking.tsp_practice"]),
        ("실전 모의고사", "speaking.tsp_mock", {}, ["speaking.tsp_mock", "speaking.mock_run:tsp", "speaking.mock_result:tsp"]),
        ("답변 틀·채점 기준", "speaking.tsp_guide", {}, ["speaking.tsp_guide"]),
    ],
    "opic": [
        ("홈", "speaking.opic_home", {}, ["speaking.opic_home"]),
        ("설문·난이도", "speaking.opic_survey", {}, ["speaking.opic_survey"]),
        ("주제별 연습", "speaking.opic_home", {"_anchor": "topics"}, ["speaking.opic_practice"]),
        ("실전 모의고사", "speaking.opic_mock", {}, ["speaking.opic_mock", "speaking.mock_run:opic", "speaking.mock_result:opic"]),
        ("답변 틀·등급 기준", "speaking.opic_guide", {}, ["speaking.opic_guide"]),
    ],
}


def _mock_exam() -> str:
    from core import speaking
    m = speaking.get_mock((request.view_args or {}).get("mid", 0))
    return m["exam"] if m else ""


def _matches(pattern: str, ep: str) -> bool:
    if pattern.startswith("speaking.mock_") and ":" in pattern:      # 모의고사 화면 → 어느 시험 것인지
        name, exam = pattern.split(":", 1)
        return ep == name and _mock_exam() == exam
    if ":" in pattern:                      # "toefl.practice:r_" → 과제 이름 앞부분까지 비교
        name, prefix = pattern.split(":", 1)
        return ep == name and str((request.view_args or {}).get("task", "")).startswith(prefix)
    return ep.startswith(pattern) if pattern.endswith(".") else ep == pattern


def current_exam(ep: str) -> str:
    if ep == "main.exam":
        return (request.view_args or {}).get("key", "toeic")
    if ep.startswith("toefl.") or ep.startswith("tvocab."):
        return "toefl"
    if ep.startswith("speaking."):
        if ep.startswith("speaking.mock_"):
            return "toeic-speaking" if _mock_exam() == "tsp" else "opic"
        return "opic" if ep.startswith("speaking.opic") else "toeic-speaking"
    if ep == "main.settings":
        return ""
    return "toeic"


def nav_menus() -> list[dict]:
    ep = request.endpoint or ""
    cur = current_exam(ep)
    out = []
    for key, e in EXAMS.items():
        items = []
        for label, endpoint, args, pats in MENUS.get(key, []):
            args = dict(args)
            anchor = args.pop("_anchor", None)
            href = url_for(endpoint, **args) + (f"#{anchor}" if anchor else "")
            items.append({"label": label, "href": href, "on": key == cur and any(_matches(p, ep) for p in pats)})
        if not e["ready"]:
            items = [{"label": f"{e['name']} 안내 (준비 중)", "href": url_for("main.exam", key=key), "on": key == cur}]
        active = next((i["label"] for i in items if i["on"]), "")
        home = url_for(e["endpoint"]) if e["ready"] else url_for("main.exam", key=key)
        out.append({"key": key, "name": e["name"], "ready": e["ready"], "on": key == cur, "items": items, "home": home,
                    "active": active if e["ready"] else ""})
    return out

from __future__ import annotations

import secrets

from flask import Flask, current_app, session
from markupsafe import Markup, escape

from core import scoring
from core.content import PART_INFO, Bank
from core.exams import EXAMS


def bank() -> Bank:
    return current_app.extensions["bank"]


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def grade_badge(level_or_grade, suffix: str = "") -> Markup:
    g = level_or_grade if isinstance(level_or_grade, scoring.Grade) else scoring.GRADE_BY_LEVEL.get(level_or_grade)
    if not g:
        return Markup('<span class="badge badge-none">미측정</span>')
    return Markup(f'<span class="badge g{g.level}">{escape(g.name)}{escape(suffix)}</span>')


def pct(v) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def mmss(sec) -> str:
    if sec is None:
        return "–"
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}시간 {sec % 3600 // 60}분"


MODE_LABEL = {"practice": "파트 연습", "diagnostic": "진단 테스트", "mock": "모의고사", "review": "오답 복습"}


def session_title(s: dict) -> str:
    if s["mode"] == "mock":
        return scoring.MOCK_FORMS.get(s["variant"] or "", {}).get("name", "모의고사")
    if s["mode"] == "practice" and s["part"]:
        t = f"Part {s['part']} {PART_INFO[s['part']]['name']}"
        if s["level"]:
            t += f" · {scoring.GRADE_BY_LEVEL[s['level']].name}"
        if s["variant"]:
            t += f" · {s['variant']}"
        return t
    if s["mode"] == "review" and s["part"]:
        return f"오답 복습 · Part {s['part']}"
    return MODE_LABEL[s["mode"]]


def register_template_helpers(app: Flask) -> None:
    app.jinja_env.globals.update(
        csrf_token=csrf_token, grade_badge=grade_badge, GRADES=scoring.GRADES,
        GRADE_BY_LEVEL=scoring.GRADE_BY_LEVEL, PART_INFO=PART_INFO, MODE_LABEL=MODE_LABEL,
        session_title=session_title, EXAMS=EXAMS)
    app.jinja_env.filters.update(pct=pct, mmss=mmss)

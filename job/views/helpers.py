"""템플릿 공통 함수·필터."""
from __future__ import annotations

import json
import secrets

from flask import Flask, session

from core import salary
from core.applications import STATUSES
from core.sources import SOURCE_NAMES


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def dday_label(d: int | None) -> str:
    if d is None:
        return "상시"
    if d < 0:
        return "마감"
    return "D-day" if d == 0 else f"D-{d}"


def signed(v: int | None) -> str:
    if v is None:
        return "-"
    return ("+" if v > 0 else "") + f"{v:,}만원"


def _source_name(key: str) -> str:
    if key and key.startswith("crawl:"):
        sub = key[6:]
        return "크롤링 · " + ("저장 공고 갱신" if sub == "refresh" else SOURCE_NAMES.get(sub, sub))
    return SOURCE_NAMES.get(key, key)


def register_template_helpers(app: Flask) -> None:
    app.jinja_env.globals.update(csrf_token=csrf_token, STATUSES=STATUSES, SOURCE_NAMES=SOURCE_NAMES)
    app.jinja_env.filters.update(
        won=salary.format_manwon,
        dday=dday_label,
        signed=signed,
        source_name=_source_name,
        fromjson=json.loads,
        grade_class={"적합": "g-a", "보통": "g-b", "낮음": "g-c", "부적합": "g-d"}.get,
    )

    @app.template_filter("salary_range")
    def _salary_range(p) -> str:
        return salary.format_range(p.get("salary_min"), p.get("salary_max"), bool(p.get("salary_negotiable")))

"""급등주 분석 화면 — data/boom.json (core/boom_study.py)."""
import json

from flask import Blueprint, render_template

from core import boom_study

bp = Blueprint("boom", __name__)


def load():
    try:
        return json.loads(boom_study.BOOM_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/boom")
def page():
    return render_template("boom.html", rep=load())

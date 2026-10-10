"""급등 전 패턴 화면 — data/pattern.json (core/pattern_study.py)."""
import json

from flask import Blueprint, render_template

from core import pattern_study

bp = Blueprint("pattern", __name__)


def load():
    try:
        return json.loads(pattern_study.PATTERN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/pattern")
def page():
    return render_template("pattern.html", rep=load())

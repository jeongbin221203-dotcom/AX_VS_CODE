"""하루 +20% 급등일 분석 화면 — data/jump.json (core/jump_study.py)."""
import json

from flask import Blueprint, render_template

from core import jump_study

bp = Blueprint("jump", __name__)


def load():
    try:
        return json.loads(jump_study.JUMP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/jump")
def page():
    return render_template("jump.html", rep=load())

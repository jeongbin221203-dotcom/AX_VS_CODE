"""+20% 급등 전조 화면 — data/precursor.json (core/precursor_study.py)."""
import json

from flask import Blueprint, render_template

from core import precursor_study

bp = Blueprint("precursor", __name__)


def load():
    try:
        return json.loads(precursor_study.PRE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/precursor")
def page():
    return render_template("precursor.html", rep=load())

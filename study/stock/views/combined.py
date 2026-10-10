"""종합 전략 화면 — data/combined.json (core/combined_study.py)."""
import json

from flask import Blueprint, render_template

from core import combined_study

bp = Blueprint("combined", __name__)


def load():
    try:
        return json.loads(combined_study.COMBINED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/combined")
def page():
    return render_template("combined.html", rep=load())

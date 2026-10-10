"""최적 매수일 화면 — data/bestday.json (core/bestday_study.py)."""
import json

from flask import Blueprint, render_template

from core import bestday_study

bp = Blueprint("bestday", __name__)


def load():
    try:
        return json.loads(bestday_study.BEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/bestday")
def page():
    return render_template("bestday.html", rep=load())

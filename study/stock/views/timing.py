"""매수 시점 화면 — data/timing.json (core/timing_study.py)."""
import json

from flask import Blueprint, render_template

from core import timing_study

bp = Blueprint("timing", __name__)


def load():
    try:
        return json.loads(timing_study.TIMING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/timing")
def page():
    return render_template("timing.html", rep=load())

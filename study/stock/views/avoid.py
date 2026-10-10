"""갭 줄이기 · 피해야 할 종목 화면 — data/avoid.json (core/avoid_study.py)."""
import json

from flask import Blueprint, render_template

from core import avoid_study

bp = Blueprint("avoid", __name__)


def load():
    try:
        return json.loads(avoid_study.AVOID_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/avoid")
def page():
    return render_template("avoid.html", rep=load())

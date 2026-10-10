"""매매 계획 화면 — data/plan.json (core/plan_study.py)."""
import json

from flask import Blueprint, render_template

from core import plan_study

bp = Blueprint("plan", __name__)


def load():
    try:
        return json.loads(plan_study.PLAN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/plan")
def page():
    return render_template("plan.html", rep=load())

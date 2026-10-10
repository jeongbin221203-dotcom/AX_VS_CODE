"""전략 보강 화면 — data/strategy.json (core/strategy_study.py)."""
import json

from flask import Blueprint, render_template

from core import strategy_study

bp = Blueprint("strategy", __name__)


def load():
    try:
        return json.loads(strategy_study.STRATEGY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/strategy")
def page():
    return render_template("strategy.html", rep=load())

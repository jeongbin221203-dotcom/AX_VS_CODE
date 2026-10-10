"""거래정지 위험·성공률 보강 화면 — data/risk.json (core/risk_study.py)."""
import json

from flask import Blueprint, render_template

from core import risk_study

bp = Blueprint("risk", __name__)


def load():
    try:
        return json.loads(risk_study.RISK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/risk")
def page():
    return render_template("risk.html", rep=load())

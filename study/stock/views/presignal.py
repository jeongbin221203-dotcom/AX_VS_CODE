"""단테 신호 한 주 전 매수 화면 — data/presignal.json (core/presignal_study.py)."""
import json

from flask import Blueprint, render_template

from core import presignal_study

bp = Blueprint("presignal", __name__)


def load():
    try:
        return json.loads(presignal_study.PRESIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/presignal")
def page():
    return render_template("presignal.html", rep=load())

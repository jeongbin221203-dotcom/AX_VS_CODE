"""조용한 대량 거래 화면 — data/quietvol.json (core/quietvol_study.py)."""
import json

from flask import Blueprint, render_template

from core import quietvol_study

bp = Blueprint("quietvol", __name__)


def load():
    try:
        return json.loads(quietvol_study.QV_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/quietvol")
def page():
    return render_template("quietvol.html", rep=load())

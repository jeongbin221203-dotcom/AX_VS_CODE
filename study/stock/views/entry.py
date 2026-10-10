"""진입 시점 연구 화면 — data/entry.json (core/entry_study.py 가 만든다)."""
import json

from flask import Blueprint, render_template

from core import entry_study

bp = Blueprint("entry", __name__)


def load():
    try:
        return json.loads(entry_study.ENTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@bp.get("/entry")
def page():
    rep = load()
    if not rep:
        return render_template("entry.html", rep=None)
    return render_template("entry.html", rep=rep)

"""지원 현황판."""
from __future__ import annotations

from flask import Blueprint, render_template

from core import applications

bp = Blueprint("apps", __name__, url_prefix="/applications")


@bp.get("")
def board():
    return render_template("applications.html", board=applications.board(),
                           active=applications.ACTIVE, done=applications.DONE)

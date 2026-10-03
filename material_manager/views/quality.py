"""🩺 데이터 점검 화면 (core/quality.py). 읽기만 하고, 항목마다 '고치러 가기'로 그 화면에 간다."""

from flask import Blueprint, g

from core import quality
from views.helpers import render_page, role_required

bp = Blueprint("quality", __name__, url_prefix="/quality")


@bp.get("/")
@role_required("MANAGER")
def index():
    checks = quality.run(g.wh_ids)
    return render_page("quality.html", "quality", checks=checks, severity=quality.SEVERITY,
                       problems=sum(c["count"] for c in checks if c["severity"] != "low"))

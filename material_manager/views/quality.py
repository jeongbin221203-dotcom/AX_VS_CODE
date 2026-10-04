"""🩺 데이터 점검 화면 (core/quality.py). 읽기만 하고, 항목마다 '고치러 가기'로 그 화면에 간다."""

import pandas as pd
from flask import Blueprint, g, request

from core import quality
from views.helpers import form_response, log_export, render_page, role_or_data

bp = Blueprint("quality", __name__, url_prefix="/quality")


@bp.get("/")
@role_or_data("MANAGER")
def index():
    from core import cache
    if request.args.get("export") == "xlsx":
        checks = quality.run(g.wh_ids, limit=None)
    else:                                                        # 거래 전체를 훑으므로 60초 보관 (저장하면 비움)
        checks = cache.memo(("quality", cache.wh_key(g.wh_ids)), 60, lambda: quality.run(g.wh_ids, limit=30))
    if request.args.get("export") == "xlsx":                    # 전부 (화면은 항목마다 30건)
        view = pd.DataFrame([{"항목": c["title"], "심각도": quality.SEVERITY[c["severity"]], "내용": label, "고칠 곳": link}
                             for c in checks for label, link in c["rows"]], columns=["항목", "심각도", "내용", "고칠 곳"])
        log_export("quality", len(view))
        return form_response("quality", view, "데이터_점검.xlsx")
    return render_page("quality.html", "quality", checks=checks, severity=quality.SEVERITY,
                       problems=sum(c["count"] for c in checks if c["severity"] != "low"))

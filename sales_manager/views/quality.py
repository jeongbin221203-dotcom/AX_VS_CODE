"""데이터 점검 (core/quality.py) — 영업지원·시스템관리자."""
from flask import Blueprint, flash, g, redirect, url_for

from core import enterprise as ent
from core import quality as qc

from .helpers import Table, f_str, render_page, role_required

bp = Blueprint("quality", __name__, url_prefix="/admin/quality")


@bp.before_request
@role_required("SUPPORT")
def _gate():
    return None


@bp.route("")
def index():
    results = qc.run_all()
    for r in results:
        endpoint, extra, arg = r["link"] or (None, {}, None)
        r["table"] = Table(r["rows"], money=[c for c in ("예상금액", "합계", "입금액", "입금내역합계") if c in r["rows"].columns],
                           drop=["id"], link=(endpoint, "id", arg, {**extra, "_anchor": "edit"}) if arg else None)
        r["go"] = url_for(endpoint, **extra) if endpoint and not arg else None
        if endpoint and endpoint.startswith("admin.") and not ent.has_role(g.user, "ADMIN"):
            r["go"] = None                              # 시스템관리자 화면(조직·담당자 이관)은 영업지원에게 링크하지 않는다
    return render_page("admin/quality.html", "quality", results=results, summary=qc.summary(results))


@bp.route("/fix", methods=["POST"])
def fix():
    try:
        changed = qc.fix(f_str("action"))
        flash(f"{changed:,}건을 고쳤습니다." if changed else "고칠 건이 없었습니다.", "success" if changed else "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("quality.index"))

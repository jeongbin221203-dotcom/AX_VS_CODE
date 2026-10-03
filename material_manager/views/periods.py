"""월 마감 화면 (마감: 관리자, 해제: 시스템관리자)."""

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import periods
from views.helpers import Table, actor, f_str, render_page, role_required, scope_all

bp = Blueprint("periods", __name__, url_prefix="/periods")


@bp.get("/")
@role_required("MANAGER")
def index():
    ym = periods.closed_through()
    hist = periods.history_df().rename(columns={
        "id": "ID", "at": "일시", "action": "구분", "closed_through": "마감월(이후)",
        "user_name": "처리자", "reason": "사유"})
    hist["구분"] = hist["구분"].map({"CLOSE": "마감", "REOPEN": "해제"})
    snap = periods.snapshot_df(ym, g.wh_ids) if ym else None
    snap_view = None
    if snap is not None:
        snap_view = snap.rename(columns={"code": "자재코드", "name": "자재명", "wh_code": "창고", "unit": "단위", "qty": "월말재고",
                                         "unit_price": "현재 단가", "value": "금액(현재 단가 기준)"})
    return render_page(
        "periods.html", "periods", closed=ym, next_ym=periods.next_closable(),
        history=Table(hist, {"ID": "{}"}),
        snapshot=Table(snap_view, {"월말재고": "{:,.2f}", "현재 단가": "₩{:,.0f}", "금액(현재 단가 기준)": "₩{:,.0f}"})
        if snap_view is not None else None,
    )


@bp.post("/close")
@role_required("MANAGER")
def close():
    if not scope_all():
        abort(403, "월 마감은 회사 전체에 적용되므로 모든 창고 권한이 있는 관리자만 할 수 있습니다.")
    result = periods.close_month(f_str("ym"), actor())
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("periods.index"))


@bp.post("/reopen")
@role_required("ADMIN")
def reopen():
    if request.form.get("confirm") != "1":
        flash("마감 해제를 확인해 주세요.", "error")
        return redirect(url_for("periods.index"))
    result = periods.reopen(f_str("reason"), actor())
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("periods.index"))

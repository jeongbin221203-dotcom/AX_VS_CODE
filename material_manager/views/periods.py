"""월 마감 화면 (마감: 관리자, 해제: 시스템관리자)."""

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import periods
from views.helpers import Table, actor, f_str, form_response, render_page, role_required, scope_all

AP_COLS = {"po_no": "발주번호", "supplier": "공급처", "wh_code": "창고", "ordered": "발주금액", "received": "입고금액",
           "invoiced": "계산서 공급가액", "gr_ir": "GR/IR(입고−계산서)", "payment_status": "지급 상태"}
AP_STATUS = {"": "-", "WAIT": "계산서 대기", "MATCHED": "일치", "BLOCKED": "지급 보류", "RELEASED": "보류 해제"}

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
        snap_view = snap.rename(columns={"code": "자재코드", "name": "자재명", "wh_code": "창고", "lot_no": "로트", "unit": "단위", "qty": "월말재고",
                                         "unit_price": "현재 단가", "value": "금액(현재 단가 기준)"})
    closed_value = periods.closed_valuation(ym) if ym else {}
    nxt = periods.next_closable()
    checks = periods.close_checks(nxt) if nxt else None
    months = periods.ap_months()
    ap_ym = request.args.get("ap") if request.args.get("ap") in months else (months[0] if months else "")
    ap_view, ap_tot = None, None
    if ap_ym:
        ap = periods.ap_df(ap_ym, g.wh_ids)
        ap["payment_status"] = ap["payment_status"].map(lambda s: AP_STATUS.get(s or "", s))
        ap_view = ap.drop(columns="backfilled").rename(columns=AP_COLS)
        ap_tot = {"received": float(ap["received"].sum()), "invoiced": float(ap["invoiced"].sum()),
                  "gr_ir": float(ap["gr_ir"].sum()), "open": float(ap.loc[ap["gr_ir"] > 0.5, "gr_ir"].sum()),
                  "backfilled": bool(len(ap) and ap["backfilled"].max())}
        if request.args.get("export") == "xlsx":
            out = ap_view.copy()
            out.insert(0, "월", ap_ym)
            return form_response("ap_snapshot", out, f"월말미지급_{ap_ym}.xlsx", period=ap_ym)
    return render_page(
        "periods.html", "periods", closed=ym, next_ym=nxt, checks=checks, closed_value=closed_value,
        ap_months=months, ap_ym=ap_ym, ap_missing=periods.ap_missing(),
        ap_grid=Table(ap_view, {"발주금액": "₩{:,.0f}", "입고금액": "₩{:,.0f}", "계산서 공급가액": "₩{:,.0f}",
                                "GR/IR(입고−계산서)": "₩{:+,.0f}"}) if ap_view is not None else None, ap_total=ap_tot,
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


@bp.post("/ap-fill")
@role_required("MANAGER")
def ap_fill():
    if not scope_all():
        abort(403, "회사 전체 스냅샷이라 모든 창고 권한이 있는 관리자만 만들 수 있습니다.")
    result = periods.ap_backfill(actor())
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

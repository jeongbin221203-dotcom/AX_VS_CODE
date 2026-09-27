"""결재함. 담당자는 본인 요청을 보고, 관리자는 권한 창고의 요청을 승인·반려한다(본인 요청 제외)."""

from flask import Blueprint, flash, g, redirect, request, url_for

from core import approvals
from views.helpers import Table, actor, can, f_str, render_page, role_required

bp = Blueprint("approvals", __name__, url_prefix="/approvals")

COLS = {"id": "결재ID", "status": "상태", "tx_date": "일자", "wh_code": "창고", "code": "자재코드", "name": "자재명",
        "qty": "조정 수량", "amount": "금액", "requested_by": "요청자", "requested_at": "요청일시",
        "decided_by": "처리자", "decided_at": "처리일시", "comment": "의견", "result_tx_id": "거래ID"}
FMT = {"결재ID": "{}", "조정 수량": "{:+,.2f}", "금액": "₩{:,.0f}", "거래ID": "{}"}


@bp.get("/")
@role_required("CLERK")
def index():
    status = request.args.get("status", "PENDING")
    status = status if status in approvals.STATUS else None
    df = approvals.requests_df(status, g.wh_ids)
    if not can("MANAGER"):
        df = df[df["requested_by_id"] == g.user["id"]]          # 담당자는 본인 요청만
    view = df.copy()
    view["status"] = view["status"].map(approvals.STATUS)
    view = view[list(COLS)].rename(columns=COLS)
    actionable = (df[(df["status"] == "PENDING") & (df["requested_by_id"] != g.user["id"])].to_dict("records")
                  if can("MANAGER") else [])
    return render_page("approvals.html", "approvals", status=status, statuses=approvals.STATUS,
                       grid=Table(view, FMT), actionable=actionable)


@bp.post("/<int:req_id>")
@role_required("MANAGER")
def decide(req_id: int):
    result = approvals.decide(req_id, request.form.get("decision") == "approve", f_str("comment"),
                              actor(), wh_ids=g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("approvals.index"))

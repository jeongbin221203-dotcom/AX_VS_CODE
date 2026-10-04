"""결재함: 실사 조정 · 구매요청 · 발주 결재를 한곳에서 (core/workflow.py).

  📥 내 결재 대기   지금 내 차례인 결재 (대결 포함) — 여기서 바로 승인·반려
  📤 내 요청 현황   내가 올린 결재가 어디까지 왔는지
  🗃️ 결재 이력      내가 처리한 결재
  🔁 대결 지정      휴가·출장 동안 내 결재를 다른 사람에게 맡기기
  📋 실사 조정 전체 (관리자) 실사 조정 결재를 상태별로
"""

from flask import Blueprint, flash, g, redirect, request, url_for

import config
from core import approvals, auth, db, delegation, workflow
from views.helpers import Table, actor, can, f_str, form_response, log_export, render_page, role_required

bp = Blueprint("approvals", __name__, url_prefix="/approvals")

COLS = {"id": "결재ID", "kind": "종류", "status": "상태", "tx_date": "일자", "wh_code": "창고", "code": "자재코드", "name": "자재명",
        "qty": "수량 (조정: 차이 · 취소: 원거래)", "amount": "금액", "requested_by": "요청자", "requested_at": "요청일시",
        "decided_by": "처리자", "decided_at": "처리일시", "comment": "의견", "result_tx_id": "거래ID"}
FMT = {"결재ID": "{}", "수량 (조정: 차이 · 취소: 원거래)": "{:+,.2f}", "금액": "₩{:,.0f}", "거래ID": "{}"}


def _tabs():
    t = [("mine", "📥 내 결재 대기"), ("requests", "📤 내 요청 현황"), ("history", "🗃️ 결재 이력"), ("delegate", "🔁 대결 지정")]
    return t + ([("adj", "📋 실사 조정 전체")] if can("MANAGER") else [])


@bp.get("/")
@role_required("CLERK")
def index():
    tab = request.args.get("tab", "mine")
    ctx = dict(tabs=_tabs(), tab=tab, kinds=workflow.KIND, sla=config.APPROVAL_SLA_HOURS)
    if tab == "requests":
        df = workflow.my_requests(g.user["id"])
        view = df.rename(columns={"kind": "종류", "no": "번호", "requested_at": "요청일시", "status": "상태", "step": "결재 단계",
                                  "amount": "금액", "title": "내용", "decided_by": "마지막 처리", "comment": "반려 사유"})
        cols = ["종류", "번호", "요청일시", "상태", "결재 단계", "금액", "내용", "마지막 처리", "반려 사유"]
        if request.args.get("export") == "xlsx":
            log_export("my_requests", len(view))
            return form_response("my_requests", view.reindex(columns=cols), "내_요청_현황.xlsx")
        return render_page("approvals.html", "approvals", **ctx,
                           grid=Table(view[cols] if len(view) else view.reindex(columns=cols), {"금액": "₩{:,.0f}"}))
    if tab == "history":
        df = workflow.history(g.user)
        view = df.rename(columns={"kind": "종류", "no": "번호", "at": "처리일시", "decision": "결정", "amount": "금액",
                                  "title": "내용", "requested_by": "요청자", "approver": "처리자(대결 표시)", "comment": "의견"})
        cols = ["처리일시", "종류", "번호", "결정", "금액", "내용", "요청자", "처리자(대결 표시)", "의견"]
        if request.args.get("export") == "xlsx":
            log_export("approval_history", len(view))
            return form_response("approval_history", view.reindex(columns=cols), "결재_이력.xlsx")
        return render_page("approvals.html", "approvals", **ctx,
                           grid=Table(view[cols] if len(view) else view.reindex(columns=cols), {"금액": "₩{:,.0f}"}))
    if tab == "delegate":
        users = db.query_df("SELECT id, name, username, role FROM users WHERE active = 1 AND role <> 'VIEWER' ORDER BY name")
        mine = delegation.list_df(None if can("ADMIN") else g.user["id"])
        return render_page("approvals.html", "approvals", **ctx, users=users.to_dict("records"),
                           delegations=mine.to_dict("records"), role_label=auth.role_label)
    if tab == "adj" and can("MANAGER"):
        status = request.args.get("status", "PENDING")
        status = status if status in approvals.STATUS else None
        df = approvals.requests_df(status, g.wh_ids)
        view = df.copy()
        view["status"] = view["status"].map(approvals.STATUS)
        view["kind"] = view["kind"].map(approvals.KINDS).fillna(view["kind"])
        return render_page("approvals.html", "approvals", **ctx, status=status, statuses=approvals.STATUS,
                           grid=Table(view[list(COLS)].rename(columns=COLS), FMT))
    items = workflow.queue(g.user)
    if request.args.get("export") == "xlsx":
        import pandas as pd
        view = pd.DataFrame([{"종류": workflow.KIND[x["kind"]], "번호": x["no"], "단계": x["step"], "내용": x["title"],
                              "금액": x["amount"], "요청자": x["requested_by"], "요청일시": x["requested_at"],
                              "대결": x["via"] or "", "기한 넘김": "예" if x["late"] else ""} for x in items],
                            columns=["종류", "번호", "단계", "내용", "금액", "요청자", "요청일시", "대결", "기한 넘김"])
        log_export("approvals", len(view))
        return form_response("approvals", view, "내_결재_대기.xlsx")
    return render_page("approvals.html", "approvals", **{**ctx, "tab": "mine"}, items=items)


@bp.post("/decide")
@role_required("CLERK")
def decide_any():
    """내 결재 대기의 한 건 (실사 조정·구매요청·발주)."""
    kind, item_id = f_str("kind"), f_str("id")
    if kind not in workflow.KIND or not item_id.isdigit():
        flash("결재를 고르세요.", "error")
        return redirect(url_for("approvals.index"))
    r = workflow.decide(g.user, kind, int(item_id), request.form.get("decision") == "approve", f_str("comment"),
                        ip=actor().get("ip", ""))
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("approvals.index"))


@bp.post("/decide-many")
@role_required("CLERK")
def decide_many():
    keys = request.form.getlist("item")
    if not keys:
        flash("승인할 결재를 고르세요.", "error")
        return redirect(url_for("approvals.index"))
    done, problems = workflow.decide_many(g.user, keys, ip=actor().get("ip", ""))
    flash(f"{done}건을 승인했습니다." + (f" 승인하지 못한 {len(problems)}건: " + " / ".join(problems[:5]) if problems else ""),
          "success" if not problems else "warning")
    return redirect(url_for("approvals.index"))


@bp.post("/<int:req_id>")
@role_required("MANAGER")
def decide(req_id: int):
    """(예전 주소) 실사 조정 결재 — 내 결재 대기 화면과 같은 규칙."""
    result = approvals.decide(req_id, request.form.get("decision") == "approve", f_str("comment"),
                              actor(), wh_ids=g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("approvals.index"))


@bp.post("/delegations")
@role_required("CLERK")
def delegate():
    frm = int(f_str("from_user")) if can("ADMIN") and f_str("from_user").isdigit() else g.user["id"]
    to = f_str("to_user")
    if not to.isdigit():
        flash("맡을 사람을 고르세요.", "error")
        return redirect(url_for("approvals.index", tab="delegate"))
    r = delegation.create(frm, int(to), f_str("start_date"), f_str("end_date"), f_str("reason"), actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("approvals.index", tab="delegate"))


@bp.post("/delegations/<int:did>/cancel")
@role_required("CLERK")
def delegate_cancel(did: int):
    r = delegation.cancel(did, actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("approvals.index", tab="delegate"))

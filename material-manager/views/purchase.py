"""구매 화면: 구매요청 → 결재 → 발주 → 입고 · 3자 대조."""

import math

from flask import Blueprint, abort, flash, g, redirect, request, url_for

import config
from core import org, purchasing, services
from views.helpers import Table, actor, as_id, can, f_str, render_page, role_required

bp = Blueprint("purchase", __name__, url_prefix="/purchase")

TABS = [("pr", "구매요청"), ("new", "새 구매요청"), ("po", "발주")]
MONEY = "₩{:,.0f}"
QTY = "{:,.2f}"


def _in_scope(wh_id) -> bool:
    return g.wh_ids is None or int(wh_id) in g.wh_ids


@bp.get("/")
@role_required("CLERK")
def index():
    tab = request.args.get("tab", "pr")
    status = request.args.get("status") or None
    if tab == "new":
        return render_page("purchase.html", "purchase", tabs=TABS, tab="new",
                           wh_opts=org.warehouse_options(g.wh_ids), mats=services.material_options(), rows=range(6))
    if tab == "po":
        df = purchasing.pos_df(status if status in purchasing.PO_STATUS else None, g.wh_ids)
        view = df.copy()
        view["status"] = view["status"].map(purchasing.PO_STATUS)
        view = view[["po_no", "status", "supplier", "wh_code", "total_amount", "sap_po_no", "pr_no", "created_by",
                     "created_at", "approved_by"]].rename(columns={
            "po_no": "발주번호", "status": "상태", "supplier": "공급처", "wh_code": "창고", "total_amount": "금액",
            "sap_po_no": "SAP PO", "pr_no": "구매요청", "created_by": "발주자", "created_at": "발주일시",
            "approved_by": "발주 결재"})
        grid = Table(view, {"금액": MONEY}, links=[url_for("purchase.po_detail", po_id=int(i)) for i in df["id"]])
        return render_page("purchase.html", "purchase", tabs=TABS, tab="po", grid=grid, status=status,
                           statuses=purchasing.PO_STATUS)
    df = purchasing.prs_df(status if status in purchasing.PR_STATUS else None, g.wh_ids)
    if not can("MANAGER"):
        df = df[df["requested_by_id"] == g.user["id"]]
    view = df.copy()
    view["status"] = view["status"].map(purchasing.PR_STATUS)
    view["steps"] = [f"{a}/{r}" for a, r in zip(df["approved_steps"], df["required_steps"])]
    view = view[["pr_no", "status", "steps", "wh_code", "total_amount", "need_date", "requested_by", "requested_at",
                 "reason"]].rename(columns={
        "pr_no": "요청번호", "status": "상태", "steps": "결재", "wh_code": "창고", "total_amount": "금액",
        "need_date": "필요일", "requested_by": "요청자", "requested_at": "요청일시", "reason": "사유"})
    grid = Table(view, {"금액": MONEY}, links=[url_for("purchase.pr_detail", pr_id=int(i)) for i in df["id"]])
    return render_page("purchase.html", "purchase", tabs=TABS, tab="pr", grid=grid, status=status,
                       statuses=purchasing.PR_STATUS)


@bp.post("/pr")
@role_required("CLERK")
def pr_create():
    items, problems = [], []
    rows = zip(request.form.getlist("material_id"), request.form.getlist("qty"), request.form.getlist("price"))
    for no, (m, q, p) in enumerate(rows, start=1):
        m, q, p = m.strip(), q.strip().replace(",", ""), p.strip().replace(",", "")
        if not m and not q and not p:
            continue                                     # 빈 줄
        if not m:
            problems.append(f"{no}번째 줄: 자재를 선택하세요.")
            continue
        if not q or not p:                               # 단가를 비우면 0원으로 계산돼 결재 단계가 낮아진다 → 받지 않는다
            problems.append(f"{no}번째 줄: " + ("수량" if not q else "예상단가") + "을(를) 입력하세요.")
            continue
        try:
            qty, price = float(q), float(p)
        except ValueError:
            problems.append(f"{no}번째 줄: 수량·예상단가는 숫자로 입력하세요.")
            continue
        if as_id(m) is None or not (math.isfinite(qty) and math.isfinite(price)) or max(abs(qty), abs(price)) > 1e15:
            problems.append(f"{no}번째 줄: 값을 다시 확인하세요.")
            continue
        items.append((as_id(m), qty, price))
    if problems:
        flash(" / ".join(problems) + " (입력은 '입력 되살리기'로 다시 불러올 수 있습니다)", "error")
        return redirect(url_for("purchase.index", tab="new"))
    raw = f_str("warehouse_id")
    result = purchasing.create_pr(as_id(raw) or 0, items, f_str("need_date"), f_str("reason"),
                                  actor(), wh_ids=g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.pr_detail", pr_id=result.id) if result.ok else url_for("purchase.index", tab="new"))


@bp.get("/pr/<int:pr_id>")
@role_required("CLERK")
def pr_detail(pr_id: int):
    pr, items, approvals = purchasing.pr_detail(pr_id)
    if pr is None or not _in_scope(pr["warehouse_id"]) or (not can("MANAGER") and pr["requested_by_id"] != g.user["id"]):
        abort(404)
    step = int((approvals["decision"] == "APPROVE").sum()) + 1 if not approvals.empty else 1
    item_view = items.drop(columns=["id"]).rename(columns={"line_no": "품목", "code": "자재코드", "name": "자재명",
                                                           "unit": "단위", "qty": "수량", "est_price": "예상단가",
                                                           "amount": "금액"})
    appr_view = approvals.rename(columns={"step": "단계", "approver": "결재자", "decision": "결정",
                                          "comment": "의견", "at": "일시"})
    appr_view["결정"] = appr_view["결정"].map({"APPROVE": "승인", "REJECT": "반려"})
    po = purchasing.pos_df(None, None)
    po = po[po["pr_no"] == pr["pr_no"]]
    return render_page("purchase_pr.html", "purchase", pr=pr, status=purchasing.PR_STATUS.get(pr["status"]),
                       items=Table(item_view, {"수량": QTY, "예상단가": MONEY, "금액": MONEY}),
                       item_rows=items.to_dict("records"), approvals=Table(appr_view),
                       step=step, step_role=purchasing.step_role(step),
                       pos=po.to_dict("records"), mine=pr["requested_by_id"] == g.user["id"])


@bp.post("/pr/<int:pr_id>/decide")
@role_required("MANAGER")
def pr_decide(pr_id: int):
    result = purchasing.decide_pr(pr_id, request.form.get("decision") == "approve", f_str("comment"), actor(), g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.pr_detail", pr_id=pr_id))


@bp.post("/pr/<int:pr_id>/cancel")
@role_required("CLERK")
def pr_cancel(pr_id: int):
    result = purchasing.cancel_pr(pr_id, actor(), g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.pr_detail", pr_id=pr_id))


@bp.post("/pr/<int:pr_id>/po")
@role_required("MANAGER")
def po_create(pr_id: int):
    prices = {}
    for key, value in request.form.items():
        if key.startswith("price_") and key[6:].isascii() and key[6:].isdigit() and len(key) < 24 and value.strip():
            try:
                prices[int(key[6:])] = float(value.replace(",", ""))
            except ValueError:
                flash("발주 단가는 숫자로 입력하세요.", "error")
                return redirect(url_for("purchase.pr_detail", pr_id=pr_id))
    result = purchasing.create_po(pr_id, f_str("supplier"), prices, f_str("sap_po_no"), f_str("note"), actor(), g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.po_detail", po_id=result.id) if result.ok
                    else url_for("purchase.pr_detail", pr_id=pr_id))


@bp.get("/po/<int:po_id>")
@role_required("CLERK")
def po_detail(po_id: int):
    po, items, match = purchasing.po_detail(po_id)
    if po is None or not _in_scope(po["warehouse_id"]):
        abort(404)
    view = items[["line_no", "code", "name", "unit", "qty", "price", "amount", "received", "remaining"]].rename(columns={
        "line_no": "품목", "code": "자재코드", "name": "자재명", "unit": "단위", "qty": "발주수량", "price": "단가",
        "amount": "발주금액", "received": "입고", "remaining": "잔량"})
    receive_links = [url_for("transactions.index", type="IN", material=int(m), wh=int(po["warehouse_id"]))
                     if po["status"] in ("OPEN", "PARTIAL") and r > 1e-9 else None
                     for m, r in zip(items["material_id"], items["remaining"])]
    return render_page("purchase_po.html", "purchase", po=po, status=purchasing.PO_STATUS.get(po["status"]),
                       grid=Table(view, {"발주수량": QTY, "단가": MONEY, "발주금액": MONEY, "입고": QTY, "잔량": QTY},
                                  links=receive_links),
                       match=match, sap_on=config.SAP_MODE != "off")


@bp.post("/po/<int:po_id>/approve")
@role_required("MANAGER")
def po_approve(po_id: int):
    result = purchasing.approve_po(po_id, actor(), g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.po_detail", po_id=po_id))


@bp.post("/po/<int:po_id>/sap")
@role_required("MANAGER")
def po_sap(po_id: int):
    result = purchasing.set_sap_po_no(po_id, f_str("sap_po_no"), actor(), g.wh_ids, expected=f_str("_ver") or None)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.po_detail", po_id=po_id))


@bp.post("/po/<int:po_id>/cancel")
@role_required("MANAGER")
def po_cancel(po_id: int):
    result = purchasing.cancel_po(po_id, actor(), g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("purchase.po_detail", po_id=po_id))


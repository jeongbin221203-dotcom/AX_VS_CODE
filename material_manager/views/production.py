"""생산 투입(BOM) 화면: 제품·수량 → 소요량 확인(부품별 필요·가용·부족, 만들 수 있는 최대 수량) → 부품 출고 + 완제품 입고를
한 번에 등록. 부족분 구매요청, 생산 이력·전체 취소, BOM 관리. 규칙은 core/production.py."""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import org, periods, production, repository as repo, services
from core.utils import month_end
from views.helpers import (Table, a_int, actor, can, f_float, f_str, form_response, log_export, render_page,
                           role_required)

bp = Blueprint("production", __name__, url_prefix="/production")

TABS = [("run", "생산 투입"), ("history", "생산 이력"), ("bom", "BOM (자재 명세서)")]
BOM_EMPTY_ROWS = 6


def _min_date() -> str:
    ym = periods.closed_through()
    return (date.fromisoformat(month_end(ym)) + timedelta(days=1)).isoformat() if ym else ""


@bp.get("/")
@role_required("CLERK")
def index():
    tab = request.args.get("tab", "run")
    if tab == "history":
        df = production.list_df(g.wh_ids)
        view = df.assign(cancelled_at=df["cancelled_at"].map(lambda v: "취소됨" if v else ""))
        view = view[["prod_no", "tx_date", "code", "name", "qty", "unit", "issue_wh", "receipt_wh", "material_cost",
                     "work_order", "created_by", "cancelled_at"]]
        view.columns = ["생산번호", "일자", "제품코드", "제품명", "수량", "단위", "부품 창고", "입고 창고", "재료비", "작업지시",
                        "등록자", "상태"]
        if request.args.get("export") == "xlsx":
            log_export("productions", len(view))
            return form_response("productions", view, "생산투입_이력.xlsx")
        return render_page("production.html", "production", tabs=TABS, tab="history",
                           grid=Table(view, {"수량": "{:,.2f}", "재료비": "₩{:,.0f}"},
                                      links=[url_for("production.detail", prod_id=int(i)) for i in df["id"]],
                                      tones=["muted" if c else None for c in df["cancelled_at"]]))
    if tab == "bom":
        df = production.boms_df()
        view = df.assign(active=df["active"].map({1: "사용", 0: "중지"}))[
            ["code", "name", "unit", "base_qty", "lines", "unit_cost", "updated_by", "updated_at", "active"]]
        view.columns = ["제품코드", "제품명", "단위", "기준 수량", "부품 수", "1개 재료비(기준단가)", "수정자", "수정일시", "상태"]
        return render_page("production.html", "production", tabs=TABS, tab="bom",
                           grid=Table(view, {"기준 수량": "{:,.2f}", "부품 수": "{}", "1개 재료비(기준단가)": "₩{:,.0f}"},
                                      links=[url_for("production.bom_edit", product=int(i)) for i in df["product_id"]],
                                      tones=["muted" if not a else None for a in df["active"]]))
    return _run_page()


def _run_page(form: dict | None = None):
    products = production.products_with_bom()
    wh_opts = org.warehouse_options(g.wh_ids)
    form = form or request.args
    pid = a_int("product") if form is request.args else (int(form.get("product")) if str(form.get("product", "")).isdigit() else None)
    if products and pid not in products:
        pid = next(iter(products))
    wh = form.get("wh")
    wh = int(wh) if str(wh or "").isdigit() and int(wh) in wh_opts else (next(iter(wh_opts)) if wh_opts else None)
    try:
        qty = float(form.get("qty") or 0)
    except ValueError:
        qty = 0.0
    req = production.requirements(pid, qty, wh, g.wh_ids) if pid and wh and qty > 0 else None
    mat = repo.get_material(pid) if pid else None
    return render_page("production.html", "production", tabs=TABS, tab="run", products=products, wh_opts=wh_opts,
                       pid=pid, wh=wh, qty=qty, req=req, mat=mat, f=form, min_date=_min_date(),
                       receipt_default=_receipt_default(wh_opts))


def _receipt_default(wh_opts: dict) -> int | None:
    """완제품 창고(코드에 FG)가 있으면 그것, 없으면 첫 창고."""
    for k, v in wh_opts.items():
        if "FG" in v.split(" ")[0].upper():
            return k
    return next(iter(wh_opts), None)


@bp.post("/run")
@role_required("CLERK")
def run():
    try:
        pid, wh, qty = int(f_str("product")), int(f_str("wh")), f_float("qty")
        tx_date = date.fromisoformat(f_str("tx_date") or date.today().isoformat()).isoformat()
    except ValueError:
        flash("제품·창고·수량·일자를 확인하세요.", "error")
        return _run_page(request.form)
    receipt = f_str("receipt_wh")
    result = production.post(pid, qty, wh, tx_date, actor=actor(), wh_ids=g.wh_ids,
                             receipt_wh_id=int(receipt) if receipt.isdigit() else None,
                             work_order=f_str("work_order"), cost_center=f_str("cost_center"), note=f_str("note"),
                             lot_no=f_str("lot_no"), expiry_date=f_str("expiry_date"))
    flash(result.message, "success" if result.ok else "error")
    if not result.ok:
        return _run_page(request.form)
    return redirect(url_for("production.detail", prod_id=result.tx_id))


@bp.post("/shortage")
@role_required("CLERK")
def shortage():
    try:
        pid, wh, qty = int(f_str("product")), int(f_str("wh")), f_float("qty")
    except ValueError:
        abort(400, "제품·창고·수량을 확인하세요.")
    need = f_str("need_date") or (date.today() + timedelta(days=7)).isoformat()
    r = production.shortage_request(pid, qty, wh, need, actor=actor(), wh_ids=g.wh_ids)
    flash(r.message, "success" if r.ok else "error")
    if r.ok:
        return redirect(url_for("purchase.pr_detail", pr_id=r.id))
    return redirect(url_for("production.index", product=pid, wh=wh, qty=f_str("qty")))


@bp.get("/<int:prod_id>")
@role_required("CLERK")
def detail(prod_id: int):
    p = production.get(prod_id) or abort(404)
    if g.wh_ids is not None and int(p["issue_wh_id"]) not in g.wh_ids:
        abort(403, "이 창고의 생산 기록을 볼 권한이 없습니다.")
    lines = production.lines_df(prod_id)
    view = lines.assign(tx_type=lines["tx_type"].map({"OUT": "부품 출고", "IN": "완제품 입고"}),
                        reversed_by=lines["reversed_by"].map(lambda v: f"취소(#{int(v)})" if v == v and v is not None else ""))
    view = view[["id", "tx_type", "code", "name", "wh_code", "lot_no", "qty", "unit", "unit_price", "amount", "reversed_by"]]
    view.columns = ["거래 ID", "구분", "자재코드", "자재명", "창고", "로트", "수량", "단위", "단가", "금액", "상태"]
    return render_page("production_detail.html", "production", p=p,
                       grid=Table(view, {"거래 ID": "{}", "수량": "{:,.4g}", "단가": "₩{:,.0f}", "금액": "₩{:,.0f}"},
                                  tones=["muted" if s else None for s in view["상태"]]),
                       can_cancel=can("MANAGER"))


@bp.post("/<int:prod_id>/cancel")
@role_required("MANAGER")
def cancel(prod_id: int):
    r = production.cancel(prod_id, f_str("reason"), actor=actor(), wh_ids=g.wh_ids)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.detail", prod_id=prod_id))


# ── BOM ──────────────────────────────────────────────────────
@bp.get("/bom")
@role_required("CLERK")
def bom_edit():
    pid = a_int("product")
    mat = repo.get_material(pid) if pid else None
    bom = production.get_bom(pid) if mat else None
    items = production.bom_items_df(int(bom["id"])).to_dict("records") if bom else []
    labels = services.material_options(active_only=False)
    rows = [{"cid": int(i["component_id"]), "label": labels.get(int(i["component_id"]), ""), "qty": f"{i['qty']:g}",
             "scrap": f"{i['scrap_pct']:g}", "wh": int(i["issue_wh_id"]) if i["issue_wh_id"] == i["issue_wh_id"] and i["issue_wh_id"] is not None else "",
             "note": i["note"] or ""} for i in items]
    rows += [{"cid": "", "label": "", "qty": "", "scrap": "", "wh": "", "note": ""} for _ in range(BOM_EMPTY_ROWS)]
    req = production.requirements(pid, 1, next(iter(org.warehouse_options(None)), 0)) if bom else None
    return render_page("bom_edit.html", "production", mat=mat, bom=bom, rows=rows, pid=pid,
                       product_label=labels.get(pid, "") if pid else "", wh_opts=org.warehouse_options(None),
                       editable=can("MANAGER"), req=req)


@bp.post("/bom")
@role_required("MANAGER")
def bom_save():
    f = request.form
    try:
        pid = int(f_str("product"))
        base = f_float("base_qty")
    except ValueError:
        flash("제품과 기준 수량을 확인하세요.", "error")
        return redirect(url_for("production.bom_edit"))
    cols = [f.getlist(k) for k in ("comp", "qty", "scrap", "wh", "line_note")]
    lines = []
    for i, (cid, q, sc, wh, note) in enumerate(zip(*cols), 1):
        if not cid.strip():
            continue
        try:
            lines.append(production.BomLine(int(cid), float(q or 0), float(sc or 0), int(wh) if wh.isdigit() else None, note))
        except ValueError:
            flash(f"{i}번 줄: 수량·손실률은 숫자로 입력하세요.", "error")
            return redirect(url_for("production.bom_edit", product=pid))
    r = production.save_bom(pid, base, lines, f_str("note"), actor(), expected_updated_at=f_str("updated_at") or None)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.bom_edit", product=pid))


@bp.post("/bom/<int:product_id>/active")
@role_required("MANAGER")
def bom_active(product_id: int):
    r = production.set_bom_active(product_id, f_str("active") == "1", actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.bom_edit", product=product_id))

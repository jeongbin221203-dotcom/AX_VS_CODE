"""생산 화면 (규칙은 core/production.py)
  간편 생산 투입  제품·수량 → 소요량 확인 → (실제 투입량·불량 고쳐서) 부품 출고 + 완제품 입고를 한 번에
  작업지시       계획 → 자재 투입·반납(재공) → 공정 실적 → 완료 입고(실제 원가), 취소
  재공품         투입했지만 아직 완료하지 않은 작업지시와 금액
  BOM·공정       자재 명세서와 공정(라우팅)"""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import bulk, org, periods, production, repository as repo, services
from views import bulk_ui
from core.utils import month_end
from views.helpers import (Table, a_int, actor, can, f_float, f_str, form_response, log_export, render_page,
                           role_required)

bp = Blueprint("production", __name__, url_prefix="/production")

TABS = [("run", "⚡ 간편 생산 투입"), ("wo", "🗂️ 작업지시"), ("wip", "🧩 재공품"), ("history", "📜 생산 이력"), ("bom", "🧬 BOM·공정")]
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
        view = df.assign(cancelled_at=df["status"].map(production.STATUS))
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
    if tab == "wo":
        return _wo_page()
    if tab == "wip":
        df = production.wip_df(g.wh_ids)
        view = df.assign(ops=[f"{d}/{t}" if t else "-" for d, t in zip(df["ops_done"], df["ops_total"])])[
            ["prod_no", "code", "name", "qty", "unit", "issue_wh", "start_date", "due_date", "ops", "wip_cost", "planned_cost"]]
        view.columns = ["작업지시", "제품코드", "제품명", "수량", "단위", "창고", "착수", "완료 예정", "공정", "재공 금액(투입)",
                        "표준 재료비"]
        if request.args.get("export") == "xlsx":
            log_export("wip", len(view))
            return form_response("wip", view, "재공품.xlsx")
        return render_page("production.html", "production", tabs=TABS, tab="wip", wip_total=float(df["wip_cost"].sum()),
                           grid=Table(view, {"수량": "{:,.4g}", "재공 금액(투입)": "₩{:,.0f}", "표준 재료비": "₩{:,.0f}"},
                                      links=[url_for("production.detail", prod_id=int(i)) for i in df["id"]],
                                      tones=["danger" if d and d < date.today().isoformat() else None for d in df["due_date"]]))
    if tab == "bom" and request.args.get("export") == "xlsx":       # BOM 전체 — 고쳐서 일괄 등록으로 다시 올린다
        view = bulk.boms_export()
        log_export("boms", len(view))
        return form_response("boms", view, "BOM_전체.xlsx")
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
    try:
        actual = {int(k[7:]): float(v.replace(",", "")) for k, v in request.form.items()
                  if k.startswith("actual_") and k[7:].isdigit() and v.strip()}
        scrap = f_float("scrap_qty")
    except ValueError:
        flash("실제 투입량·불량은 숫자로 입력하세요.", "error")
        return _run_page(request.form)
    result = production.post(pid, qty, wh, tx_date, actor=actor(), wh_ids=g.wh_ids,
                             receipt_wh_id=int(receipt) if receipt.isdigit() else None,
                             work_order=f_str("work_order"), cost_center=f_str("cost_center"), note=f_str("note"),
                             lot_no=f_str("lot_no"), expiry_date=f_str("expiry_date"), actual=actual or None,
                             scrap_qty=scrap)
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
    view = lines.assign(tx_type=[{"OUT": "부품 출고", "IN": "반납" if n.startswith("생산 반납") else "완제품 입고"}.get(t, t)
                                 for t, n in zip(lines["tx_type"], lines["note"])],
                        reversed_by=lines["reversed_by"].map(lambda v: f"취소(#{int(v)})" if v == v and v is not None else ""))
    view = view[["id", "tx_type", "code", "name", "wh_code", "lot_no", "qty", "unit", "unit_price", "amount", "reversed_by"]]
    view.columns = ["거래 ID", "구분", "자재코드", "자재명", "창고", "로트", "수량", "단위", "단가", "금액", "상태"]
    wl = production.wo_lines(prod_id).to_dict("records")
    for r in wl:
        r["rest"] = round(float(r["planned_qty"]) - float(r["issued_qty"]), 4)
        r["std_cost"] = float(r["planned_qty"]) * float(r["std_price"] or 0)
    std_unit = (float(p["planned_cost"] or 0) / float(p["qty"])) if p["qty"] else 0
    actual_unit = (float(p["material_cost"] or 0) / float(p["good_qty"])) if p["good_qty"] else 0
    return render_page("production_detail.html", "production", p=p, wl=wl, ops=production.wo_ops(prod_id).to_dict("records"),
                       status_label=production.STATUS.get(p["status"], p["status"]), std_unit=std_unit, actual_unit=actual_unit,
                       grid=Table(view, {"거래 ID": "{}", "수량": "{:,.4g}", "단가": "₩{:,.0f}", "금액": "₩{:,.0f}"},
                                  tones=["muted" if s else None for s in view["상태"]]),
                       wh_opts=org.warehouse_options(g.wh_ids), can_cancel=can("MANAGER"), min_date=_min_date(),
                       open_wo=p["status"] in ("PLANNED", "RELEASED"))


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
    ops = production.routing(pid) if mat else []
    ops += [{"op_name": "", "workcenter": "", "std_minutes": "", "note": ""} for _ in range(4)]
    return render_page("bom_edit.html", "production", mat=mat, bom=bom, rows=rows, pid=pid, ops=ops,
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


# ── 작업지시 ─────────────────────────────────────────────────
def _wo_page(form: dict | None = None):
    show = request.args.get("show", "open")
    df = production.list_df(g.wh_ids)
    if request.args.get("view") == "board":                 # ▦ 보드: 계획 · 진행(재공) · 완료(최근 30일)
        since = (date.today() - timedelta(days=30)).isoformat()
        today_s = date.today().isoformat()
        cols = {"PLANNED": [], "RELEASED": [], "DONE": []}
        for r in df.to_dict("records"):
            if r["status"] in cols and not (r["status"] == "DONE" and str(r["tx_date"]) < since):
                r["late"] = r["status"] != "DONE" and r["due_date"] and r["due_date"] < today_s
                cols[r["status"]].append(r)
        return render_page("production.html", "production", tabs=TABS, tab="wo", board=cols, status_label=production.STATUS,
                           show=show, products=production.products_with_bom(), wh_opts=org.warehouse_options(g.wh_ids),
                           f=form or {}, receipt_default=_receipt_default(org.warehouse_options(g.wh_ids)), grid=None)
    if show == "open":
        df = df[df["status"].isin(["PLANNED", "RELEASED"])]
    view = df.assign(status=df["status"].map(production.STATUS), source=df["source"].map(
        {"QUICK": "간편", "MANUAL": "직접", "MRP": "MRP"}))[
        ["prod_no", "status", "source", "code", "name", "qty", "unit", "due_date", "issue_wh", "receipt_wh", "material_cost",
         "work_order", "created_by"]]
    view.columns = ["작업지시", "상태", "출처", "제품코드", "제품명", "수량", "단위", "완료 예정", "부품 창고", "입고 창고",
                    "투입 금액", "작업지시 번호", "등록자"]
    if request.args.get("export") == "xlsx":
        log_export("work_orders", len(view), show=show)
        return form_response("work_orders", view, "작업지시.xlsx")
    return render_page("production.html", "production", tabs=TABS, tab="wo", show=show,
                       products=production.products_with_bom(), wh_opts=org.warehouse_options(g.wh_ids), f=form or {},
                       receipt_default=_receipt_default(org.warehouse_options(g.wh_ids)),
                       grid=Table(view, {"수량": "{:,.4g}", "투입 금액": "₩{:,.0f}"},
                                  links=[url_for("production.detail", prod_id=int(i)) for i in df["id"]],
                                  tones=["danger" if s == "RELEASED" and d and d < date.today().isoformat() else None
                                         for s, d in zip(df["status"], df["due_date"])]))


@bp.post("/wo")
@role_required("CLERK")
def wo_create():
    try:
        pid, wh, qty = int(f_str("product")), int(f_str("wh")), f_float("qty")
        due = date.fromisoformat(f_str("due_date") or date.today().isoformat()).isoformat()
    except ValueError:
        flash("제품·창고·수량·완료 예정일을 확인하세요.", "error")
        return redirect(url_for("production.index", tab="wo"))
    receipt = f_str("receipt_wh")
    r = production.create_wo(pid, qty, wh, due_date=due, actor=actor(), wh_ids=g.wh_ids,
                             receipt_wh_id=int(receipt) if receipt.isdigit() else None, work_order=f_str("work_order"),
                             cost_center=f_str("cost_center"), note=f_str("note"))
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.detail", prod_id=r.tx_id) if r.ok else url_for("production.index", tab="wo"))


def _date_arg() -> str:
    try:
        return date.fromisoformat(f_str("tx_date") or date.today().isoformat()).isoformat()
    except ValueError:
        return date.today().isoformat()


@bp.post("/<int:prod_id>/issue")
@role_required("CLERK")
def wo_issue(prod_id: int):
    try:
        qty = {int(k[5:]): float(v.replace(",", "")) for k, v in request.form.items()
               if k.startswith("line_") and k[5:].isdigit() and v.strip()}
        extra = []
        if f_str("extra_mid").isdigit() and f_str("extra_qty"):
            extra = [(int(f_str("extra_mid")), f_float("extra_qty"), int(f_str("extra_wh")))]
    except ValueError:
        flash("수량은 숫자로 입력하세요.", "error")
        return redirect(url_for("production.detail", prod_id=prod_id))
    r = production.issue(prod_id, qty, _date_arg(), actor=actor(), wh_ids=g.wh_ids, extra=extra)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.detail", prod_id=prod_id))


@bp.post("/<int:prod_id>/op/<int:op_id>")
@role_required("CLERK")
def wo_operation(prod_id: int, op_id: int):
    try:
        good, scrap, minutes = f_float("good_qty"), f_float("scrap_qty"), f_float("minutes")
    except ValueError:
        flash("수량·시간은 숫자로 입력하세요.", "error")
        return redirect(url_for("production.detail", prod_id=prod_id))
    r = production.report_operation(prod_id, op_id, good, scrap, minutes, f_str("worker"), f_str("note"), actor(),
                                     wh_ids=g.wh_ids)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.detail", prod_id=prod_id))


@bp.post("/<int:prod_id>/complete")
@role_required("CLERK")
def wo_complete(prod_id: int):
    try:
        good, scrap = f_float("good_qty"), f_float("scrap_qty")
    except ValueError:
        flash("양품·불량 수량은 숫자로 입력하세요.", "error")
        return redirect(url_for("production.detail", prod_id=prod_id))
    r = production.complete(prod_id, good, scrap, _date_arg(), actor=actor(), wh_ids=g.wh_ids,
                            backflush=request.form.get("backflush") == "1", lot_no=f_str("lot_no"),
                            expiry_date=f_str("expiry_date"))
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.detail", prod_id=prod_id))


@bp.post("/routing")
@role_required("MANAGER")
def routing_save():
    try:
        pid = int(f_str("product"))
        f = request.form
        ops = [production.Op(n, w, float(m or 0), note) for n, w, m, note in
               zip(f.getlist("op_name"), f.getlist("workcenter"), f.getlist("std_minutes"), f.getlist("op_note"))]
    except ValueError:
        flash("표준 시간은 숫자로 입력하세요.", "error")
        return redirect(url_for("production.bom_edit", product=f_str("product")))
    r = production.save_routing(pid, ops, actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("production.bom_edit", product=pid))


# ── BOM 엑셀 일괄 등록 ───────────────────────────────────────
BOM_TITLE = "BOM 엑셀 일괄 등록"


@bp.get("/bom/import")
@role_required("MANAGER")
def bom_import():
    return bulk_ui.page("bom", "production", BOM_TITLE)


@bp.get("/bom/import/template.xlsx")
@role_required("MANAGER")
def bom_import_template():
    return bulk_ui.template("bom_template", [["FG-001", 1, "PT-BLT-001", 8, 2, "CW-PT", ""],
                                             ["FG-001", "", "PT-NUT-001", 8, 2, "CW-PT", ""]], "BOM_일괄등록_양식.xlsx")


@bp.post("/bom/import")
@role_required("MANAGER")
def bom_import_upload():
    return bulk_ui.upload("bom", "bom_upload", bulk.preview_boms, url_for("production.bom_import"), "production", BOM_TITLE)


@bp.post("/bom/import/apply")
@role_required("MANAGER")
def bom_import_apply():
    return bulk_ui.apply("bom", bulk.apply_boms, url_for("production.bom_import"))

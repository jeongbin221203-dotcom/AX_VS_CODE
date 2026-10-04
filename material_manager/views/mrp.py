"""MRP 화면: 플랜트별 수요 등록 → 실행 → 계획(구매·생산, 발주·착수일, 지연) → 고른 계획을 구매요청·작업지시로.
규칙은 core/mrp.py. 보기·수요 등록은 담당자, 실행·바꾸기는 관리자."""

from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, request, url_for

from core import db, mrp
from views.helpers import (MAX_ID, a_int, actor, as_id, can, f_float, f_str, form_response, log_export, render_page,
                           role_required)

bp = Blueprint("mrp", __name__, url_prefix="/mrp")


def _plants() -> dict[int, str]:
    """사용자 창고 범위 안의 플랜트."""
    frag, wp = db.in_clause(g.wh_ids)
    df = db.query_df(f"SELECT DISTINCT p.id, p.code, p.name FROM plants p JOIN warehouses w ON w.plant_id = p.id "
                     f"WHERE p.active = 1 AND w.active = 1{' AND w.id' + frag if frag else ''} ORDER BY p.code", wp)
    return {int(r.id): f"{r.code} {r.name}" for r in df.itertuples()}


def _plant() -> int | None:
    plants = _plants()
    pid = a_int("plant") or (int(f_str("plant")) if f_str("plant").isdigit() else None)
    return pid if pid in plants else next(iter(plants), None)


@bp.get("/")
@role_required("CLERK")
def index():
    plants = _plants()
    plant = _plant()
    tab = request.args.get("tab", "plan")
    ctx = dict(plants=plants, plant=plant, tab=tab, can_run=can("MANAGER"))
    if plant is None:
        return render_page("mrp.html", "mrp", **ctx)
    if tab == "demand":
        df = mrp.demands_df(plant)
        return render_page("mrp.html", "mrp", **ctx, demands=df.to_dict("records"),
                           default_due=(date.today() + timedelta(days=30)).isoformat())
    run = mrp.latest_run(plant)
    if request.args.get("export") == "xlsx" and run:
        df = mrp.plans_df(run["id"])
        view = df.assign(kind=df["kind"].map(mrp.KIND), status=df["status"].map({"OPEN": "계획", "CONVERTED": "바꿈"}))[
            ["kind", "level", "code", "name", "qty", "unit", "order_date", "need_date", "lead_time_days", "wh_code", "pegging",
             "status", "ref"]]
        view.columns = ["구분", "단계", "자재코드", "자재명", "수량", "단위", "발주·착수일", "필요일", "리드타임", "창고", "근거", "상태",
                        "바꾼 번호"]
        log_export("mrp_plans", len(view), run=int(run["id"]))
        return form_response("mrp_plans", view, f"MRP_계획_{run['run_at'][:10]}.xlsx")
    plans = mrp.plans_df(run["id"]).to_dict("records") if run else []
    t0 = date.today().isoformat()
    for p in plans:
        p["late"] = p["status"] == "OPEN" and p["order_date"] < t0
    return render_page("mrp.html", "mrp", **ctx, run=run, plans=plans, kind=mrp.KIND, t0=t0)


@bp.post("/demand")
@role_required("CLERK")
def demand_add():
    plant = _plant()
    try:
        qty = f_float("qty")
        mid = int(f_str("material"))
    except ValueError:
        flash("자재와 수량을 확인하세요.", "error")
        return redirect(url_for("mrp.index", plant=plant, tab="demand"))
    r = mrp.add_demand(plant, mid, qty, f_str("due_date"), f_str("note"), actor())
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("mrp.index", plant=plant, tab="demand"))


@bp.post("/demand/<int:did>/close")
@role_required("CLERK")
def demand_close(did: int):
    r = mrp.close_demand(did, actor(), wh_ids=g.wh_ids)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("mrp.index", plant=_plant(), tab="demand"))


@bp.post("/run")
@role_required("MANAGER")
def run():
    plant = _plant()
    try:
        days = int(f_float("horizon", 90))
    except ValueError:
        days = 90
    r = mrp.run(plant, actor(), max(7, min(days, 365)))
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("mrp.index", plant=plant))


@bp.post("/convert")
@role_required("MANAGER")
def convert():
    plant = _plant()
    ids = [int(v) for v in request.form.getlist("plan") if v.isdigit()]
    run_id = as_id(f_str("run_id"))
    if run_id is None:
        flash("MRP 실행을 다시 고르세요 (화면을 새로 고친 뒤).", "error")
        return redirect(url_for("mrp.index", plant=plant))
    ids = [i for i in ids if i <= MAX_ID]
    r = mrp.convert(run_id, ids, actor=actor(), wh_ids=g.wh_ids)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("mrp.index", plant=plant))


# ── 수요 엑셀 일괄 등록 ──────────────────────────────────────
DEMAND_TITLE = "MRP 수요 엑셀 일괄 등록"


@bp.get("/demand/import")
@role_required("CLERK")
def demand_import():
    from views import bulk_ui
    return bulk_ui.page("mrp_demand", "mrp", DEMAND_TITLE, extra={"plants": _plants(), "plant": _plant()})


@bp.get("/demand/import/template.xlsx")
@role_required("CLERK")
def demand_template():
    from views import bulk_ui
    return bulk_ui.template("mrp_demand_template", [["FG-FAN-001", 120, (date.today() + timedelta(days=21)).isoformat(),
                                                     "대리점 11월 출하"]], "MRP_수요_양식.xlsx")


@bp.post("/demand/import")
@role_required("CLERK")
def demand_import_upload():
    from core import bulk
    from views import bulk_ui
    plant, replace = _plant(), request.form.get("replace") == "1"
    return bulk_ui.upload("mrp_demand", "mrp_demand_upload", lambda raw: bulk.preview_demands(raw, plant, replace),
                          url_for("mrp.demand_import", plant=plant), "mrp", DEMAND_TITLE,
                          extra={"plants": _plants(), "plant": plant})


@bp.post("/demand/import/apply")
@role_required("CLERK")
def demand_import_apply():
    from core import bulk
    from views import bulk_ui
    return bulk_ui.apply("mrp_demand", bulk.apply_demands, url_for("mrp.index", plant=_plant(), tab="demand"))

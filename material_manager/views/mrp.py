"""MRP 화면: 플랜트별 수요 등록 → 실행 → 계획(구매·생산, 발주·착수일, 지연) → 고른 계획을 구매요청·작업지시로.
규칙은 core/mrp.py. 보기·수요 등록은 담당자, 실행·바꾸기는 관리자."""

from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, request, url_for

from core import db, mrp, services
from views.helpers import Table, a_int, actor, can, f_float, f_str, render_page, role_required

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
    r = mrp.close_demand(did, actor())
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
    r = mrp.convert(int(f_str("run_id") or 0), ids, actor=actor(), wh_ids=g.wh_ids)
    flash(r.message, "success" if r.ok else "error")
    return redirect(url_for("mrp.index", plant=plant))

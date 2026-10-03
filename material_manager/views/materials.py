"""자재 마스터 화면."""

import hashlib

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

import config
from core import db, repository as repo, services
from views.helpers import (Table, a_int, actor, can, f_float, f_str, page_arg, pager, render_page,
                           role_required)

bp = Blueprint("materials", __name__, url_prefix="/materials")

TABS = [("list", "목록"), ("new", "신규 등록"), ("edit", "수정 / 사용중지")]


def _master_editor() -> bool:
    """자재 마스터는 회사 전체 데이터 → 모든 창고 권한이 있는 관리자만 바꾼다."""
    return can("MANAGER") and g.wh_ids is None


def _tabs():
    return TABS if _master_editor() else TABS[:1]


@bp.before_request
def _guard_writes():
    if request.method == "POST" and not _master_editor():
        abort(403, "자재 마스터는 모든 창고 권한이 있는 관리자만 바꿀 수 있습니다.")


def _form_data() -> dict:
    """폼 입력 → 자재 dict. 숫자 형식이 틀리면 ValueError."""
    return {
        "code": f_str("code").upper(), "name": f_str("name"), "spec": f_str("spec"),
        "unit": f_str("unit") or config.DEFAULT_UNIT,
        "category": f_str("category") or config.DEFAULT_CATEGORY,
        "safety_stock": max(f_float("safety_stock"), 0.0),
        "unit_price": max(f_float("unit_price"), 0.0),
        "location": f_str("location"), "supplier": f_str("supplier"),
        "sap_matnr": f_str("sap_matnr").upper(),
        "barcode": f_str("barcode").replace(" ", "").upper(),
        # 유효기한 관리는 로트 관리가 전제
        "lot_managed": 1 if request.form.get("lot_managed") or request.form.get("expiry_managed") else 0,
        "expiry_managed": 1 if request.form.get("expiry_managed") else 0,
    }


@bp.get("/")
def index():
    tab = request.args.get("tab", "list")
    if tab in ("new", "edit") and not _master_editor():
        tab = "list"
    if tab == "new":
        return render_page("materials.html", "materials", tabs=_tabs(), tab=tab, form={})
    if tab == "edit":
        return _edit_page(a_int("id"))
    return _list_page(request.args.get("inactive") == "1")


def _list_page(show_inactive: bool):
    df = repo.stock_df(include_inactive=show_inactive, wh_ids=g.wh_ids)
    df["flags"] = [" · ".join(x for x, on in (("로트", lm), ("기한", em), ("SAP", bool(sy))) if on)
                   for lm, em, sy in zip(df["lot_managed"], df["expiry_managed"], df["sap_synced_at"].fillna(""))]
    view = df[["code", "name", "spec", "unit", "category", "safety_stock",
               "unit_price", "location", "supplier", "sap_matnr", "flags", "stock", "active"]].copy()
    view["active"] = view["active"].map({1: "사용", 0: "중지"})
    view.columns = ["자재코드", "자재명", "규격", "단위", "분류", "안전재고",
                    "단가", "보관위치", "공급처", "SAP자재번호", "관리", "현재고", "상태"]
    p = pager(len(view), page_arg())
    rows = slice(p["first"] - 1 if p["total"] else 0, p["last"])
    table = Table(view.iloc[rows], {"안전재고": "{:,.2f}", "현재고": "{:,.2f}", "단가": "₩{:,.0f}"},
                  tones=["muted" if a == 0 else None for a in df["active"].iloc[rows]])
    return render_page("materials.html", "materials", tabs=_tabs(), tab="list",
                       show_inactive=show_inactive, grid=table, pager=p)


def _edit_page(mid: int | None, form: dict | None = None):
    opts = services.material_options(active_only=False)
    if opts and (mid is None or mid not in opts):
        mid = next(iter(opts))
    row = repo.get_material(mid) if opts else None
    stock_now = 0.0
    if row:
        mine = repo.stock_df(include_inactive=True, wh_ids=g.wh_ids)
        stock_now = float(mine.loc[mine["id"] == mid, "stock"].sum())
    return render_page("materials.html", "materials", tabs=_tabs(), tab="edit",
                       opts=opts, mid=mid, row=row, form=form or row or {},
                       sap_lock=bool(row and row.get("sap_synced_at") and config.SAP_MASTER_READONLY),
                       stock_now=stock_now, is_active=bool(row and int(row["active"]) == 1))


@bp.get("/lookup.json")
def lookup():
    """자재 찾기·바코드 스캔용 목록 (static/js/picker.js). 사용 중인 자재만(all=1 이면 중지 자재도 — 자재 수정 화면).
    바뀌지 않았으면 304."""
    where = "" if request.args.get("all") == "1" else "WHERE active = 1"
    df = db.query_df("SELECT id, code, name, spec, unit, barcode, sap_matnr, lot_managed, unit_price, expiry_managed, "
                     f"updated_at FROM materials {where} ORDER BY code")
    etag = hashlib.sha1(f"{len(df)}|{df['updated_at'].max() if len(df) else ''}|{df['id'].sum() if len(df) else 0}"
                        .encode(), usedforsecurity=False).hexdigest()[:20]
    if request.if_none_match.contains(etag):
        return "", 304
    rows = [[int(r.id), r.code, r.name, r.spec or "", r.unit or "", r.barcode or "", r.sap_matnr or "",
             int(r.lot_managed or 0), float(r.unit_price or 0), int(r.expiry_managed or 0)] for r in df.itertuples()]
    res = jsonify(v=etag, items=rows)
    res.set_etag(etag)
    res.headers["Cache-Control"] = "private, no-cache"
    return res


@bp.post("/new")
@role_required("MANAGER")
def create():
    try:
        data = _form_data()
    except ValueError as exc:
        flash(str(exc), "error")
        return render_page("materials.html", "materials", tabs=_tabs(), tab="new", form=request.form)
    result = services.create_material(data, actor())
    flash(result.message, "success" if result.ok else "error")
    if not result.ok:
        return render_page("materials.html", "materials", tabs=_tabs(), tab="new", form=request.form)
    return redirect(url_for("materials.index", tab="new"))


@bp.post("/<int:mid>/edit")
@role_required("MANAGER")
def update(mid: int):
    row = repo.get_material(mid) or abort(404)
    try:
        data = _form_data()
    except ValueError as exc:
        flash(str(exc), "error")
        return _edit_page(mid, form={**row, **request.form.to_dict()})
    result = services.update_material(mid, data, actor(), expected_updated_at=f_str("updated_at") or None)
    flash(result.message, "success" if result.ok else "error")
    if not result.ok:
        return _edit_page(mid, form={**row, **request.form.to_dict()})
    return redirect(url_for("materials.index", tab="edit", id=mid))


@bp.post("/<int:mid>/active")
@role_required("MANAGER")
def set_active(mid: int):
    repo.get_material(mid) or abort(404)
    result = services.set_material_active(mid, request.form.get("active") == "1", actor())
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("materials.index", tab="edit", id=mid))

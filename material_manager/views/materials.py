"""자재 마스터 화면."""

import hashlib

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

import config
from markupsafe import Markup

from core import audit, barcode, db, repository as repo, services, uom
from views import bulk_ui
from views.helpers import (Table, a_int, actor, can, f_float, f_str, form_response, log_export, page_arg, pager,
                           render_page, role_required)

bp = Blueprint("materials", __name__, url_prefix="/materials")

TABS = [("list", "📋 목록"), ("new", "➕ 신규 등록"), ("edit", "✏️ 수정 / 사용중지")]


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
        "lead_time_days": int(max(f_float("lead_time_days"), 0)),
        "min_order_qty": max(f_float("min_order_qty"), 0.0),
        "order_multiple": max(f_float("order_multiple"), 0.0),
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


def _master_export(show_inactive: bool):
    """자재 마스터 — 올리기 양식과 같은 열 (내려받아 고친 뒤 데이터 관리 → 일괄 업로드로 다시 올린다)."""
    df = repo.list_materials(active_only=not show_inactive)
    view = df[list(config.MATERIAL_COLS)].rename(columns=config.MATERIAL_COLS)
    log_export("materials_master", len(view), inactive=show_inactive)
    return form_response("materials_master", view, "자재마스터.xlsx")


def _list_page(show_inactive: bool):
    if request.args.get("export") == "xlsx":
        return _master_export(show_inactive)
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
                  tones=["muted" if a == 0 else None for a in df["active"].iloc[rows]],
                  links=[url_for("materials.index", tab="edit", id=int(i)) for i in df["id"].iloc[rows]]
                  if _master_editor() else None)                 # 행을 누르면 그 자재 수정
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
                       units=uom.units(mid) if row else [],
                       sap_lock=bool(row and row.get("sap_synced_at") and config.SAP_MASTER_READONLY),
                       stock_now=stock_now, is_active=bool(row and int(row["active"]) == 1),
                       history=audit.entity_history("material", mid) if row else [])


@bp.get("/lookup.json")
def lookup():
    """자재 찾기·바코드 스캔용 목록 (static/js/picker.js). 사용 중인 자재만(all=1 이면 중지 자재도 — 자재 수정 화면).
    바뀌지 않았으면 304."""
    where = "" if request.args.get("all") == "1" else "WHERE active = 1"
    total = int(db.scalar(f"SELECT COUNT(*) FROM materials {where}"))
    if total > config.LOOKUP_MAX:                       # 너무 많으면 브라우저가 서버에 물어서 찾는다
        return jsonify(v=f"remote-{total}", remote=True, items=[])
    df = db.query_df("SELECT id, code, name, spec, unit, barcode, sap_matnr, lot_managed, unit_price, expiry_managed, "
                     f"updated_at FROM materials {where} ORDER BY code")
    unit_ver = db.scalar("SELECT COUNT(*) || '-' || COALESCE(MAX(id), 0) FROM material_units")
    etag = hashlib.sha1(f"{len(df)}|{df['updated_at'].max() if len(df) else ''}|{df['id'].sum() if len(df) else 0}|{unit_ver}"
                        .encode(), usedforsecurity=False).hexdigest()[:20]
    if request.if_none_match.contains(etag):
        return "", 304
    units = uom.all_units()
    rows = [[int(r.id), r.code, r.name, r.spec or "", r.unit or "", r.barcode or "", r.sap_matnr or "",
             int(r.lot_managed or 0), float(r.unit_price or 0), int(r.expiry_managed or 0), units.get(int(r.id), [])]
            for r in df.itertuples()]
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


SEARCH_COLS = "id, code, name, spec, unit, barcode, sap_matnr, lot_managed, unit_price, expiry_managed"


def _rows(df) -> list[list]:
    units = uom.all_units() if len(df) else {}
    return [[int(r.id), r.code, r.name, r.spec or "", r.unit or "", r.barcode or "", r.sap_matnr or "",
             int(r.lot_managed or 0), float(r.unit_price or 0), int(r.expiry_managed or 0), units.get(int(r.id), [])]
            for r in df.itertuples()]


@bp.get("/search.json")
def search():
    """자재가 아주 많을 때의 서버 찾기 (picker.js). exact=코드·바코드·SAP 번호·단위 바코드, q=낱말(모두 포함) 30개."""
    active = "" if request.args.get("all") == "1" else " AND active = 1"
    exact = request.args.get("exact", "").strip().upper()[:64]
    if exact:
        df = db.query_df(f"SELECT {SEARCH_COLS} FROM materials WHERE (UPPER(code) = ? OR barcode = ? OR UPPER(sap_matnr) = ?)"
                         f"{active} LIMIT 1", (exact, exact, exact))
        unit = ""
        if df.empty:
            row = db.query_df("SELECT material_id, unit FROM material_units WHERE barcode = ?", (exact,))
            if not row.empty:
                unit = row.iloc[0]["unit"]
                df = db.query_df(f"SELECT {SEARCH_COLS} FROM materials WHERE id = ?{active}", (int(row.iloc[0]["material_id"]),))
        return jsonify(items=_rows(df), unit=unit)
    words = [w for w in request.args.get("q", "").lower().split() if w][:5]
    sql, params = f"SELECT {SEARCH_COLS} FROM materials WHERE 1 = 1{active}", []
    for w in words:
        sql += (" AND (LOWER(code) LIKE ? OR LOWER(name) LIKE ? OR LOWER(spec) LIKE ? OR LOWER(barcode) LIKE ?"
                " OR LOWER(sap_matnr) LIKE ?)")
        params += [f"%{w}%"] * 5
    df = db.query_df(sql + " ORDER BY code LIMIT 30", params)
    return jsonify(items=_rows(df))


@bp.post("/<int:mid>/units")
@role_required("MANAGER")
def unit_add(mid: int):
    try:
        factor = f_float("factor")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("materials.index", tab="edit", id=mid))
    ok, msg = uom.add(mid, f_str("unit"), factor, f_str("barcode"), actor())
    flash(msg, "success" if ok else "error")
    return redirect(url_for("materials.index", tab="edit", id=mid))


@bp.post("/units/<int:uid>/delete")
@role_required("MANAGER")
def unit_delete(uid: int):
    ok, msg, mid = uom.remove(uid, actor())
    flash(msg, "success" if ok else "error")
    return redirect(url_for("materials.index", tab="edit", id=mid) if mid else url_for("materials.index"))


# ── 바코드 라벨 ──────────────────────────────────────────────
LABEL_LAYOUTS = {"a4-24": "A4 24칸 (70×37mm, 3×8)", "a4-10": "A4 10칸 (105×57mm, 2×5)", "roll": "라벨 프린터 1장씩 (100×50mm)"}
MAX_LABELS = 1000


@bp.get("/labels")
def labels():
    """라벨 고르기: 자재 검색 → 장수 → 인쇄 화면."""
    q = request.args.get("q", "").strip()[:60]
    df = db.query_df("SELECT id, code, name, spec, unit, barcode, category FROM materials WHERE active = 1 ORDER BY code")
    if q:
        k = q.lower()
        df = df[df.apply(lambda r: k in f"{r.code} {r['name']} {r.spec} {r.barcode} {r.category}".lower(), axis=1)]
    units = uom.all_units()
    rows = [{**r, "units": units.get(int(r["id"]), [])} for r in df.head(500).to_dict("records")]
    return render_page("labels.html", "materials", rows=rows, q=q, layouts=LABEL_LAYOUTS, total=len(df))


@bp.get("/labels/print")
def labels_print():
    """인쇄용 라벨 (브라우저 인쇄 → 라벨 용지). 바코드 = 자재 바코드, 없으면 자재코드. 단위(상자) 바코드는 따로 고른다."""
    layout = request.args.get("layout", "a4-24")
    layout = layout if layout in LABEL_LAYOUTS else "a4-24"
    labels_out, skipped = [], []
    ids = repo.ids_in(request.args.getlist("id"))
    units = uom.all_units()
    for mid in ids:
        m = repo.get_material(mid)
        if m is None:
            continue
        try:
            n = max(0, min(int(request.args.get(f"n_{mid}", "1") or 0), 200))
        except ValueError:
            n = 1
        value = (m["barcode"] or m["code"]).strip()
        art = barcode.svg(value)
        if art is None:
            skipped.append(m["code"])
            continue
        for _ in range(n):
            labels_out.append({"code": m["code"], "name": m["name"], "spec": m["spec"], "value": value,
                               "unit": f"1 {m['unit']}", "svg": Markup(art)})
        for u, f, bc in units.get(mid, []):
            k = request.args.get(f"u_{mid}_{u}", "0")
            if bc and k.isdigit() and int(k) > 0:
                art_u = barcode.svg(bc)
                for _ in range(min(int(k), 200)):
                    labels_out.append({"code": m["code"], "name": m["name"], "spec": m["spec"], "value": bc,
                                       "unit": f"1 {u} = {f:g} {m['unit']}", "svg": Markup(art_u)})
        if len(labels_out) > MAX_LABELS:
            break
    labels_out = labels_out[:MAX_LABELS]
    return render_template("labels_print.html", labels=labels_out, layout=layout, skipped=skipped,
                           layout_label=LABEL_LAYOUTS[layout])


# ── 단위 환산 엑셀 (내려받기 · 일괄 등록) ──────────────────────
UNIT_TITLE = "단위 환산 엑셀 일괄 등록"


@bp.get("/units/export.xlsx")
def units_export():
    from core import bulk
    view = bulk.units_export()
    log_export("units", len(view))
    return form_response("units", view, "단위환산.xlsx")


@bp.get("/units/import")
@role_required("MANAGER")
def units_import():
    return bulk_ui.page("units", "materials", UNIT_TITLE)


@bp.post("/units/import")
@role_required("MANAGER")
def units_import_upload():
    from core import bulk
    return bulk_ui.upload("units", "unit_upload", bulk.preview_units, url_for("materials.units_import"), "materials", UNIT_TITLE)


@bp.post("/units/import/apply")
@role_required("MANAGER")
def units_import_apply():
    from core import bulk
    return bulk_ui.apply("units", bulk.apply_units, url_for("materials.units_import"))

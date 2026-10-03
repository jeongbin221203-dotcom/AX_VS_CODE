"""입출고 등록 화면 (입고 · 출고 · 실사조정 · 창고 간 이동)."""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

import config
from core import audit, db, documents, org, periods, purchasing, repository as repo, services
from core.utils import month_end
from views.documents import meta_from_form, uploaded_file
from views.helpers import Table, a_int, actor, can, f_float, f_id, f_str, render_page, role_required

bp = Blueprint("transactions", __name__, url_prefix="/transactions")

TYPES = {**config.TX_LABEL, "TRF": "창고 간 이동"}


def _page(tx_type: str, mid: int | None, wh: int | None, form: dict | None = None):
    opts = services.material_options(active_only=True)
    wh_opts = org.warehouse_options(g.wh_ids)
    if not opts or not wh_opts:
        return render_page("transactions.html", "transactions", opts=opts, wh_opts=wh_opts)
    if mid not in opts:
        mid = next(iter(opts))
    if wh not in wh_opts:
        wh = next(iter(wh_opts))
    mat = repo.get_material(mid)
    warehouse = org.get_warehouse(wh)
    with db.get_conn() as conn:
        stock_now = repo.current_stock(conn, mid, wh)
    ym = periods.closed_through()
    min_date = (date.fromisoformat(month_end(ym)) + timedelta(days=1)).isoformat() if ym else ""
    lots = repo.stock_by_lot(material_id=mid, warehouse_id=wh).to_dict("records") if mat["lot_managed"] else []
    frag, wp = db.in_clause(g.wh_ids)
    known_lots = (db.query_df(
        "SELECT DISTINCT l.lot_no, l.expiry_date FROM lots l JOIN transactions t ON t.material_id = l.material_id "
        f"AND t.lot_no = l.lot_no WHERE l.material_id = ?{' AND t.warehouse_id' + frag if frag else ''} ORDER BY l.lot_no",
        (mid, *wp)).to_dict("records") if mat["lot_managed"] else [])
    po_lines = purchasing.open_po_lines(wh, mid) if tx_type == "IN" else []
    return render_page("transactions.html", "transactions", opts=opts, tx_type=tx_type, types=TYPES,
                       lots=lots, known_lots=known_lots, po_lines=po_lines,
                       mid=mid, mat=mat, wh=wh, warehouse=warehouse, wh_opts=wh_opts,
                       all_wh_opts=org.warehouse_options(None), stock_now=stock_now, form=form or {},
                       accept=documents.ACCEPT, min_date=min_date, movement=config.SAP_MOVEMENT_TYPES,
                       approval_limit=config.ADJ_APPROVAL_AMOUNT)


@bp.get("/")
@role_required("CLERK")
def index():
    tx_type = request.args.get("type", "IN")
    if tx_type not in TYPES:
        tx_type = "IN"
    return _page(tx_type, a_int("material"), a_int("wh"))


def _ids() -> tuple[int, int]:
    try:
        return f_id("material_id"), f_id("warehouse_id")
    except ValueError:
        abort(400, "자재와 창고를 선택하세요.")


@bp.post("/")
@role_required("CLERK")
def create():
    tx_type = f_str("tx_type")
    if tx_type not in config.TX_LABEL:
        abort(400, f"알 수 없는 거래 유형: {tx_type}")
    mid, wh = _ids()
    try:
        qty_input = f_float("qty")
        price = f_float("unit_price")
        tx_date = date.fromisoformat(f_str("tx_date") or date.today().isoformat())
    except ValueError as exc:
        flash(str(exc) if "숫자" in str(exc) else "일자 형식이 올바르지 않습니다.", "error")
        return _page(tx_type, mid, wh, form=request.form)

    # 증빙을 함께 올렸으면 먼저 검증한다 → 증빙이 잘못되면 거래도 등록하지 않는다
    data, filename = uploaded_file()
    prepared = documents.prepare(data, filename, meta_from_form()) if data else None
    if prepared and not prepared.ok:
        for msg in prepared.errors:
            flash(f"증빙: {msg}", "error")
        flash("증빙을 고치거나 빼고 다시 등록하세요. 거래는 등록되지 않았습니다.", "info")
        return _page(tx_type, mid, wh, form=request.form)

    po_no, po_item = f_str("po_no"), f_str("po_item")
    if "|" in f_str("po_line"):                        # 이 시스템 발주 품목을 고른 경우
        po_no, po_item = f_str("po_line").split("|", 1)
    result = services.register_transaction(
        material_id=mid, tx_type=tx_type, qty_input=qty_input,
        tx_date=tx_date.isoformat(), unit_price=price,
        ref_no=f_str("ref_no"), partner=f_str("partner"), note=f_str("note"),
        actor=actor(), po_no=po_no, po_item=po_item, cost_center=f_str("cost_center"),
        warehouse_id=wh, wh_ids=g.wh_ids, lot_no=f_str("lot_no"), expiry_date=f_str("expiry_date"),
    )
    if not result.ok:
        flash(result.message, "error")
        return _page(tx_type, mid, wh, form=request.form)

    flash(result.message, "info" if result.pending else "success")
    if result.warning:
        flash(result.warning, "warning")
    if prepared and result.pending:
        flash("결재 대기 중이라 증빙은 저장하지 않았습니다. 승인된 뒤 증빙 화면에서 그 거래에 올려 주세요.", "warning")
    elif prepared:
        for msg in prepared.warnings:
            flash(f"증빙: {msg}", "warning")
        saved = documents.save(prepared, tx_id=result.tx_id, actor=actor())
        flash(saved.message if saved.ok else
              f"거래는 등록됐지만 증빙 저장에 실패했습니다. 증빙 화면에서 거래 #{result.tx_id}에 다시 올려 주세요.",
              "success" if saved.ok else "error")
    return redirect(url_for("transactions.index", type=tx_type, material=mid, wh=wh))


@bp.post("/transfer")
@role_required("CLERK")
def transfer():
    mid, wh = _ids()
    try:
        to_wh = f_id("to_warehouse_id")
        qty = f_float("qty")
        tx_date = date.fromisoformat(f_str("tx_date") or date.today().isoformat())
    except ValueError:
        flash("받는 창고·수량·일자를 확인하세요.", "error")
        return _page("TRF", mid, wh, form=request.form)
    result = services.transfer(mid, wh, to_wh, qty, tx_date.isoformat(), actor=actor(),
                               ref_no=f_str("ref_no"), note=f_str("note"), wh_ids=g.wh_ids, lot_no=f_str("lot_no"))
    flash(result.message, "success" if result.ok else "error")
    if not result.ok:
        return _page("TRF", mid, wh, form=request.form)
    return redirect(url_for("transactions.index", type="TRF", material=mid, wh=wh))


@bp.post("/queue")
@role_required("CLERK")
def queue():
    """오프라인 대기열 반영 (static/js/app.js가 연결이 돌아오면 입력한 순서대로 한 건씩 보낸다).

    화면 등록과 같은 규칙(재고·권한·마감·결재·SAP 매핑)으로 판정한다 — 끊긴 동안 받은 출고도 재고가 모자라면 거부된다.
    같은 입력은 한 번 쓰는 표(_once, 브라우저가 입력할 때 만든 값)로 두 번 반영되지 않는다(core/once.py).
    증빙 파일은 대기열에 담지 않는다.
    """
    kind = f_str("kind")
    captured_at = f_str("captured_at")[:30]
    mid, wh = _ids()
    try:
        qty = f_float("qty")
        tx_date = date.fromisoformat(f_str("tx_date") or date.today().isoformat()).isoformat()
    except ValueError:
        return jsonify(ok=False, message="수량·일자 형식이 올바르지 않습니다.")
    if kind == "TRF":
        try:
            to_wh = f_id("to_warehouse_id")
        except ValueError:
            return jsonify(ok=False, message="받는 창고를 확인하세요.")
        result = services.transfer(mid, wh, to_wh, qty, tx_date, actor=actor(), ref_no=f_str("ref_no"),
                                   note=f_str("note"), wh_ids=g.wh_ids, lot_no=f_str("lot_no"))
    else:
        tx_type = f_str("tx_type")
        if tx_type not in config.TX_LABEL:
            return jsonify(ok=False, message=f"알 수 없는 거래 유형: {tx_type}")
        try:
            price = f_float("unit_price")
        except ValueError:
            return jsonify(ok=False, message="단가 형식이 올바르지 않습니다.")
        po_no, po_item = f_str("po_no"), f_str("po_item")
        if "|" in f_str("po_line"):
            po_no, po_item = f_str("po_line").split("|", 1)
        result = services.register_transaction(
            material_id=mid, tx_type=tx_type, qty_input=qty, tx_date=tx_date, unit_price=price,
            ref_no=f_str("ref_no"), partner=f_str("partner"), note=f_str("note"), actor=actor(),
            po_no=po_no, po_item=po_item, cost_center=f_str("cost_center"), warehouse_id=wh, wh_ids=g.wh_ids,
            lot_no=f_str("lot_no"), expiry_date=f_str("expiry_date"))
    if result.ok:
        g.once_done = True                              # JSON 응답이어도 '처리 완료'로 남긴다 (재전송 시 두 번 반영 금지)
        audit.log(actor(), "OFFLINE_SYNC", "transaction", result.tx_id or "",
                  {"captured_at": captured_at, "kind": kind or f_str("tx_type"), "pending": result.pending})
    return jsonify(ok=result.ok, message=result.message, tx_id=result.tx_id, pending=result.pending,
                   warning=result.warning)


# ── 여러 줄·스캔 입출고 ───────────────────────────────────────
def _batch_lines_from_form() -> tuple[list[services.LineIn], list[dict], str]:
    """줄 칸들(line_mid·line_qty·…) → (등록할 줄, 다시 그릴 줄, 문제)."""
    f = request.form
    cols = {c: f.getlist(f"line_{c}") for c in ("mid", "qty", "lot", "exp", "price", "note")}
    n = len(cols["mid"])
    if any(len(v) != n for v in cols.values()):
        return [], [], "줄 입력이 맞지 않습니다. 화면을 새로고침해 다시 입력하세요."
    lines, shown, problem = [], [], ""
    labels = services.material_options(active_only=False)
    with db.get_conn() as conn:
        for i in range(n):
            mid_s, qty_s, price_s = cols["mid"][i].strip(), cols["qty"][i].strip(), cols["price"][i].strip()
            mid = int(mid_s) if mid_s.isascii() and mid_s.isdigit() else 0
            mat = repo.get_material(mid, conn) if mid else None
            shown.append({"mid": mid, "label": labels.get(mid, ""), "unit": mat["unit"] if mat else "",
                          "lot_managed": bool(mat and mat["lot_managed"]), "qty": qty_s, "lot": cols["lot"][i],
                          "exp": cols["exp"][i], "price": price_s, "note": cols["note"][i]})
            if not mid:
                continue
            try:
                qty = float(qty_s.replace(",", ""))
                price = float(price_s.replace(",", "")) if price_s else None
            except ValueError:
                problem = problem or f"{i + 1}번 줄: 수량·단가는 숫자로 입력하세요."
                continue
            lines.append(services.LineIn(mid, qty, cols["lot"][i].strip(), cols["exp"][i].strip(), price,
                                         cols["note"][i].strip()))
    return lines, shown, problem


def _batch_page(form: dict | None = None, shown: list[dict] | None = None, status: int = 200):
    form = form or {"kind": request.args.get("kind", "IN"), "tx_date": date.today().isoformat()}
    wh_opts = org.warehouse_options(g.wh_ids)
    ym = periods.closed_through()
    min_date = (date.fromisoformat(month_end(ym)) + timedelta(days=1)).isoformat() if ym else ""
    frag, wp = db.in_clause(g.wh_ids)
    recent = db.query_df(f"""
        SELECT t.batch_no, MIN(t.tx_date) AS tx_date, MIN(t.tx_type) AS tx_type, MIN(w.code) AS wh_code,
               COUNT(*) AS lines, SUM(t.qty * t.unit_price) AS amount, MIN(t.partner) AS partner,
               MIN(t.created_by) AS created_by, MIN(t.created_at) AS created_at,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM transactions r WHERE r.reversal_of = t.id) THEN 1 ELSE 0 END) AS reversed
        FROM transactions t JOIN warehouses w ON w.id = t.warehouse_id
        WHERE t.batch_no <> '' AND t.reversal_of IS NULL{' AND t.warehouse_id' + frag if frag else ''}
        GROUP BY t.batch_no ORDER BY MIN(t.id) DESC LIMIT 15""", wp)
    return render_page("batch.html", "batch", f=form, lines=shown or [], wh_opts=wh_opts, min_date=min_date,
                       recent=recent.to_dict("records"), max_lines=services.MAX_BATCH_LINES,
                       can_cancel=can("MANAGER")), status


@bp.get("/batch")
@role_required("CLERK")
def batch():
    return _batch_page()


@bp.post("/batch")
@role_required("CLERK")
def batch_post():
    form = {k: f_str(k) for k in ("kind", "warehouse_id", "tx_date", "ref_no", "partner", "cost_center", "note")}
    lines, shown, problem = _batch_lines_from_form()
    wh = int(form["warehouse_id"]) if form["warehouse_id"].isdigit() else 0
    if not problem and wh not in org.warehouse_options(g.wh_ids):
        problem = "창고를 고르세요 (권한이 있는 창고만)."
    try:
        tx_date = date.fromisoformat(form["tx_date"] or date.today().isoformat()).isoformat()
    except ValueError:
        problem = problem or "일자 형식이 올바르지 않습니다."
    if problem:
        flash(problem, "error")
        return _batch_page(form, shown)
    result = services.register_lines(form["kind"], wh, tx_date, lines, actor=actor(), wh_ids=g.wh_ids,
                                     ref_no=form["ref_no"], partner=form["partner"], cost_center=form["cost_center"],
                                     note=form["note"])
    if not result.ok:
        flash(result.message, "error")
        return _batch_page(form, shown)
    flash(result.message, "success")
    if result.warning:
        flash(result.warning, "warning")
    return redirect(url_for("transactions.batch", kind=form["kind"]))


@bp.post("/batch/<batch_no>/cancel")
@role_required("MANAGER")
def batch_cancel(batch_no: str):
    result = services.cancel_group("batch_no", batch_no, f_str("reason"), actor=actor(), wh_ids=g.wh_ids,
                                   label=f"묶음 {batch_no}")
    flash(result.message, "success" if result.ok else "error")
    return redirect(url_for("transactions.batch"))

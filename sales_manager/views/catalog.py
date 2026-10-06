"""품목·단가 · 견적."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, send_file, url_for

import io

from core import catalog
from core import database
from core import orders as so
from core import entities as ent_mod
from core import enterprise as ent
from core import quotes as qt
from core import sales_db as db

from .crm import deal_choices, visible_customer, visible_deal
from .helpers import (Table, a_int, a_str, csv_response, f_int, f_str, render_page, role_required)

bp = Blueprint("catalog", __name__)

PAGE_SIZE = 50


# ============================================================================
# 품목 · 거래처 특가
# ============================================================================
@bp.route("/products")
def products():
    tab = request.args.get("tab", "products")
    keyword = a_str("q")
    plist = catalog.list_products(keyword)
    cust_id = a_int("customer_id")
    prices = catalog.list_customer_prices(cust_id)
    if request.args.get("export") == "products":
        return csv_response(plist.drop(columns=["id"], errors="ignore"), "품목.csv")
    if request.args.get("export") == "prices":
        return csv_response(prices.drop(columns=["id"], errors="ignore"), "거래처특가.csv")
    edit = catalog.get_product(a_int("pid")) if a_int("pid") else None
    return render_page(
        "catalog/products.html", "products", tab=tab, keyword=keyword,
        plist=Table(plist, money=["정가"], link=("catalog.products", "id", "pid"), page_size=PAGE_SIZE),
        prices=Table(prices, money=["정가", "특가"], drop=["id"], page_size=PAGE_SIZE),
        edit=edit or {"unit": "EA", "tax_type": "과세", "active": 1},
        can_edit_product=ent.has_role(g.user, "SUPPORT"),
        can_set_price=ent.has_role(g.user, "MANAGER"),
        customers=db.customer_options(include_closed=False), cust_id=cust_id,
        product_opts=[(p["id"], f"{p['code']} · {p['name']} (정가 {int(p['list_price']):,}원)")
                      for p in catalog.product_options()],
        tax_types=db.TAX_TYPES, today_str=date.today().isoformat(),
    )


@bp.route("/products/save", methods=["POST"])
@role_required("SUPPORT")
def product_save():
    try:
        pid = catalog.upsert_product({
            "id": f_int("id") or None, "code": f_str("code"), "name": f_str("name"), "spec": f_str("spec"),
            "category": f_str("category"), "unit": f_str("unit"), "list_price": f_int("list_price"),
            "tax_type": f_str("tax_type"), "erp_material": f_str("erp_material"),
            "active": 1 if request.form.get("active") else 0, "memo": f_str("memo"),
            "row_version": f_str("row_version") or None})
        flash("품목을 저장했습니다.", "success")
        return redirect(url_for("catalog.products", pid=pid))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("catalog.products", pid=f_int("id") or None))


@bp.route("/products/prices", methods=["POST"])
@role_required("MANAGER")
def price_save():
    cid = visible_customer(f_int("customer_id"))
    try:
        catalog.set_customer_price(cid, f_int("product_id"), f_int("unit_price"), f_str("valid_from"),
                                   f_str("valid_to") or None, f_str("memo"))
        flash("거래처 특가를 등록했습니다. 겹치는 이전 특가는 전날로 종료했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("catalog.products", tab="prices", customer_id=cid))


@bp.route("/products/price")
def price_lookup():
    """매출·견적 입력 화면에서 품목을 고를 때 적용 단가(특가 → 정가)를 알려준다."""
    pid = a_int("product_id")
    cid = a_int("customer_id")
    if not pid:
        abort(400, "product_id 가 필요합니다.")
    if cid:
        visible_customer(cid)
    try:
        return jsonify(catalog.price_for(cid, pid, a_str("on") or None))
    except ValueError as exc:
        abort(404, str(exc))


# ============================================================================
# 견적
# ============================================================================
def _items_from_form() -> list[dict]:
    f = request.form
    rows = zip(f.getlist("product_id"), f.getlist("item_name"), f.getlist("qty"),
               f.getlist("unit_price"), f.getlist("tax_type"))
    return [{"product_id": p, "item_name": n, "qty": q, "unit_price": u, "tax_type": t}
            for p, n, q, u, t in rows if p or n.strip()]


def _visible_quote(qid: int) -> dict:
    try:
        return qt.get_quote(qid)
    except (ValueError, KeyError, TypeError):
        abort(404)


@bp.route("/quotes")
def quotes():
    return _quotes_page()


def _quotes_page(submitted: dict | None = None, http_status: int = 200):
    """견적 화면. submitted 가 있으면 저장에 실패한 입력(머리·품목 줄 전부)을 그대로 다시 그린다."""
    status = a_str("status")
    ready = a_str("ready") == "1"                    # 수주 전: 수락했지만 아직 수주·매출로 넘어가지 않은 견적
    df = qt.list_quotes("수락" if ready else status, customer_id=a_int("customer_id") or None, owner_id=g.owner_filter,
                        deal_id=a_int("deal_id") or None, keyword=a_str("q"))
    if ready and not df.empty:
        done = {int(r["quote_id"]) for r in database.rows(          # 취소한 수주·매출은 '넘어간 것'으로 보지 않는다
            "SELECT quote_id FROM sales_orders WHERE quote_id IS NOT NULL AND status <> '취소' "
            "UNION SELECT quote_id FROM sales WHERE quote_id IS NOT NULL AND status <> '취소'")}
        df = df[~df["id"].isin(done)]
    if request.args.get("export") == "quotes":
        return csv_response(df.drop(columns=["id", "owner_id", "customer_id"], errors="ignore"), "견적목록.csv")
    qid = a_int("qid") or (int(submitted["id"]) if submitted and submitted.get("id") else None)
    quote = _visible_quote(qid) if qid else None
    editing = request.args.get("edit") == "1" or request.args.get("new") == "1" or submitted is not None
    if editing and quote and quote["status"] != "작성중":
        editing = False
    form = quote if (editing and quote) else {
        "customer_id": a_int("customer_id") or "", "deal_id": a_int("deal_id") or "",
        "issue_date": date.today().isoformat(), "items": [{}]}
    if submitted is not None:
        form = {**(quote or {}), **submitted, "items": submitted.get("items") or [{}]}
    active = df[~df["상태"].isin(["거절", "만료"])] if not df.empty else df
    return render_page(
        "catalog/quotes.html", "quotes", status=status, statuses=qt.QUOTE_STATUS[:-1],
        tbl=Table(df, money=["공급가액", "부가세", "합계"], drop=["owner_id", "customer_id"],
                  link=("catalog.quotes", "id", "qid"), page_size=PAGE_SIZE,
                  highlight={"상태": {"만료": "muted", "거절": "muted", "수락": "success"}}),
        m_count=len(df), m_open=int(active[active["상태"] == "발송"]["합계"].sum()) if not active.empty else 0,
        m_won=int(df[df["상태"] == "수락"]["합계"].sum()) if not df.empty else 0,
        quote=None if editing and not quote else quote, editing=editing, form=form,
        customers=db.customer_options(include_closed=False), deals=deal_choices((form or {}).get("customer_id")),
        products=catalog.product_options(), tax_types=db.TAX_TYPES,
        entity_opts=ent_mod.options(), currencies=ent_mod.CURRENCIES,
    ), http_status


@bp.route("/quotes/save", methods=["POST"])
def quote_save():
    cid = visible_customer(f_int("customer_id"))
    deal_id = f_int("deal_id") or None
    if deal_id:
        visible_deal(deal_id)
    data = {"id": f_int("id") or None, "customer_id": cid, "deal_id": deal_id, "title": f_str("title"),
            "issue_date": f_str("issue_date"), "valid_until": f_str("valid_until"), "terms": f_str("terms"),
            "memo": f_str("memo"), "row_version": f_str("row_version"),
            "entity_id": f_str("entity_id") or None, "currency": f_str("currency") or "KRW",
            "fx_rate": f_str("fx_rate") or None}
    items = _items_from_form()
    try:
        qid = qt.save_quote(data, items)
        flash("견적을 저장했습니다.", "success")
        return redirect(url_for("catalog.quotes", qid=qid))
    except ValueError as exc:     # ConflictError 도 ValueError
        flash(str(exc), "error")
        # 다른 화면으로 보내지 않고 입력한 그대로 다시 그린다 (영업기회·건명·유효기한·품목 줄이 사라지지 않게)
        return _quotes_page({**data, "items": items or [{}]}, 400)


@bp.route("/quotes/<int:qid>/<action>", methods=["POST"])
def quote_action(qid: int, action: str):
    _visible_quote(qid)
    target = qid
    try:
        if action == "send":
            qt.send(qid)
            flash("발송 처리했습니다. PDF 를 내려받아 거래처에 전달하세요.", "success")
        elif action == "accept":
            qt.decide(qid, True, f_str("reason"))
            flash("수락 처리했습니다. 매출로 전환할 수 있습니다.", "success")
        elif action == "reject":
            qt.decide(qid, False, f_str("reason"))
            flash("거절 처리했습니다.", "warning")
        elif action == "revise":
            target = qt.revise(qid)
            flash("새 판을 만들었습니다. 수정 후 다시 발송하세요.", "success")
            return redirect(url_for("catalog.quotes", qid=target, edit=1))
        elif action == "order":
            oid = so.from_quote(qid, {"delivery_date": f_str("delivery_date") or None,
                                      "customer_po": f_str("customer_po")})
            flash("수주로 등록했습니다. 납품할 때마다 수량을 골라 매출을 등록하세요.", "success")
            return redirect(url_for("catalog.orders", oid=oid))
        elif action == "convert":
            ids = qt.convert_to_sales(qid, f_str("sale_date") or None)
            flash(f"매출 {len(ids)}건을 등록했습니다. ERP 전송 대기열에 올라갔습니다.", "success")
        else:
            abort(404)
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("catalog.quotes", qid=target))


@bp.route("/quotes/<int:qid>/pdf")
def quote_pdf(qid: int):
    q = _visible_quote(qid)
    data = qt.pdf(qid)
    db.audit("다운로드", "견적", qid, {"파일": "PDF", "견적번호": q["quote_no"]})
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=False,
                     download_name=f"견적서_{q['quote_no']}_Rev{q['revision']}.pdf")


# ============================================================================
# 수주 — 견적 수락 뒤 여러 번 나눠 납품·매출 (수주 잔량)
# ============================================================================
@bp.route("/orders")
def orders():
    status = a_str("status")
    df = so.list_orders(status)
    if request.args.get("export") == "orders":
        return csv_response(df.drop(columns=["id", "owner_id"], errors="ignore"), "수주목록.csv")
    oid = a_int("oid")
    order = None
    if oid:
        try:
            order = so.get(oid)
        except (TypeError, ValueError):
            abort(404)
    return render_page(
        "catalog/orders.html", "orders", status=status, statuses=so.STATUS,
        tbl=Table(df, money=["수주금액", "잔량금액"], drop=["owner_id"], link=("catalog.orders", "id", "oid"),
                  page_size=PAGE_SIZE, highlight={"상태": {"완료": "muted", "취소": "muted"}}),
        order=order, new=request.args.get("new") == "1",
        customers=db.customer_options(include_closed=False), products=catalog.product_options(),
        tax_types=db.TAX_TYPES, m_open=int(df[df["상태"] == "진행"]["잔량금액"].sum()) if not df.empty else 0,
        m_count=int((df["상태"] == "진행").sum()) if not df.empty else 0,
    )


@bp.route("/orders/save", methods=["POST"])
def order_save():
    cid = visible_customer(f_int("customer_id"))
    try:
        oid = so.create({"customer_id": cid, "order_date": f_str("order_date"), "delivery_date": f_str("delivery_date"),
                         "customer_po": f_str("customer_po"), "memo": f_str("memo")}, _items_from_form())
        flash("수주를 등록했습니다.", "success")
        return redirect(url_for("catalog.orders", oid=oid))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("catalog.orders", new=1))


@bp.route("/orders/<int:oid>/deliver", methods=["POST"])
def order_deliver(oid: int):
    qty = {int(k[4:]): int(v) for k, v in request.form.items() if k.startswith("qty_") and str(v).strip().isdigit()}
    try:
        ids = so.deliver(oid, qty, f_str("sale_date") or None)
        flash(f"납품 {len(ids)}건을 매출로 등록했습니다.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("catalog.orders", oid=oid))


@bp.route("/orders/<int:oid>/cancel-remaining", methods=["POST"])
def order_cancel_remaining(oid: int):
    try:
        so.cancel_remaining(oid, f_str("reason"))
        flash("남은 수량을 취소했습니다.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("catalog.orders", oid=oid))

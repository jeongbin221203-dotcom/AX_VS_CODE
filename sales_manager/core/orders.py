"""수주 — 견적과 매출 사이의 주문. 한 번 수주한 뒤 여러 번 나눠 납품·매출한다(수주 잔량).

  흐름: 견적 수락 → 수주 등록(견적 품목 복사) 또는 직접 수주 → 납품할 때마다 품목별 수량을 골라 매출 등록
        → 모두 납품되면 '완료', 남은 수량은 사유를 적어 '잔량 취소'
  * 매출은 수주 품목의 단가·과세·법인·통화를 물려받고 sales.order_id·order_item_id 로 이어진다
  * 납품 수량은 수주 잔량을 넘을 수 없다(동시에 두 번 납품해도 넘치지 않게 조건부 갱신)
  * 매출을 취소하면 그 수량은 다시 잔량으로 돌아온다
  * 자재관리와 합치면 '납품'이 창고 출고와 연결된다 (지금은 매출 등록까지)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from . import sales_db as db

STATUS = ["진행", "완료", "취소"]


def _next_no(conn) -> str:
    year = date.today().year
    row = conn.execute("SELECT order_no FROM sales_orders WHERE order_no LIKE ? ORDER BY order_no DESC LIMIT 1",
                       (f"SO-{year}-%",)).fetchone()
    seq = int(row["order_no"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"SO-{year}-{seq:04d}"


def create(data: dict, items: list[dict]) -> int:
    from . import catalog
    from . import entities as ent_mod
    cust = db.get_customer(int(data.get("customer_id") or 0))
    db.check_record_scope(cust, "거래처")
    if cust.get("trade_blocked"):
        raise ValueError("거래정지 거래처는 수주할 수 없습니다.")
    owner_id, owner_name = db.resolve_owner(data.get("owner_id") or db.current_actor_id())
    order_date = db._d(data.get("order_date")) or date.today().isoformat()
    lines = []
    for raw in items:
        pid = int(raw["product_id"]) if str(raw.get("product_id") or "").isdigit() else None
        qty = int(raw.get("qty") or 0)
        if not pid and not str(raw.get("item_name") or "").strip():
            continue
        if qty <= 0:
            raise ValueError("수주 수량은 1 이상이어야 합니다.")
        if pid:
            price = catalog.price_for(int(cust["id"]), pid, order_date)
            unit_price = int(raw["unit_price"]) if str(raw.get("unit_price") or "").strip() else price["unit_price"]
            name, code, unit, tax = price["name"], price["code"], price["unit"], price["tax_type"]
        else:
            unit_price = int(raw.get("unit_price") or 0)
            name, code, unit, tax = str(raw["item_name"]).strip(), raw.get("item_code"), raw.get("unit") or "EA", \
                raw.get("tax_type") or "과세"
        if unit_price <= 0:
            raise ValueError("단가는 1원 이상이어야 합니다 (0원 줄은 납품해도 매출이 되지 않습니다).")
        lines.append((pid, code, name, unit, qty, unit_price, tax))
    if not lines:
        raise ValueError("수주 품목을 한 줄 이상 입력하세요.")
    with db.get_conn() as conn:
        db.lock(conn, "order-no")
        no = _next_no(conn)
        cur = conn.execute("INSERT INTO sales_orders (order_no, customer_id, deal_id, quote_id, entity_id, currency, fx_rate, "
                           "order_date, delivery_date, status, customer_po, owner, owner_id, memo, created_by, created_at, "
                           "updated_at) VALUES (?,?,?,?,?,?,?,?,?, '진행', ?,?,?,?,?,?,?)",
                           (no, int(cust["id"]), data.get("deal_id"), data.get("quote_id"),
                            ent_mod.resolve(data.get("entity_id")), data.get("currency") or "KRW",
                            float(data.get("fx_rate") or 1), order_date, db._d(data.get("delivery_date")),
                            (data.get("customer_po") or "").strip() or None, owner_name, owner_id, data.get("memo"),
                            db.current_actor(), db._now(), db._now()))
        oid = int(cur.lastrowid)
        for n, (pid, code, name, unit, qty, unit_price, tax) in enumerate(lines, start=1):
            conn.execute("INSERT INTO sales_order_items (order_id, line_no, product_id, item_code, item_name, unit, qty, "
                         "unit_price, tax_type) VALUES (?,?,?,?,?,?,?,?,?)", (oid, n, pid, code, name, unit, qty,
                                                                             unit_price, tax))
    db.audit("수주등록", "영업기회", data.get("deal_id"), {"수주번호": no, "거래처": cust["name"], "품목수": len(lines),
                                                         "금액": sum(q * p for _a, _b, _c, _d, q, p, _t in lines)})
    return oid


def from_quote(quote_id: int, data: Optional[dict] = None) -> int:
    from . import quotes as qt
    q = qt.get_quote(int(quote_id))
    if q["status"] != "수락":
        raise ValueError("수락된 견적만 수주로 등록할 수 있습니다.")
    if q["sales"] or db._one("SELECT id FROM sales_orders WHERE quote_id=? AND status<>'취소'", [int(quote_id)]):
        raise ValueError("이미 매출이나 수주로 넘어간 견적입니다.")
    qt.claim(q)
    items = [{"product_id": it["product_id"], "item_name": it["item_name"], "item_code": it["item_code"],
              "unit": it["unit"], "qty": it["qty"], "unit_price": it["unit_price"], "tax_type": it["tax_type"]}
             for it in q["items"]]
    return create({"customer_id": q["customer_id"], "deal_id": q["deal_id"], "quote_id": q["id"],
                   "entity_id": q.get("entity_id"), "currency": q.get("currency"), "fx_rate": q.get("fx_rate"),
                   "owner_id": q["owner_id"], "memo": f"견적 {q['quote_no']} Rev.{q['revision']}", **(data or {})}, items)


def get(order_id: int) -> dict:
    o = db._one("SELECT o.*, c.name AS customer_name FROM sales_orders o JOIN customers c ON c.id = o.customer_id "
                "WHERE o.id=?", [int(order_id)])
    db.check_record_scope(o, "수주")
    o["items"] = db._df("SELECT *, qty - delivered_qty - cancelled_qty AS remain FROM sales_order_items WHERE order_id=? "
                        "ORDER BY line_no", [int(order_id)]).to_dict("records")
    o["sales"] = db._df("SELECT id, sale_date, item, qty, amount, total_amount, status FROM sales WHERE order_id=? "
                        "ORDER BY id", [int(order_id)]).to_dict("records")
    return o


def list_orders(status: str = "") -> pd.DataFrame:
    sql = ("SELECT o.id, o.order_no AS 수주번호, c.name AS 거래처, o.order_date AS 수주일, o.delivery_date AS 납기, "
           "o.status AS 상태, SUM(i.qty * i.unit_price) AS 수주금액, "
           "SUM((i.qty - i.delivered_qty - i.cancelled_qty) * i.unit_price) AS 잔량금액, o.customer_po AS 고객발주번호, "
           "o.owner AS 담당자, o.owner_id FROM sales_orders o JOIN customers c ON c.id = o.customer_id "
           "JOIN sales_order_items i ON i.order_id = o.id WHERE 1=1")
    params: list = []
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    sc, sp = db._scope_clause("o")
    return db._df(sql + sc + " GROUP BY o.id, o.order_no, c.name, o.order_date, o.delivery_date, o.status, "
                  "o.customer_po, o.owner, o.owner_id ORDER BY o.id DESC", params + sp)


def deliver(order_id: int, quantities: dict[int, int], sale_date: Optional[str] = None) -> list[int]:
    """품목별 납품 수량 → 매출 등록. quantities = {order_item_id: 수량}."""
    o = get(int(order_id))
    if o["status"] != "진행":
        raise ValueError(f"'{o['status']}' 수주는 납품할 수 없습니다.")
    items = {int(i["id"]): i for i in o["items"]}
    todo = {int(k): int(v) for k, v in quantities.items() if int(v or 0) > 0}
    if not todo:
        raise ValueError("납품할 수량을 입력하세요.")
    for iid, qty in todo.items():
        if iid not in items:
            raise ValueError("이 수주의 품목이 아닙니다.")
        if qty > int(items[iid]["remain"]):
            raise ValueError(f"'{items[iid]['item_name']}' 납품 수량({qty})이 잔량({items[iid]['remain']})을 넘습니다.")
    sale_ids = []
    for iid, qty in todo.items():
        with db.get_conn() as conn:          # 잔량 선점 — 동시에 두 번 납품해도 넘치지 않게
            ok = conn.execute("UPDATE sales_order_items SET delivered_qty = delivered_qty + ? WHERE id=? "
                              "AND qty - delivered_qty - cancelled_qty >= ?", (qty, iid, qty)).rowcount
        if not ok:
            raise db.ConflictError("그 사이 다른 납품이 등록되어 잔량이 바뀌었습니다. 새로고침 후 다시 입력하세요.")
        it = items[iid]
        try:
            sid = db.upsert_sale({
                "customer_id": o["customer_id"], "deal_id": o["deal_id"], "sale_date": sale_date, "item": it["item_name"],
                "item_code": it["item_code"], "product_id": it["product_id"], "qty": qty, "unit_price": it["unit_price"],
                "amount": qty * int(it["unit_price"]), "tax_type": it["tax_type"], "owner_id": o["owner_id"],
                "quote_id": o["quote_id"], "entity_id": o["entity_id"], "currency": o.get("currency") or "KRW",
                "fx_rate": o.get("fx_rate") or 1, "memo": f"수주 {o['order_no']}"})
        except Exception:
            with db.get_conn() as conn:
                conn.execute("UPDATE sales_order_items SET delivered_qty = delivered_qty - ? WHERE id=?", (qty, iid))
            raise
        with db.get_conn() as conn:
            conn.execute("UPDATE sales SET order_id=?, order_item_id=? WHERE id=?", (int(order_id), iid, sid))
        sale_ids.append(sid)
    _refresh_status(int(order_id))
    db.audit("납품", "매출", None, {"수주번호": o["order_no"], "매출": sale_ids, "수량": todo})
    return sale_ids


def cancel_remaining(order_id: int, reason: str) -> None:
    o = get(int(order_id))
    if not str(reason or "").strip():
        raise ValueError("잔량 취소 사유를 입력하세요.")
    with db.get_conn() as conn:
        conn.execute("UPDATE sales_order_items SET cancelled_qty = qty - delivered_qty WHERE order_id=?", (int(order_id),))
        conn.execute("UPDATE sales_orders SET memo = COALESCE(memo, '') || ?, updated_at=? WHERE id=?",
                     (f"\n[잔량 취소] {reason.strip()}", db._now(), int(order_id)))
    _refresh_status(int(order_id))
    db.audit("수주잔량취소", "매출", None, {"수주번호": o["order_no"], "사유": reason.strip()})


def release_cancelled_sale(sale: dict) -> None:
    """수주에서 납품한 매출을 취소하면 그 수량을 잔량으로 돌린다."""
    if not sale.get("order_item_id"):
        return
    with db.get_conn() as conn:
        conn.execute("UPDATE sales_order_items SET delivered_qty = delivered_qty - ? WHERE id=? AND delivered_qty >= ?",
                     (int(sale["qty"]), int(sale["order_item_id"]), int(sale["qty"])))
    _refresh_status(int(sale["order_id"]))


def _refresh_status(order_id: int) -> None:
    left = int(db._scalar("SELECT COALESCE(SUM(qty - delivered_qty - cancelled_qty), 0) FROM sales_order_items "
                          "WHERE order_id=?", [order_id]))
    delivered = int(db._scalar("SELECT COALESCE(SUM(delivered_qty), 0) FROM sales_order_items WHERE order_id=?", [order_id]))
    status = "진행" if left > 0 else ("완료" if delivered > 0 else "취소")
    with db.get_conn() as conn:
        conn.execute("UPDATE sales_orders SET status=?, updated_at=? WHERE id=?", (status, db._now(), order_id))

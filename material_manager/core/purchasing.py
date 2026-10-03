"""구매: 구매요청(PR) → 결재 → 발주(PO) → 입고(GR) → 3자 대조(발주·입고·세금계산서).

- 결재 단계 수는 요청 금액으로 정한다(config.PR_APPROVAL_TIERS). 1·2단계는 관리자, 3단계는 시스템관리자.
- 직무 분리: 요청자는 자기 요청을 결재할 수 없고, 한 사람이 두 단계를 결재할 수 없으며, 요청자는 발주를 만들 수 없다.
- 발주 금액이 승인된 요청 금액보다 허용치(10%) 넘게 크면 발주도 결재(요청자·발주자가 아닌 관리자).
- 입고는 입출고 화면에서 구매오더를 고르면 발주 창고·자재·남은 수량을 확인한다(초과 입고 차단).
- SAP 연동 중에는 발주에 SAP 구매오더 번호가 있어야 입고가 SAP로 전기된다(101).
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

import config
from core import audit, auth, db, notify, org, partners, version
from core.utils import now_str

PR_STATUS = {"PENDING": "결재 중", "APPROVED": "승인", "REJECTED": "반려", "ORDERED": "발주 완료", "CANCELLED": "취소"}
PO_STATUS = {"PENDING_APPROVAL": "발주 결재 중", "OPEN": "발주", "PARTIAL": "부분 입고", "CLOSED": "입고 완료",
             "CANCELLED": "취소"}


@dataclass
class PResult:
    ok: bool
    message: str
    id: int = 0


def required_steps(amount: float) -> int:
    for limit, steps in config.PR_APPROVAL_TIERS:
        if amount <= limit:
            return steps
    return config.PR_APPROVAL_TIERS[-1][1]


def step_role(step: int) -> str:
    return "ADMIN" if step >= 3 else "MANAGER"


def _next_no(conn, prefix: str, table: str, column: str) -> str:
    ym = date.today().strftime("%Y%m")
    db.lock(conn, f"seq:{prefix}")
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE ?", (f"{prefix}-{ym}-%",)).fetchone()[0]
    return f"{prefix}-{ym}-{int(n) + 1:04d}"


# ── 구매요청 ─────────────────────────────────────────────────
def create_pr(warehouse_id: int, items: list[tuple[int, float, float]], need_date: str, reason: str,
              actor: dict, wh_ids=None) -> PResult:
    """items: [(자재ID, 수량, 예상단가)]"""
    items = [(int(m), float(q), float(p)) for m, q, p in items if q]
    if not items:
        return PResult(False, "요청할 품목을 하나 이상 입력하세요.")
    if any(q <= 0 or p < 0 for _, q, p in items):
        return PResult(False, "수량은 0보다, 단가는 0 이상이어야 합니다.")
    if not reason.strip():
        return PResult(False, "구매 사유를 입력하세요.")
    with db.transaction() as conn:
        wh = org.get_warehouse(warehouse_id, conn)
        if wh is None or not wh["active"] or (wh_ids is not None and warehouse_id not in wh_ids):
            return PResult(False, "요청할 수 있는 창고가 아닙니다.")
        for mid, _, _ in items:
            m = conn.execute("SELECT active FROM materials WHERE id = ?", (mid,)).fetchone()
            if m is None or not m["active"]:
                return PResult(False, "사용 중인 자재가 아닌 품목이 있습니다.")
        amount = sum(q * p for _, q, p in items)
        steps = required_steps(amount)
        pr_no = _next_no(conn, "PR", "purchase_requests", "pr_no")
        ts = now_str()
        pr_id = conn.execute(
            "INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, reason, status, total_amount, "
            "required_steps, requested_by_id, requested_by, requested_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)",
            (pr_no, warehouse_id, need_date, reason.strip(), amount, steps, actor.get("id"), actor["name"], ts, ts)
        ).lastrowid
        conn.executemany("INSERT INTO pr_items (pr_id, line_no, material_id, qty, est_price) VALUES (?, ?, ?, ?, ?)",
                         [(pr_id, (i + 1) * 10, m, q, p) for i, (m, q, p) in enumerate(items)])
        audit.record(conn, actor, "PR_CREATE", "purchase_request", pr_id,
                     {"pr_no": pr_no, "amount": amount, "steps": steps, "items": len(items)})
        notify.queue(conn, notify.recipients(conn, step_role(1), warehouse_id, [actor.get("id")]),
                     f"구매요청 {pr_no} 결재 요청 (1/{steps}단계)",
                     [f"{actor['name']}님이 구매요청을 올렸습니다.", f"창고: {wh['code']} {wh['name']}",
                      f"금액: ₩{amount:,.0f} · 품목 {len(items)}개 · 결재 {steps}단계", f"사유: {reason.strip()}"],
                     f"/purchase/pr/{pr_id}", f"pr:{pr_id}")
    return PResult(True, f"구매요청 {pr_no} 등록 — 금액 ₩{amount:,.0f}, 결재 {steps}단계", pr_id)


def decide_pr(pr_id: int, approve: bool, comment: str, actor: dict, wh_ids=None) -> PResult:
    comment = comment.strip()
    with db.transaction() as conn:
        db.lock(conn, f"pr:{pr_id}")
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr is None or pr["status"] != "PENDING":
            return PResult(False, "결재할 수 있는 요청이 아닙니다.")
        if pr["requested_by_id"] == actor.get("id"):
            return PResult(False, "본인 요청은 결재할 수 없습니다(직무 분리).")
        if wh_ids is not None and pr["warehouse_id"] not in wh_ids:
            return PResult(False, "이 창고의 요청을 결재할 권한이 없습니다.")
        done = conn.execute("SELECT approver_id FROM pr_approvals WHERE pr_id = ? AND decision = 'APPROVE'",
                            (pr_id,)).fetchall()
        if any(d["approver_id"] == actor.get("id") for d in done):
            return PResult(False, "이미 결재한 요청입니다. 다음 단계는 다른 사람이 결재해야 합니다.")
        step = len(done) + 1
        if not auth.has_role(actor, step_role(step)):
            return PResult(False, f"{step}단계 결재는 {auth.role_label(step_role(step))}이 해야 합니다.")
        if not approve and not comment:
            return PResult(False, "반려 사유를 입력하세요.")
        conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (pr_id, step, actor.get("id"), actor["name"], "APPROVE" if approve else "REJECT", comment, now_str()))
        status = "REJECTED" if not approve else ("APPROVED" if step >= pr["required_steps"] else "PENDING")
        conn.execute("UPDATE purchase_requests SET status = ?, updated_at = ? WHERE id = ?", (status, now_str(), pr_id))
        audit.record(conn, actor, "PR_DECIDE", "purchase_request", pr_id,
                     {"step": step, "approve": approve, "comment": comment, "status": status})
        path = f"/purchase/pr/{pr_id}"
        if status == "PENDING":                       # 다음 단계 결재자에게
            done_ids = [d["approver_id"] for d in done] + [actor.get("id"), pr["requested_by_id"]]
            notify.queue(conn, notify.recipients(conn, step_role(step + 1), pr["warehouse_id"], done_ids),
                         f"구매요청 {pr['pr_no']} 결재 요청 ({step + 1}/{pr['required_steps']}단계)",
                         [f"{actor['name']}님이 {step}단계를 승인했습니다. 다음 단계 결재를 부탁드립니다.",
                          f"금액: ₩{float(pr['total_amount']):,.0f} · 요청자 {pr['requested_by']}"], path, f"pr:{pr_id}")
        else:                                         # 결과를 요청자에게
            result_line = ("최종 승인됐습니다. 구매 담당자가 발주를 만들 수 있습니다." if approve
                           else f"반려 사유: {comment}")
            notify.queue(conn, notify.user(conn, pr["requested_by_id"]),
                         f"구매요청 {pr['pr_no']} {'최종 승인' if approve else '반려'}",
                         [f"{actor['name']}님이 {step}단계에서 {'승인' if approve else '반려'}했습니다.", result_line],
                         path, f"pr:{pr_id}")
    if not approve:
        return PResult(True, f"{pr['pr_no']} 반려", pr_id)
    return PResult(True, f"{pr['pr_no']} {step}단계 승인" + (" — 최종 승인, 발주할 수 있습니다." if status == "APPROVED"
                                                              else f" (남은 단계 {pr['required_steps'] - step})"), pr_id)


def cancel_pr(pr_id: int, actor: dict, wh_ids=None) -> PResult:
    with db.transaction() as conn:
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr is None or (wh_ids is not None and pr["warehouse_id"] not in wh_ids):
            return PResult(False, "요청이 없거나 권한 밖입니다.")
        if pr["status"] not in ("PENDING", "APPROVED"):
            return PResult(False, "취소할 수 있는 요청이 아닙니다.")
        if pr["requested_by_id"] != actor.get("id") and not auth.has_role(actor, "MANAGER"):
            return PResult(False, "요청자나 관리자만 취소할 수 있습니다.")
        conn.execute("UPDATE purchase_requests SET status = 'CANCELLED', updated_at = ? WHERE id = ?", (now_str(), pr_id))
        audit.record(conn, actor, "PR_CANCEL", "purchase_request", pr_id, {"pr_no": pr["pr_no"]})
    return PResult(True, f"{pr['pr_no']} 취소", pr_id)


# ── 발주 ─────────────────────────────────────────────────────
def create_po(pr_id: int, supplier: str, prices: dict[int, float], sap_po_no: str, note: str,
              actor: dict, wh_ids=None) -> PResult:
    """승인된 구매요청으로 발주를 만든다. prices: {pr_item_id: 발주단가} (없으면 예상단가)."""
    supplier = supplier.strip()
    if not supplier:
        return PResult(False, "공급처를 입력하세요.")
    with db.transaction() as conn:
        supplier, supplier_id, problem = partners.apply(conn, supplier)
        if problem:
            return PResult(False, problem)
        db.lock(conn, f"pr:{pr_id}")
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr is None or pr["status"] != "APPROVED":
            return PResult(False, "최종 승인된 구매요청만 발주할 수 있습니다.")
        if pr["requested_by_id"] == actor.get("id"):
            return PResult(False, "구매요청자는 발주를 만들 수 없습니다(직무 분리).")
        if wh_ids is not None and pr["warehouse_id"] not in wh_ids:
            return PResult(False, "이 창고의 발주 권한이 없습니다.")
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id = ? ORDER BY line_no", (pr_id,)).fetchall()
        lines = []
        for it in items:
            price = float(prices.get(int(it["id"]), it["est_price"]))
            if price < 0:
                return PResult(False, "발주 단가는 0 이상이어야 합니다.")
            lines.append((it["line_no"], it["material_id"], float(it["qty"]), price))
        total = sum(q * p for _, _, q, p in lines)
        needs_approval = total > float(pr["total_amount"]) * (1 + config.PO_OVER_PR_TOLERANCE) + 1e-6
        po_no = _next_no(conn, "PO", "purchase_orders", "po_no")
        po_id = conn.execute(
            "INSERT INTO purchase_orders (po_no, pr_id, supplier, supplier_id, warehouse_id, status, sap_po_no, total_amount, "
            "note, created_by_id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (po_no, pr_id, supplier, supplier_id, pr["warehouse_id"], "PENDING_APPROVAL" if needs_approval else "OPEN",
             sap_po_no.strip(), total, note.strip(), actor.get("id"), actor["name"], now_str())).lastrowid
        conn.executemany("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, ?, ?, ?, ?)",
                         [(po_id, *line) for line in lines])
        conn.execute("UPDATE purchase_requests SET status = 'ORDERED', updated_at = ? WHERE id = ?", (now_str(), pr_id))
        audit.record(conn, actor, "PO_CREATE", "purchase_order", po_id,
                     {"po_no": po_no, "pr_no": pr["pr_no"], "supplier": supplier, "total": total,
                      "needs_approval": needs_approval})
        if needs_approval:
            notify.queue(conn, notify.recipients(conn, "MANAGER", pr["warehouse_id"], [actor.get("id"), pr["requested_by_id"]]),
                         f"발주 {po_no} 결재 요청",
                         [f"{actor['name']}님이 만든 발주가 요청 승인액보다 {config.PO_OVER_PR_TOLERANCE:.0%} 넘게 커서 결재가 필요합니다.",
                          f"공급처: {supplier} · 발주 ₩{total:,.0f} (요청 승인액 ₩{float(pr['total_amount']):,.0f})"],
                         f"/purchase/po/{po_id}", f"po:{po_id}")
    msg = f"발주 {po_no} 생성 — ₩{total:,.0f}"
    if needs_approval:
        msg += f" (요청 승인액 ₩{pr['total_amount']:,.0f}보다 {config.PO_OVER_PR_TOLERANCE:.0%} 넘게 커서 발주 결재 필요)"
    if not supplier_id:
        msg += " · " + partners.unknown_warning(supplier, None)
    return PResult(True, msg, po_id)


def approve_po(po_id: int, actor: dict, wh_ids=None) -> PResult:
    with db.transaction() as conn:
        po = conn.execute("SELECT o.*, r.requested_by_id FROM purchase_orders o "
                          "LEFT JOIN purchase_requests r ON r.id = o.pr_id WHERE o.id = ?", (po_id,)).fetchone()
        if po is None or po["status"] != "PENDING_APPROVAL":
            return PResult(False, "결재할 발주가 아닙니다.")
        if actor.get("id") in (po["created_by_id"], po["requested_by_id"]):
            return PResult(False, "발주자·구매요청자는 이 발주를 결재할 수 없습니다(직무 분리).")
        if not auth.has_role(actor, "MANAGER") or (wh_ids is not None and po["warehouse_id"] not in wh_ids):
            return PResult(False, "발주 결재 권한이 없습니다.")
        conn.execute("UPDATE purchase_orders SET status = 'OPEN', approved_by = ?, approved_at = ? WHERE id = ?",
                     (actor["name"], now_str(), po_id))
        audit.record(conn, actor, "PO_APPROVE", "purchase_order", po_id, {"po_no": po["po_no"]})
        notify.queue(conn, notify.user(conn, po["created_by_id"]), f"발주 {po['po_no']} 승인",
                     [f"{actor['name']}님이 발주를 승인했습니다. 입고할 수 있습니다."], f"/purchase/po/{po_id}", f"po:{po_id}")
    return PResult(True, f"발주 {po['po_no']} 승인 — 입고할 수 있습니다.", po_id)


def set_sap_po_no(po_id: int, sap_po_no: str, actor: dict, wh_ids=None, expected: str | None = None) -> PResult:
    sap_po_no = sap_po_no.strip()
    with db.transaction() as conn:
        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if po is None or (wh_ids is not None and po["warehouse_id"] not in wh_ids):
            return PResult(False, "발주가 없거나 권한 밖입니다.")
        if po["status"] == "CANCELLED":
            return PResult(False, "취소된 발주는 바꿀 수 없습니다.")
        if version.stale("purchase_orders", po, expected):
            return PResult(False, version.STALE, po_id)
        conn.execute("UPDATE purchase_orders SET sap_po_no = ? WHERE id = ?", (sap_po_no, po_id))
        audit.record(conn, actor, "PO_UPDATE", "purchase_order", po_id, {"sap_po_no": [po["sap_po_no"], sap_po_no]})
    return PResult(True, "SAP 구매오더 번호를 저장했습니다.", po_id)


def cancel_po(po_id: int, actor: dict, wh_ids=None) -> PResult:
    with db.transaction() as conn:
        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if po is None or (wh_ids is not None and po["warehouse_id"] not in wh_ids):
            return PResult(False, "발주가 없거나 권한 밖입니다.")
        if po["status"] not in ("PENDING_APPROVAL", "OPEN"):
            return PResult(False, "입고가 시작된 발주는 취소할 수 없습니다(입고를 먼저 취소하세요).")
        if _received(conn, po["po_no"]):
            return PResult(False, "입고 기록이 있는 발주는 취소할 수 없습니다.")
        conn.execute("UPDATE purchase_orders SET status = 'CANCELLED' WHERE id = ?", (po_id,))
        conn.execute("UPDATE purchase_requests SET status = 'APPROVED', updated_at = ? WHERE id = ?",
                     (now_str(), po["pr_id"]))
        audit.record(conn, actor, "PO_CANCEL", "purchase_order", po_id, {"po_no": po["po_no"]})
    return PResult(True, f"발주 {po['po_no']} 취소 — 구매요청은 다시 발주할 수 있는 상태가 됐습니다.", po_id)


# ── 입고 연계 ───────────────────────────────────────────────
def _received(conn, po_no: str, line_no: int | None = None) -> float:
    sql = "SELECT COALESCE(SUM(qty), 0) FROM transactions WHERE tx_type = 'IN' AND po_no = ? AND transfer_no = ''"
    params: list = [po_no]
    if line_no is not None:
        sql += " AND po_item = ?"
        params.append(str(line_no))
    return float(conn.execute(sql, params).fetchone()[0])


def receipt_problem(conn, po_no: str, po_item: str, material_id: int, warehouse_id: int, qty: float) -> str:
    """이 시스템에서 만든 발주면 입고 가능 여부를 확인한다. 외부(SAP) 구매오더 번호면 ''."""
    po = conn.execute("SELECT * FROM purchase_orders WHERE po_no = ?", (po_no,)).fetchone()
    if po is None:
        return ""
    if po["status"] not in ("OPEN", "PARTIAL"):
        return f"발주 {po_no}는 {PO_STATUS.get(po['status'], po['status'])} 상태라 입고할 수 없습니다."
    if po["warehouse_id"] != warehouse_id:
        return f"발주 {po_no}의 입고 창고와 다릅니다."
    if not (str(po_item).isascii() and str(po_item).isdigit()) or len(str(po_item)) > 9:
        return "발주 품목 번호(10, 20 …)를 선택하세요."
    item = conn.execute("SELECT * FROM po_items WHERE po_id = ? AND line_no = ?", (po["id"], int(po_item))).fetchone()
    if item is None or item["material_id"] != material_id:
        return f"발주 {po_no} 품목 {po_item}의 자재가 아닙니다."
    remaining = float(item["qty"]) - _received(conn, po_no, int(po_item))
    if qty > remaining * (1 + config.GR_OVER_TOLERANCE) + 1e-9:
        return f"발주 잔량({remaining:,.2f})보다 많이 입고할 수 없습니다."
    return ""


def refresh_po_status(conn, po_no: str) -> None:
    po = conn.execute("SELECT * FROM purchase_orders WHERE po_no = ?", (po_no,)).fetchone()
    if po is None or po["status"] not in ("OPEN", "PARTIAL", "CLOSED"):
        return
    items = conn.execute("SELECT line_no, qty FROM po_items WHERE po_id = ?", (po["id"],)).fetchall()
    got = [_received(conn, po_no, it["line_no"]) for it in items]
    status = ("CLOSED" if all(g >= float(it["qty"]) - 1e-9 for g, it in zip(got, items))
              else "PARTIAL" if any(g > 1e-9 for g in got) else "OPEN")
    if status != po["status"]:
        conn.execute("UPDATE purchase_orders SET status = ? WHERE id = ?", (status, po["id"]))


# ── 조회 ─────────────────────────────────────────────────────
def _scope(col: str, wh_ids) -> tuple[str, list]:
    frag, wp = db.in_clause(wh_ids)
    return (f" AND {col}{frag}" if frag else ""), wp


def prs_df(status: str | None = None, wh_ids=None) -> pd.DataFrame:
    sql = """SELECT r.id, r.pr_no, r.status, w.code AS wh_code, r.need_date, r.total_amount, r.required_steps,
                    (SELECT COUNT(*) FROM pr_approvals a WHERE a.pr_id = r.id AND a.decision = 'APPROVE') AS approved_steps,
                    r.requested_by, r.requested_by_id, r.requested_at, r.reason
             FROM purchase_requests r JOIN warehouses w ON w.id = r.warehouse_id WHERE 1 = 1"""
    params: list = []
    if status:
        sql += " AND r.status = ?"
        params.append(status)
    wsql, wp = _scope("r.warehouse_id", wh_ids)
    return db.query_df(sql + wsql + " ORDER BY r.id DESC LIMIT 500", params + wp)


def pr_detail(pr_id: int) -> tuple[dict | None, pd.DataFrame, pd.DataFrame]:
    pr = db.query_df("SELECT r.*, w.code AS wh_code FROM purchase_requests r JOIN warehouses w ON w.id = r.warehouse_id "
                     "WHERE r.id = ?", (pr_id,))
    if pr.empty:
        return None, pd.DataFrame(), pd.DataFrame()
    items = db.query_df("SELECT i.id, i.line_no, m.code, m.name, m.unit, i.qty, i.est_price, i.qty * i.est_price AS amount "
                        "FROM pr_items i JOIN materials m ON m.id = i.material_id WHERE i.pr_id = ? ORDER BY i.line_no",
                        (pr_id,))
    approvals = db.query_df("SELECT step, approver, decision, comment, at FROM pr_approvals WHERE pr_id = ? ORDER BY id",
                            (pr_id,))
    return pr.iloc[0].to_dict(), items, approvals


def pos_df(status: str | None = None, wh_ids=None) -> pd.DataFrame:
    sql = """SELECT o.id, o.po_no, o.status, o.supplier, w.code AS wh_code, o.total_amount, o.sap_po_no,
                    r.pr_no, o.created_by, o.created_at, o.approved_by
             FROM purchase_orders o JOIN warehouses w ON w.id = o.warehouse_id
             LEFT JOIN purchase_requests r ON r.id = o.pr_id WHERE 1 = 1"""
    params: list = []
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    wsql, wp = _scope("o.warehouse_id", wh_ids)
    return db.query_df(sql + wsql + " ORDER BY o.id DESC LIMIT 500", params + wp)


def po_detail(po_id: int) -> tuple[dict | None, pd.DataFrame, dict]:
    """(발주, 품목별 입고 현황, 3자 대조 합계)."""
    po = db.query_df("SELECT o.*, w.code AS wh_code, r.pr_no, r.total_amount AS pr_amount FROM purchase_orders o "
                     "JOIN warehouses w ON w.id = o.warehouse_id LEFT JOIN purchase_requests r ON r.id = o.pr_id "
                     "WHERE o.id = ?", (po_id,))
    if po.empty:
        return None, pd.DataFrame(), {}
    header = po.iloc[0].to_dict()
    items = db.query_df("""
        SELECT i.line_no, i.material_id, m.code, m.name, m.unit, i.qty, i.price, i.qty * i.price AS amount,
               COALESCE((SELECT SUM(t.qty) FROM transactions t WHERE t.tx_type = 'IN' AND t.po_no = ?
                         AND t.po_item = CAST(i.line_no AS TEXT) AND t.transfer_no = ''), 0) AS received
        FROM po_items i JOIN materials m ON m.id = i.material_id WHERE i.po_id = ? ORDER BY i.line_no
        """, (header["po_no"], po_id))
    items["remaining"] = (items["qty"] - items["received"]).clip(lower=0)
    items["received_amount"] = items["received"] * items["price"]
    inv = db.query_df("""
        SELECT COALESCE(SUM(d.supply_amount), 0) AS supply, COUNT(*) AS n FROM documents d
        JOIN transactions t ON t.id = d.tx_id WHERE t.po_no = ? AND d.doc_type IN ('E_TAX_INVOICE', 'TAX_INVOICE', 'INVOICE')
        """, (header["po_no"],)).iloc[0]
    match = {"ordered": float(items["amount"].sum()), "received": float(items["received_amount"].sum()),
             "invoiced": float(inv["supply"]), "invoice_count": int(inv["n"])}
    match["gap"] = match["invoiced"] - match["received"]
    return header, items, match


def open_po_lines(warehouse_id: int, material_id: int) -> list[dict]:
    """입고 화면에서 고를 수 있는 발주 품목 (그 창고·자재, 잔량 있는 것)."""
    df = db.query_df("""
        SELECT o.po_no, i.line_no, i.qty, i.price, o.supplier,
               COALESCE((SELECT SUM(t.qty) FROM transactions t WHERE t.tx_type = 'IN' AND t.po_no = o.po_no
                         AND t.po_item = CAST(i.line_no AS TEXT) AND t.transfer_no = ''), 0) AS received
        FROM purchase_orders o JOIN po_items i ON i.po_id = o.id
        WHERE o.status IN ('OPEN', 'PARTIAL') AND o.warehouse_id = ? AND i.material_id = ?
        ORDER BY o.po_no, i.line_no
        """, (warehouse_id, material_id))
    df["remaining"] = df["qty"] - df["received"]
    return df[df["remaining"] > 1e-9].to_dict("records")

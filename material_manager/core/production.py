"""자재 명세서(BOM)와 생산 투입.

BOM: 제품(완제품·반제품) base_qty 개를 만드는 데 드는 부품·원자재와 수량, 손실률(%), 꺼내는 창고.
  - 제품마다 BOM 하나. 부품이 다시 BOM을 가진 반제품일 수 있다(다단계) — 반제품은 따로 생산 투입해 입고한 뒤 쓴다.
  - 저장할 때 막는 것: 제품을 자기 부품으로 쓰기, 순환(A의 부품 B의 BOM에 다시 A), 같은 부품 두 줄, 수량 0 이하.
생산 투입: 제품 N개 → 부품마다 소요량 = N ÷ base_qty × 수량 × (1 + 손실률) 을 한 트랜잭션에서 출고하고,
  (선택) 완제품을 입고한다. 완제품 입고 단가 = 투입 부품 금액(기준단가) ÷ N.
  - 판정은 입출고 화면과 같은 규칙(services._register): 재고 부족·마감·창고 권한·로트(유효기한 빠른 로트부터 자동)·SAP 매핑.
  - 한 부품이라도 모자라면 아무것도 등록하지 않는다. 등록 전에 소요량·가용 재고·부족을 미리 보여 준다.
  - 등록 당시 BOM을 생산 기록에 남긴다(나중에 BOM을 바꿔도 그 생산의 근거가 남는다).
  - 취소는 생산 전체를 취소 거래로 되돌린다(services.cancel_group).
SAP: 부품 출고는 원가센터 출고(201), 완제품 입고는 무PO 입고(501)로 전송된다 — SAP 생산오더(261/101) 연동은 하지 않는다.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from core import audit, db, org, purchasing, repository as repo, services
from core.utils import fmt_qty, now_str


@dataclass
class BomLine:
    component_id: int
    qty: float
    scrap_pct: float = 0.0
    issue_wh_id: int | None = None
    note: str = ""


# ── BOM ──────────────────────────────────────────────────────
def get_bom(product_id: int, conn=None) -> dict | None:
    if conn is None:
        with db.get_conn() as c:
            return get_bom(product_id, c)
    row = conn.execute("SELECT * FROM boms WHERE product_id = ?", (product_id,)).fetchone()
    return dict(row) if row is not None else None


def bom_items_df(bom_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT i.line_no, i.component_id, m.code, m.name, m.spec, m.unit, m.unit_price, m.lot_managed, m.active,
               i.qty, i.scrap_pct, i.issue_wh_id, w.code AS wh_code, i.note
        FROM bom_items i JOIN materials m ON m.id = i.component_id LEFT JOIN warehouses w ON w.id = i.issue_wh_id
        WHERE i.bom_id = ? ORDER BY i.line_no""", (bom_id,))


def boms_df() -> pd.DataFrame:
    """BOM 목록 + 제품 1개 표준 재료비(기준단가)."""
    df = db.query_df("""
        SELECT b.id, b.product_id, m.code, m.name, m.unit, b.base_qty, b.active, b.updated_at, b.updated_by,
               COUNT(i.id) AS lines,
               COALESCE(SUM(i.qty * (1 + i.scrap_pct / 100.0) * c.unit_price), 0) / b.base_qty AS unit_cost
        FROM boms b JOIN materials m ON m.id = b.product_id
        LEFT JOIN bom_items i ON i.bom_id = b.id LEFT JOIN materials c ON c.id = i.component_id
        GROUP BY b.id, b.product_id, m.code, m.name, m.unit, b.base_qty, b.active, b.updated_at, b.updated_by
        ORDER BY m.code""")
    return df


def _components(conn, product_id: int) -> set[int]:
    bom = conn.execute("SELECT id FROM boms WHERE product_id = ? AND active = 1", (product_id,)).fetchone()
    if bom is None:
        return set()
    return {int(r[0]) for r in conn.execute("SELECT component_id FROM bom_items WHERE bom_id = ?", (bom["id"],))}


def _uses(conn, start: int, target: int, depth: int = 0) -> bool:
    """start 의 BOM을 따라 내려가면 target 이 나오는가 (순환 확인)."""
    if depth > 20:
        return True                                           # 비정상적으로 깊으면 순환으로 본다
    for c in _components(conn, start):
        if c == target or _uses(conn, c, target, depth + 1):
            return True
    return False


def check_bom(product_id: int, base_qty: float, lines: list[BomLine]) -> tuple[list[BomLine], str]:
    """DB를 보지 않는 검사: 기준 수량, 수량·손실률, 자기 자신, 같은 부품 두 줄. (빈 줄을 뺀 줄, 문제)"""
    lines = [ln for ln in lines if ln.component_id]
    if not services._finite(base_qty) or base_qty <= 0:
        return lines, "기준 수량은 0보다 커야 합니다."
    if not lines:
        return lines, "부품을 한 줄 이상 넣으세요."
    seen = set()
    for i, ln in enumerate(lines, 1):
        if not services._finite(ln.qty, ln.scrap_pct) or ln.qty <= 0:
            return lines, f"{i}번 줄: 수량은 0보다 커야 합니다."
        if not 0 <= ln.scrap_pct < 100:
            return lines, f"{i}번 줄: 손실률은 0 이상 100 미만(%)이어야 합니다."
        if ln.component_id == product_id:
            return lines, f"{i}번 줄: 제품을 자기 부품으로 넣을 수 없습니다."
        if ln.component_id in seen:
            return lines, f"{i}번 줄: 같은 부품이 두 줄 있습니다. 한 줄로 합치세요."
        seen.add(ln.component_id)
    return lines, ""


def save_bom(product_id: int, base_qty: float, lines: list[BomLine], note: str, actor: dict | None,
             expected_updated_at: str | None = None) -> services.Result:
    lines, problem = check_bom(product_id, base_qty, lines)
    if problem:
        return services.Result(False, problem)
    with db.transaction() as conn:
        r = _save_bom(conn, product_id, base_qty, lines, note, actor or audit.SYSTEM, expected_updated_at)
    return r


def _save_bom(conn, product_id: int, base_qty: float, lines: list[BomLine], note: str, who: dict,
              expected_updated_at: str | None = None) -> services.Result:
    """save_bom 의 본문 — 호출하는 쪽 트랜잭션 안에서 (엑셀 일괄 등록은 여러 BOM을 한 트랜잭션에)."""
    ts = now_str()
    db.lock(conn, "bom")                                 # 순환 확인과 저장 사이에 다른 BOM이 바뀌지 않게
    prod = repo.get_material(product_id, conn)
    if prod is None or not prod["active"]:
        return services.Result(False, "사용 중인 자재를 제품으로 고르세요.")
    for i, ln in enumerate(lines, 1):
        comp = repo.get_material(ln.component_id, conn)
        if comp is None or not comp["active"]:
            return services.Result(False, f"{i}번 줄: 사용 중인 자재가 아닙니다.")
        if _uses(conn, ln.component_id, product_id):
            return services.Result(False, f"{i}번 줄: {comp['code']}의 BOM에 {prod['code']}가 들어 있어 순환이 됩니다.")
        if ln.issue_wh_id is not None:
            wh = org.get_warehouse(ln.issue_wh_id, conn)
            if wh is None or not wh["active"]:
                return services.Result(False, f"{i}번 줄: 사용 중인 창고가 아닙니다.")
    bom = get_bom(product_id, conn)
    if bom and expected_updated_at and str(bom["updated_at"]) != expected_updated_at:
        return services.Result(False, "화면을 연 뒤 다른 사용자가 이 BOM을 먼저 바꿨습니다. 새로고침한 뒤 다시 저장하세요.")
    before = ([dict(r) for r in conn.execute("SELECT component_id, qty, scrap_pct, issue_wh_id FROM bom_items "
                                             "WHERE bom_id = ? ORDER BY line_no", (bom["id"],))] if bom else None)
    if bom:
        bom_id = int(bom["id"])
        conn.execute("UPDATE boms SET base_qty = ?, note = ?, active = 1, updated_by = ?, updated_at = ? WHERE id = ?",
                     (base_qty, note.strip(), who["name"], ts, bom_id))
        conn.execute("DELETE FROM bom_items WHERE bom_id = ?", (bom_id,))
    else:
        bom_id = conn.execute("INSERT INTO boms (product_id, base_qty, note, active, updated_by, created_at, updated_at) "
                              "VALUES (?, ?, ?, 1, ?, ?, ?)", (product_id, base_qty, note.strip(), who["name"], ts, ts)).lastrowid
    conn.executemany("INSERT INTO bom_items (bom_id, line_no, component_id, qty, scrap_pct, issue_wh_id, note) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     [(bom_id, (i + 1) * 10, ln.component_id, ln.qty, ln.scrap_pct, ln.issue_wh_id, ln.note.strip())
                      for i, ln in enumerate(lines)])
    audit.record(conn, who, "BOM_SAVE", "bom", bom_id,
                 {"product": prod["code"], "base_qty": base_qty, "before": before,
                  "after": [{"component_id": ln.component_id, "qty": ln.qty, "scrap_pct": ln.scrap_pct,
                             "issue_wh_id": ln.issue_wh_id} for ln in lines]})
    return services.Result(True, f"{prod['code']} BOM 저장 — 부품 {len(lines)}줄", tx_id=bom_id)


def set_bom_active(product_id: int, active: bool, actor: dict | None) -> services.Result:
    with db.transaction() as conn:
        bom = get_bom(product_id, conn)
        if bom is None:
            return services.Result(False, "BOM이 없습니다.")
        conn.execute("UPDATE boms SET active = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                     (1 if active else 0, (actor or audit.SYSTEM)["name"], now_str(), bom["id"]))
        audit.record(conn, actor, "BOM_ACTIVE", "bom", bom["id"], {"product_id": product_id, "active": active})
    return services.Result(True, "BOM을 다시 씁니다." if active else "BOM 사용을 멈췄습니다(생산 투입 목록에서 빠짐).")


# ── 소요량 ───────────────────────────────────────────────────
def requirements(product_id: int, qty: float, issue_wh_id: int, wh_ids=None) -> dict:
    """제품 qty 개에 드는 부품별 소요량·가용 재고·부족, 지금 재고로 만들 수 있는 최대 수량."""
    bom = get_bom(product_id)
    if bom is None or not bom["active"]:
        return {"ok": False, "message": "이 제품의 BOM이 없습니다.", "lines": []}
    items = bom_items_df(int(bom["id"]))
    base = float(bom["base_qty"])
    out, makeable = [], math.inf
    with db.get_conn() as conn:
        for r in items.itertuples():
            wh_id = int(r.issue_wh_id) if pd.notna(r.issue_wh_id) else issue_wh_id
            per_unit = float(r.qty) * (1 + float(r.scrap_pct or 0) / 100) / base
            need = round(per_unit * qty, 4)
            stock = repo.current_stock(conn, int(r.component_id), wh_id)
            if bool(r.lot_managed):                          # 유효기한 지난 로트는 쓸 수 없다
                stock = sum(float(b["qty"]) for b in repo.lot_balances(conn, int(r.component_id), wh_id)
                            if not (b["expiry_date"] and b["expiry_date"] < date.today().isoformat()))
            allowed = wh_ids is None or wh_id in wh_ids
            wh = org.get_warehouse(wh_id, conn)
            makeable = min(makeable, math.floor(stock / per_unit + 1e-9) if per_unit > 0 else math.inf)
            out.append({"component_id": int(r.component_id), "code": r.code, "name": r.name, "unit": r.unit,
                        "per_unit": per_unit, "need": need, "stock": stock, "short": max(need - stock, 0.0),
                        "wh_id": wh_id, "wh_code": wh["code"] if wh else "?", "allowed": allowed,
                        "unit_price": float(r.unit_price or 0), "amount": need * float(r.unit_price or 0),
                        "lot_managed": bool(r.lot_managed), "active": bool(r.active)})
    cost = sum(x["amount"] for x in out)
    return {"ok": True, "lines": out, "base_qty": base, "makeable": 0 if makeable is math.inf else int(makeable),
            "short_cnt": sum(1 for x in out if x["short"] > 1e-9), "cost": cost,
            "unit_cost": cost / qty if qty else 0.0, "bom": bom}


# ── 원가 ─────────────────────────────────────────────────────
COST_DAYS = 180


def current_cost(conn, material_id: int) -> float:
    """실제 원가에 쓰는 자재 단가: 최근 {COST_DAYS}일 입고(이동·취소 제외)의 가중평균. 입고가 없으면 기준단가."""
    since = (date.today() - timedelta(days=COST_DAYS)).isoformat()
    row = conn.execute("""
        SELECT SUM(t.qty * t.unit_price), SUM(t.qty) FROM transactions t
        WHERE t.material_id = ? AND t.tx_type = 'IN' AND t.transfer_no = '' AND t.reversal_of IS NULL
          AND t.unit_price > 0 AND t.qty > 0 AND t.tx_date >= ?
          AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.reversal_of = t.id)""", (material_id, since)).fetchone()
    if row and row[1] and float(row[1]) > 0:
        return round(float(row[0]) / float(row[1]), 4)
    m = conn.execute("SELECT unit_price FROM materials WHERE id = ?", (material_id,)).fetchone()
    return float(m[0] or 0) if m else 0.0


# ── 공정(라우팅) ──────────────────────────────────────────────
@dataclass
class Op:
    op_name: str
    workcenter: str = ""
    std_minutes: float = 0.0
    note: str = ""


def routing(product_id: int) -> list[dict]:
    return db.query_df("SELECT seq, op_name, workcenter, std_minutes, note FROM routings WHERE product_id = ? ORDER BY seq",
                       (product_id,)).to_dict("records")


def save_routing(product_id: int, ops: list[Op], actor: dict | None) -> services.Result:
    ops = [o for o in ops if o.op_name.strip()]
    for i, o in enumerate(ops, 1):
        if not services._finite(o.std_minutes) or o.std_minutes < 0:
            return services.Result(False, f"{i}번 공정: 표준 시간은 0 이상이어야 합니다.")
    with db.transaction() as conn:
        prod = repo.get_material(product_id, conn)
        if prod is None:
            return services.Result(False, "제품이 없습니다.")
        conn.execute("DELETE FROM routings WHERE product_id = ?", (product_id,))
        conn.executemany("INSERT INTO routings (product_id, seq, op_name, workcenter, std_minutes, note) VALUES (?, ?, ?, ?, ?, ?)",
                         [(product_id, (i + 1) * 10, o.op_name.strip(), o.workcenter.strip(), o.std_minutes, o.note.strip())
                          for i, o in enumerate(ops)])
        audit.record(conn, actor, "ROUTING_SAVE", "material", product_id,
                     {"product": prod["code"], "ops": [o.op_name for o in ops]})
    return services.Result(True, f"{prod['code']} 공정 {len(ops)}개 저장")


# ── 작업지시 ─────────────────────────────────────────────────
# 상태: PLANNED(계획) → RELEASED(자재 투입 시작 = 재공) → DONE(완료 입고) / CANCELLED
STATUS = {"PLANNED": "계획", "RELEASED": "진행(재공)", "DONE": "완료", "CANCELLED": "취소"}


def _who(actor):
    return services._actor(actor, "")


def _create(conn, who: dict, product_id: int, qty: float, issue_wh_id: int, receipt_wh_id: int | None, *,
            due_date: str, work_order: str, cost_center: str, note: str, source: str, wh_ids) -> int | services.Result:
    """작업지시 한 건 (계획 상태): BOM 줄 → production_lines(계획 수량), 공정 → wo_operations."""
    if not services._finite(qty) or qty <= 0:
        return services.Result(False, "수량은 0보다 커야 합니다.")
    prod = repo.get_material(product_id, conn)
    bom = get_bom(product_id, conn)
    if prod is None or not prod["active"] or bom is None or not bom["active"]:
        return services.Result(False, "BOM이 있는 사용 중인 제품을 고르세요.")
    for w in {issue_wh_id, receipt_wh_id} - {None}:
        problem = services._warehouse_problem(org.get_warehouse(w, conn), wh_ids)
        if problem:
            return services.Result(False, problem)
    base = float(bom["base_qty"])
    items = conn.execute("SELECT i.*, m.unit_price FROM bom_items i JOIN materials m ON m.id = i.component_id "
                         "WHERE i.bom_id = ? ORDER BY i.line_no", (bom["id"],)).fetchall()
    due = due_date or date.today().isoformat()
    start = (date.fromisoformat(due) - timedelta(days=int(prod["lead_time_days"] or 0))).isoformat()
    prod_no = purchasing._next_no(conn, "MO", "productions", "prod_no")
    planned_cost = 0.0
    lines = []
    for it in items:
        need = round(float(it["qty"]) * (1 + float(it["scrap_pct"] or 0) / 100) / base * qty, 4)
        lines.append((int(it["component_id"]), int(it["issue_wh_id"]) if it["issue_wh_id"] is not None else issue_wh_id,
                      need, float(it["unit_price"] or 0)))
        planned_cost += need * float(it["unit_price"] or 0)
    snapshot = [{"component_id": c, "need": n, "wh_id": w, "std_price": p} for c, w, n, p in lines]
    pid = conn.execute(
        "INSERT INTO productions (prod_no, product_id, qty, issue_wh_id, receipt_wh_id, tx_date, work_order, cost_center, "
        "bom_snapshot, note, created_by_id, created_by, created_at, status, source, due_date, start_date, planned_cost) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', ?, ?, ?, ?)",
        (prod_no, product_id, qty, issue_wh_id, receipt_wh_id, due, work_order.strip(), cost_center.strip(),
         json.dumps(snapshot, ensure_ascii=False), note.strip(), who.get("id"), who["name"], now_str(), source, due, start,
         planned_cost)).lastrowid
    conn.executemany("INSERT INTO production_lines (production_id, line_no, component_id, wh_id, planned_qty, std_price) "
                     "VALUES (?, ?, ?, ?, ?, ?)", [(pid, (i + 1) * 10, c, w, n, p) for i, (c, w, n, p) in enumerate(lines)])
    ops = conn.execute("SELECT * FROM routings WHERE product_id = ? ORDER BY seq", (product_id,)).fetchall()
    conn.executemany("INSERT INTO wo_operations (production_id, seq, op_name, workcenter, std_minutes) VALUES (?, ?, ?, ?, ?)",
                     [(pid, o["seq"], o["op_name"], o["workcenter"], o["std_minutes"]) for o in ops])
    audit.record(conn, who, "WO_CREATE", "production", pid,
                 {"prod_no": prod_no, "product": prod["code"], "qty": qty, "due": due, "source": source})
    return pid


def _issue(conn, who: dict, pid: int, quantities: dict[int, float], tx_date: str, wh_ids,
           extra: list[tuple[int, float, int]] = ()) -> str | services.Result:
    """자재 투입. quantities: {production_lines.id: 수량(+ 투입 / - 반납)}, extra: [(자재, 수량, 창고)] BOM에 없는 추가 투입.
    투입은 출고 거래(단가 = 지금 실제 단가), 반납은 입고 거래(그 줄의 평균 투입 단가). 줄마다 투입 누계·금액을 남긴다."""
    p = conn.execute("SELECT p.*, m.code AS product_code FROM productions p JOIN materials m ON m.id = p.product_id "
                     "WHERE p.id = ?", (pid,)).fetchone()
    if p is None or p["status"] not in ("PLANNED", "RELEASED"):
        return services.Result(False, "자재를 투입할 수 있는 작업지시가 아닙니다 (완료·취소됨).")
    if _scope_problem(p, wh_ids):
        return services.Result(False, _scope_problem(p, wh_ids))
    for comp, q, wh in extra:
        if not q:
            continue
        line = conn.execute("SELECT id FROM production_lines WHERE production_id = ? AND component_id = ? AND wh_id = ?",
                            (pid, comp, wh)).fetchone()
        if line is None:
            n = conn.execute("SELECT COALESCE(MAX(line_no), 0) FROM production_lines WHERE production_id = ?", (pid,)).fetchone()[0]
            lid = conn.execute("INSERT INTO production_lines (production_id, line_no, component_id, wh_id, planned_qty, std_price, note) "
                               "VALUES (?, ?, ?, ?, 0, (SELECT unit_price FROM materials WHERE id = ?), '추가 투입')",
                               (pid, int(n) + 10, comp, wh, comp)).lastrowid
        else:
            lid = int(line["id"])
        quantities = {**quantities, lid: quantities.get(lid, 0) + q}
    ref = p["work_order"] or p["prod_no"]
    moved = 0
    for lid, q in quantities.items():
        if not services._finite(q) or abs(q) < 1e-9:
            continue
        ln = conn.execute("SELECT l.*, m.code FROM production_lines l JOIN materials m ON m.id = l.component_id "
                          "WHERE l.id = ? AND l.production_id = ?", (lid, pid)).fetchone()
        if ln is None:
            return services.Result(False, "작업지시에 없는 줄입니다.")
        if q > 0:
            price = current_cost(conn, int(ln["component_id"]))
            out = services._register(conn, who, int(ln["component_id"]), "OUT", q, tx_date, price, ref, "",
                                     f"생산 투입 {p['prod_no']} → {p['product_code']}", "", "", p["cost_center"] or "",
                                     int(ln["wh_id"]), wh_ids, "", "", production_id=pid)
            if isinstance(out, services.Result):
                return services.Result(False, f"{ln['code']} — {out.message}")
            cost = q * price
        else:                                                   # 반납: 남은 자재를 창고로 되돌린다
            back = -q
            if back > float(ln["issued_qty"]) + 1e-9:
                return services.Result(False, f"{ln['code']} — 투입한 {fmt_qty(float(ln['issued_qty']))}보다 많이 반납할 수 없습니다.")
            price = float(ln["issued_cost"]) / float(ln["issued_qty"]) if float(ln["issued_qty"]) else 0.0
            for lot, part in _return_lots(conn, pid, int(ln["component_id"]), int(ln["wh_id"]), back):
                out = services._register(conn, who, int(ln["component_id"]), "IN", part, tx_date, price, ref, "",
                                         f"생산 반납 {p['prod_no']}", "", "", p["cost_center"] or "", int(ln["wh_id"]), wh_ids,
                                         lot, "", production_id=pid)
                if isinstance(out, services.Result):
                    return services.Result(False, f"{ln['code']} 반납 — {out.message}")
            cost = -back * price
        conn.execute("UPDATE production_lines SET issued_qty = issued_qty + ?, issued_cost = issued_cost + ? WHERE id = ?",
                     (q, cost, lid))
        moved += 1
    if not moved:
        return services.Result(False, "투입하거나 반납할 수량을 넣으세요.")
    total = conn.execute("SELECT COALESCE(SUM(issued_cost), 0) FROM production_lines WHERE production_id = ?", (pid,)).fetchone()[0]
    conn.execute("UPDATE productions SET status = 'RELEASED', material_cost = ? WHERE id = ?", (float(total), pid))
    audit.record(conn, who, "WO_ISSUE", "production", pid, {"prod_no": p["prod_no"], "lines": moved, "wip": float(total)})
    return f"{moved}줄 투입·반납 (재공 ₩{float(total):,.0f})"


def _return_lots(conn, pid: int, material_id: int, wh_id: int, qty: float) -> list[tuple[str, float]]:
    """반납을 어느 로트로: 로트 관리 자재면 이 작업지시가 투입하고 아직 반납하지 않은 로트에서, 마지막에 투입한 로트부터.
    로트 관리가 아니면 [('', 수량)]."""
    if not conn.execute("SELECT lot_managed FROM materials WHERE id = ?", (material_id,)).fetchone()[0]:
        return [("", qty)]
    rows = conn.execute("""
        SELECT lot_no, SUM(CASE WHEN tx_type = 'OUT' THEN qty ELSE -qty END) AS net, MAX(id) AS last_id
        FROM transactions WHERE production_id = ? AND material_id = ? AND warehouse_id = ? AND tx_type IN ('IN', 'OUT')
        GROUP BY lot_no ORDER BY MAX(id) DESC""", (pid, material_id, wh_id)).fetchall()
    out, rest = [], qty
    for r in rows:
        take = min(rest, float(r["net"] or 0))
        if take > 1e-9 and r["lot_no"]:
            out.append((r["lot_no"], round(take, 6)))
            rest -= take
        if rest <= 1e-9:
            break
    if rest > 1e-9:                                   # 기록과 맞지 않으면 등록 단계에서 '로트 번호' 오류가 난다
        out.append(("", round(rest, 6)))
    return out


def _scope_problem(p, wh_ids) -> str:
    """작업지시의 부품·입고 창고가 사용자 범위 안인가 (범위 밖 작업지시는 완료·취소·실적 입력 불가)."""
    if wh_ids is None or p is None:
        return ""
    whs = {int(w) for w in (p["issue_wh_id"], p["receipt_wh_id"]) if w is not None}
    return "" if whs <= {int(w) for w in wh_ids} else "이 작업지시의 창고 권한이 없습니다."


def _complete(conn, who: dict, pid: int, good_qty: float, scrap_qty: float, tx_date: str, wh_ids, *,
              backflush: bool, lot_no: str = "", expiry_date: str = "") -> str | services.Result:
    """완료: (선택) 미투입분 자동 투입(백플러시) → 양품 입고(단가 = 실제 투입 금액 ÷ 양품) → 완료."""
    p = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
    if p is None or p["status"] not in ("PLANNED", "RELEASED"):
        return services.Result(False, "완료할 수 있는 작업지시가 아닙니다.")
    if _scope_problem(p, wh_ids):
        return services.Result(False, _scope_problem(p, wh_ids))
    if not services._finite(good_qty, scrap_qty) or good_qty < 0 or scrap_qty < 0 or good_qty + scrap_qty <= 0:
        return services.Result(False, "양품·불량 수량을 확인하세요 (합이 0보다 커야 함).")
    if backflush:
        rest = {int(r["id"]): round(float(r["planned_qty"]) - float(r["issued_qty"]), 4)
                for r in conn.execute("SELECT id, planned_qty, issued_qty FROM production_lines WHERE production_id = ?", (pid,))}
        rest = {k: v for k, v in rest.items() if v > 1e-9}
        if rest:
            out = _issue(conn, who, pid, rest, tx_date, wh_ids)
            if isinstance(out, services.Result):
                return out
    cost = float(conn.execute("SELECT COALESCE(SUM(issued_cost), 0) FROM production_lines WHERE production_id = ?",
                              (pid,)).fetchone()[0])
    receipt = ""
    if p["receipt_wh_id"] is not None and good_qty > 0:
        prod = repo.get_material(int(p["product_id"]), conn)
        unit_cost = round(cost / good_qty, 4)
        out = services._register(conn, who, int(p["product_id"]), "IN", good_qty, tx_date, unit_cost,
                                 p["work_order"] or p["prod_no"], "", f"생산 입고 {p['prod_no']}", "", "", "",
                                 int(p["receipt_wh_id"]), wh_ids, (lot_no.strip() or p["prod_no"]).upper(), expiry_date.strip(),
                                 production_id=pid)
        if isinstance(out, services.Result):
            return services.Result(False, f"완제품 입고 — {out.message}")
        receipt = f" · {prod['code']} {fmt_qty(good_qty)} {prod['unit']} 입고({out['wh']['code']}, 실제 단가 ₩{unit_cost:,.0f})"
    conn.execute("UPDATE productions SET status = 'DONE', good_qty = ?, scrap_qty = ?, material_cost = ?, completed_at = ?, "
                 "tx_date = ? WHERE id = ?", (good_qty, scrap_qty, cost, now_str(), tx_date, pid))
    conn.execute("UPDATE wo_operations SET status = 'DONE', good_qty = CASE WHEN good_qty = 0 THEN ? ELSE good_qty END, "
                 "done_at = CASE WHEN done_at = '' THEN ? ELSE done_at END WHERE production_id = ? AND status = 'WAIT'",
                 (good_qty, now_str(), pid))
    audit.record(conn, who, "WO_COMPLETE", "production", pid,
                 {"prod_no": p["prod_no"], "good": good_qty, "scrap": scrap_qty, "cost": cost})
    return f"완료 — 양품 {fmt_qty(good_qty)} · 불량 {fmt_qty(scrap_qty)} · 실제 재료비 ₩{cost:,.0f}{receipt}"


def _run(fn, *args, **kw) -> services.Result:
    """한 트랜잭션에서 돌리고, 거부(Result)면 되돌린다."""
    class _Undo(Exception):
        pass
    holder = {}
    try:
        with db.transaction() as conn:
            out = fn(conn, *args, **kw)
            if isinstance(out, services.Result) and not out.ok:
                holder["r"] = out
                raise _Undo()
            holder["r"] = out
    except _Undo:
        return holder["r"]
    out = holder["r"]
    return out if isinstance(out, services.Result) else services.Result(True, str(out))


def create_wo(product_id: int, qty: float, issue_wh_id: int, *, due_date: str, actor: dict | None, wh_ids=None,
              receipt_wh_id: int | None = None, work_order: str = "", cost_center: str = "", note: str = "",
              source: str = "MANUAL") -> services.Result:
    who = _who(actor)

    def go(conn):
        pid = _create(conn, who, product_id, qty, issue_wh_id, receipt_wh_id, due_date=due_date, work_order=work_order,
                      cost_center=cost_center, note=note, source=source, wh_ids=wh_ids)
        if isinstance(pid, services.Result):
            return pid
        no = conn.execute("SELECT prod_no, start_date FROM productions WHERE id = ?", (pid,)).fetchone()
        return services.Result(True, f"작업지시 {no['prod_no']} — 착수 {no['start_date']} · 완료 예정 {due_date or date.today()}",
                               tx_id=pid)
    return _run(go)


def issue(pid: int, quantities: dict[int, float], tx_date: str, *, actor: dict | None, wh_ids=None,
          extra: list[tuple[int, float, int]] = ()) -> services.Result:
    who = _who(actor)
    return _run(lambda conn: _issue(conn, who, pid, quantities, tx_date, wh_ids, extra))


def report_operation(pid: int, op_id: int, good: float, scrap: float, minutes: float, worker: str, note: str,
                     actor: dict | None, wh_ids=None) -> services.Result:
    if not services._finite(good, scrap, minutes) or min(good, scrap, minutes) < 0:
        return services.Result(False, "수량·시간은 0 이상이어야 합니다.")
    who = _who(actor)
    with db.transaction() as conn:
        p = conn.execute("SELECT prod_no, status, issue_wh_id, receipt_wh_id FROM productions WHERE id = ?", (pid,)).fetchone()
        op = conn.execute("SELECT * FROM wo_operations WHERE id = ? AND production_id = ?", (op_id, pid)).fetchone()
        if p is None or op is None or p["status"] in ("DONE", "CANCELLED"):
            return services.Result(False, "실적을 넣을 수 있는 공정이 아닙니다.")
        if _scope_problem(p, wh_ids):
            return services.Result(False, _scope_problem(p, wh_ids))
        conn.execute("UPDATE wo_operations SET good_qty = ?, scrap_qty = ?, minutes = ?, worker = ?, note = ?, status = 'DONE', "
                     "done_at = ? WHERE id = ?", (good, scrap, minutes, worker.strip() or who["name"], note.strip(), now_str(), op_id))
        audit.record(conn, who, "WO_OPERATION", "production", pid,
                     {"prod_no": p["prod_no"], "op": op["op_name"], "good": good, "scrap": scrap, "minutes": minutes})
    return services.Result(True, f"{op['op_name']} 실적 — 양품 {fmt_qty(good)} · 불량 {fmt_qty(scrap)}")


def complete(pid: int, good_qty: float, scrap_qty: float, tx_date: str, *, actor: dict | None, wh_ids=None,
             backflush: bool = True, lot_no: str = "", expiry_date: str = "") -> services.Result:
    who = _who(actor)
    return _run(lambda conn: _complete(conn, who, pid, good_qty, scrap_qty, tx_date, wh_ids, backflush=backflush,
                                       lot_no=lot_no, expiry_date=expiry_date))


def post(product_id: int, qty: float, issue_wh_id: int, tx_date: str, *, actor: dict | None, wh_ids=None,
         receipt_wh_id: int | None = None, work_order: str = "", cost_center: str = "", note: str = "",
         lot_no: str = "", expiry_date: str = "", actual: dict[int, float] | None = None,
         scrap_qty: float = 0.0) -> services.Result:
    """간편 생산 투입: 작업지시 생성 → 투입(기본 BOM 소요량, actual={부품 id: 실제 수량}이면 그 수량) → 완료를 한 트랜잭션에.
    scrap_qty: 불량(양품 = qty - 불량). 하나라도 안 되면 아무것도 등록하지 않는다."""
    if not services._finite(qty, scrap_qty) or qty <= 0 or scrap_qty < 0 or scrap_qty > qty + 1e-9:
        return services.Result(False, "생산 수량은 0보다 크고, 불량은 생산 수량 이하여야 합니다.")
    who = _who(actor)

    def go(conn):
        pid = _create(conn, who, product_id, qty, issue_wh_id, receipt_wh_id, due_date=tx_date, work_order=work_order,
                      cost_center=cost_center, note=note, source="QUICK", wh_ids=wh_ids)
        if isinstance(pid, services.Result):
            return pid
        lines = conn.execute("SELECT l.id, l.component_id, l.planned_qty, m.code FROM production_lines l "
                             "JOIN materials m ON m.id = l.component_id WHERE l.production_id = ? ORDER BY l.line_no",
                             (pid,)).fetchall()
        quantities = {int(r["id"]): (float(actual[int(r["component_id"])]) if actual and int(r["component_id"]) in actual
                                     else float(r["planned_qty"])) for r in lines}
        for i, r in enumerate(lines, 1):                   # 줄 번호를 붙여 어느 부품이 문제인지 알린다
            q = quantities[int(r["id"])]
            if q < 0:
                return services.Result(False, f"{i}번 줄: 실제 투입량은 0 이상이어야 합니다.")
        out = _issue(conn, who, pid, {k: v for k, v in quantities.items() if v > 0}, tx_date, wh_ids) \
            if any(v > 0 for v in quantities.values()) else ""
        if isinstance(out, services.Result):
            return services.Result(False, f"아무것도 등록하지 않았습니다: {out.message}")
        done = _complete(conn, who, pid, qty - scrap_qty, scrap_qty, tx_date, wh_ids, backflush=False,
                         lot_no=lot_no, expiry_date=expiry_date)
        if isinstance(done, services.Result):
            return services.Result(False, f"아무것도 등록하지 않았습니다: {done.message}")
        no = conn.execute("SELECT prod_no, material_cost FROM productions WHERE id = ?", (pid,)).fetchone()
        prod = repo.get_material(product_id, conn)
        return services.Result(True, f"생산 투입 {no['prod_no']} — {prod['code']} {qty:,.2f}개분 부품 {len(lines)}종 출고 · {done}",
                               qty=qty, tx_id=pid)
    return _run(go)


def cancel(prod_id: int, reason: str, *, actor: dict | None, wh_ids=None) -> services.Result:
    """작업지시 취소: 거래가 없으면(계획) 상태만, 있으면 모든 거래를 취소 거래로 되돌린다(완료 입고 포함)."""
    p = get(prod_id)
    if p is None:
        return services.Result(False, "생산 기록이 없습니다.")
    if p["status"] == "CANCELLED" or p["cancelled_at"]:
        return services.Result(False, f"이미 취소한 작업지시입니다 ({p['cancelled_at']}).")
    if not (reason or "").strip():
        return services.Result(False, "취소 사유를 입력하세요.")
    if _scope_problem(p, wh_ids):
        return services.Result(False, _scope_problem(p, wh_ids))
    has_tx = db.scalar("SELECT COUNT(*) FROM transactions WHERE production_id = ? AND reversal_of IS NULL", (prod_id,))
    if not has_tx:
        who = _who(actor)
        with db.transaction() as conn:
            conn.execute("UPDATE productions SET status = 'CANCELLED', cancelled_at = ?, cancelled_by = ?, cancel_reason = ? "
                         "WHERE id = ?", (now_str(), who["name"], reason.strip(), prod_id))
            audit.record(conn, who, "TX_GROUP_CANCEL", "production", prod_id, {"reason": reason, "reversed": []})
        return services.Result(True, f"작업지시 {p['prod_no']}를 취소했습니다 (거래 없음).")
    return services.cancel_group("production_id", prod_id, reason, actor=actor, wh_ids=wh_ids, label=f"생산 {p['prod_no']}")


def wip_df(wh_ids=None) -> pd.DataFrame:
    """재공품: 자재를 투입했지만 아직 완료 입고하지 않은 작업지시와 투입 금액."""
    frag, wp = db.in_clause(wh_ids)
    return db.query_df(f"""
        SELECT p.id, p.prod_no, m.code, m.name, p.qty, m.unit, p.start_date, p.due_date, w.code AS issue_wh,
               COALESCE(SUM(l.issued_cost), 0) AS wip_cost, p.planned_cost,
               (SELECT COUNT(*) FROM wo_operations o WHERE o.production_id = p.id AND o.status = 'DONE') AS ops_done,
               (SELECT COUNT(*) FROM wo_operations o WHERE o.production_id = p.id) AS ops_total
        FROM productions p JOIN materials m ON m.id = p.product_id JOIN warehouses w ON w.id = p.issue_wh_id
        LEFT JOIN production_lines l ON l.production_id = p.id
        WHERE p.status = 'RELEASED'{' AND p.issue_wh_id' + frag if frag else ''}
        GROUP BY p.id, p.prod_no, m.code, m.name, p.qty, m.unit, p.start_date, p.due_date, w.code, p.planned_cost
        ORDER BY p.due_date""", wp)


def wo_lines(prod_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT l.id, l.line_no, l.component_id, m.code, m.name, m.unit, w.code AS wh_code, l.wh_id, l.planned_qty,
               l.issued_qty, l.issued_cost, l.std_price, l.note
        FROM production_lines l JOIN materials m ON m.id = l.component_id LEFT JOIN warehouses w ON w.id = l.wh_id
        WHERE l.production_id = ? ORDER BY l.line_no""", (prod_id,))


def wo_ops(prod_id: int) -> pd.DataFrame:
    return db.query_df("SELECT * FROM wo_operations WHERE production_id = ? ORDER BY seq", (prod_id,))


def shortage_request(product_id: int, qty: float, issue_wh_id: int, need_date: str, *, actor: dict,
                     wh_ids=None) -> purchasing.PResult:
    """부족한 부품만 모아 구매요청을 만든다 (부족분 = 소요량 - 가용 재고, 단가는 기준단가). 창고마다 따로."""
    req = requirements(product_id, qty, issue_wh_id, wh_ids)
    short = [x for x in req.get("lines", []) if x["short"] > 1e-9]
    if not short:
        return purchasing.PResult(False, "부족한 부품이 없습니다.")
    prod_mat = repo.get_material(product_id)
    by_wh: dict[int, list] = {}
    for x in short:
        by_wh.setdefault(x["wh_id"], []).append((x["component_id"], math.ceil(x["short"] * 100) / 100, x["unit_price"]))
    msgs, first = [], 0
    for wh_id, items in by_wh.items():
        r = purchasing.create_pr(wh_id, items, need_date, f"생산 {prod_mat['code']} {qty:,.0f}개 부품 부족분", actor, wh_ids)
        if not r.ok:
            return r
        first = first or r.id
        msgs.append(r.message)
    return purchasing.PResult(True, " / ".join(msgs), first)


# ── 조회 ─────────────────────────────────────────────────────
def get(prod_id: int) -> dict | None:
    df = db.query_df("""
        SELECT p.*, m.code, m.name, m.unit, wi.code AS issue_wh, wr.code AS receipt_wh
        FROM productions p JOIN materials m ON m.id = p.product_id JOIN warehouses wi ON wi.id = p.issue_wh_id
        LEFT JOIN warehouses wr ON wr.id = p.receipt_wh_id WHERE p.id = ?""", (prod_id,))
    return None if df.empty else df.iloc[0].to_dict()


def list_df(wh_ids=None, limit: int = 300) -> pd.DataFrame:
    frag, wp = db.in_clause(wh_ids)
    return db.query_df(f"""
        SELECT p.id, p.prod_no, p.tx_date, m.code, m.name, p.qty, m.unit, wi.code AS issue_wh, wr.code AS receipt_wh,
               p.material_cost, p.work_order, p.created_by, p.cancelled_at, p.status, p.good_qty, p.scrap_qty,
               p.planned_cost, p.due_date, p.source
        FROM productions p JOIN materials m ON m.id = p.product_id JOIN warehouses wi ON wi.id = p.issue_wh_id
        LEFT JOIN warehouses wr ON wr.id = p.receipt_wh_id
        {'WHERE p.issue_wh_id' + frag if frag else ''}
        ORDER BY p.id DESC LIMIT ?""", (*wp, limit))


def lines_df(prod_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT t.id, t.tx_type, m.code, m.name, m.unit, w.code AS wh_code, t.lot_no, t.qty, t.unit_price, t.note,
               t.qty * t.unit_price AS amount,
               (SELECT r.id FROM transactions r WHERE r.reversal_of = t.id) AS reversed_by
        FROM transactions t JOIN materials m ON m.id = t.material_id JOIN warehouses w ON w.id = t.warehouse_id
        WHERE t.production_id = ? AND t.reversal_of IS NULL ORDER BY t.tx_type DESC, t.id""", (prod_id,))


def products_with_bom() -> dict[int, str]:
    df = db.query_df("SELECT m.id, m.code, m.name FROM boms b JOIN materials m ON m.id = b.product_id "
                     "WHERE b.active = 1 AND m.active = 1 ORDER BY m.code")
    return {int(r.id): f"[{r.code}] {r.name}" for r in df.itertuples()}


# ── 시연 샘플 (창원 제조공장) ──────────────────────────────────
SAMPLE_PRODUCTS = [
    # (코드, 이름, 규격, 단위, 분류, 안전재고, 기준단가, 보관위치)
    ("SA-BRK-001", "모터 브래킷 (반제품)", "SGCC 0.8t 분체도장", "EA", "반제품", 40, 0, "F-01"),
    ("FG-FAN-001", "BLDC 송풍기 모듈", "24V 60W Ø180", "EA", "완제품", 20, 0, "F-02"),
]
SAMPLE_BOMS = {
    # 제품: [(부품, 수량, 손실률%, 꺼내는 창고)]  — 기준 수량 1
    "SA-BRK-001": [("RM-STL-002", 1.6, 5, "CW-RM"), ("PT-BLT-001", 4, 0, "CW-PT"), ("CH-PNT-001", 0.12, 3, "CW-CH")],
    "FG-FAN-001": [("PT-MTR-001", 1, 0, "CW-PT"), ("PT-PCB-001", 1, 0, "CW-PT"), ("PT-HRN-001", 2, 1, "CW-PT"),
                   ("PT-BRG-001", 2, 0, "CW-PT"), ("PT-BLT-001", 8, 2, "CW-PT"), ("PT-NUT-001", 8, 2, "CW-PT"),
                   ("PT-SEL-001", 2, 0, "CW-PT"), ("SA-BRK-001", 1, 0, "CW-FG"), ("PK-BOX-001", 1, 0, "CW-PT")],
}


def seed_sample() -> dict:
    """창원 제조공장(core/seed_mfg.py)에 완제품 창고·제품 2종·BOM·생산 투입 2건을 더한다. 이미 있으면 건너뛴다."""
    plant = db.scalar("SELECT id FROM plants WHERE code = 'P-CW'")
    if not plant or db.scalar("SELECT COUNT(*) FROM boms"):
        return {"skipped": True}
    ts = now_str()
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM warehouses WHERE code = 'CW-FG'").fetchone():
            conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, 'CW-FG', ?, '', ?)",
                         (plant, "완제품·반제품 창고", ts))
        for code, name, spec, unit, cat, safety, price, loc in SAMPLE_PRODUCTS:
            conn.execute("INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, supplier, "
                         "active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 1, ?, ?) ON CONFLICT (code) DO NOTHING",
                         (code, name, spec, unit, cat, safety, price, loc, ts, ts))
    ids = {r.code: int(r.id) for r in db.query_df("SELECT id, code FROM materials").itertuples()}
    whs = {r.code: int(r.id) for r in db.query_df("SELECT id, code FROM warehouses").itertuples()}
    system = {**audit.SYSTEM, "name": "system"}
    for product, items in SAMPLE_BOMS.items():
        save_bom(ids[product], 1, [BomLine(ids[c], q, s, whs[w]) for c, q, s, w in items], "시연 샘플", system)
    # 표준 원가를 기준단가로 (브래킷 → 송풍기 순서)
    for product in SAMPLE_BOMS:
        cost = float(boms_df().set_index("code").loc[product, "unit_cost"])
        db.execute("UPDATE materials SET unit_price = ? WHERE id = ?", (round(cost), ids[product]))
    today = date.today().isoformat()
    made = []
    for product, qty, src, dst in (("SA-BRK-001", 60, "CW-RM", "CW-FG"), ("FG-FAN-001", 20, "CW-PT", "CW-FG")):
        r = post(ids[product], qty, whs[src], today, actor=system, receipt_wh_id=whs[dst],
                 work_order=f"WO-{date.today():%y%m}-{len(made) + 1:03d}", note="시연 샘플")
        made.append(r.ok)
    return {"boms": len(SAMPLE_BOMS), "productions": sum(made)}

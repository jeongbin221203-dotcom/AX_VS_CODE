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
from datetime import date

import pandas as pd

from core import audit, db, org, purchasing, repository as repo, services
from core.utils import now_str


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


def save_bom(product_id: int, base_qty: float, lines: list[BomLine], note: str, actor: dict | None,
             expected_updated_at: str | None = None) -> services.Result:
    lines = [ln for ln in lines if ln.component_id]
    if not services._finite(base_qty) or base_qty <= 0:
        return services.Result(False, "기준 수량은 0보다 커야 합니다.")
    if not lines:
        return services.Result(False, "부품을 한 줄 이상 넣으세요.")
    seen = set()
    for i, ln in enumerate(lines, 1):
        if not services._finite(ln.qty, ln.scrap_pct) or ln.qty <= 0:
            return services.Result(False, f"{i}번 줄: 수량은 0보다 커야 합니다.")
        if not 0 <= ln.scrap_pct < 100:
            return services.Result(False, f"{i}번 줄: 손실률은 0 이상 100 미만(%)이어야 합니다.")
        if ln.component_id == product_id:
            return services.Result(False, f"{i}번 줄: 제품을 자기 부품으로 넣을 수 없습니다.")
        if ln.component_id in seen:
            return services.Result(False, f"{i}번 줄: 같은 부품이 두 줄 있습니다. 한 줄로 합치세요.")
        seen.add(ln.component_id)
    who = actor or audit.SYSTEM
    ts = now_str()
    with db.transaction() as conn:
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


# ── 생산 투입 ────────────────────────────────────────────────
def post(product_id: int, qty: float, issue_wh_id: int, tx_date: str, *, actor: dict | None, wh_ids=None,
         receipt_wh_id: int | None = None, work_order: str = "", cost_center: str = "", note: str = "",
         lot_no: str = "", expiry_date: str = "") -> services.Result:
    if not services._finite(qty) or qty <= 0:
        return services.Result(False, "생산 수량은 0보다 커야 합니다.")
    who = services._actor(actor, "")
    req = requirements(product_id, qty, issue_wh_id, wh_ids)
    if not req["ok"]:
        return services.Result(False, req["message"])
    prod_mat = repo.get_material(product_id)
    try:
        with db.transaction() as conn:
            prod_no = purchasing._next_no(conn, "MO", "productions", "prod_no")
            snapshot = [{k: x[k] for k in ("component_id", "code", "per_unit", "need", "wh_code", "unit_price")}
                        for x in req["lines"]]
            pid = conn.execute(
                "INSERT INTO productions (prod_no, product_id, qty, issue_wh_id, receipt_wh_id, tx_date, work_order, "
                "cost_center, bom_snapshot, note, created_by_id, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (prod_no, product_id, qty, issue_wh_id, receipt_wh_id, tx_date, work_order.strip(), cost_center.strip(),
                 json.dumps(snapshot, ensure_ascii=False), note.strip(), who.get("id"), who["name"], now_str())).lastrowid
            ref = work_order.strip() or prod_no
            cost, first_tx = 0.0, 0
            for i, x in enumerate(req["lines"], 1):
                out = services._register(conn, who, x["component_id"], "OUT", x["need"], tx_date, x["unit_price"], ref,
                                         "", f"생산 투입 {prod_no} → {prod_mat['code']}", "", "", cost_center,
                                         x["wh_id"], wh_ids, "", "", production_id=pid)
                if isinstance(out, services.Result):
                    raise services._Rejected(i, f"{x['code']} — {out.message}")
                first_tx = first_tx or out["tx_ids"][0]
                cost += x["need"] * x["unit_price"]
            receipt_msg = ""
            if receipt_wh_id is not None:
                unit_cost = round(cost / qty, 4)
                out = services._register(conn, who, product_id, "IN", qty, tx_date, unit_cost, ref, "",
                                         f"생산 입고 {prod_no}", "", "", "", receipt_wh_id, wh_ids,
                                         (lot_no.strip() or prod_no).upper(), expiry_date.strip(), production_id=pid)
                if isinstance(out, services.Result):
                    raise services._Rejected(len(req["lines"]) + 1, f"완제품 입고 — {out.message}")
                receipt_msg = f" · {prod_mat['code']} {qty:,.2f} {prod_mat['unit']} 입고({out['wh']['code']}, 단가 ₩{unit_cost:,.0f})"
            conn.execute("UPDATE productions SET material_cost = ? WHERE id = ?", (cost, pid))
            audit.record(conn, who, "PRODUCTION_CREATE", "production", pid,
                         {"prod_no": prod_no, "product": prod_mat["code"], "qty": qty, "lines": len(req["lines"]),
                          "cost": cost, "receipt_wh_id": receipt_wh_id, "work_order": work_order})
    except services._Rejected as exc:
        return services.Result(False, f"{exc.no}번 줄이 거부되어 아무것도 등록하지 않았습니다: {exc.message}")
    return services.Result(True, f"생산 투입 {prod_no} — {prod_mat['code']} {qty:,.2f}개분 부품 {len(req['lines'])}종 출고"
                                 f" (재료비 ₩{cost:,.0f}){receipt_msg}", qty=qty, tx_id=pid)


def cancel(prod_id: int, reason: str, *, actor: dict | None, wh_ids=None) -> services.Result:
    p = get(prod_id)
    if p is None:
        return services.Result(False, "생산 기록이 없습니다.")
    if p["cancelled_at"]:
        return services.Result(False, f"이미 취소한 생산입니다 ({p['cancelled_at']}).")
    return services.cancel_group("production_id", prod_id, reason, actor=actor, wh_ids=wh_ids, label=f"생산 {p['prod_no']}")


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
               p.material_cost, p.work_order, p.created_by, p.cancelled_at
        FROM productions p JOIN materials m ON m.id = p.product_id JOIN warehouses wi ON wi.id = p.issue_wh_id
        LEFT JOIN warehouses wr ON wr.id = p.receipt_wh_id
        {'WHERE p.issue_wh_id' + frag if frag else ''}
        ORDER BY p.id DESC LIMIT ?""", (*wp, limit))


def lines_df(prod_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT t.id, t.tx_type, m.code, m.name, m.unit, w.code AS wh_code, t.lot_no, t.qty, t.unit_price,
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

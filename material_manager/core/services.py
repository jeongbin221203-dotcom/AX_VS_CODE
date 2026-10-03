"""업무 규칙 계층.

화면(Flask)과 무관하게 '무엇이 허용되는가'를 판정한다.
따라서 이 파일의 규칙은 단위 테스트로 그대로 검증 가능하다.
"""

import math
import re
import secrets
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

import config
from core import approvals, audit, db, master_sync, org, partners, periods, purchasing, repository as repo, sap
from core.utils import clean_str_series, code_series, now_str


@dataclass
class Result:
    ok: bool
    message: str
    qty: float = 0.0
    stock_after: float = 0.0
    warning: str = ""
    tx_id: int = 0
    pending: bool = False        # 결재 대기로 넘어감


# SAP 마스터 동기화 자재에서 SAP가 원본인 항목 (여기서 고치면 다음 동기화 때 덮어써진다)
SAP_OWNED_FIELDS = ("name", "unit", "category", "unit_price", "sap_matnr")


@dataclass
class UploadResult:
    df: pd.DataFrame
    dropped: int = 0
    duplicated: int = 0
    errors: list[str] = field(default_factory=list)
    bad_numbers: int = 0          # 숫자가 아닌 안전재고·단가 칸 (기존 값 유지)


# 업로드에서 비어 있으면 기존 자재의 값을 그대로 두는 항목 (자재코드·자재명은 필수)
UPLOAD_OPTIONAL = ("spec", "unit", "category", "safety_stock", "unit_price", "location", "supplier", "sap_matnr",
                   "barcode")


def _actor(actor: dict | None, created_by: str) -> dict:
    """로그인 사용자가 있으면 그 사람, 없으면(스크립트·테스트) 입력한 담당자 이름으로 기록한다."""
    if actor:
        return actor
    return {**audit.SYSTEM, "name": created_by.strip() or audit.SYSTEM["name"]}


def _warehouse_problem(wh: dict | None, wh_ids) -> str:
    if wh is None or not wh["active"] or not wh["plant_active"]:
        return "사용 중인 창고가 아닙니다."
    if wh_ids is not None and int(wh["id"]) not in wh_ids:
        return f"창고 {wh['code']}에 대한 권한이 없습니다."
    return ""


def register_transaction(material_id: int, tx_type: str, qty_input: float, tx_date: str,
                         unit_price: float, ref_no: str = "", partner: str = "",
                         note: str = "", created_by: str = "", *, actor: dict | None = None,
                         po_no: str = "", po_item: str = "", cost_center: str = "",
                         warehouse_id: int | None = None, wh_ids=None,
                         lot_no: str = "", expiry_date: str = "") -> Result:
    """입고 / 출고 / 실사조정 등록 (창고 단위).

    - 같은 자재를 바꾸는 작업은 잠금으로 줄 세운 뒤 재고를 다시 읽어 판정한다(동시 등록 방어).
    - 조정(ADJ)은 '실사수량'을 받아 현재고와의 차이만 기록한다. 차이 금액이 결재 기준 이상이면
      바로 반영하지 않고 결재 요청을 만든다(직무 분리).
    - 로트 관리 자재: 입고는 로트(+유효기한) 필수, 출고는 유효기한 빠른 로트부터 자동 배정(FEFO, 여러 행이 될 수 있음),
      조정은 로트별. 유효기한 지난 로트는 출고할 수 없다.
    - 구매오더(이 시스템에서 만든 발주) 입고는 발주 창고·자재·남은 수량을 확인한다.
    - 마감된 기간·미래 일자·권한 밖 창고는 거부한다.
    - SAP 연동 중이면 자재·창고 매핑과 원가센터를 확인하고, 같은 트랜잭션에서 전송 대기열에 넣는다.
    """
    if tx_type not in config.TX_LABEL:
        return Result(False, f"알 수 없는 거래 유형: {tx_type}")
    who = _actor(actor, created_by)
    po_no, po_item, cost_center = po_no.strip(), po_item.strip(), cost_center.strip()
    lot_no, expiry_date = lot_no.strip().upper(), expiry_date.strip()
    if tx_type != "IN":
        po_no = po_item = ""
    if po_item.isascii() and po_item.isdigit():
        po_item = str(int(po_item))                    # '010'과 '10'을 같은 품목으로 (입고 누계가 새지 않게)
    if tx_type != "OUT":
        cost_center = ""

    with db.transaction() as conn:
        out = _register(conn, who, material_id, tx_type, qty_input, tx_date, unit_price, ref_no, partner, note,
                        po_no, po_item, cost_center, warehouse_id, wh_ids, lot_no, expiry_date)
    if isinstance(out, Result):
        return out
    mat, qty, stock_after, tx_ids = out["mat"], out["qty"], out["stock_after"], out["tx_ids"]
    wh, sap_status, allocations, lot_managed = out["wh"], out["sap_status"], out["allocations"], out["lot_managed"]

    result = _done(mat, tx_type, qty, stock_after, tx_ids[0], wh, sap_status)
    if lot_managed:
        result.message += " · 로트 " + ", ".join(f"{lot} {q:,.2f}" for lot, q in allocations)
    result.warning = " ".join(filter(None, [result.warning, out["partner_warning"]]))
    return result


def _register(conn, who: dict, material_id: int, tx_type: str, qty_input: float, tx_date: str, unit_price: float,
              ref_no: str, partner: str, note: str, po_no: str, po_item: str, cost_center: str,
              warehouse_id: int | None, wh_ids, lot_no: str, expiry_date: str, statement_id: int | None = None,
              production_id: int | None = None, batch_no: str = ""):
    """register_transaction의 본문 — 호출하는 쪽의 트랜잭션 안에서 돈다(거래명세서·여러 줄 입출고·생산 투입은
    여러 줄을 한 트랜잭션에). 실패·결재 대기는 Result, 성공은 결과 dict.
    거래처 이름은 거래처 마스터에 있으면 정식 이름과 partner_id로 남긴다(입고와 원가센터 없는 출고만)."""
    if not _finite(qty_input, unit_price):
        return Result(False, "수량·단가를 숫자로 다시 입력하세요.")
    lot_no, expiry_date = (lot_no or "").strip().upper(), (expiry_date or "").strip()
    wh_id = warehouse_id or repo.default_warehouse_id(conn)
    wh = org.get_warehouse(wh_id, conn)
    problem = _warehouse_problem(wh, wh_ids) or periods.date_problem(conn, tx_date)
    if problem:
        return Result(False, problem)
    mat = repo.get_material(material_id, conn)
    if mat is None or not mat["active"]:
        return Result(False, "사용 중인 자재가 아닙니다.")
    lot_managed = bool(mat["lot_managed"])
    if not lot_managed:
        lot_no = expiry_date = ""
    partner_id, partner_warning = None, ""
    if partners.is_partner_tx(tx_type, cost_center):
        partner, partner_id, problem = partners.apply(conn, partner)
        if problem:
            return Result(False, problem)
        partner_warning = partners.unknown_warning(partner, partner_id)
    problem = (master_sync.cost_center_problem(conn, cost_center) if tx_type == "OUT" else "")
    if not problem and sap.enabled():
        problem = sap.mapping_problem(mat, tx_type, cost_center, po_no, po_item, warehouse=wh)
    if problem:
        return Result(False, problem)

    db.lock(conn, f"stock:{material_id}")
    allocations: list[tuple[str, float]]
    if tx_type == "IN":
        qty = qty_input
        if qty <= 0:
            return Result(False, "수량은 0보다 커야 합니다.")
        if lot_managed:
            problem = _lot_problem(conn, mat, lot_no, expiry_date, tx_date, receiving=True)
            if problem:
                return Result(False, problem)
        if po_no:
            db.lock(conn, f"po:{po_no}")
            problem = purchasing.receipt_problem(conn, po_no, po_item, material_id, wh_id, qty)
            if problem:
                return Result(False, problem)
        allocations = [(lot_no, qty)]
    elif tx_type == "OUT":
        qty = qty_input
        if qty <= 0:
            return Result(False, "수량은 0보다 커야 합니다.")
        if lot_managed:
            allocations, problem = _allocate(conn, mat, wh_id, qty, lot_no or None, tx_date)
            if problem:
                return Result(False, problem)
        else:
            _, avail = repo.balance_window(conn, material_id, wh_id, None, tx_date)
            if qty > avail + 1e-9:
                return Result(False, f"재고 부족: {wh['code']} {tx_date} 이후 출고 가능 {avail:,.2f}, "
                                     f"출고 요청 {qty:,.2f}")
            allocations = [("", qty)]
    else:                                               # ADJ
        if qty_input < 0:
            return Result(False, "실사수량은 0 이상이어야 합니다.")
        if lot_managed:
            if not lot_no:
                return Result(False, "로트 관리 자재는 로트별로 실사수량을 입력하세요.")
            problem = _lot_problem(conn, mat, lot_no, expiry_date, tx_date, receiving=False)
            if problem:
                return Result(False, problem)
        stock_now, low = repo.balance_window(conn, material_id, wh_id, lot_no if lot_managed else None, tx_date)
        qty = qty_input - stock_now                    # 실사일 말 장부수량과의 차이
        if abs(qty) < 1e-9:
            return Result(False, "실사수량이 장부수량과 같아 조정할 내용이 없습니다.")
        if low + qty < -1e-9:
            return Result(False, f"{tx_date} 이후 출고가 있어 이 조정을 넣으면 재고가 음수가 됩니다. "
                                 "실사일을 확인하세요.")
        # 결재 기준 금액은 자재 마스터 단가로 계산한다 (화면 단가를 낮춰 결재를 피할 수 없게)
        amount = abs(qty) * max(float(mat["unit_price"] or 0), float(unit_price or 0), 0.0)
        if config.ADJ_APPROVAL_AMOUNT and amount >= config.ADJ_APPROVAL_AMOUNT:
            req_id = approvals.create(conn, "ADJ", material_id, wh_id, tx_date, qty, amount, who, {
                "book_qty": stock_now, "counted_qty": qty_input, "unit_price": max(float(unit_price), 0.0),
                "ref_no": ref_no.strip(), "partner": partner.strip(), "note": note.strip(), "lot_no": lot_no})
            return Result(True, f"조정 금액 ₩{amount:,.0f}이 결재 기준(₩{config.ADJ_APPROVAL_AMOUNT:,})을 넘어 "
                                f"결재 요청 #{req_id}로 올렸습니다. 관리자가 승인하면 반영됩니다.",
                          qty=qty, stock_after=stock_now, pending=True)
        allocations = [(lot_no, qty)]

    tx_ids = []
    for lot, q in allocations:
        tx_ids.append(_insert(conn, who, {
            "material_id": material_id, "tx_type": tx_type, "qty": q, "warehouse_id": wh_id,
            "unit_price": max(float(unit_price), 0.0), "tx_date": tx_date, "lot_no": lot,
            "ref_no": ref_no.strip(), "partner": partner.strip(), "note": note.strip(),
            "po_no": po_no, "po_item": po_item, "cost_center": cost_center,
            "movement_type": sap.movement_type(tx_type, q, po_no), "statement_id": statement_id,
            "partner_id": partner_id, "production_id": production_id, "batch_no": batch_no,
        }, mat["code"]))
    if po_no:
        purchasing.refresh_po_status(conn, po_no)
    qty = sum(q for _, q in allocations)
    stock_after = repo.current_stock(conn, material_id, wh_id)
    sap_status = conn.execute("SELECT status FROM sap_outbox WHERE tx_id = ?", (tx_ids[0],)).fetchone()
    return {"mat": mat, "qty": qty, "stock_after": stock_after, "tx_ids": tx_ids, "wh": wh,
            "sap_status": sap_status, "allocations": allocations, "lot_managed": lot_managed,
            "partner": partner, "partner_id": partner_id, "partner_warning": partner_warning,
            "unit_price": max(float(unit_price), 0.0)}

# ── 여러 줄 입출고 (스캔·한 화면에서 여러 품목) ─────────────────
MAX_BATCH_LINES = 200


@dataclass
class LineIn:
    material_id: int
    qty: float
    lot_no: str = ""
    expiry_date: str = ""
    unit_price: float | None = None     # 비우면 자재 기준단가
    note: str = ""


class _Rejected(Exception):
    def __init__(self, no: int, message: str):
        super().__init__(message)
        self.no, self.message = no, message


def register_lines(kind: str, warehouse_id: int, tx_date: str, lines: list[LineIn], *, actor: dict | None,
                   wh_ids=None, ref_no: str = "", partner: str = "", cost_center: str = "", note: str = "") -> Result:
    """입고·출고 여러 줄을 한 트랜잭션으로 등록한다 (바코드로 찍은 품목 등).
    한 줄이라도 거부되면(재고 부족·마감·권한·로트 등) 아무것도 등록하지 않는다 — 판정은 한 건 등록과 같은 _register.
    같은 묶음 번호(batch_no)가 붙어 이력에서 함께 보고, 묶음 전체를 한 번에 취소할 수 있다."""
    if kind not in ("IN", "OUT"):
        return Result(False, "입고 또는 출고를 고르세요.")
    lines = [ln for ln in lines if ln.material_id]
    if not lines:
        return Result(False, "등록할 품목을 한 줄 이상 넣으세요 (자재를 찾거나 바코드를 스캔).")
    if len(lines) > MAX_BATCH_LINES:
        return Result(False, f"한 번에 {MAX_BATCH_LINES}줄까지 등록할 수 있습니다. 나눠서 등록하세요.")
    for i, ln in enumerate(lines, 1):
        if not _finite(ln.qty, ln.unit_price) or ln.qty <= 0:
            return Result(False, f"{i}번 줄: 수량은 0보다 큰 숫자여야 합니다.")
    who = _actor(actor, "")
    batch_no = f"B-{tx_date.replace('-', '')}-{secrets.token_hex(3).upper()}"
    warnings, first_tx, amount, shown_partner = [], 0, 0.0, partner
    try:
        with db.transaction() as conn:
            for i, ln in enumerate(lines, 1):
                mat = repo.get_material(ln.material_id, conn)
                price = ln.unit_price if ln.unit_price is not None else float(mat["unit_price"] or 0) if mat else 0.0
                out = _register(conn, who, ln.material_id, kind, float(ln.qty), tx_date, price, ref_no or batch_no,
                                partner, (ln.note or note).strip(), "", "", cost_center if kind == "OUT" else "",
                                warehouse_id, wh_ids, ln.lot_no, ln.expiry_date, batch_no=batch_no)
                if isinstance(out, Result):
                    raise _Rejected(i, out.message)
                first_tx = first_tx or out["tx_ids"][0]
                amount += float(out["qty"]) * out["unit_price"]
                shown_partner = out["partner"] or shown_partner
                if out["partner_warning"] and out["partner_warning"] not in warnings:
                    warnings.append(out["partner_warning"])
                safety = float(out["mat"]["safety_stock"] or 0)
                if kind == "OUT" and out["stock_after"] < safety:
                    warnings.append(f"{out['mat']['code']} 안전재고({safety:,.2f}) 미달 — 현재 {out['stock_after']:,.2f}")
            audit.record(conn, who, "TX_BATCH", "transaction", first_tx,
                         {"batch_no": batch_no, "kind": kind, "lines": len(lines), "warehouse_id": warehouse_id,
                          "partner": shown_partner, "amount": amount})
    except _Rejected as exc:
        return Result(False, f"{exc.no}번 줄이 거부되어 아무것도 등록하지 않았습니다: {exc.message}")
    return Result(True, f"{config.TX_LABEL[kind]} {len(lines)}줄 등록 (묶음 {batch_no}, 금액 ₩{amount:,.0f})",
                  qty=len(lines), warning=" · ".join(warnings[:6]), tx_id=first_tx)


def cancel_group(column: str, value, reason: str, *, actor: dict | None, wh_ids=None, label: str = "") -> Result:
    """묶음(batch_no)·생산(production_id)으로 함께 등록한 거래를 한 번에 취소한다. 한 줄이라도 못 하면 아무것도 안 한다.
    이미 따로 취소한 줄은 건너뛴다."""
    if column not in ("batch_no", "production_id"):
        raise ValueError(column)
    reason = (reason or "").strip()
    if not reason:
        return Result(False, "취소 사유를 입력하세요.")
    who = _actor(actor, "")
    label = label or str(value)
    try:
        with db.transaction() as conn:
            db.lock(conn, f"group:{column}:{value}")
            ids = [int(r["id"]) for r in conn.execute(
                f"SELECT t.id FROM transactions t WHERE t.{column} = ? AND t.reversal_of IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.reversal_of = t.id) ORDER BY t.id DESC",
                (value,)).fetchall()]
            if not ids:
                return Result(False, f"{label}: 취소할 거래가 없습니다 (이미 취소됨).")
            for tx_id in ids:                           # 나중 거래부터 (완제품 입고 → 부품 출고)
                out = _reverse(conn, who, tx_id, f"{label} 취소: {reason}", date.today().isoformat(), wh_ids)
                if isinstance(out, Result):
                    raise _Rejected(tx_id, out.message)
            if column == "production_id":
                conn.execute("UPDATE productions SET cancelled_at = ?, cancelled_by = ?, cancel_reason = ? WHERE id = ?",
                             (now_str(), who["name"], reason, value))
            audit.record(conn, who, "TX_GROUP_CANCEL", "transaction", ids[-1],
                         {column: value, "reason": reason, "reversed": ids})
    except _Rejected as exc:
        return Result(False, f"거래 #{exc.no}를 취소할 수 없어 아무것도 취소하지 않았습니다: {exc.message}")
    return Result(True, f"{label}를 취소했습니다 (취소 거래 {len(ids)}건).", qty=len(ids))


def _lot_problem(conn, mat: dict, lot_no: str, expiry_date: str, tx_date: str, receiving: bool) -> str:
    """로트 입력 검사. 입고면 로트 마스터를 만들거나 유효기한이 같은지 확인한다."""
    if not lot_no:
        return "로트 관리 자재입니다. 로트 번호를 입력하세요."
    if not re.fullmatch(r"[A-Z0-9._/-]{1,40}", lot_no):
        return "로트 번호는 영문·숫자·._/- 40자 이내로 입력하세요."
    if expiry_date:
        try:
            date.fromisoformat(expiry_date)
        except ValueError:
            return "유효기한 형식이 올바르지 않습니다."
    existing = repo.get_lot(conn, mat["id"], lot_no)
    if mat["expiry_managed"] and not expiry_date and existing is None:
        return "유효기한 관리 자재입니다. 새 로트의 유효기한을 입력하세요."
    if receiving and mat["expiry_managed"]:
        exp = expiry_date or (existing["expiry_date"] if existing else "")
        if exp and exp < tx_date:
            return f"유효기한({exp})이 이미 지난 로트는 입고할 수 없습니다."
    return repo.ensure_lot(conn, mat["id"], lot_no, expiry_date)


def _allocate(conn, mat: dict, wh_id: int, qty: float, lot_no: str | None, tx_date: str
              ) -> tuple[list[tuple[str, float]], str]:
    """출고·이동할 로트 배정. 로트를 주면 그 로트에서, 안 주면 유효기한이 빠른 로트부터(FEFO)."""
    balances = [{**b, "qty": min(float(b["qty"]),
                                 repo.balance_window(conn, mat["id"], wh_id, b["lot_no"], tx_date)[1])}
                for b in repo.lot_balances(conn, mat["id"], wh_id)]
    balances = [b for b in balances if b["qty"] > 1e-9]
    expired = {b["lot_no"] for b in balances if b["expiry_date"] and b["expiry_date"] < tx_date}
    if lot_no:
        if mat["expiry_managed"] and lot_no in expired:
            return [], f"로트 {lot_no}는 유효기한이 지났습니다. 출고할 수 없습니다(폐기는 실사조정으로)."
        have = next((b["qty"] for b in balances if b["lot_no"] == lot_no), 0.0)
        if qty > have + 1e-9:
            return [], f"재고 부족: 로트 {lot_no} 현재고 {have:,.2f}, 요청 {qty:,.2f}"
        return [(lot_no, qty)], ""
    usable = [b for b in balances if not (mat["expiry_managed"] and b["lot_no"] in expired)]
    total = sum(b["qty"] for b in usable)
    if qty > total + 1e-9:
        extra = f" (유효기한 지난 로트 {len(expired)}개 제외)" if expired and mat["expiry_managed"] else ""
        return [], f"재고 부족: 출고 가능 {total:,.2f}{extra}, 요청 {qty:,.2f}"
    out, left = [], qty
    for b in usable:                                   # lot_balances는 유효기한 빠른 순
        if left <= 1e-9:
            break
        use = min(b["qty"], left)
        out.append((b["lot_no"], use))
        left -= use
    return out, ""


def _insert(conn, who: dict, payload: dict, material_code: str, approved_by: str = "") -> int:
    """거래 한 행 + SAP 대기열 + 감사로그 (호출하는 쪽의 트랜잭션 안에서)."""
    payload = {**payload, "created_by": who["name"], "created_by_id": who.get("id"), "approved_by": approved_by}
    tx_id = repo.insert_transaction(conn, payload)
    if sap.enabled() and not (payload.get("transfer_no") and payload["tx_type"] == "IN"):
        sap.enqueue(conn, tx_id)                       # 창고 간 이동은 출고 쪽 한 건만 보낸다
    audit.record(conn, who, "TX_CREATE", "transaction", tx_id,
                 {**{k: payload.get(k) for k in ("tx_type", "qty", "tx_date", "warehouse_id", "ref_no",
                                                 "po_no", "cost_center", "transfer_no", "approved_by")},
                  "material": material_code})
    return tx_id


def _done(mat: dict, tx_type: str, qty: float, stock_after: float, tx_id: int, wh: dict, sap_status) -> Result:
    unit = mat.get("unit", "")
    safety = float(mat.get("safety_stock", 0) or 0)
    sign = f"{qty:+,.2f}" if tx_type == "ADJ" else f"{qty:,.2f}"
    warning = (f"안전재고({safety:,.2f}) 미달입니다. 발주를 검토하세요." if stock_after < safety else "")
    sap_note = " · SAP 전송 대기" if sap_status and sap_status[0] == "PENDING" else ""
    return Result(True, f"{config.TX_LABEL[tx_type]} {sign} {unit} 등록 → {wh['code']} 현재고 "
                        f"{stock_after:,.2f} {unit}{sap_note}",
                  qty=qty, stock_after=stock_after, warning=warning, tx_id=tx_id)


def post_approved_adjustment(conn, req: dict, approver: dict) -> Result:
    """결재 승인된 조정을 반영한다. 요청 당시 계산한 차이(qty)를 그대로 올린다(실사 시점 장부 기준)."""
    import json
    extra = json.loads(req["payload"] or "{}")
    problem = periods.date_problem(conn, req["tx_date"])
    if problem:
        return Result(False, problem)
    mat = repo.get_material(req["material_id"], conn)
    wh = org.get_warehouse(req["warehouse_id"], conn)
    if sap.enabled():
        problem = sap.mapping_problem(mat, "ADJ", "", "", "", warehouse=wh)
        if problem:
            return Result(False, problem)
    db.lock(conn, f"stock:{req['material_id']}")
    qty = float(req["qty"])
    lot = extra.get("lot_no", "")
    _, low = repo.balance_window(conn, req["material_id"], req["warehouse_id"], lot if lot else None, req["tx_date"])
    now = repo.current_stock(conn, req["material_id"], req["warehouse_id"], lot if lot else None)
    if low + qty < -1e-9:
        return Result(False, f"반영하면 재고가 {low + qty:,.2f}로 음수가 되는 날이 생깁니다. 실사를 다시 하세요.")
    requester = {"id": req["requested_by_id"], "name": req["requested_by"], "ip": approver.get("ip", "")}
    tx_id = _insert(conn, requester, {
        "material_id": req["material_id"], "tx_type": "ADJ", "qty": qty, "warehouse_id": req["warehouse_id"],
        "unit_price": extra.get("unit_price", 0), "tx_date": req["tx_date"], "ref_no": extra.get("ref_no", ""),
        "partner": extra.get("partner", ""), "note": (extra.get("note", "") + f" (결재 #{req['id']})").strip(),
        "movement_type": sap.movement_type("ADJ", qty), "lot_no": lot,
    }, mat["code"], approved_by=approver["name"])
    return Result(True, f"결재 #{req['id']} 승인 → 조정 {qty:+,.2f} 반영 (거래 #{tx_id})", tx_id=tx_id,
                  stock_after=now + qty)


def _finite(*values) -> bool:
    """nan·무한대·터무니없이 큰 값은 거래로 받지 않는다 (오프라인 대기열 JSON도 여기로 들어온다)."""
    try:
        return all(v is None or (math.isfinite(float(v)) and abs(float(v)) <= 1e15) for v in values)
    except (TypeError, ValueError):
        return False


def transfer(material_id: int, from_wh: int, to_wh: int, qty: float, tx_date: str, *,
             actor: dict | None = None, ref_no: str = "", note: str = "", wh_ids=None, lot_no: str = "") -> Result:
    """창고 간 이동: 보내는 창고 출고 + 받는 창고 입고를 같은 이동번호로 한 번에 기록한다.
    보내는 창고 권한이 있어야 한다(받는 창고는 다른 공장이어도 된다).
    로트 관리 자재는 로트를 유지한 채 옮긴다(로트를 안 주면 유효기한 빠른 로트부터)."""
    who = _actor(actor, "")
    if from_wh == to_wh:
        return Result(False, "보내는 창고와 받는 창고가 같습니다.")
    if not _finite(qty):
        return Result(False, "수량을 숫자로 다시 입력하세요.")
    if qty <= 0:
        return Result(False, "수량은 0보다 커야 합니다.")
    with db.transaction() as conn:
        src, dst = org.get_warehouse(from_wh, conn), org.get_warehouse(to_wh, conn)
        problem = (_warehouse_problem(src, wh_ids) or _warehouse_problem(dst, None)
                   or periods.date_problem(conn, tx_date))
        if problem:
            return Result(False, problem)
        mat = repo.get_material(material_id, conn)
        if mat is None or not mat["active"]:
            return Result(False, "사용 중인 자재가 아닙니다.")
        if sap.enabled():
            problem = (sap.mapping_problem(mat, "IN", "", "", "", warehouse=src)
                       or sap.warehouse_problem(dst))
            if problem:
                return Result(False, problem)
        db.lock(conn, f"stock:{material_id}")
        if mat["lot_managed"]:
            allocations, problem = _allocate(conn, mat, from_wh, qty, lot_no.strip().upper() or None, tx_date)
            if problem:
                return Result(False, problem)
        else:
            _, avail = repo.balance_window(conn, material_id, from_wh, None, tx_date)
            if qty > avail + 1e-9:
                return Result(False, f"재고 부족: {src['code']} {tx_date} 이후 이동 가능 {avail:,.2f}, 이동 요청 {qty:,.2f}")
            allocations = [("", qty)]
        transfer_no = f"TRF-{tx_date.replace('-', '')}-{secrets.token_hex(4).upper()}"
        mvt = sap.transfer_movement_type(src["plant_id"] == dst["plant_id"])
        common = {"material_id": material_id, "unit_price": mat["unit_price"], "tx_date": tx_date,
                  "ref_no": ref_no.strip(), "note": note.strip(), "transfer_no": transfer_no, "movement_type": mvt}
        out_id = None
        for lot, q in allocations:
            oid = _insert(conn, who, {**common, "qty": q, "lot_no": lot, "tx_type": "OUT", "warehouse_id": from_wh,
                                      "partner": f"→ {dst['code']}"}, mat["code"])
            out_id = out_id or oid
            _insert(conn, who, {**common, "qty": q, "lot_no": lot, "tx_type": "IN", "warehouse_id": to_wh,
                                "partner": f"← {src['code']}"}, mat["code"])
        audit.record(conn, who, "TX_TRANSFER", "transaction", out_id,
                     {"transfer_no": transfer_no, "from": src["code"], "to": dst["code"], "qty": qty,
                      "material": mat["code"], "movement_type": mvt, "lots": allocations if mat["lot_managed"] else None})
        stock_after = repo.current_stock(conn, material_id, from_wh)
    lots = (" · 로트 " + ", ".join(f"{lot} {q:,.2f}" for lot, q in allocations)) if mat["lot_managed"] else ""
    return Result(True, f"{src['code']} → {dst['code']} {qty:,.2f} {mat['unit']} 이동 ({transfer_no}, SAP {mvt}){lots}",
                  qty=qty, stock_after=stock_after, tx_id=out_id)


def reverse_transaction(tx_id: int, reason: str, *, actor: dict | None = None,
                        reverse_date: str | None = None, wh_ids=None) -> Result:
    """거래 취소. 원거래는 그대로 두고, 수량 부호가 반대인 취소 거래를 오늘(열린 기간) 일자로 남긴다.

    - 한 거래는 한 번만 취소할 수 있고, 취소 거래를 다시 취소할 수는 없다.
    - 본인이 등록한 거래는 다른 관리자가 취소해야 한다(직무 분리).
    - 취소 후 재고가 음수가 되면 거부한다. 창고 간 이동은 출고·입고 두 행을 함께 취소한다.
    """
    reason = reason.strip()
    if not reason:
        return Result(False, "취소 사유를 입력하세요.")
    who = _actor(actor, "")
    reverse_date = reverse_date or date.today().isoformat()
    with db.transaction() as conn:
        out = _reverse(conn, who, tx_id, reason, reverse_date, wh_ids)
    if isinstance(out, Result):
        return out
    tx, rev_ids, sap_status, after = out["tx"], out["rev_ids"], out["sap_status"], out["after"]
    note = {"PENDING": " · SAP 취소 전송 대기", "CANCELLED": " · SAP 전송 전이라 둘 다 전송 안 함"}.get(sap_status, "")
    what = f"이동 {tx['transfer_no']}" if tx["transfer_no"] else f"거래 #{tx_id}"
    return Result(True, f"{what}를 취소했습니다 (취소 거래 #{', #'.join(map(str, rev_ids))}){note}",
                  stock_after=after, tx_id=rev_ids[0])


def _reverse(conn, who: dict, tx_id: int, reason: str, reverse_date: str, wh_ids=None):
    """reverse_transaction의 본문 — 호출하는 쪽 트랜잭션 안에서 돈다(거래명세서 전체 취소는 여러 거래를 한 번에).
    거부는 Result, 성공은 {"tx", "rev_ids", "sap_status", "after"}."""
    problem = periods.date_problem(conn, reverse_date)
    if problem:
        return Result(False, problem)
    tx = repo.get_transaction(conn, tx_id)
    if tx is None:
        return Result(False, "거래가 없습니다.")
    if wh_ids is not None and tx["warehouse_id"] not in wh_ids:
        return Result(False, "이 창고의 거래를 취소할 권한이 없습니다.")
    if tx["reversal_of"] is not None:
        return Result(False, "취소 거래는 다시 취소할 수 없습니다. 필요하면 새로 등록하세요.")
    if config.SOD_ENFORCE and who.get("id") is not None and tx["created_by_id"] == who["id"]:
        return Result(False, "본인이 등록한 거래는 다른 관리자가 취소해야 합니다(직무 분리).")
    legs = repo.transfer_legs(conn, tx["transfer_no"]) if tx["transfer_no"] else [tx]
    if wh_ids is not None and any(leg["tx_type"] == "OUT" and leg["warehouse_id"] not in wh_ids for leg in legs):
        return Result(False, "보낸 창고 권한이 있어야 이동을 취소할 수 있습니다.")
    for leg in legs:
        done = repo.reversal_id(conn, leg["id"])
        if done is not None:
            return Result(False, f"이미 취소된 거래입니다 (취소 거래 #{done}).")
    db.lock(conn, f"stock:{tx['material_id']}")
    for po in sorted({leg["po_no"] for leg in legs if leg["po_no"]}):
        db.lock(conn, f"po:{po}")
    after = 0.0
    for leg in legs:
        effect = leg["qty"] if leg["tx_type"] in ("IN", "ADJ") else -leg["qty"]
        after = repo.current_stock(conn, leg["material_id"], leg["warehouse_id"],
                                   leg["lot_no"] if leg["lot_no"] else None) - effect
        if after < 0:
            return Result(False, f"취소하면 창고 재고가 {after:,.2f}로 음수가 됩니다. 이후 출고 건을 먼저 취소하세요.")
    rev_ids = []
    for leg in legs:
        rev_id = repo.insert_transaction(conn, {
            "material_id": leg["material_id"], "tx_type": leg["tx_type"], "qty": -leg["qty"],
            "unit_price": leg["unit_price"], "tx_date": reverse_date, "ref_no": leg["ref_no"],
            "partner": leg["partner"], "note": f"취소(#{leg['id']}): {reason}", "created_by": who["name"],
            "created_by_id": who.get("id"), "reversal_of": leg["id"], "po_no": leg["po_no"],
            "po_item": leg["po_item"], "cost_center": leg["cost_center"], "warehouse_id": leg["warehouse_id"],
            "transfer_no": leg["transfer_no"], "lot_no": leg["lot_no"],
            "movement_type": sap.reversal_movement_type(leg["movement_type"] or ""),
            "partner_id": leg["partner_id"], "production_id": leg["production_id"],
            "batch_no": leg["batch_no"] or "",
        })
        rev_ids.append(rev_id)
    # SAP: 이동은 출고 쪽만 전기했으므로 출고 쪽 취소만 전송 (로트가 여럿이면 로트마다)
    statuses = [sap.enqueue(conn, rev_id, reversal_of=leg["id"])
                for leg, rev_id in zip(legs, rev_ids)
                if sap.enabled() and not (leg["transfer_no"] and leg["tx_type"] == "IN")]
    sap_status = statuses[0] if statuses else ""
    for leg in legs:
        if leg["po_no"]:
            purchasing.refresh_po_status(conn, leg["po_no"])
    audit.record(conn, who, "TX_REVERSE", "transaction", tx_id,
                 {"reversal_tx": rev_ids, "reason": reason, "transfer_no": tx["transfer_no"] or None})
    return {"tx": tx, "rev_ids": rev_ids, "sap_status": sap_status, "after": after}

# ── 자재 마스터 (감사로그 포함) ─────────────────────────────────
BARCODE_RE = re.compile(r"[A-Z0-9._/+-]{4,64}")


def barcode_problem(conn, barcode: str, material_id: int | None = None) -> str:
    """바코드는 다른 자재의 바코드·자재코드·SAP 자재번호와 겹치면 안 된다(스캔하면 어느 자재인지 하나여야 한다)."""
    if not barcode:
        return ""
    if not BARCODE_RE.fullmatch(barcode):
        return "바코드는 영문·숫자(·._/+-) 4~64자로 입력하세요."
    row = conn.execute("SELECT code FROM materials WHERE id <> ? AND (barcode = ? OR UPPER(code) = ? OR UPPER(sap_matnr) = ?)",
                       (material_id or 0, barcode, barcode, barcode)).fetchone()
    return f"바코드 {barcode}가 자재 {row['code']}의 바코드·자재코드·SAP 번호와 겹칩니다." if row else ""


def _canonical_supplier(conn, name: str) -> str:
    """자재의 공급처 이름: 거래처 마스터에 있으면 정식 이름으로 (없으면 적은 그대로 — 자재 저장은 막지 않는다)."""
    p = partners.resolve(conn, name or "")
    return p["name"] if p else partners.norm_name(name or "")


def create_material(data: dict, actor: dict | None = None) -> Result:
    if not data.get("code") or not data.get("name"):
        return Result(False, "자재코드와 자재명은 필수입니다.")
    data = {**data, "barcode": (data.get("barcode") or "").strip().upper()}
    try:
        with db.transaction() as conn:
            problem = barcode_problem(conn, data["barcode"])
            if problem:
                return Result(False, problem)
            data["supplier"] = _canonical_supplier(conn, data.get("supplier", ""))
            mid = repo.insert_material(data, conn)
            audit.record(conn, actor, "MATERIAL_CREATE", "material", mid,
                         {f: data.get(f) for f in repo.MATERIAL_FIELDS})
    except db.IntegrityError:
        return Result(False, f"자재코드 '{data['code']}'는 이미 존재합니다.")
    return Result(True, f"자재 [{data['code']}] {data['name']} 등록 완료", tx_id=mid)


def update_material(material_id: int, data: dict, actor: dict | None = None,
                    expected_updated_at: str | None = None) -> Result:
    """expected_updated_at: 화면을 열 때의 수정 시각. 그 사이 다른 사람이 고쳤으면 덮어쓰지 않는다."""
    if not data.get("name"):
        return Result(False, "자재명은 필수입니다.")
    with db.transaction() as conn:
        before = repo.get_material(material_id, conn)
        if before is None:
            return Result(False, "자재를 찾을 수 없습니다.")
        if expected_updated_at and str(before["updated_at"]) != expected_updated_at:
            return Result(False, "화면을 연 뒤 다른 사용자(또는 SAP 동기화)가 이 자재를 먼저 바꿨습니다"
                                 f"({before['updated_at']}). 최신 내용을 확인한 뒤 다시 저장하세요.")
        data = {"lot_managed": int(before["lot_managed"] or 0), "expiry_managed": int(before["expiry_managed"] or 0),
                "barcode": before["barcode"] or "", **data}
        data["barcode"] = (data["barcode"] or "").strip().upper()
        problem = barcode_problem(conn, data["barcode"], material_id)
        if problem:
            return Result(False, problem)
        if "supplier" in data:
            data["supplier"] = _canonical_supplier(conn, data["supplier"])
        diff = audit.changes(before, data, repo.MATERIAL_FIELDS[1:])
        if not diff:
            return Result(True, "변경된 내용이 없습니다.")
        if ("lot_managed" in diff or "expiry_managed" in diff) and conn.execute(
                "SELECT 1 FROM transactions WHERE material_id = ? LIMIT 1", (material_id,)).fetchone():
            return Result(False, "거래가 있는 자재는 로트·유효기한 관리 방식을 바꿀 수 없습니다(재고가 로트와 맞지 않게 됨).")
        if before["sap_synced_at"] and config.SAP_MASTER_READONLY:
            locked = [f for f in SAP_OWNED_FIELDS if f in diff]
            if locked:
                return Result(False, f"SAP에서 동기화되는 항목({', '.join(locked)})은 SAP에서 바꿔야 합니다.")
        repo.update_material(material_id, data, conn)
        audit.record(conn, actor, "MATERIAL_UPDATE", "material", material_id, {"code": before["code"], **diff})
    return Result(True, "수정되었습니다.")


def set_material_active(material_id: int, active: bool, actor: dict | None = None) -> Result:
    with db.transaction() as conn:
        before = repo.get_material(material_id, conn)
        if before is None:
            return Result(False, "자재를 찾을 수 없습니다.")
        repo.set_material_active(material_id, active, conn)
        audit.record(conn, actor, "MATERIAL_ACTIVE", "material", material_id,
                     {"code": before["code"], "active": [bool(before["active"]), active]})
    return Result(True, "다시 사용합니다." if active else "사용중지했습니다. 이력은 유지됩니다.")


def import_materials(df: pd.DataFrame, actor: dict | None = None) -> Result:
    """정제된 업로드 결과를 반영한다. 신규/갱신 건수와 코드 목록을 감사로그에 남긴다.

    기존 자재는 파일에 값이 있는 칸만 바꾼다 — 빈 칸·파일에 없는 열은 기존 값을 그대로 둔다
    (일부 열만 담은 엑셀로 단가·안전재고·SAP 번호가 0이나 빈 값으로 덮어써지지 않게).
    SAP 마스터 동기화 자재의 SAP 항목은 업로드로도 바꾸지 않는다(화면 수정과 같은 규칙).
    """
    rows = df.to_dict("records")
    new, updated, sap_kept = [], [], []
    ts = now_str()
    try:
        with db.transaction() as conn:
            for r in rows:
                blank = set(filter(None, str(r.get("_blank") or "").split(",")))
                data = {f: r.get(f, "") for f in repo.UPLOAD_FIELDS}
                data["barcode"] = data["barcode"] or ""
                cur = conn.execute("SELECT id, sap_synced_at FROM materials WHERE code = ?", (data["code"],)).fetchone()
                if "barcode" not in blank:          # 바코드는 다른 자재와 겹치면 파일 전체를 반영하지 않는다
                    problem = barcode_problem(conn, data["barcode"], int(cur["id"]) if cur else None)
                    if problem:
                        raise _Rejected(0, f"{data['code']}: {problem}")
                if cur is None:
                    repo.insert_material(data, conn)
                    new.append(data["code"])
                    continue
                changes = {f: data[f] for f in repo.UPLOAD_FIELDS[1:] if f not in blank}
                if cur["sap_synced_at"] and config.SAP_MASTER_READONLY:
                    locked = [f for f in SAP_OWNED_FIELDS if f in changes]
                    for f in locked:
                        changes.pop(f)
                    if locked:
                        sap_kept.append(data["code"])
                if changes:
                    conn.execute(f"UPDATE materials SET {', '.join(f'{f} = ?' for f in changes)}, updated_at = ? "
                                 "WHERE id = ?", (*changes.values(), ts, cur["id"]))
                updated.append(data["code"])
            audit.record(conn, actor, "MATERIAL_IMPORT", "material", "",
                         {"new": new[:200], "updated": updated[:200], "count": len(rows), "sap_kept": sap_kept[:200]})
    except _Rejected as exc:
        return Result(False, f"반영하지 않았습니다 — {exc.message}")
    msg = f"{len(rows)}건 반영 완료 (신규 {len(new)} · 갱신 {len(updated)})"
    if sap_kept:
        msg += f" · SAP 동기화 자재 {len(sap_kept)}건은 SAP 항목(이름·단위·분류·단가·SAP번호)을 바꾸지 않았습니다"
    return Result(True, msg)


def normalize_upload(raw: pd.DataFrame) -> UploadResult:
    """업로드 엑셀/CSV를 자재 마스터 형식으로 정제한다."""
    reverse = {v: k for k, v in config.MATERIAL_COLS.items()}
    df = raw.rename(columns=lambda c: reverse.get(str(c).strip(), str(c).strip()))

    missing = [config.MATERIAL_COLS[c] for c in ("code", "name") if c not in df.columns]
    if missing:
        return UploadResult(pd.DataFrame(), errors=[f"필수 컬럼 누락: {', '.join(missing)}"])

    for col in config.MATERIAL_COLS:
        if col not in df.columns:
            df[col] = None
    df = df[list(config.MATERIAL_COLS)]

    raw_rows = len(df)
    df["code"] = clean_str_series(df["code"]).str.upper()
    df["name"] = clean_str_series(df["name"])
    df = df[(df["code"] != "") & (df["name"] != "")]
    dropped = raw_rows - len(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset="code", keep="last")
    duplicated = before_dedup - len(df)

    # 빈 칸(또는 숫자가 아닌 숫자 칸)은 기존 자재의 값을 유지한다 → 행마다 빈 항목을 적어 둔다
    blank = {c: clean_str_series(df[c]) == "" for c in UPLOAD_OPTIONAL}
    bad_numbers = 0
    for col in ("safety_stock", "unit_price"):
        num = pd.to_numeric(df[col], errors="coerce")
        bad = num.isna() & ~blank[col]
        bad_numbers += int(bad.sum())
        blank[col] = blank[col] | bad
        df[col] = num.fillna(0).clip(lower=0)
    for col in ("spec", "location", "supplier"):
        df[col] = clean_str_series(df[col])
    df["sap_matnr"] = code_series(df["sap_matnr"]).str.upper()
    df["barcode"] = code_series(df["barcode"]).str.upper().str.replace(r"\s+", "", regex=True)
    df["unit"] = clean_str_series(df["unit"], default=config.DEFAULT_UNIT)
    df["category"] = clean_str_series(df["category"], default=config.DEFAULT_CATEGORY)
    df["_blank"] = [",".join(c for c in UPLOAD_OPTIONAL if blank[c].iloc[i]) for i in range(len(df))]

    return UploadResult(df.reset_index(drop=True), dropped=dropped, duplicated=duplicated, bad_numbers=bad_numbers)


def stock_display(df: pd.DataFrame) -> pd.DataFrame:
    """재고 DataFrame을 화면/엑셀용 한글 컬럼으로 변환."""
    out = df[["code", "name", "spec", "unit", "category", "stock", "safety_stock",
              "shortage_qty", "unit_price", "stock_value", "location", "supplier"]].copy()
    out.columns = ["자재코드", "자재명", "규격", "단위", "분류", "현재고", "안전재고",
                   "부족수량", "단가", "재고금액", "보관위치", "공급처"]
    return out


def stock_by_wh_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["code", "name", "plant_code", "wh_code", "wh_name", "unit", "stock", "unit_price", "stock_value"]].copy()
    out.columns = ["자재코드", "자재명", "플랜트", "창고", "창고명", "단위", "현재고", "단가", "재고금액"]
    return out


def material_options(active_only: bool = True) -> dict[int, str]:
    df = repo.list_materials(active_only=active_only)
    return {
        int(r.id): f"[{r.code}] {r.name}" + (f" ({r.spec})" if r.spec else "")
        for r in df.itertuples()
    }

"""월 마감.

- 마감월(closed_through)까지의 날짜로는 입출고·취소를 등록할 수 없다.
- 마감하면 그 달 말일 기준 자재별 재고를 스냅샷으로 저장한다 → 현재고 계산은 스냅샷 + 그 이후 거래만 더한다.
- 마감은 한 달씩 앞으로만 간다(관리자). 해제는 한 달씩 되돌리며 사유가 필요하다(시스템관리자).
- SAP 연동 중이면 그 달 거래가 모두 SAP에 전기된 뒤에만 마감할 수 있다.
"""

import json
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

from core import audit, db, repository as repo, sap, valuation
from core.utils import fmt_qty, month_end, now_str, prev_month


@dataclass
class PeriodResult:
    ok: bool
    message: str


def closed_through() -> str:
    with db.get_conn() as conn:
        return repo.closed_through(conn)


def date_problem(conn, tx_date: str) -> str:
    """거래 일자로 쓸 수 없으면 사유.
    모든 거래 등록 경로가 여기를 지나므로 기간 공유 잠금을 잡는다 → 마감(배타 잠금)과 동시에 진행되지 않는다."""
    db.lock_shared(conn, "period")
    ym = repo.closed_through(conn)
    if ym and tx_date <= month_end(ym):
        return f"{ym}월까지 마감되어 {tx_date} 일자로는 등록할 수 없습니다. 마감 이후 일자로 입력하세요."
    if tx_date > date.today().isoformat():
        return "미래 일자로는 등록할 수 없습니다."
    return ""


def next_closable(conn=None) -> str:
    """다음에 마감할 달. 마감 이력이 없으면 가장 오래된 거래의 달부터."""
    if conn is None:
        with db.get_conn() as c:
            return next_closable(c)
    ym = repo.closed_through(conn)
    if ym:
        y, m = int(ym[:4]), int(ym[5:7])
        return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
    first = conn.execute("SELECT MIN(tx_date) FROM transactions").fetchone()[0]
    return str(first)[:7] if first else prev_month(date.today().strftime("%Y-%m"))


def closed_valuation(ym: str) -> dict:
    """마감 때 저장한 재고 평가 합계 {방법: 금액} — 나중에 기준단가가 바뀌어도 그대로."""
    df = db.query_df("SELECT method, SUM(value) AS v FROM valuation_snapshots WHERE ym = ? GROUP BY method", (ym,))
    return {r.method: float(r.v or 0) for r in df.itertuples(index=False)}


INVOICE_TYPES = "('E_TAX_INVOICE', 'TAX_INVOICE', 'INVOICE')"


def ap_snapshot(conn, ym: str, backfilled: bool = False) -> int:
    """월말 미지급·GR/IR 스냅샷: 그 달 말일 기준 발주별 입고 금액 · 계산서 공급가액 · 차이(입고 − 계산서)를 저장한다.
    입고는 거래 일자, 계산서는 작성일자가 월말 이내인 것만 센다 → 나중에 계산서를 올려도 마감 당시 잔액이 그대로 남는다.
    차이 > 0 = 입고했지만 계산서가 안 온 금액(미착 계산서 = 미지급금 추정), < 0 = 계산서가 먼저 온 금액.
    지급 상태는 이 스냅샷을 저장한 시점의 것이다. 저장한 행 수."""
    end = month_end(ym)
    rows = conn.execute(f"""
        SELECT o.po_no, o.supplier, o.warehouse_id, o.payment_status,
               COALESCE((SELECT SUM(i.qty * i.price) FROM po_items i WHERE i.po_id = o.id), 0) AS ordered,
               COALESCE((SELECT SUM(t.qty * t.unit_price) FROM transactions t WHERE t.tx_type = 'IN' AND t.po_no = o.po_no
                         AND t.transfer_no = '' AND t.tx_date <= ?), 0) AS received,
               COALESCE((SELECT SUM(d.supply_amount) FROM documents d JOIN transactions x ON x.id = d.tx_id
                         WHERE x.po_no = o.po_no AND d.issue_date <= ? AND d.doc_type IN {INVOICE_TYPES}), 0) AS invoiced
        FROM purchase_orders o""", (end, end)).fetchall()
    ts = now_str()
    data = []
    for r in rows:
        received, invoiced = round(float(r["received"] or 0), 2), round(float(r["invoiced"] or 0), 2)
        if abs(received) < 0.5 and abs(invoiced) < 0.5:
            continue
        data.append((ym, r["po_no"], r["supplier"] or "", r["warehouse_id"], round(float(r["ordered"] or 0), 2), received,
                     invoiced, round(received - invoiced, 2), r["payment_status"] or "", 1 if backfilled else 0, ts))
    conn.execute("DELETE FROM ap_snapshots WHERE ym = ?", (ym,))
    conn.executemany("INSERT INTO ap_snapshots (ym, po_no, supplier, warehouse_id, ordered, received, invoiced, gr_ir, "
                     "payment_status, backfilled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
    return len(data)


def ap_months() -> list[str]:
    """미지급 스냅샷이 있는 달 (최근 순)."""
    try:
        df = db.query_df("SELECT DISTINCT ym FROM ap_snapshots ORDER BY ym DESC")
    except db.DBError:
        return []
    return df["ym"].tolist()


def ap_missing() -> list[str]:
    """마감했지만 미지급 스냅샷이 없는 달 (이 기능을 넣기 전에 마감한 달) — 날짜 기준으로 만들어 채울 수 있다."""
    try:
        have = set(ap_months())
        closed = db.query_df("SELECT DISTINCT ym FROM inventory_snapshots ORDER BY ym")["ym"].tolist()
    except db.DBError:
        return []
    return [ym for ym in closed if ym not in have]


def ap_backfill(actor: dict | None) -> PeriodResult:
    missing = ap_missing()
    if not missing:
        return PeriodResult(False, "채울 달이 없습니다.")
    total = 0
    with db.transaction() as conn:
        for ym in missing:
            total += ap_snapshot(conn, ym, backfilled=True)
        audit.record(conn, actor, "AP_SNAPSHOT", "period", ",".join(missing), {"months": missing, "rows": total, "backfilled": True})
    return PeriodResult(True, f"{', '.join(missing)} 미지급 스냅샷 {total}건을 만들었습니다 (입고·계산서 날짜 기준, 지급 상태는 지금 값).")


def ap_df(ym: str, wh_ids=None) -> pd.DataFrame:
    frag, wp = db.in_clause(wh_ids)
    return db.query_df(f"""
        SELECT s.po_no, s.supplier, w.code AS wh_code, s.ordered, s.received, s.invoiced, s.gr_ir, s.payment_status, s.backfilled
        FROM ap_snapshots s LEFT JOIN warehouses w ON w.id = s.warehouse_id
        WHERE s.ym = ?{' AND s.warehouse_id' + frag if frag else ''} ORDER BY ABS(s.gr_ir) DESC, s.po_no""", (ym, *wp))


def close_checks(ym: str, conn=None) -> dict:
    """마감 전 점검 (막지는 않고 마감 기록에 남긴다):
    재공품 — 투입했지만 완료하지 않은 작업지시 (월말 재공으로 남음) / GR/IR — 월말까지 입고했는데 세금계산서가 없는 발주 입고,
    거래에 연결 안 된 계산서 / 음수 재고 / 지급 보류 발주."""
    if conn is None:
        with db.get_conn() as c:
            return close_checks(ym, c)
    end = month_end(ym)
    # 재공품: 월말까지 투입 − 반납이 남았는데 월말까지 완제품 입고·완료가 없는 작업지시 (거래 일자 기준)
    wip_rows = conn.execute("""
        SELECT t.production_id,
               SUM(CASE WHEN t.tx_type = 'OUT' THEN t.qty * t.unit_price
                        WHEN t.tx_type = 'IN' AND t.material_id <> p.product_id THEN -t.qty * t.unit_price ELSE 0 END) AS wip,
               SUM(CASE WHEN t.tx_type = 'IN' AND t.material_id = p.product_id THEN 1 ELSE 0 END) AS fg,
               MAX(CASE WHEN p.completed_at <> '' AND SUBSTR(p.completed_at, 1, 10) <= ? THEN 1 ELSE 0 END) AS done
        FROM transactions t JOIN productions p ON p.id = t.production_id
        WHERE t.tx_date <= ? GROUP BY t.production_id""", (end, end)).fetchall()
    wip_open = [float(r["wip"] or 0) for r in wip_rows if not r["fg"] and not r["done"] and float(r["wip"] or 0) > 0.5]
    wip = (len(wip_open), sum(wip_open))
    # GR/IR: 발주별 (월말까지 입고 금액 − 월말까지 작성된 계산서 공급가액) 이 남은 것 (미착 계산서)
    gr_rows = conn.execute("""
        SELECT t.po_no, SUM(t.qty * t.unit_price) AS received,
               COALESCE((SELECT SUM(d.supply_amount) FROM documents d JOIN transactions x ON x.id = d.tx_id
                         WHERE x.po_no = t.po_no AND d.issue_date <= ?
                           AND d.doc_type IN ('E_TAX_INVOICE', 'TAX_INVOICE', 'INVOICE')), 0) AS invoiced
        FROM transactions t WHERE t.tx_type = 'IN' AND t.po_no <> '' AND t.transfer_no = '' AND t.tx_date <= ?
        GROUP BY t.po_no""", (end, end)).fetchall()
    gr_open = [float(r["received"] or 0) - float(r["invoiced"] or 0) for r in gr_rows]
    gr_open = [v for v in gr_open if v > 0.5]
    gr = (len(gr_open), sum(gr_open))
    ir = conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(supply_amount), 0) FROM documents
        WHERE tx_id IS NULL AND doc_type IN ('E_TAX_INVOICE', 'TAX_INVOICE', 'INVOICE') AND issue_date <= ?""", (end,)).fetchone()
    stock = repo.stock_as_of(conn, end)
    codes = {int(r[0]): r[1] for r in conn.execute("SELECT id, code FROM materials")}
    negatives = [f"{codes.get(mid, mid)}{'/' + lot if lot else ''} {fmt_qty(q)}" for (mid, wh, lot), q in stock.items() if q < -1e-9]
    blocked = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE payment_status = 'BLOCKED'").fetchone()[0]
    return {"wip_count": int(wip[0] or 0), "wip_value": float(wip[1] or 0),
            "gr_no_invoice_count": int(gr[0] or 0), "gr_no_invoice_value": float(gr[1] or 0),
            "invoice_no_receipt_count": int(ir[0] or 0), "invoice_no_receipt_value": float(ir[1] or 0),
            "negative": negatives[:50], "negative_count": len(negatives), "payment_blocked": int(blocked or 0)}


def close_month(ym: str, actor: dict | None) -> PeriodResult:
    this_month = date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", ym or ""):
        return PeriodResult(False, "마감할 달을 선택하세요 (예: 2026-08).")
    if ym >= this_month:
        return PeriodResult(False, "진행 중이거나 미래인 달은 마감할 수 없습니다.")
    end = month_end(ym)
    with db.transaction() as conn:
        db.lock(conn, "period")
        expected = next_closable(conn)               # 잠금 안에서 확인 (두 서버가 같은 달을 동시에 마감하지 않게)
        if ym != expected:
            return PeriodResult(False, f"마감은 한 달씩 순서대로 합니다. 다음 마감 대상은 {expected}입니다.")
        adj = conn.execute("SELECT COUNT(*) FROM approval_requests WHERE kind = 'ADJ' AND status = 'PENDING' AND tx_date <= ?",
                           (end,)).fetchone()[0]
        if adj:
            return PeriodResult(False, f"{ym}월까지 실사일인 실사 조정 {adj}건이 결재 대기 중입니다. 마감하면 승인할 수 없으니 "
                                       "결재함에서 먼저 승인·반려하세요.")
        if sap.enabled():
            unsent = sap.unsent_until(conn, end)
            if unsent:
                return PeriodResult(False, f"{ym}월까지의 거래 중 SAP 전기가 끝나지 않은 건이 {unsent}건 있습니다. "
                                           "SAP 연동 화면에서 먼저 처리하세요.")
        # (자재, 창고, 로트) → 월말 재고. 소수 수량을 더하며 생기는 부동소수 오차(-1e-14 등)는 반올림해 없앤다
        stock = {k: (round(q, 6) or 0.0) for k, q in repo.stock_as_of(conn, end).items()}
        conn.executemany(
            "INSERT INTO inventory_snapshots (ym, material_id, warehouse_id, lot_no, qty) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (ym, material_id, warehouse_id, lot_no) DO UPDATE SET qty = excluded.qty",
            [(ym, mid, wh, lot, qty) for (mid, wh, lot), qty in stock.items()])
        valuation.snapshot(conn, ym)               # 재고 평가(이동평균·선입선출)도 함께 저장
        ap_rows = ap_snapshot(conn, ym)            # 월말 미지급·GR/IR 잔액도 함께 고정
        checks = close_checks(ym, conn)                # 재공품 · GR/IR · 음수 재고 · 지급 보류 — 마감 기록에 함께 남긴다
        conn.execute("INSERT INTO period_closes (closed_through, action, user_name, at, checks) VALUES (?, 'CLOSE', ?, ?, ?)",
                     (ym, (actor or audit.SYSTEM)["name"], now_str(), json.dumps(checks, ensure_ascii=False)))
        negatives = [f"{mid}@{wh}{'/' + lot if lot else ''}" for (mid, wh, lot), q in stock.items() if q < 0]
        audit.record(conn, actor, "PERIOD_CLOSE", "period", ym,
                     {"rows": len(stock), "negative_stock": negatives or None, "ap_rows": ap_rows})
    notes = []
    if checks["wip_count"]:
        notes.append(f"재공품 {checks['wip_count']}건 ₩{checks['wip_value']:,.0f}")
    if checks["gr_no_invoice_count"]:
        notes.append(f"미착 계산서(GR/IR) 발주 {checks['gr_no_invoice_count']}건 ₩{checks['gr_no_invoice_value']:,.0f}")
    if checks["negative_count"]:
        notes.append(f"음수 재고 {checks['negative_count']}건")
    return PeriodResult(True, f"{ym}월을 마감했습니다. 자재·창고 {len(stock)}건의 월말 재고를 저장했습니다."
                              + (f" 점검: {' · '.join(notes)} (마감 기록에 남김)." if notes else ""))


def reopen(reason: str, actor: dict | None) -> PeriodResult:
    reason = reason.strip()
    if not reason:
        return PeriodResult(False, "마감 해제 사유를 입력하세요.")
    with db.transaction() as conn:
        db.lock(conn, "period")
        ym = repo.closed_through(conn)
        if not ym:
            return PeriodResult(False, "마감된 달이 없습니다.")
        prev = conn.execute(
            "SELECT closed_through FROM period_closes WHERE action = 'CLOSE' AND closed_through < ? "
            "ORDER BY closed_through DESC LIMIT 1", (ym,)).fetchone()
        new_through = prev["closed_through"] if prev else ""
        conn.execute("DELETE FROM inventory_snapshots WHERE ym = ?", (ym,))
        conn.execute("DELETE FROM valuation_snapshots WHERE ym = ?", (ym,))
        conn.execute("DELETE FROM ap_snapshots WHERE ym = ?", (ym,))
        conn.execute("INSERT INTO period_closes (closed_through, action, reason, user_name, at) "
                     "VALUES (?, 'REOPEN', ?, ?, ?)", (new_through, reason, (actor or audit.SYSTEM)["name"], now_str()))
        audit.record(conn, actor, "PERIOD_REOPEN", "period", ym, {"reason": reason, "now_closed_through": new_through})
    return PeriodResult(True, f"{ym}월 마감을 해제했습니다. 현재 마감: {new_through or '없음'}")


def history_df() -> pd.DataFrame:
    return db.query_df("SELECT id, at, action, closed_through, user_name, reason FROM period_closes ORDER BY id DESC")


def snapshot_df(ym: str, wh_ids=None) -> pd.DataFrame:
    frag, wp = db.in_clause(wh_ids)
    return db.query_df(
        f"""
        SELECT m.code, m.name, w.code AS wh_code, s.lot_no, m.unit, s.qty, m.unit_price, s.qty * m.unit_price AS value
        FROM inventory_snapshots s JOIN materials m ON m.id = s.material_id
        JOIN warehouses w ON w.id = s.warehouse_id
        WHERE s.ym = ?{' AND s.warehouse_id' + frag if frag else ''} ORDER BY m.code, w.code
        """, (ym, *wp))

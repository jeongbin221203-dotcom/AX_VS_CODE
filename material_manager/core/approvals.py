"""결재 (직무 분리). 금액이 큰 실사 조정은 요청자가 아닌 관리자가 승인해야 반영된다.

- 요청은 거래를 만들지 않는다 → 재고에 영향이 없다.
- 승인자 ≠ 요청자. 승인자는 그 창고 권한이 있어야 한다.
- 승인 시 거래에는 요청자가 등록자, 승인자가 approved_by로 남는다.
"""

import json

import pandas as pd

from core import audit, db, notify
from core.utils import now_str

STATUS = {"PENDING": "결재 대기", "APPROVED": "승인", "REJECTED": "반려"}
KINDS = {"ADJ": "실사 조정", "CANCEL": "거래 취소"}


def create(conn, kind: str, material_id: int, warehouse_id: int, tx_date: str, qty: float, amount: float,
           requester: dict, extra: dict) -> int:
    req_id = conn.execute(
        """
        INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status,
                                       requested_by_id, requested_by, requested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
        """,
        (kind, material_id, warehouse_id, tx_date, qty, amount, json.dumps(extra, ensure_ascii=False),
         requester.get("id"), requester["name"], now_str())).lastrowid
    audit.record(conn, requester, "APPROVAL_REQUEST", "approval", req_id,
                 {"kind": kind, "material_id": material_id, "warehouse_id": warehouse_id, "qty": qty,
                  "amount": amount})
    mat = conn.execute("SELECT code, name, unit FROM materials WHERE id = ?", (material_id,)).fetchone()
    wh = conn.execute("SELECT code FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
    what = KINDS.get(kind, kind)
    notify.queue(conn, notify.recipients(conn, "MANAGER", warehouse_id, [requester.get("id")]),
                 f"{what} 결재 요청 #{req_id} ({mat['code']})",
                 [f"{requester['name']}님이 {what} 결재를 올렸습니다." + (f" 사유: {extra.get('reason')}" if extra.get("reason") else ""),
                  f"자재: [{mat['code']}] {mat['name']} · 창고 {wh['code']} · 실사일 {tx_date}",
                  (f"수량: {qty:,.2f} {mat['unit']} · 금액 ₩{amount:,.0f}" if kind == "CANCEL"
                   else f"조정 수량: {qty:+,.2f} {mat['unit']} · 금액 ₩{amount:,.0f}")],
                 "/approvals/", f"approval:{req_id}")
    return req_id


def request_cancel(tx_id: int, reason: str, requester: dict, wh_ids=None):
    """거래 취소 요청: 담당자는 직접 취소할 수 없으므로(관리자·직무 분리) 사유와 함께 요청 → 관리자가 승인하면 취소 거래.
    같은 거래에 처리 중인 요청이 있거나, 이미 취소됐거나, 취소 거래이면 받지 않는다."""
    from core import services
    from datetime import date
    reason = (reason or "").strip()
    if not reason:
        return services.Result(False, "취소 사유를 입력하세요.")
    with db.transaction() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if tx is None or (wh_ids is not None and tx["warehouse_id"] not in wh_ids):
            return services.Result(False, "거래가 없거나 권한 밖입니다.")
        if tx["reversal_of"] is not None:
            return services.Result(False, "취소 거래는 다시 취소할 수 없습니다.")
        if conn.execute("SELECT 1 FROM transactions WHERE reversal_of = ?", (tx_id,)).fetchone():
            return services.Result(False, "이미 취소된 거래입니다.")
        dup = conn.execute("SELECT id FROM approval_requests WHERE kind = 'CANCEL' AND status = 'PENDING' AND payload LIKE ?",
                           (f'%"tx_id": {tx_id},%',)).fetchone()
        if dup:
            return services.Result(False, f"이 거래는 이미 취소 요청 #{dup[0]}가 처리 중입니다.")
        amount = abs(float(tx["qty"]) * float(tx["unit_price"] or 0))
        req_id = create(conn, "CANCEL", int(tx["material_id"]), int(tx["warehouse_id"]), date.today().isoformat(),
                        float(tx["qty"]), amount, requester, {"tx_id": int(tx_id), "reason": reason, "tx_type": tx["tx_type"],
                                                              "tx_date": tx["tx_date"]})
    return services.Result(True, f"거래 #{tx_id} 취소 요청 #{req_id}를 올렸습니다. 관리자가 승인하면 취소 거래가 생깁니다.", tx_id=req_id)


def decide(req_id: int, approve: bool, comment: str, actor: dict, wh_ids=None):
    """승인 또는 반려. 결과(services.Result)를 돌려준다."""
    from core import services              # 순환 import 방지
    comment = comment.strip()
    with db.transaction() as conn:
        db.lock(conn, f"approval:{req_id}")
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (req_id,)).fetchone()
        if row is None or row["status"] != "PENDING":
            return services.Result(False, "이미 처리됐거나 없는 결재입니다.")
        req = dict(row)
        if req["requested_by_id"] is not None and req["requested_by_id"] == actor.get("id"):
            return services.Result(False, "본인이 올린 결재는 다른 관리자가 처리해야 합니다(직무 분리).")
        if wh_ids is not None and req["warehouse_id"] not in wh_ids:
            return services.Result(False, "이 창고의 결재 권한이 없습니다.")
        if not approve and not comment:
            return services.Result(False, "반려 사유를 입력하세요.")
        result, tx_id = services.Result(True, f"결재 #{req_id}를 반려했습니다."), None
        if approve and req["kind"] == "CANCEL":                 # 거래 취소 요청 → 취소 거래 (직무 분리: 등록자 본인은 승인 불가)
            extra = json.loads(req["payload"] or "{}")
            from datetime import date
            out = services._reverse(conn, actor, int(extra["tx_id"]), f"취소 요청 #{req_id} ({req['requested_by']}): {extra.get('reason', '')}",
                                     date.today().isoformat(), wh_ids)
            if isinstance(out, services.Result):
                return out
            tx_id = out["rev_ids"][0]
            result = services.Result(True, f"취소 요청 #{req_id} 승인 → 거래 #{extra['tx_id']} 취소 (취소 거래 #{tx_id})", tx_id=tx_id)
        elif approve:
            result = services.post_approved_adjustment(conn, req, actor)
            if not result.ok:
                return result
            tx_id = result.tx_id
        conn.execute(
            "UPDATE approval_requests SET status = ?, decided_by_id = ?, decided_by = ?, decided_at = ?, "
            "comment = ?, result_tx_id = ? WHERE id = ?",
            ("APPROVED" if approve else "REJECTED", actor.get("id"), actor["name"], now_str(), comment, tx_id, req_id))
        audit.record(conn, actor, "APPROVAL_DECIDE", "approval", req_id,
                     {"approve": approve, "comment": comment, "tx_id": tx_id})
        verdict = "승인" if approve else "반려"
        notify.queue(conn, notify.user(conn, req["requested_by_id"]), f"{KINDS.get(req['kind'], '결재')} #{req_id} {verdict}",
                     [f"{actor['name']}님이 {verdict}했습니다." + (f" 사유: {comment}" if comment else ""),
                      (f"거래 #{json.loads(req['payload'] or '{}').get('tx_id', '')} 취소 요청 · 금액 ₩{float(req['amount']):,.0f}"
                       if req["kind"] == "CANCEL" else
                       f"조정 수량 {float(req['qty']):+,.2f} · 금액 ₩{float(req['amount']):,.0f}")],
                     "/approvals/", f"approval:{req_id}")
    return result


def requests_df(status: str | None = None, wh_ids=None, limit: int = 500) -> pd.DataFrame:
    sql = """
        SELECT a.id, a.kind, a.status, a.tx_date, m.code, m.name, m.unit, w.code AS wh_code, a.qty, a.amount,
               a.requested_by, a.requested_at, a.decided_by, a.decided_at, a.comment, a.result_tx_id,
               a.requested_by_id, a.warehouse_id, a.payload
        FROM approval_requests a
        JOIN materials m ON m.id = a.material_id JOIN warehouses w ON w.id = a.warehouse_id
        WHERE 1 = 1
    """
    params: list = []
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    frag, wp = db.in_clause(wh_ids)
    if frag:
        sql += f" AND a.warehouse_id{frag}"
        params += wp
    sql += " ORDER BY a.id DESC LIMIT ?"
    return db.query_df(sql, [*params, limit])


def pending_count(wh_ids=None) -> int:
    frag, wp = db.in_clause(wh_ids)
    return int(db.scalar(f"SELECT COUNT(*) FROM approval_requests WHERE status = 'PENDING'"
                         f"{' AND warehouse_id' + frag if frag else ''}", wp) or 0)

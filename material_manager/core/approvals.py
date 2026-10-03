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
    notify.queue(conn, notify.recipients(conn, "MANAGER", warehouse_id, [requester.get("id")]),
                 f"실사 조정 결재 요청 #{req_id} ({mat['code']})",
                 [f"{requester['name']}님이 실사 조정 결재를 올렸습니다.",
                  f"자재: [{mat['code']}] {mat['name']} · 창고 {wh['code']} · 실사일 {tx_date}",
                  f"조정 수량: {qty:+,.2f} {mat['unit']} · 금액 ₩{amount:,.0f}"],
                 "/approvals/", f"approval:{req_id}")
    return req_id


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
        if approve:
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
        notify.queue(conn, notify.user(conn, req["requested_by_id"]), f"실사 조정 결재 #{req_id} {verdict}",
                     [f"{actor['name']}님이 {verdict}했습니다." + (f" 사유: {comment}" if comment else ""),
                      f"조정 수량 {float(req['qty']):+,.2f} · 금액 ₩{float(req['amount']):,.0f}"],
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

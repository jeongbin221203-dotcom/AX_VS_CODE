"""대손 처리 · 연체 거래처 자동 거래정지 · 거래정지 해제 결재

대손
  받을 수 없게 된 채권을 결재를 받아 정리한다. 금액이 회사 설정(writeoff_exec_threshold) 이하면 팀장, 넘으면 임원 결재.
  승인되면 그 매출에 '대손' 입금 행이 생겨 채권에서 빠지고(입금액 = 입금 내역 합계 유지), ERP 에 대손 전표를 보낸다.
  요청자는 결재할 수 없고, 월 마감한 날짜로는 처리하지 않는다(승인일 기준).

자동 거래정지 (회사 설정 auto_block_overdue_days · auto_block_over_credit)
  매일 배치가 결제기일이 N일 넘게 지난 미수가 있거나 미수 잔액이 여신한도를 넘은 거래처를 거래정지한다(사유 기록, 담당자·팀장 알림).
  해제는 결재(팀장 이상, 요청자 제외) — 승인하면 유예기간(auto_block_exempt_days) 동안 다시 자동 정지하지 않는다.
  ERP 가 정지한 거래처는 이 시스템에서 해제할 수 없다(ERP 에서 해제).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from . import sales_db as db

KINDS = ("대손", "거래정지해제")


def _setting(key: str):
    from . import company
    return company.get(key)


# ---------------------------------------------------------------------------
# 결재 요청 공통
# ---------------------------------------------------------------------------
def _role_for(kind: str, amount: int) -> str:
    if kind == "대손":
        return "EXEC" if amount > int(_setting("writeoff_exec_threshold")) else "MANAGER"
    return "MANAGER"


def request_writeoff(sale_id: int, reason: str, actor: dict) -> int:
    sale = db.get_sale(int(sale_id))
    db.check_record_scope(sale, "매출")
    if sale["status"] == db.SALE_CANCELLED or (sale.get("sale_kind") or "매출") != "매출":
        raise ValueError("대손 처리할 수 없는 매출입니다.")
    remain = int(sale.get("total_amount") or sale["amount"]) - int(sale.get("paid_amount") or 0)
    if remain <= 0:
        raise ValueError("남은 미수금이 없습니다.")
    return _request("대손", int(sale["customer_id"]), reason, actor, sale_id=int(sale_id), amount=remain)


def request_unblock(customer_id: int, reason: str, actor: dict) -> int:
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    if not cust.get("trade_blocked"):
        raise ValueError("거래정지 상태가 아닙니다.")
    if cust.get("block_source") == "ERP" or cust.get("erp_synced_at") and cust.get("block_source") != "자동":
        raise ValueError("ERP 가 정지한 거래처는 ERP 에서 해제해야 합니다.")
    return _request("거래정지해제", int(customer_id), reason, actor)


def _request(kind: str, customer_id: int, reason: str, actor: dict, sale_id: Optional[int] = None,
             amount: int = 0) -> int:
    if not str(reason or "").strip():
        raise ValueError("사유를 입력하세요.")
    with db.get_conn() as conn:
        dup = conn.execute("SELECT id FROM fin_requests WHERE kind=? AND status='대기' AND customer_id=? "
                           "AND COALESCE(sale_id, 0)=?", (kind, customer_id, int(sale_id or 0))).fetchone()
        if dup:
            raise ValueError("이미 결재 대기 중인 요청이 있습니다.")
        cur = conn.execute("INSERT INTO fin_requests (kind, sale_id, customer_id, amount, reason, required_role, status, "
                           "requested_by, requested_by_id, requested_at) VALUES (?,?,?,?,?,?, '대기', ?,?,?)",
                           (kind, sale_id, customer_id, int(amount), reason.strip(), _role_for(kind, int(amount)),
                            actor.get("name"), actor.get("id"), db._now()))
        rid = int(cur.lastrowid)
    db.audit(f"{kind}요청", "거래처", customer_id, {"요청번호": rid, "매출": sale_id, "금액": amount, "사유": reason.strip()})
    _notify_approvers(rid)
    return rid


def _approvers(req: dict) -> list[dict]:
    from . import enterprise as ent
    users = db._df("SELECT * FROM users WHERE active = 1").to_dict("records")
    cust = db.get_customer(int(req["customer_id"])) or {}
    out = []
    for u in users:
        if int(u["id"]) == int(req.get("requested_by_id") or 0) or not ent.has_role(u, req["required_role"]):
            continue
        if u["role"] == "MANAGER" and cust.get("owner_id") not in (ent.visible_owners(u) or []):
            continue
        out.append(u)
    return out


def _notify_approvers(rid: int) -> None:
    from . import notify
    req = db._one("SELECT * FROM fin_requests WHERE id=?", [rid])
    notify.notify([int(u["id"]) for u in _approvers(req)], "결재요청", f"{req['kind']} 결재 요청",
                  f"{req['requested_by']} · {int(req['amount']):,}원 · {req['reason']}", "/approvals?tab=finance")


def pending_for(user: dict) -> pd.DataFrame:
    rows = db._df("SELECT r.*, c.name AS customer_name FROM fin_requests r JOIN customers c ON c.id = r.customer_id "
                  "WHERE r.status='대기' ORDER BY r.id").to_dict("records")
    mine = [r for r in rows if any(int(u["id"]) == int(user["id"]) for u in _approvers(r))]
    return pd.DataFrame([{"id": r["id"], "구분": r["kind"], "거래처": r["customer_name"], "매출번호": r["sale_id"],
                          "금액": int(r["amount"]), "사유": r["reason"], "요청자": r["requested_by"],
                          "요청일시": r["requested_at"], "필요권한": db.ROLE_LABEL.get(r["required_role"])} for r in mine])


def history(limit: int = 200) -> pd.DataFrame:
    sc, sp = db._scope_clause("c")
    return db._df("SELECT r.id, r.kind AS 구분, c.name AS 거래처, r.sale_id AS 매출번호, r.amount AS 금액, r.reason AS 사유, "
                  "r.status AS 상태, r.requested_by AS 요청자, r.decided_by AS 결재자, r.decided_at AS 결재일시, "
                  f"r.comment AS 의견 FROM fin_requests r JOIN customers c ON c.id = r.customer_id WHERE 1=1{sc} "
                  "ORDER BY r.id DESC LIMIT ?", [*sp, int(limit)])


def decide(request_id: int, approve: bool, comment: str, actor: dict) -> dict:
    from . import enterprise as ent
    from . import notify
    req = db._one("SELECT * FROM fin_requests WHERE id=?", [int(request_id)])
    if not req or req["status"] != "대기":
        raise ValueError("대기 중인 요청이 아닙니다.")
    if int(req.get("requested_by_id") or 0) == int(actor["id"]):
        raise PermissionError("본인이 요청한 건은 결재할 수 없습니다.")
    if not any(int(u["id"]) == int(actor["id"]) for u in _approvers(req)):
        raise PermissionError(f"이 요청은 {db.ROLE_LABEL.get(req['required_role'])} 이상이 결재합니다.")
    if not approve and not str(comment or "").strip():
        raise ValueError("반려 사유를 입력하세요.")
    result: dict = {}
    if approve and req["kind"] == "대손":
        sale = db.get_sale(int(req["sale_id"]))
        remain = int(sale.get("total_amount") or sale["amount"]) - int(sale.get("paid_amount") or 0)
        if remain <= 0:
            raise ValueError("그 사이 입금되어 남은 미수금이 없습니다. 반려하세요.")
        ent.record_payment(int(req["sale_id"]), remain, source="대손", method="기타", memo=f"대손 처리 (결재 #{req['id']})")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO erp_outbox (doc_type, ref_id, status, created_at) VALUES ('대손', ?, '대기', ?)",
                         (int(req["id"]), db._now()))
        result["대손금액"] = remain
    if approve and req["kind"] == "거래정지해제":
        exempt = (date.today() + timedelta(days=int(_setting("auto_block_exempt_days")))).isoformat()
        with db.get_conn() as conn:
            conn.execute("UPDATE customers SET trade_blocked=0, block_source=NULL, block_reason=NULL, blocked_at=NULL, "
                         "block_exempt_until=? WHERE id=?", (exempt, int(req["customer_id"])))
        result["재정지유예"] = exempt
    with db.get_conn() as conn:
        conn.execute("UPDATE fin_requests SET status=?, decided_by=?, decided_by_id=?, decided_at=?, comment=? WHERE id=?",
                     ("승인" if approve else "반려", actor.get("name"), int(actor["id"]), db._now(), comment or None,
                      int(req["id"])))
    db.audit(f"{req['kind']}{'승인' if approve else '반려'}", "거래처", int(req["customer_id"]),
             {"요청번호": req["id"], "의견": comment or None, **result})
    if req.get("requested_by_id"):
        notify.notify([int(req["requested_by_id"])], "결재결과", f"{req['kind']} {'승인' if approve else '반려'}",
                      comment or "", "/approvals?tab=finance")
    return result


# ---------------------------------------------------------------------------
# 자동 거래정지
# ---------------------------------------------------------------------------
def block_candidates(today: Optional[str] = None) -> pd.DataFrame:
    days = int(_setting("auto_block_overdue_days") or 0)
    over_credit = bool(_setting("auto_block_over_credit"))
    today = today or date.today().isoformat()
    rows = []
    if days > 0:
        limit = (datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=days)).isoformat()
        for r in db._df("SELECT customer_id, COUNT(*) AS n, MIN(due_date) AS oldest FROM sales WHERE status NOT IN "
                        "('입금완료', ?) AND COALESCE(total_amount, amount) > COALESCE(paid_amount, 0) AND due_date < ? "
                        "GROUP BY customer_id", [db.SALE_CANCELLED, limit]).itertuples():
            rows.append({"customer_id": int(r.customer_id), "reason": f"결제기일 {days}일 초과 연체 {int(r.n)}건 (가장 오래된 기일 {r.oldest})"})
    if over_credit:
        for r in db._df("SELECT c.id, c.credit_limit, SUM(COALESCE(s.total_amount, s.amount) - COALESCE(s.paid_amount, 0)) AS ar "
                        "FROM customers c JOIN sales s ON s.customer_id = c.id AND s.status NOT IN ('입금완료', ?) "
                        "WHERE COALESCE(c.credit_limit, 0) > 0 GROUP BY c.id, c.credit_limit",
                        [db.SALE_CANCELLED]).itertuples():
            if int(r.ar or 0) > int(r.credit_limit):
                rows.append({"customer_id": int(r.id), "reason": f"미수 {int(r.ar):,}원이 여신한도 {int(r.credit_limit):,}원 초과"})
    out = pd.DataFrame(rows)
    return out.groupby("customer_id")["reason"].apply(" · ".join).reset_index() if not out.empty else out


def auto_block(today: Optional[str] = None) -> dict:
    from . import notify
    today = today or date.today().isoformat()
    cand = block_candidates(today)
    blocked = []
    for r in cand.itertuples():
        cust = db.get_customer(int(r.customer_id)) or {}
        if cust.get("trade_blocked") or cust.get("status") == "종료" or cust.get("merged_into"):
            continue
        if cust.get("block_exempt_until") and cust["block_exempt_until"] >= today:
            continue
        with db.get_conn() as conn:
            conn.execute("UPDATE customers SET trade_blocked=1, block_source='자동', block_reason=?, blocked_at=? WHERE id=?",
                         (r.reason, db._now(), int(r.customer_id)))
        db.audit("자동거래정지", "거래처", int(r.customer_id), {"거래처": cust.get("name"), "사유": r.reason})
        if cust.get("owner_id"):
            notify.notify([int(cust["owner_id"])], "거래정지", f"거래정지: {cust.get('name')}",
                          f"{r.reason}. 해제하려면 거래처 화면에서 해제 결재를 요청하세요.", f"/customers?tab=edit&id={r.customer_id}")
        blocked.append(int(r.customer_id))
    return {"blocked": len(blocked), "customers": blocked}

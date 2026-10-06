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

KINDS = ("대손", "거래정지해제", "매출취소", "입금반제")

# 대손 사유 (부가가치세법 시행령 제87조 → 법인세법 시행령 제19조의2 · 소득세법 시행령 제55조)
#   코드: (이름, 사유 발생일이 필요한지, 확인할 경과 개월, 경과를 세는 기준)
WRITEOFF_REASONS = {
    "BANKRUPT": ("채무자 파산", True, 0, "event"),
    "EXECUTION": ("강제집행 불능", True, 0, "event"),
    "DEATH": ("채무자 사망·실종", True, 0, "event"),
    "REHAB": ("회생계획 인가·면책 결정", True, 0, "event"),
    "PRESCRIPTION": ("소멸시효 완성", True, 0, "event"),
    "DISHONOR": ("부도 발생일부터 6개월 경과", True, 6, "event"),
    "SME_2Y": ("중소기업 외상매출금 회수기일부터 2년 경과", False, 24, "due"),
    "SMALL": ("30만원 이하 채권 회수기일부터 6개월 경과", False, 6, "due"),
}
SMALL_LIMIT = 300_000


def _add_months(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def bad_debt_vat(sale: dict, amount: int) -> int:
    """대손세액 = 대손금액(부가세 포함) × 10/110 — 과세 매출만 (영세·면세는 0)."""
    return int(int(amount) * 10 / 110) if (sale.get("tax_type") or "과세") == "과세" else 0


def check_writeoff(sale: dict, amount: int, code: str, event_date: Optional[str], today: Optional[date] = None) -> date:
    """대손 요건 확인. 사유가 확정된 날을 돌려준다 (요건이 안 되면 ValueError)."""
    today = today or date.today()
    if code not in WRITEOFF_REASONS:
        raise ValueError("대손 사유를 고르세요 (파산·강제집행·사망·회생·소멸시효·부도 6개월·중소기업 2년·30만원 이하 6개월).")
    name, need_event, months, basis = WRITEOFF_REASONS[code]
    supply = datetime.strptime(str(sale["sale_date"])[:10], "%Y-%m-%d").date()
    if need_event:
        if not db._d(event_date):
            raise ValueError(f"'{name}' 사유가 생긴 날(선고일·부도일·시효 완성일 등)을 입력하세요.")
        base = datetime.strptime(db._d(event_date), "%Y-%m-%d").date()
        if not supply <= base <= today:
            raise ValueError("사유 발생일은 매출일부터 오늘 사이여야 합니다.")
    else:
        base = datetime.strptime(str(sale.get("due_date") or sale["sale_date"])[:10], "%Y-%m-%d").date()
    if code == "SMALL" and int(amount) > SMALL_LIMIT:
        raise ValueError(f"30만원 이하 사유는 채권이 {SMALL_LIMIT:,}원 이하일 때만 씁니다 (지금 {int(amount):,}원).")
    confirmed = _add_months(base, months) if months else base
    if confirmed > today:
        raise ValueError(f"'{name}' 요건이 아직 안 됩니다 — {confirmed} 이후에 대손 처리할 수 있습니다.")
    if today > _add_months(supply, 120):
        raise ValueError("공급일로부터 10년이 지나 부가가치세 대손세액공제를 받을 수 없습니다 — 세무 담당과 확인하세요.")
    return confirmed


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


# ---------------------------------------------------------------------------
# 매출 취소 · 입금 반제 요청 (자재관리의 '거래 취소 요청' 과 같은 흐름)
#   직접 처리할 수 없는 사람(영업사원, 본인이 등록한 ERP 전송 매출·본인이 넣은 입금)은 요청을 올리고
#   팀장 이상(요청자·등록자 제외)이 승인하면 그때 취소·반제한다.
# ---------------------------------------------------------------------------
def can_cancel_directly(sale: dict, user: dict) -> bool:
    if sale.get("erp_status") not in ("전송완료", "취소대기"):
        return True
    return db.ROLES.get(user.get("role"), 0) >= db.ROLES["MANAGER"] and \
        int(sale.get("created_by_id") or 0) != int(user.get("id") or 0)


def can_reverse_directly(payment: dict, user: dict) -> bool:
    """입금 반제는 팀장 이상, 그 입금을 넣은 본인은 안 된다 (직무 분리)."""
    return db.ROLES.get(user.get("role"), 0) >= db.ROLES["MANAGER"] and \
        int(payment.get("created_by_id") or 0) != int(user.get("id") or 0)


def request_cancel(sale_id: int, reason: str, actor: dict) -> int:
    sale = db.get_sale(int(sale_id))
    db.check_record_scope(sale, "매출")
    if sale["status"] == db.SALE_CANCELLED:
        raise ValueError("이미 취소된 매출입니다.")
    if (sale.get("sale_kind") or "매출") != "매출":
        raise ValueError("반품·정정 행은 취소하지 않습니다. 반대 방향 정정으로 바로잡으세요.")
    if int(sale.get("paid_amount") or 0) > 0:
        raise ValueError("입금 내역이 있는 매출은 취소할 수 없습니다. 입금 반제를 먼저 처리(요청)하세요.")
    return _request("매출취소", int(sale["customer_id"]), reason, actor, sale_id=int(sale_id),
                    amount=int(sale.get("total_amount") or sale["amount"]))


def request_reversal(payment_id: int, reason: str, actor: dict) -> int:
    from . import enterprise as ent
    pay = db._one("SELECT * FROM payments WHERE id=?", [int(payment_id)])
    if not pay:
        raise ValueError("입금을 찾을 수 없습니다.")
    sale = db.get_sale(int(pay["sale_id"]))
    db.check_record_scope(sale, "매출")
    ent.check_reversible(pay)
    return _request("입금반제", int(sale["customer_id"]), reason, actor, sale_id=int(sale["id"]),
                    amount=int(pay["amount"]), payment_id=int(payment_id))


def request_writeoff(sale_id: int, reason: str, actor: dict, code: Optional[str] = None,
                     event_date: Optional[str] = None) -> int:
    sale = db.get_sale(int(sale_id))
    db.check_record_scope(sale, "매출")
    if sale["status"] == db.SALE_CANCELLED or (sale.get("sale_kind") or "매출") != "매출":
        raise ValueError("대손 처리할 수 없는 매출입니다.")
    remain = int(sale.get("total_amount") or sale["amount"]) - int(sale.get("paid_amount") or 0)
    if remain <= 0:
        raise ValueError("남은 미수금이 없습니다.")
    check_writeoff(sale, remain, str(code or ""), event_date)
    rid = _request("대손", int(sale["customer_id"]), reason, actor, sale_id=int(sale_id), amount=remain)
    with db.get_conn() as conn:
        conn.execute("UPDATE fin_requests SET wo_reason_code=?, wo_event_date=?, bad_debt_vat=? WHERE id=?",
                     (code, db._d(event_date), bad_debt_vat(sale, remain), rid))
    return rid


def request_unblock(customer_id: int, reason: str, actor: dict) -> int:
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    if not cust.get("trade_blocked"):
        raise ValueError("거래정지 상태가 아닙니다.")
    if cust.get("block_source") == "ERP" or cust.get("erp_synced_at") and cust.get("block_source") != "자동":
        raise ValueError("ERP 가 정지한 거래처는 ERP 에서 해제해야 합니다.")
    return _request("거래정지해제", int(customer_id), reason, actor)


def _request(kind: str, customer_id: int, reason: str, actor: dict, sale_id: Optional[int] = None,
             amount: int = 0, payment_id: Optional[int] = None) -> int:
    if not str(reason or "").strip():
        raise ValueError("사유를 입력하세요.")
    with db.get_conn() as conn:
        dup = conn.execute("SELECT id FROM fin_requests WHERE kind=? AND status='대기' AND customer_id=? "
                           "AND COALESCE(sale_id, 0)=? AND COALESCE(payment_id, 0)=?",
                           (kind, customer_id, int(sale_id or 0), int(payment_id or 0))).fetchone()
        if dup:
            raise ValueError("이미 결재 대기 중인 요청이 있습니다.")
        cur = conn.execute("INSERT INTO fin_requests (kind, sale_id, customer_id, amount, reason, required_role, status, "
                           "requested_by, requested_by_id, requested_at, payment_id) VALUES (?,?,?,?,?,?, '대기', ?,?,?,?)",
                           (kind, sale_id, customer_id, int(amount), reason.strip(), _role_for(kind, int(amount)),
                            actor.get("name"), actor.get("id"), db._now(), payment_id))
        rid = int(cur.lastrowid)
    db.audit(f"{kind}요청", "거래처", customer_id, {"요청번호": rid, "매출": sale_id, "금액": amount, "사유": reason.strip()})
    _notify_approvers(rid)
    return rid


def _approvers(req: dict) -> list[dict]:
    from . import enterprise as ent
    users = db._df("SELECT * FROM users WHERE active = 1").to_dict("records")
    cust = db.get_customer(int(req["customer_id"])) or {}
    out = []
    # 직무 분리: 매출 취소는 그 매출 등록자, 입금 반제는 그 입금을 넣은 사람도 결재할 수 없다
    excluded = {int(req.get("requested_by_id") or 0)}
    if req["kind"] == "매출취소" and req.get("sale_id"):
        excluded.add(int((db.get_sale(int(req["sale_id"])) or {}).get("created_by_id") or 0))
    if req["kind"] == "입금반제" and req.get("payment_id"):
        excluded.add(int((db._one("SELECT created_by_id FROM payments WHERE id=?", [int(req["payment_id"])]) or {})
                         .get("created_by_id") or 0))
    for u in users:
        if int(u["id"]) in excluded or not ent.has_role(u, req["required_role"]):
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
    return pd.DataFrame([{"id": r["id"], "구분": r["kind"], "거래처": r["customer_name"], "customer_id": r["customer_id"],
                          "매출번호": r["sale_id"],
                          "금액": int(r["amount"]), "사유": r["reason"],
                          "대손사유": WRITEOFF_REASONS.get(r.get("wo_reason_code") or "", ("",))[0] or None,
                          "대손세액": r.get("bad_debt_vat"), "요청자": r["requested_by"],
                          "요청일시": r["requested_at"], "필요권한": db.ROLE_LABEL.get(r["required_role"])} for r in mine])


def withdraw(request_id: int, actor: dict, reason: str = "") -> None:
    """요청자가 결재 대기 중인 대손·거래정지 해제 요청을 회수한다."""
    req = db._one("SELECT * FROM fin_requests WHERE id=?", [int(request_id)])
    if not req:
        raise ValueError("요청을 찾을 수 없습니다.")
    if int(req.get("requested_by_id") or 0) != int(actor["id"]):
        raise PermissionError("본인이 요청한 건만 회수할 수 있습니다.")
    with db.get_conn() as conn:
        if conn.execute("UPDATE fin_requests SET status='회수', decided_at=?, comment=? WHERE id=? AND status='대기'",
                        (db._now(), f"[요청자 회수] {reason or ''}".strip(), int(request_id))).rowcount == 0:
            raise ValueError(f"이미 처리된 요청이라 회수할 수 없습니다(현재 상태: {req['status']}).")
    db.audit(f"{req['kind']}회수", "거래처", int(req["customer_id"]), {"요청번호": int(request_id), "사유": reason or None})


def mine_pending(user: dict) -> pd.DataFrame:
    """내가 요청해서 아직 결재 대기 중인 대손·거래정지 해제."""
    return db._df("SELECT r.id, r.kind AS 구분, c.name AS 거래처, r.sale_id AS 매출번호, r.amount AS 금액, "
                  "r.requested_at AS 요청일시 FROM fin_requests r JOIN customers c ON c.id = r.customer_id "
                  "WHERE r.status='대기' AND r.requested_by_id=? ORDER BY r.id DESC", [int(user["id"])])


def recover(request_id: int, amount: int, pay_date: Optional[str], actor: dict, method: str = "계좌이체",
            ref_no: str = "") -> dict:
    """대손 처리한 채권을 나중에 받았을 때: 대손 일부를 되돌리고(음수 '대손' 행) 실제 입금으로 바꾼다.
    회수한 금액의 대손세액(× 10/110)은 회수한 날이 속하는 과세기간의 매출세액에 더해 신고한다(부가법 제45조 제1항 단서)."""
    from . import enterprise as ent
    req = db._one("SELECT * FROM fin_requests WHERE id=?", [int(request_id)])
    if not req or req["kind"] != "대손" or req["status"] != "승인":
        raise ValueError("승인된 대손 건만 회수 처리할 수 있습니다.")
    sale = db.get_sale(int(req["sale_id"]))
    db.check_record_scope(sale, "매출")
    amount = int(amount or 0)
    left = int(req["amount"]) - int(req.get("recovered_amount") or 0)
    if not 1 <= amount <= left:
        raise ValueError(f"회수 금액은 1 ~ {left:,}원 (대손 처리했지만 아직 회수하지 않은 금액) 이어야 합니다.")
    day = db._d(pay_date) or date.today().isoformat()
    vat = bad_debt_vat(sale, amount)
    with db.transaction():
        with db.get_conn() as conn:
            if conn.execute("UPDATE fin_requests SET recovered_amount=COALESCE(recovered_amount,0)+? WHERE id=? "
                            "AND COALESCE(recovered_amount,0)+? <= amount", (amount, int(req["id"]), amount)).rowcount == 0:
                raise db.ConflictError("그 사이 다른 회수가 등록되었습니다. 새로고침해서 확인하세요.")
        ent.record_payment(int(req["sale_id"]), -amount, source="대손", pay_date=day, method="기타",
                           memo=f"대손 회수 — 결재 #{req['id']} 대손분 되돌림")
        ent.record_payment(int(req["sale_id"]), amount, pay_date=day, method=method, ref_no=ref_no,
                           memo=f"대손 회수 (결재 #{req['id']})")
        db.audit("대손회수", "매출", int(req["sale_id"]), {"요청번호": req["id"], "회수액": amount, "대손세액가산": vat,
                                                        "처리자": actor.get("name")})
    return {"회수액": amount, "대손세액가산": vat}


def tax_report(year: int, half: int) -> dict[str, pd.DataFrame]:
    """부가가치세 신고용 대손세액 자료 (확정신고 단위: 1기 1~6월, 2기 7~12월).
    {'공제': 그 기간에 대손 확정(승인)한 채권, '가산': 그 기간에 회수한 대손 채권(회수일 = 되돌린 '대손' 행의 날짜)}."""
    start, end = (f"{year}-01-01", f"{year}-06-30 23:59:59") if half == 1 else (f"{year}-07-01", f"{year}-12-31 23:59:59")
    sc, sp = db._scope_clause("c")
    rows = db._df("SELECT r.id AS 요청번호, s.sale_date AS 공급일, c.name AS 거래처, c.biz_no AS 사업자번호, "
                  "r.amount AS 대손금액, r.bad_debt_vat AS 대손세액, r.wo_reason_code AS 사유코드, "
                  "r.wo_event_date AS 사유발생일, r.decided_at AS 대손확정일, COALESCE(r.recovered_amount,0) AS 회수액 "
                  "FROM fin_requests r JOIN customers c ON c.id = r.customer_id JOIN sales s ON s.id = r.sale_id "
                  f"WHERE r.kind='대손' AND r.status='승인' AND r.decided_at BETWEEN ? AND ?{sc} ORDER BY r.decided_at",
                  [start, end, *sp])
    if not rows.empty:
        rows["사유"] = rows["사유코드"].map(lambda c: WRITEOFF_REASONS.get(c, ("(사유 미기재 — 예전 기록)",))[0])
    sc2, sp2 = db._scope_clause("s")
    rec = db._df("SELECT p.pay_date AS 회수일, s.sale_date AS 공급일, c.name AS 거래처, c.biz_no AS 사업자번호, "
                 "-p.amount AS 회수액, COALESCE(s.tax_type, '과세') AS 과세구분, p.memo AS 메모 FROM payments p "
                 "JOIN sales s ON s.id = p.sale_id JOIN customers c ON c.id = s.customer_id "
                 f"WHERE p.source='대손' AND p.amount < 0 AND p.pay_date BETWEEN ? AND ?{sc2} ORDER BY p.pay_date",
                 [start[:10], end[:10], *sp2])
    if not rec.empty:
        rec["대손세액가산"] = [int(int(a) * 10 / 110) if t == "과세" else 0 for a, t in zip(rec["회수액"], rec["과세구분"])]
    return {"공제": rows, "가산": rec}


def history(limit: int = 200) -> pd.DataFrame:
    sc, sp = db._scope_clause("c")
    return db._df("SELECT r.id, r.kind AS 구분, c.name AS 거래처, r.sale_id AS 매출번호, r.amount AS 금액, r.reason AS 사유, "
                  "r.bad_debt_vat AS 대손세액, COALESCE(r.recovered_amount, 0) AS 회수액, "
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
    # 결재 상태 · 대손 입금 · ERP 전송 대기 · 거래정지 해제를 한 트랜잭션으로 (중간에 실패하면 모두 되돌림)
    with db.transaction():
        result: dict = {}
        with db.get_conn() as conn:         # 먼저 결재를 차지한다 — 두 사람이 동시에 승인해 대손이 두 번 잡히지 않게
            if conn.execute("UPDATE fin_requests SET status=?, decided_by=?, decided_by_id=?, decided_at=?, comment=? "
                            "WHERE id=? AND status='대기'",
                            ("승인" if approve else "반려", actor.get("name"), int(actor["id"]), db._now(), comment or None,
                             int(req["id"]))).rowcount == 0:
                raise db.ConflictError("그 사이 다른 사람이 이 요청을 처리했습니다. 새로고침해서 확인하세요.")
        if approve and req["kind"] == "대손":
            sale = db.get_sale(int(req["sale_id"]))
            remain = int(sale.get("total_amount") or sale["amount"]) - int(sale.get("paid_amount") or 0)
            if remain <= 0:
                raise ValueError("그 사이 입금되어 남은 미수금이 없습니다. 반려하세요.")
            # 결재 한도는 요청 금액으로 정했다 → 그 사이 미수금이 늘었어도 요청 금액까지만 대손 (더 크면 다시 요청)
            remain = min(remain, int(req.get("amount") or remain))
            if req.get("wo_reason_code"):
                check_writeoff(sale, remain, req["wo_reason_code"], req.get("wo_event_date"))
            with db.get_conn() as conn:
                conn.execute("UPDATE fin_requests SET amount=?, bad_debt_vat=? WHERE id=?",
                             (remain, bad_debt_vat(sale, remain), int(req["id"])))
            ent.record_payment(int(req["sale_id"]), remain, source="대손", method="기타", memo=f"대손 처리 (결재 #{req['id']})")
            with db.get_conn() as conn:
                conn.execute("INSERT INTO erp_outbox (doc_type, ref_id, status, created_at) VALUES ('대손', ?, '대기', ?)",
                             (int(req["id"]), db._now()))
            result["대손금액"] = remain
        if approve and req["kind"] == "매출취소":
            db.cancel_sale(int(req["sale_id"]), f"{req['reason']} (결재 #{req['id']})", actor=actor)
            result["취소매출"] = int(req["sale_id"])
        if approve and req["kind"] == "입금반제":
            from . import enterprise as ent_mod
            result["반제입금"] = ent_mod.reverse_payment(int(req["payment_id"]), f"{req['reason']} (결재 #{req['id']})")
        if approve and req["kind"] == "거래정지해제":
            exempt = (date.today() + timedelta(days=int(_setting("auto_block_exempt_days")))).isoformat()
            with db.get_conn() as conn:
                conn.execute("UPDATE customers SET trade_blocked=0, block_source=NULL, block_reason=NULL, blocked_at=NULL, "
                             "block_exempt_until=? WHERE id=?", (exempt, int(req["customer_id"])))
            result["재정지유예"] = exempt
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
        for r in db._df("SELECT c.id, c.credit_limit, SUM(COALESCE(s.total_amount, s.amount) - COALESCE(s.paid_amount, 0)) "
                        "- COALESCE((SELECT SUM(a.amount) FROM advances a WHERE a.customer_id = c.id), 0) AS ar "
                        "FROM customers c JOIN sales s ON s.customer_id = c.id AND s.status NOT IN ('입금완료', ?) "
                        "WHERE COALESCE(c.credit_limit, 0) > 0 GROUP BY c.id, c.credit_limit",
                        [db.SALE_CANCELLED]).itertuples():
            if int(r.ar or 0) > int(r.credit_limit):
                rows.append({"customer_id": int(r.id), "reason": f"순채권(미수−선수금) {int(r.ar):,}원이 여신한도 {int(r.credit_limit):,}원 초과"})
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

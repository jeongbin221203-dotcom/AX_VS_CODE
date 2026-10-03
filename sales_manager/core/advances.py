"""선수금 — 계약금처럼 매출보다 먼저 받은 돈, 매출보다 많이 받은 돈, 반품으로 돌려줄 돈을 거래처별로 쌓아 두고 나중에 매출에 배분한다.

  advances 행 (금액 부호 = 선수금 잔액의 증감)
    입금      +  거래처가 보낸 돈 중 매출에 배분하지 않은 금액 (계약금, 과입금)
    반품초과  +  이미 다 받은 매출을 반품·정정해 돌려줘야 할 금액
    배분      −  선수금을 매출 입금으로 돌림 (그 매출에 '선수금' 입금 행이 같이 생김)
    환불      −  거래처에 돈을 돌려줌
  잔액은 음수가 될 수 없다. 월 마감한 날짜로는 등록할 수 없다.

  receive(): 한 번에 받은 돈을 그 거래처의 미수 매출에 결제기일이 빠른 순서로 나눠 넣고, 남으면 선수금으로 둔다.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from . import sales_db as db

KINDS = {"입금": 1, "반품초과": 1, "배분": -1, "환불": -1}


def balance(customer_id: int) -> int:
    return int(db._scalar("SELECT COALESCE(SUM(amount), 0) FROM advances WHERE customer_id=?", [int(customer_id)]))


def add(customer_id: int, amount: int, kind: str, day: Optional[str] = None, sale_id: Optional[int] = None,
        method: str = "", ref_no: str = "", memo: str = "") -> int:
    from . import periods
    if kind not in KINDS:
        raise ValueError(f"선수금 구분이 올바르지 않습니다: {kind}")
    amount = int(amount)
    if amount <= 0:
        raise ValueError("금액은 0보다 커야 합니다.")
    day = db._d(day) or date.today().isoformat()
    periods.check(day, "선수금")
    signed = amount * KINDS[kind]
    with db.get_conn() as conn:
        db.lock(conn, f"advance-{int(customer_id)}")
        bal = int(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM advances WHERE customer_id=?",
                               (int(customer_id),)).fetchone()[0])
        if bal + signed < 0:
            raise ValueError(f"선수금 잔액({bal:,}원)보다 많이 쓸 수 없습니다.")
        cur = conn.execute("INSERT INTO advances (customer_id, entry_date, amount, kind, sale_id, method, ref_no, memo, "
                           "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (int(customer_id), day, signed, kind, sale_id, method or None, ref_no or None, memo or None,
                            db.current_actor(), db._now()))
        aid = int(cur.lastrowid)
    db.audit(f"선수금{kind}", "거래처", int(customer_id), {"금액": signed, "일자": day, "매출": sale_id, "잔액": bal + signed})
    return aid


def apply_to_sale(sale_id: int, amount: Optional[int] = None, day: Optional[str] = None) -> int:
    """선수금을 매출 입금으로 배분한다. amount 를 비우면 미수 전액(잔액 한도)."""
    from . import enterprise as ent
    sale = db.get_sale(int(sale_id))
    db.check_record_scope(sale, "매출")
    remain = int(sale.get("total_amount") or sale["amount"]) - int(sale.get("paid_amount") or 0)
    use = min(int(amount) if amount else remain, remain, balance(int(sale["customer_id"])))
    if use <= 0:
        raise ValueError("배분할 선수금이나 미수금이 없습니다.")
    add(int(sale["customer_id"]), use, "배분", day, sale_id=int(sale_id), memo=f"매출 #{sale_id} 에 배분")
    try:
        ent.record_payment(int(sale_id), use, source="선수금", pay_date=day, method="상계", memo="선수금 배분")
    except Exception:
        add(int(sale["customer_id"]), use, "입금", day, memo=f"매출 #{sale_id} 배분 실패 되돌림")
        raise
    return use


def receive(customer_id: int, amount: int, day: Optional[str] = None, method: str = "계좌이체", ref_no: str = "",
            memo: str = "", sale_ids: Optional[list[int]] = None) -> dict:
    """거래처 일괄 입금: 미수 매출(결제기일 순, 또는 고른 매출)에 나눠 넣고 남으면 선수금."""
    from . import enterprise as ent
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    left = int(amount)
    if left <= 0:
        raise ValueError("입금액은 0보다 커야 합니다.")
    day = db._d(day) or date.today().isoformat()
    sql = ("SELECT id, COALESCE(total_amount, amount) - COALESCE(paid_amount, 0) AS remain FROM sales "
           "WHERE customer_id=? AND status NOT IN ('입금완료', ?) AND COALESCE(total_amount, amount) > 0")
    params: list = [int(customer_id), db.SALE_CANCELLED]
    if sale_ids:
        sql += f" AND id IN ({','.join('?' * len(sale_ids))})"
        params += [int(i) for i in sale_ids]
    open_sales = db._df(sql + " ORDER BY COALESCE(due_date, sale_date), id", params).to_dict("records")
    applied = []
    for s in open_sales:
        if left <= 0:
            break
        use = min(left, int(s["remain"]))
        if use <= 0:
            continue
        ent.record_payment(int(s["id"]), use, pay_date=day, method=method, ref_no=ref_no, memo=memo or "일괄 입금")
        applied.append({"매출": int(s["id"]), "금액": use})
        left -= use
    if left:
        add(int(customer_id), left, "입금", day, method=method, ref_no=ref_no, memo=memo or "매출에 배분하지 않은 입금")
    db.audit("일괄입금", "거래처", int(customer_id), {"입금액": int(amount), "배분": applied, "선수금": left})
    return {"applied": applied, "advance": left}


def history(customer_id: int) -> pd.DataFrame:
    return db._df("SELECT entry_date AS 일자, kind AS 구분, amount AS 금액, sale_id AS 매출번호, method AS 방법, "
                  "ref_no AS 참조번호, memo AS 메모, created_by AS 처리자 FROM advances WHERE customer_id=? ORDER BY id",
                  [int(customer_id)])


def balances() -> pd.DataFrame:
    sc, sp = db._scope_clause("c")
    return db._df("SELECT c.id, c.name AS 거래처, SUM(a.amount) AS 선수금잔액 FROM advances a JOIN customers c "
                  f"ON c.id = a.customer_id WHERE 1=1{sc} GROUP BY c.id, c.name HAVING SUM(a.amount) <> 0 "
                  "ORDER BY SUM(a.amount) DESC", sp)

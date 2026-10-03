"""매출 월 마감 · 월말 채권 스냅샷 · 거래처 원장 (자재관리의 월 마감·수불부와 같은 규칙)

월 마감
  * 마감월(closed_through)까지의 날짜로는 매출 등록·수정·취소, 입금·반제를 할 수 없다
    → 세금계산서·부가세 신고·ERP 전기가 끝난 달의 숫자가 나중에 바뀌지 않는다
  * 한 달씩 앞으로만 마감한다(관리자). ERP 를 쓰면 그 달 매출이 모두 ERP 로 전송된 뒤에만 마감된다
  * 마감하면 그 달 말일 기준 거래처별 채권(매출 누계·입금 누계·잔액·연체)을 스냅샷으로 남긴다
  * 해제는 마지막 마감월 하나만, 사유를 적어 되돌린다(감사로그)

거래처 원장
  기초 잔액 + 기간 중 매출(부가세 포함 합계)·입금·반제를 날짜순으로 → 잔액. 취소된 매출은 빠진다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from . import sales_db as db


def _month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def _next_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"


def closed_through() -> str:
    """마감된 마지막 월 ('' = 마감 없음)."""
    try:
        row = db._one("SELECT closed_through FROM sales_period_closes ORDER BY id DESC LIMIT 1")
    except Exception:   # noqa: BLE001 - 마이그레이션 전
        return ""
    return (row or {}).get("closed_through") or ""


def date_problem(day: Optional[str], what: str = "매출") -> str:
    """그 날짜로 등록·변경할 수 없으면 사유."""
    closed = closed_through()
    if closed and day and str(day)[:7] <= closed:
        return (f"{closed} 까지 월 마감되어 {str(day)[:7]} {what}은(는) 등록·변경할 수 없습니다. "
                f"마감 이후 날짜로 처리하거나 관리자에게 마감 해제를 요청하세요.")
    return ""


def check(day: Optional[str], what: str = "매출") -> None:
    problem = date_problem(day, what)
    if problem:
        raise ValueError(problem)


def next_closable() -> str:
    closed = closed_through()
    if closed:
        return _next_month(closed)
    first = db._one("SELECT MIN(substr(sale_date, 1, 7)) AS ym FROM sales")
    return (first or {}).get("ym") or date.today().replace(day=1).isoformat()[:7]


def close_month(ym: str, actor: dict) -> dict:
    from . import erp
    expected = next_closable()
    if ym != expected:
        raise ValueError(f"마감은 한 달씩 순서대로 합니다. 다음 마감할 달은 {expected} 입니다.")
    if ym >= date.today().strftime("%Y-%m"):
        raise ValueError("아직 끝나지 않은 달은 마감할 수 없습니다.")
    if erp.settings()["adapter"] != "none":
        unsent = int(db._scalar("SELECT COUNT(*) FROM sales WHERE substr(sale_date, 1, 7)=? AND status <> ? "
                                "AND COALESCE(erp_status, '') NOT IN ('전송완료', '취소완료')", [ym, db.SALE_CANCELLED]))
        if unsent:
            raise ValueError(f"{ym} 매출 중 ERP 로 전송되지 않은 건이 {unsent}건 있습니다. 전송을 끝낸 뒤 마감하세요.")
    end = _month_end(ym)
    snap = ar_balances(end)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM ar_snapshots WHERE ym=?", (ym,))
        for r in snap.itertuples():
            conn.execute("INSERT INTO ar_snapshots (ym, customer_id, sales_total, paid_total, balance, overdue, created_at) "
                         "VALUES (?,?,?,?,?,?,?)", (ym, int(r.customer_id), int(r.sales_total), int(r.paid_total),
                                                    int(r.balance), int(r.overdue), db._now()))
        conn.execute("INSERT INTO sales_period_closes (closed_through, action, actor, at) VALUES (?, 'CLOSE', ?, ?)",
                     (ym, actor.get("name"), db._now()))
    result = {"ym": ym, "customers": len(snap), "balance": int(snap["balance"].sum()) if not snap.empty else 0}
    db.audit("월마감", "매출", None, result)
    return result


def reopen(reason: str, actor: dict) -> str:
    closed = closed_through()
    if not closed:
        raise ValueError("마감된 달이 없습니다.")
    if not str(reason or "").strip():
        raise ValueError("마감 해제 사유를 입력하세요.")
    prev = _prev_month(closed)
    first = db._one("SELECT closed_through FROM sales_period_closes WHERE action='CLOSE' ORDER BY id LIMIT 1")
    back_to = "" if first and first["closed_through"] == closed else prev
    with db.get_conn() as conn:
        conn.execute("DELETE FROM ar_snapshots WHERE ym=?", (closed,))
        conn.execute("INSERT INTO sales_period_closes (closed_through, action, reason, actor, at) VALUES (?, 'REOPEN', ?, ?, ?)",
                     (back_to, reason.strip(), actor.get("name"), db._now()))
    db.audit("마감해제", "매출", None, {"해제월": closed, "사유": reason.strip()})
    return closed


def history() -> pd.DataFrame:
    return db._df("SELECT at AS 일시, CASE action WHEN 'CLOSE' THEN '마감' ELSE '해제' END AS 구분, "
                  "closed_through AS 마감월, reason AS 사유, actor AS 처리자 FROM sales_period_closes ORDER BY id DESC")


def ar_balances(as_of: str) -> pd.DataFrame:
    """as_of(그날 포함) 기준 거래처별 매출 누계(부가세 포함)·입금 누계·잔액·연체(결제기일 지난 잔액)."""
    # 반품·감액 정정(합계가 음수인 행)은 원매출의 '반품상계' 입금으로 채권에 반영되므로 여기서는 뺀다
    sales = db._df("SELECT s.id, s.customer_id, COALESCE(s.total_amount, s.amount) AS total, s.due_date "
                   "FROM sales s WHERE s.status <> ? AND s.sale_date <= ? AND COALESCE(s.total_amount, s.amount) >= 0",
                   [db.SALE_CANCELLED, as_of])
    if sales.empty:
        return pd.DataFrame(columns=["customer_id", "sales_total", "paid_total", "balance", "overdue"])
    pays = db._df("SELECT p.sale_id, SUM(p.amount) AS paid FROM payments p JOIN sales s ON s.id = p.sale_id "
                  "WHERE s.status <> ? AND p.pay_date <= ? GROUP BY p.sale_id", [db.SALE_CANCELLED, as_of])
    sales = sales.merge(pays, how="left", left_on="id", right_on="sale_id")
    sales["paid"] = sales["paid"].fillna(0).astype(int)
    sales["remain"] = (sales["total"].astype(int) - sales["paid"]).clip(lower=0)
    sales["late"] = sales["remain"].where(sales["due_date"].fillna("9999-12-31") < as_of, 0)
    out = sales.groupby("customer_id").agg(sales_total=("total", "sum"), paid_total=("paid", "sum"),
                                           balance=("remain", "sum"), overdue=("late", "sum")).reset_index()
    return out[(out["balance"] != 0) | (out["sales_total"] != 0)]


def snapshot_table(ym: str) -> pd.DataFrame:
    sc, sp = db._scope_clause("c")
    return db._df("SELECT c.name AS 거래처, a.sales_total AS 매출누계, a.paid_total AS 입금누계, a.balance AS 월말잔액, "
                  "a.overdue AS 연체잔액 FROM ar_snapshots a JOIN customers c ON c.id = a.customer_id "
                  f"WHERE a.ym = ?{sc} ORDER BY a.balance DESC", [ym, *sp])


def customer_ledger(customer_id: int, date_from: str, date_to: str) -> tuple[int, pd.DataFrame]:
    """(기초 잔액, 원장 행). 매출(반품·정정 포함)은 부가세 포함 합계로 차변, 입금·대손·선수금 입금은 대변.

    잔액이 음수면 거래처에 돌려줄 돈(선수금)이 있다는 뜻. 반품을 원매출 채권에서 빼는 '반품상계'와
    선수금을 매출로 돌리는 '선수금' 배분은 같은 돈을 옮기는 내부 처리라 원장에 따로 적지 않는다.
    """
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    hidden = "('반품상계', '선수금')"
    sales_sql = ("SELECT sale_date AS 일자, COALESCE(sale_kind, '매출') AS 구분, item AS 적요, id AS 매출번호, "
                 "COALESCE(total_amount, amount) AS 차변, 0 AS 대변 FROM sales WHERE customer_id=? AND status <> ? ")
    pays_sql = ("SELECT p.pay_date AS 일자, CASE WHEN p.source = '대손' THEN '대손' WHEN p.amount < 0 THEN '반제' "
                "ELSE '입금' END AS 구분, COALESCE(p.method, '') || CASE WHEN p.ref_no IS NOT NULL THEN ' ' || p.ref_no "
                "ELSE '' END AS 적요, p.sale_id AS 매출번호, 0 AS 차변, p.amount AS 대변 FROM payments p "
                f"JOIN sales s ON s.id = p.sale_id WHERE s.customer_id=? AND s.status <> ? "
                f"AND COALESCE(p.source, '') NOT IN {hidden} ")
    adv_sql = ("SELECT entry_date AS 일자, '선수금 ' || kind AS 구분, COALESCE(memo, '') AS 적요, sale_id AS 매출번호, "
               "0 AS 차변, amount AS 대변 FROM advances WHERE customer_id=? AND kind IN ('입금', '환불') ")
    base = [customer_id, db.SALE_CANCELLED]
    opening = int(db._scalar(f"SELECT COALESCE(SUM(차변 - 대변), 0) FROM ({sales_sql} AND sale_date < ? "
                             f"UNION ALL {pays_sql} AND p.pay_date < ? UNION ALL {adv_sql} AND entry_date < ?) x",
                             [*base, date_from, *base, date_from, customer_id, date_from]))
    sales = db._df(sales_sql + "AND sale_date BETWEEN ? AND ?", [*base, date_from, date_to])
    pays = db._df(pays_sql + "AND p.pay_date BETWEEN ? AND ?", [*base, date_from, date_to])
    advs = db._df(adv_sql + "AND entry_date BETWEEN ? AND ?", [customer_id, date_from, date_to])
    rows = pd.concat([f for f in (sales, pays, advs) if not f.empty], ignore_index=True) \
        if not (sales.empty and pays.empty and advs.empty) else pd.DataFrame()
    if rows.empty:
        return opening, pd.DataFrame(columns=["일자", "구분", "적요", "매출번호", "차변", "대변", "잔액"])
    rows["순서"] = rows["구분"].map({"매출": 0, "정정": 1, "반품": 2, "입금": 3, "선수금 입금": 3, "반제": 4,
                                    "대손": 5, "선수금 환불": 6}).fillna(9)
    rows = rows.sort_values(["일자", "순서", "매출번호"]).drop(columns=["순서"]).reset_index(drop=True)
    rows["잔액"] = opening + (rows["차변"].astype(int) - rows["대변"].astype(int)).cumsum()
    return opening, rows


def today_or(day: Optional[str]) -> str:
    return db._d(day) or datetime.now().strftime("%Y-%m-%d")

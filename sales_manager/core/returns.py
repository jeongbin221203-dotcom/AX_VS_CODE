"""반품 · 단가 정정 — 매출을 통째로 취소하지 않고 일부만 되돌리거나 금액을 바로잡는다.

  반품      : 원매출 수량 중 일부를 되돌린다 → 마이너스 매출(sale_kind='반품', 원매출 연결)
  단가 정정 : 단가를 내리면 차액만큼 마이너스 매출, 올리면 플러스 매출(sale_kind='정정')
  * 실적·부가세는 원매출 + 반품·정정 행을 더한 순액이 된다 (반품 행은 공급가액·부가세·합계가 음수)
  * 채권: 마이너스 금액은 원매출의 미수에서 '반품' 상계 입금으로 빼고, 이미 다 받은 매출이면 남는 금액은 거래처 선수금으로 쌓는다
  * ERP 에는 반품 전표(문서종류 '반품', 원 ERP 번호 참조)를 보내고, 원매출이 전자세금계산서로 발행됐으면
    수정세금계산서(환입 03 / 공급가액 변동 02)를 같은 발행 흐름으로 낸다
  * 월 마감 이후 날짜로만 등록한다 (마감월 원매출이어도 반품은 이번 달 날짜로)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from . import sales_db as db

KINDS = ("반품", "정정")


def linked(sale_id: int) -> pd.DataFrame:
    return db._df("SELECT id, sale_kind AS 구분, sale_date AS 일자, qty AS 수량, unit_price AS 단가, amount AS 공급가액, "
                  "vat_amount AS 부가세, total_amount AS 합계, memo AS 사유, erp_status AS \"ERP\" FROM sales "
                  "WHERE original_sale_id=? AND status <> ? ORDER BY id", [int(sale_id), db.SALE_CANCELLED])


def returnable_qty(sale: dict) -> int:
    done = db._scalar("SELECT COALESCE(SUM(qty), 0) FROM sales WHERE original_sale_id=? AND sale_kind='반품' "
                      "AND status <> ?", [int(sale["id"]), db.SALE_CANCELLED])
    return int(sale["qty"]) + int(done)          # 반품 행 수량은 음수


def _insert(orig: dict, kind: str, qty: int, unit_price: int, reason: str, day: str) -> int:
    """원매출의 거래처·담당자·품목·과세·법인·통화를 물려받은 반품/정정 행."""
    amount = qty * unit_price
    vat = int(amount * db.VAT_RATE[orig.get("tax_type") or "과세"]) if amount >= 0 else \
        -int(-amount * db.VAT_RATE[orig.get("tax_type") or "과세"])
    total = amount + vat
    fx = float(orig.get("fx_rate") or 1)
    record = {f: orig.get(f) for f in db.SALE_FIELDS}
    record.update(sale_date=day, qty=qty, unit_price=unit_price, amount=amount, vat_amount=vat, total_amount=total,
                  memo=f"[{kind}] {reason}", due_date=day,
                  status="입금완료" if total < 0 else "입금대기", paid_amount=0,
                  foreign_amount=round(amount / fx, 2) if (orig.get("currency") or "KRW") != "KRW" else None)
    cols = [*db.SALE_FIELDS, "sale_kind", "original_sale_id", "erp_status", "created_by_id", "created_at"]
    vals = [record[f] for f in db.SALE_FIELDS] + [kind, int(orig["id"]), "대기", db.current_actor_id(), db._now()]
    with db.get_conn() as conn:
        cur = conn.execute(f"INSERT INTO sales ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        sid = int(cur.lastrowid)
        conn.execute("INSERT INTO erp_outbox (doc_type, ref_id, status, created_at) VALUES (?, ?, '대기', ?)",
                     ("반품" if total < 0 else "매출", sid, db._now()))
    return sid


def _settle_credit(orig: dict, credit: int, day: str, sid: int) -> dict:
    """마이너스 금액(credit>0)을 원매출 미수에서 빼고, 남으면 선수금으로."""
    from . import advances
    from . import enterprise as ent
    fresh = db.get_sale(int(orig["id"]))
    remain = int(fresh.get("total_amount") or fresh["amount"]) - int(fresh.get("paid_amount") or 0)
    to_ar = max(0, min(credit, remain))
    if to_ar:
        ent.record_payment(int(orig["id"]), to_ar, source="반품상계", pay_date=day, method="상계",
                           memo=f"반품·정정 매출 #{sid}")
    extra = credit - to_ar
    if extra:
        advances.add(int(orig["customer_id"]), extra, "반품초과", day, sale_id=sid,
                     memo=f"원매출 #{orig['id']} 이미 입금 완료 — 반품 금액을 선수금으로")
    return {"채권상계": to_ar, "선수금": extra}


def current_unit_price(orig: dict) -> int:
    """지금 단가 = 원단가 + 취소되지 않은 정정 행의 차액(정정 행 unit_price 가 차액)."""
    extra = db._scalar("SELECT COALESCE(SUM(unit_price), 0) FROM sales WHERE original_sale_id=? AND sale_kind='정정' "
                       "AND status <> ?", [int(orig["id"]), db.SALE_CANCELLED])
    return int(orig["unit_price"]) + int(extra or 0)


def create(sale_id: int, kind: str, reason: str, qty: Optional[int] = None, new_unit_price: Optional[int] = None,
           day: Optional[str] = None) -> dict:
    from . import periods
    if kind not in KINDS:
        raise ValueError("구분은 반품 또는 정정입니다.")
    if not str(reason or "").strip():
        raise ValueError("사유를 입력하세요.")
    orig = db.get_sale(int(sale_id))
    db.check_record_scope(orig, "매출")
    if orig["status"] == db.SALE_CANCELLED:
        raise ValueError("취소된 매출입니다.")
    if (orig.get("sale_kind") or "매출") != "매출":
        raise ValueError("반품·정정 행에는 다시 반품·정정을 할 수 없습니다. 원매출에서 하세요.")
    day = db._d(day) or date.today().isoformat()
    periods.check(day, "반품·정정")
    if day < orig["sale_date"]:
        raise ValueError("반품·정정 일자는 원매출 일자보다 빠를 수 없습니다.")
    # 반품 행 · 원매출 상계 입금 · 선수금(반품초과)을 한 트랜잭션으로 — 중간에 실패하면 모두 되돌린다
    with db.transaction() as conn:
        db.lock(conn, f"sale-adjust-{int(orig['id'])}")      # 같은 매출에 동시에 반품해 수량이 넘치지 않게
        unit_now = current_unit_price(orig)                 # 원단가 + 지금까지 정정한 차액 (정정·반품은 이 단가 기준)
        if kind == "반품":
            qty = int(qty or 0)
            can = returnable_qty(orig)
            if not 1 <= qty <= can:
                raise ValueError(f"반품 수량은 1 ~ {can} 사이여야 합니다 (원수량 {orig['qty']}, 이미 반품 {int(orig['qty']) - can}).")
            sid = _insert(orig, "반품", -qty, unit_now, reason.strip(), day)
        else:
            if new_unit_price is None or int(new_unit_price) < 0:
                raise ValueError("정정할 단가를 입력하세요.")
            delta = int(new_unit_price) - unit_now
            if delta == 0:
                raise ValueError("단가가 같습니다.")
            qty_now = returnable_qty(orig)                       # 반품되지 않은 수량에만 정정 적용
            if qty_now <= 0:
                raise ValueError("남은 수량이 없습니다.")
            sid = _insert(orig, "정정", qty_now, delta, f"단가 {unit_now:,} → {int(new_unit_price):,} · {reason.strip()}",
                          day)
        row = db.get_sale(sid)
        settle = _settle_credit(orig, -int(row["total_amount"]), day, sid) if int(row["total_amount"]) < 0 else {}
        db.audit(kind, "매출", int(orig["id"]), {"반품·정정매출": sid, "수량": row["qty"], "합계": row["total_amount"],
                                                 "사유": reason.strip(), **settle})
    return {"sale_id": sid, "total": int(row["total_amount"]), **settle}


def original_approval_no(sale_id: int) -> Optional[str]:
    row = db._one("SELECT approval_no FROM sale_documents WHERE sale_id=? AND voided_at IS NULL "
                  "AND approval_no IS NOT NULL AND doc_type IN ('전자세금계산서','세금계산서') ORDER BY id DESC LIMIT 1",
                  [int(sale_id)])
    return (row or {}).get("approval_no")

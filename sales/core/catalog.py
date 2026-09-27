"""품목·단가 마스터 — 제품 정가, 과세구분, 거래처별 특가(유효기간)

  단가 결정 순서: 거래처 특가(해당 일자에 유효) → 품목 정가
  품목 등록·수정은 관리자, 거래처 특가는 팀장 이상(담당 범위의 거래처)만 한다. 모든 변경은 감사로그에 남는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

from . import sales_db as db

PRODUCT_FIELDS = ["code", "name", "category", "unit", "list_price", "tax_type", "erp_material", "active", "memo"]


def list_products(keyword: str = "", active_only: bool = False) -> pd.DataFrame:
    sql = ("SELECT id, code AS 품목코드, name AS 품목명, category AS 분류, unit AS 단위, list_price AS 정가, "
           'tax_type AS 과세구분, erp_material AS "ERP자재", active AS 사용, updated_at AS 수정일 FROM products WHERE 1=1')
    params: list[Any] = []
    if keyword:
        sql += " AND (code LIKE ? OR name LIKE ? OR category LIKE ?)"
        params += [f"%{keyword}%"] * 3
    if active_only:
        sql += " AND active = 1"
    return db._df(sql + " ORDER BY code", params)


def get_product(product_id: int) -> Optional[dict]:
    return db._one("SELECT * FROM products WHERE id=?", [product_id])


def product_options(active_only: bool = True) -> list[dict]:
    sql = "SELECT id, code, name, unit, list_price, tax_type FROM products"
    if active_only:
        sql += " WHERE active = 1"
    return db._df(sql + " ORDER BY code").to_dict("records")


def upsert_product(data: dict) -> int:
    code = str(data.get("code") or "").strip()
    name = str(data.get("name") or "").strip()
    if not code or not name:
        raise ValueError("품목코드와 품목명은 필수입니다.")
    tax_type = data.get("tax_type") or "과세"
    if tax_type not in db.TAX_TYPES:
        raise ValueError(f"과세구분 값이 올바르지 않습니다: {tax_type}")
    price = int(data.get("list_price") or 0)
    if price < 0:
        raise ValueError("정가는 0 이상이어야 합니다.")
    record = {"code": code, "name": name, "category": (data.get("category") or "").strip() or None,
              "unit": (data.get("unit") or "EA").strip() or "EA", "list_price": price, "tax_type": tax_type,
              "erp_material": (data.get("erp_material") or "").strip() or code,
              "active": int(data.get("active", 1)), "memo": data.get("memo")}
    dup = db._one("SELECT id FROM products WHERE code=? AND id<>?", [code, int(data.get("id") or 0)])
    if dup:
        raise ValueError(f"품목코드 {code} 는 이미 있습니다.")
    values = [record[f] for f in PRODUCT_FIELDS]
    prev = get_product(int(data["id"])) if data.get("id") else None
    with db.get_conn() as conn:
        if prev:
            conn.execute(f"UPDATE products SET {', '.join(f'{f}=?' for f in PRODUCT_FIELDS)}, updated_at=? WHERE id=?",
                         (*values, db._now(), prev["id"]))
            pid = int(prev["id"])
        else:
            cur = conn.execute(f"INSERT INTO products ({', '.join(PRODUCT_FIELDS)}, created_at, updated_at) "
                               f"VALUES ({', '.join('?' * len(PRODUCT_FIELDS))}, ?, ?)", (*values, db._now(), db._now()))
            pid = int(cur.lastrowid)
    db.audit("수정" if prev else "등록", "품목", pid,
             {"품목코드": code, "변경": db.diff(prev, record, PRODUCT_FIELDS) if prev else None})
    return pid


# ---------------------------------------------------------------------------
# 거래처 특가
# ---------------------------------------------------------------------------
def list_customer_prices(customer_id: Optional[int] = None) -> pd.DataFrame:
    sc, sp = db._scope_clause("c")
    sql = ("SELECT cp.id, c.name AS 거래처, p.code AS 품목코드, p.name AS 품목명, p.list_price AS 정가, "
           "cp.unit_price AS 특가, cp.valid_from AS 시작일, cp.valid_to AS 종료일, cp.memo AS 메모, "
           "cp.created_by AS 등록자 FROM customer_prices cp JOIN customers c ON c.id = cp.customer_id "
           "JOIN products p ON p.id = cp.product_id WHERE 1=1")
    params: list[Any] = []
    if customer_id:
        sql += " AND cp.customer_id = ?"
        params.append(int(customer_id))
    return db._df(sql + sc + " ORDER BY c.name, p.code, cp.valid_from DESC", params + sp)


def set_customer_price(customer_id: int, product_id: int, unit_price: int, valid_from: str,
                       valid_to: Optional[str] = None, memo: str = "") -> int:
    """특가 등록. 같은 거래처·품목의 앞선 특가는 새 시작일 전날로 끝낸다(기간 겹침 방지)."""
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    product = get_product(int(product_id))
    if not product:
        raise ValueError("품목을 찾을 수 없습니다.")
    unit_price = int(unit_price)
    if unit_price <= 0:
        raise ValueError("특가는 0보다 커야 합니다.")
    start = db._d(valid_from) or date.today().isoformat()
    end = db._d(valid_to)
    if end and end < start:
        raise ValueError("종료일이 시작일보다 빠릅니다.")
    day_before = (datetime.strptime(start, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE customer_prices SET valid_to=? WHERE customer_id=? AND product_id=? "
                     "AND valid_from < ? AND (valid_to IS NULL OR valid_to >= ?)",
                     (day_before, customer_id, product_id, start, start))
        existing = conn.execute("SELECT id FROM customer_prices WHERE customer_id=? AND product_id=? AND valid_from=?",
                                (customer_id, product_id, start)).fetchone()
        if existing:
            conn.execute("UPDATE customer_prices SET unit_price=?, valid_to=?, memo=?, created_by=? WHERE id=?",
                         (unit_price, end, memo, db.current_actor(), existing["id"]))
            new_id = int(existing["id"])
        else:
            cur = conn.execute("INSERT INTO customer_prices (customer_id, product_id, unit_price, valid_from, valid_to, "
                               "memo, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                               (customer_id, product_id, unit_price, start, end, memo, db.current_actor(), db._now()))
            new_id = int(cur.lastrowid)
    rate = (1 - unit_price / product["list_price"]) * 100 if product["list_price"] else 0
    db.audit("특가설정", "품목", int(product_id),
             {"거래처": cust["name"], "품목": product["code"], "특가": unit_price, "정가": product["list_price"],
              "할인율": round(rate, 1), "기간": [start, end]})
    return new_id


def price_for(customer_id: Optional[int], product_id: int, on: Optional[str] = None) -> dict:
    """해당 일자의 적용 단가 {'unit_price','list_price','source','tax_type','unit','code','name'}."""
    product = get_product(int(product_id))
    if not product:
        raise ValueError("품목을 찾을 수 없습니다.")
    on = db._d(on) or date.today().isoformat()
    special = None
    if customer_id:
        special = db._one("SELECT unit_price FROM customer_prices WHERE customer_id=? AND product_id=? "
                          "AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?) "
                          "ORDER BY valid_from DESC LIMIT 1", [int(customer_id), int(product_id), on, on])
    return {"unit_price": int(special["unit_price"]) if special else int(product["list_price"]),
            "list_price": int(product["list_price"]), "source": "특가" if special else "정가",
            "tax_type": product["tax_type"], "unit": product["unit"], "code": product["code"],
            "name": product["name"], "erp_material": product.get("erp_material") or product["code"]}


def seed_products() -> int:
    """샘플 품목 (개발·시연용)."""
    samples = [("SW-ERP-01", "ERP 라이선스(연간)", "소프트웨어", "식", 12_000_000, "과세"),
               ("SV-MNT-01", "유지보수(월)", "서비스", "월", 1_500_000, "과세"),
               ("HW-SRV-01", "서버 장비", "하드웨어", "대", 8_500_000, "과세"),
               ("SV-EDU-01", "사용자 교육(일)", "서비스", "일", 900_000, "면세"),
               ("EX-PKG-01", "수출용 패키지", "하드웨어", "식", 20_000_000, "영세")]
    created = 0
    for code, name, cat, unit, price, tax in samples:
        if not db._one("SELECT id FROM products WHERE code=?", [code]):
            upsert_product({"code": code, "name": name, "category": cat, "unit": unit,
                            "list_price": price, "tax_type": tax})
            created += 1
    return created

"""거래처 담당자 여러 명 — 구매·회계·현업·의사결정 담당을 따로 둔다.

  대표 담당자(is_primary)는 거래처의 고객 담당자·연락처·이메일(customers.manager·phone·email)과 항상 같게 맞춘다
  → 목록·추출·견적서·개인정보 마스킹 등 기존 화면은 그대로 대표 담당자를 쓴다.
  삭제 대신 '사용 안 함'(퇴사·이동), 개인정보 파기·열람 요청과 보관기간 파기에도 포함된다.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from . import sales_db as db

ROLES = ["대표", "구매", "회계·세금계산서", "현업", "의사결정", "기술", "기타"]
FIELDS = ["name", "dept", "title", "role", "phone", "email", "memo"]


def list_for(customer_id: int, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM customer_contacts WHERE customer_id=?"
    if not include_inactive:
        sql += " AND active = 1"
    return db._df(sql + " ORDER BY is_primary DESC, id", [int(customer_id)]).to_dict("records")


def save(customer_id: int, data: dict, contact_id: Optional[int] = None) -> int:
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    record = {f: (str(data.get(f) or "").strip() or None) for f in FIELDS}
    if not record["name"]:
        raise ValueError("담당자 이름을 입력하세요.")
    if record["role"] and record["role"] not in ROLES:
        record["role"] = "기타"
    if record["email"] and "@" not in record["email"]:
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    primary = 1 if data.get("is_primary") in (1, "1", True, "on") else 0
    with db.get_conn() as conn:
        if contact_id:
            row = conn.execute("SELECT * FROM customer_contacts WHERE id=? AND customer_id=?",
                               (int(contact_id), int(customer_id))).fetchone()
            if not row:
                raise ValueError("담당자를 찾을 수 없습니다.")
            conn.execute(f"UPDATE customer_contacts SET {', '.join(f'{f}=?' for f in FIELDS)}, is_primary=?, "
                         "active=1, updated_at=? WHERE id=?", (*record.values(), primary, db._now(), int(contact_id)))
            cid = int(contact_id)
        else:
            first = conn.execute("SELECT COUNT(*) FROM customer_contacts WHERE customer_id=? AND active=1",
                                 (int(customer_id),)).fetchone()[0] == 0
            primary = 1 if first else primary
            cur = conn.execute(f"INSERT INTO customer_contacts (customer_id, {', '.join(FIELDS)}, is_primary, created_at, "
                               f"updated_at) VALUES (?, {', '.join('?' * len(FIELDS))}, ?, ?, ?)",
                               (int(customer_id), *record.values(), primary, db._now(), db._now()))
            cid = int(cur.lastrowid)
        if primary:
            conn.execute("UPDATE customer_contacts SET is_primary=0 WHERE customer_id=? AND id<>?", (int(customer_id), cid))
    _sync_primary(int(customer_id))
    db.audit("담당자저장", "거래처", int(customer_id), {"담당자": record["name"], "역할": record["role"], "대표": bool(primary)})
    return cid


def deactivate(customer_id: int, contact_id: int) -> None:
    cust = db.get_customer(int(customer_id))
    db.check_record_scope(cust, "거래처")
    with db.get_conn() as conn:
        conn.execute("UPDATE customer_contacts SET active=0, is_primary=0, updated_at=? WHERE id=? AND customer_id=?",
                     (db._now(), int(contact_id), int(customer_id)))
        nxt = conn.execute("SELECT id FROM customer_contacts WHERE customer_id=? AND active=1 ORDER BY id LIMIT 1",
                           (int(customer_id),)).fetchone()
        if nxt and not conn.execute("SELECT 1 FROM customer_contacts WHERE customer_id=? AND active=1 AND is_primary=1",
                                    (int(customer_id),)).fetchone():
            conn.execute("UPDATE customer_contacts SET is_primary=1 WHERE id=?", (nxt["id"],))
    _sync_primary(int(customer_id))
    db.audit("담당자사용중지", "거래처", int(customer_id), {"담당자ID": contact_id})


def _sync_primary(customer_id: int) -> None:
    """대표 담당자 → customers.manager·phone·email (거래처 수정 화면의 버전 충돌을 피하려 row_version 은 올리지 않음)."""
    p = db._one("SELECT name, phone, email FROM customer_contacts WHERE customer_id=? AND active=1 AND is_primary=1",
                [customer_id])
    with db.get_conn() as conn:
        conn.execute("UPDATE customers SET manager=?, phone=?, email=? WHERE id=?",
                     ((p or {}).get("name"), (p or {}).get("phone"), (p or {}).get("email"), customer_id))


def sync_from_customer(customer_id: int, manager: Optional[str], phone: Optional[str], email: Optional[str]) -> None:
    """거래처 화면·업로드에서 고객 담당자를 고치면 대표 담당자에 반영한다."""
    if not manager:
        return
    with db.get_conn() as conn:
        p = conn.execute("SELECT id FROM customer_contacts WHERE customer_id=? AND active=1 AND is_primary=1",
                         (customer_id,)).fetchone()
        if p:
            conn.execute("UPDATE customer_contacts SET name=?, phone=?, email=?, updated_at=? WHERE id=?",
                         (manager, phone, email, db._now(), p["id"]))
        else:
            conn.execute("INSERT INTO customer_contacts (customer_id, name, phone, email, role, is_primary, created_at, "
                         "updated_at) VALUES (?,?,?,?, '대표', 1, ?, ?)", (customer_id, manager, phone, email, db._now(),
                                                                       db._now()))


def table(customer_id: int) -> pd.DataFrame:
    return pd.DataFrame(list_for(customer_id))

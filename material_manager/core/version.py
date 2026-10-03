"""동시 수정 확인: 화면을 연 뒤 다른 사람이 같은 항목을 먼저 바꿨으면 저장하지 않는다.

화면은 항목의 '판'(주요 값의 해시)을 숨은 값(_ver)으로 함께 보낸다. 저장할 때 같은 트랜잭션에서 지금 값의 판을 다시 계산해
다르면 거부한다 → 나중 저장이 먼저 저장을 조용히 덮어쓰지 않는다. (자재는 updated_at으로 같은 일을 한다.)
판을 보내지 않는 요청(스크립트·테스트)은 확인하지 않는다.
"""

import hashlib
import json

FIELDS = {
    "plants": ("name", "sap_plant", "active"),
    "warehouses": ("name", "sap_sloc", "active"),
    "users": ("name", "role", "active"),
    "purchase_orders": ("sap_po_no", "status"),
}
STALE = "화면을 연 뒤 다른 사용자가 이 항목을 먼저 바꿨습니다. 화면을 새로고침해 최신 내용을 확인한 뒤 다시 저장하세요."


def _norm(v):
    if v is None or (isinstance(v, float) and v != v):          # None · NaN
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if hasattr(v, "item"):                                       # numpy 숫자
        return _norm(v.item())
    return v


def _digest(values) -> str:
    return hashlib.sha1(json.dumps(values, ensure_ascii=False, default=str).encode(),
                        usedforsecurity=False).hexdigest()[:16]


def of_row(table: str, row) -> str:
    return _digest([_norm(row[f]) for f in FIELDS[table]])


def of_scope(all_warehouses, plants, warehouses) -> str:
    return _digest([_norm(all_warehouses), sorted(int(p) for p in plants), sorted(int(w) for w in warehouses)])


def scope_now(conn, user_id: int) -> str:
    row = conn.execute("SELECT all_warehouses FROM users WHERE id = ?", (user_id,)).fetchone()
    rows = conn.execute("SELECT plant_id, warehouse_id FROM user_scopes WHERE user_id = ?", (user_id,)).fetchall()
    return of_scope(row["all_warehouses"] if row else 0,
                    [r["plant_id"] for r in rows if r["plant_id"] is not None],
                    [r["warehouse_id"] for r in rows if r["warehouse_id"] is not None])


def stale(table: str, row, expected: str | None) -> bool:
    return bool(expected) and of_row(table, row) != expected

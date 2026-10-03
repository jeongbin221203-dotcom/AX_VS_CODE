"""단위 환산: 자재마다 기본 단위(재고 단위, materials.unit) 외에 BOX·PALLET 같은 다른 단위를 둔다.

- 1 다른 단위 = factor × 기본 단위 (예: BOX = 100 EA). 재고·거래 수량은 늘 기본 단위로 저장하고,
  입력한 단위와 수량은 거래에 함께 남긴다(transactions.entry_unit · entry_qty).
- 단위마다 바코드를 둘 수 있다(상자 바코드) → 상자를 찍으면 그 단위 1개(=factor 개)로 들어온다.
  바코드는 자재 바코드·자재코드·SAP 번호·다른 단위 바코드와 겹칠 수 없다.
- 단가는 늘 기본 단위당이다.
"""
from __future__ import annotations

import re

import pandas as pd

from core import audit, db
from core.utils import now_str

UNIT_RE = re.compile(r"[A-Za-z가-힣0-9._-]{1,12}")


def units(material_id: int, conn=None) -> list[dict]:
    sql = "SELECT id, unit, factor, barcode FROM material_units WHERE material_id = ? ORDER BY factor"
    if conn is not None:
        return [dict(r) for r in conn.execute(sql, (material_id,))]
    return db.query_df(sql, (material_id,)).to_dict("records")


def all_units() -> dict[int, list[list]]:
    """lookup.json 용: 자재 id → [[단위, 배수, 바코드], ...]"""
    out: dict[int, list[list]] = {}
    for r in db.query_df("SELECT material_id, unit, factor, barcode FROM material_units ORDER BY factor").itertuples():
        out.setdefault(int(r.material_id), []).append([r.unit, float(r.factor), r.barcode or ""])
    return out


def factor(conn, material_id: int, unit: str, base_unit: str) -> tuple[float, str]:
    """(기본 단위 배수, 문제). 비우거나 기본 단위면 1."""
    unit = (unit or "").strip()
    if not unit or unit.upper() == (base_unit or "").upper():
        return 1.0, ""
    row = conn.execute("SELECT factor FROM material_units WHERE material_id = ? AND UPPER(unit) = ?",
                       (material_id, unit.upper())).fetchone()
    if row is None:
        return 1.0, f"이 자재에 '{unit}' 단위가 없습니다 (자재 마스터 → 단위 환산에서 추가)."
    return float(row[0]), ""


def add(material_id: int, unit: str, factor_: float, barcode: str, actor: dict | None) -> tuple[bool, str]:
    from core import repository as repo, services
    unit, barcode = unit.strip().upper(), barcode.replace(" ", "").strip().upper()
    if not UNIT_RE.fullmatch(unit):
        return False, "단위는 영문·한글·숫자 12자 이내로 입력하세요 (예: BOX)."
    if not services._finite(factor_) or factor_ <= 0:
        return False, "배수는 0보다 커야 합니다."
    with db.transaction() as conn:
        mat = repo.get_material(material_id, conn)
        if mat is None:
            return False, "자재가 없습니다."
        if unit == (mat["unit"] or "").upper():
            return False, f"{unit}은 기본 단위입니다."
        if barcode:
            problem = services.barcode_problem(conn, barcode, material_id)
            if not problem and conn.execute("SELECT 1 FROM material_units WHERE barcode = ?", (barcode,)).fetchone():
                problem = f"바코드 {barcode}는 다른 단위에 이미 있습니다."
            if not problem and barcode in {(mat["barcode"] or "").upper(), mat["code"].upper(), (mat["sap_matnr"] or "").upper()}:
                problem = "이 자재의 바코드·코드·SAP 번호와 같습니다 (그것들은 기본 단위 1개로 스캔됩니다)."
            if problem:
                return False, problem
        if conn.execute("SELECT 1 FROM material_units WHERE material_id = ? AND unit = ?", (material_id, unit)).fetchone():
            return False, f"{unit} 단위가 이미 있습니다. 지우고 다시 넣으세요."
        conn.execute("INSERT INTO material_units (material_id, unit, factor, barcode, created_at) VALUES (?, ?, ?, ?, ?)",
                     (material_id, unit, factor_, barcode, now_str()))
        audit.record(conn, actor, "UNIT_ADD", "material", material_id,
                     {"code": mat["code"], "unit": unit, "factor": factor_, "barcode": barcode})
    return True, f"단위 추가: 1 {unit} = {factor_:g} {mat['unit']}"


def remove(unit_id: int, actor: dict | None) -> tuple[bool, str, int]:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM material_units WHERE id = ?", (unit_id,)).fetchone()
        if row is None:
            return False, "단위가 없습니다.", 0
        conn.execute("DELETE FROM material_units WHERE id = ?", (unit_id,))
        audit.record(conn, actor, "UNIT_REMOVE", "material", row["material_id"], {"unit": row["unit"]})
    return True, f"{row['unit']} 단위를 지웠습니다 (지난 거래의 입력 단위 기록은 그대로).", int(row["material_id"])


def barcode_owner(conn, barcode: str) -> str:
    """다른 단위 바코드와 겹치는지 (자재 바코드 검사에서 함께 쓴다)."""
    row = conn.execute("SELECT m.code, u.unit FROM material_units u JOIN materials m ON m.id = u.material_id "
                       "WHERE u.barcode = ?", (barcode,)).fetchone()
    return f"{row['code']} {row['unit']}" if row else ""


def describe(qty: float, unit: str, f: float, base_unit: str) -> str:
    return f"{qty:,.4g} {unit}(={qty * f:,.4g} {base_unit})" if unit and f != 1 else ""


def units_df(material_id: int) -> pd.DataFrame:
    return db.query_df("SELECT id, unit, factor, barcode, created_at FROM material_units WHERE material_id = ? ORDER BY factor",
                       (material_id,))

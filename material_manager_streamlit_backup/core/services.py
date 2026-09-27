"""업무 규칙 계층.

화면(Streamlit)과 무관하게 '무엇이 허용되는가'를 판정한다.
따라서 이 파일의 규칙은 단위 테스트로 그대로 검증 가능하다.
"""

from dataclasses import dataclass, field

import pandas as pd

import config
from core import db, repository as repo
from core.utils import clean_str_series


@dataclass
class Result:
    ok: bool
    message: str
    qty: float = 0.0
    stock_after: float = 0.0
    warning: str = ""


@dataclass
class UploadResult:
    df: pd.DataFrame
    dropped: int = 0
    duplicated: int = 0
    errors: list[str] = field(default_factory=list)


def register_transaction(material_id: int, tx_type: str, qty_input: float, tx_date: str,
                         unit_price: float, ref_no: str = "", partner: str = "",
                         note: str = "", created_by: str = "") -> Result:
    """입고 / 출고 / 실사조정 등록.

    - 출고는 등록 직전 재고를 다시 읽어 초과 출고를 차단한다(동시 등록 방어).
    - 조정(ADJ)은 '실사수량'을 받아 현재고와의 차이만 기록한다.
    """
    if tx_type not in config.TX_LABEL:
        return Result(False, f"알 수 없는 거래 유형: {tx_type}")

    with db.transaction() as conn:
        stock_now = repo.current_stock(conn, material_id)

        if tx_type == "ADJ":
            if qty_input < 0:
                return Result(False, "실사수량은 0 이상이어야 합니다.")
            qty = qty_input - stock_now
            if qty == 0:
                return Result(False, "실사수량이 현재고와 같아 조정할 내용이 없습니다.")
        else:
            qty = qty_input
            if qty <= 0:
                return Result(False, "수량은 0보다 커야 합니다.")
            if tx_type == "OUT" and qty > stock_now:
                return Result(False, f"재고 부족: 현재고 {stock_now:,.2f}, 출고 요청 {qty:,.2f}")

        repo.insert_transaction(conn, {
            "material_id": material_id, "tx_type": tx_type, "qty": qty,
            "unit_price": max(float(unit_price), 0.0), "tx_date": tx_date,
            "ref_no": ref_no.strip(), "partner": partner.strip(),
            "note": note.strip(), "created_by": created_by.strip(),
        })
        stock_after = repo.current_stock(conn, material_id)

    mat = repo.get_material(material_id) or {}
    unit = mat.get("unit", "")
    safety = float(mat.get("safety_stock", 0) or 0)
    sign = f"{qty:+,.2f}" if tx_type == "ADJ" else f"{qty:,.2f}"
    warning = (f"안전재고({safety:,.2f}) 미달입니다. 발주를 검토하세요."
               if stock_after < safety else "")
    return Result(
        True,
        f"{config.TX_LABEL[tx_type]} {sign} {unit} 등록 → 현재고 {stock_after:,.2f} {unit}",
        qty=qty, stock_after=stock_after, warning=warning,
    )


def delete_transaction(tx_id: int) -> Result:
    """거래 삭제. 삭제 후 재고가 음수가 되면 거부한다."""
    with db.transaction() as conn:
        tx = repo.get_transaction(conn, tx_id)
        if tx is None:
            return Result(False, "이미 삭제된 거래입니다.")
        effect = tx["qty"] if tx["tx_type"] in ("IN", "ADJ") else -tx["qty"]
        after = repo.current_stock(conn, tx["material_id"]) - effect
        if after < 0:
            return Result(False, f"삭제 시 재고가 {after:,.2f}로 음수가 됩니다. "
                                 "이후 출고 건을 먼저 정리하세요.")
        repo.delete_transaction(conn, tx_id)
    return Result(True, f"거래 ID {tx_id} 삭제 완료", stock_after=after)


def normalize_upload(raw: pd.DataFrame) -> UploadResult:
    """업로드 엑셀/CSV를 자재 마스터 형식으로 정제한다."""
    reverse = {v: k for k, v in config.MATERIAL_COLS.items()}
    df = raw.rename(columns=lambda c: reverse.get(str(c).strip(), str(c).strip()))

    missing = [config.MATERIAL_COLS[c] for c in ("code", "name") if c not in df.columns]
    if missing:
        return UploadResult(pd.DataFrame(), errors=[f"필수 컬럼 누락: {', '.join(missing)}"])

    for col in config.MATERIAL_COLS:
        if col not in df.columns:
            df[col] = None
    df = df[list(config.MATERIAL_COLS)]

    raw_rows = len(df)
    df["code"] = clean_str_series(df["code"]).str.upper()
    df["name"] = clean_str_series(df["name"])
    df = df[(df["code"] != "") & (df["name"] != "")]
    dropped = raw_rows - len(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset="code", keep="last")
    duplicated = before_dedup - len(df)

    for col in ("safety_stock", "unit_price"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).clip(lower=0)
    for col in ("spec", "location", "supplier"):
        df[col] = clean_str_series(df[col])
    df["unit"] = clean_str_series(df["unit"], default=config.DEFAULT_UNIT)
    df["category"] = clean_str_series(df["category"], default=config.DEFAULT_CATEGORY)

    return UploadResult(df.reset_index(drop=True), dropped=dropped, duplicated=duplicated)


def stock_display(df: pd.DataFrame) -> pd.DataFrame:
    """재고 DataFrame을 화면/엑셀용 한글 컬럼으로 변환."""
    out = df[["code", "name", "spec", "unit", "category", "stock", "safety_stock",
              "shortage_qty", "unit_price", "stock_value", "location", "supplier"]].copy()
    out.columns = ["자재코드", "자재명", "규격", "단위", "분류", "현재고", "안전재고",
                   "부족수량", "단가", "재고금액", "보관위치", "공급처"]
    return out


def material_options(active_only: bool = True) -> dict[int, str]:
    df = repo.list_materials(active_only=active_only)
    return {
        int(r.id): f"[{r.code}] {r.name}" + (f" ({r.spec})" if r.spec else "")
        for r in df.itertuples()
    }

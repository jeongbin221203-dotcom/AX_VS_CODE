"""거래명세서로 입출고 등록.

공급처가 보낸 거래명세서(입고) 또는 납품처에 보낸 거래명세서(출고) 한 장을 받아, 품목 줄마다 입고·출고 거래를 만든다.

  1) 줄 읽기   회사 엑셀/CSV(엑셀 양식 '거래명세서 품목'으로 열 이름·머리글 행을 맞춤) 또는 화면에 직접 입력.
               합계·소계 줄과 빈 줄은 건너뛴다.
  2) 확인      자재 찾기(자재코드 → SAP 자재번호 → 품명[+규격]), 수량·단가·공급가액(수량×단가, 원 미만 절사),
               세액(10%, 다르면 경고 — 면세·영세는 0), 같은 명세서 번호 중복.
  3) 등록      모든 줄을 한 트랜잭션으로 등록한다. 한 줄이라도 거부되면(재고 부족·마감·권한·발주 잔량 등)
               아무것도 등록하지 않는다. 판정은 입출고 화면과 같은 규칙(services._register)을 쓴다.
               명세서 이미지·PDF를 붙이면 '거래명세서' 증빙으로 저장해 첫 거래에 연결한다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import pandas as pd

import config
from core import audit, db, documents, org, repository as repo, services
from core.utils import clean_str_series, code_series, now_str

# 엑셀 열: 항목 → 표준 머리글 (별칭은 excel_forms.IMPORT_FORMS['statement_lines'])
LINE_COLS = {"code": "자재코드", "name": "품명", "spec": "규격", "qty": "수량", "unit_price": "단가",
             "supply": "공급가액", "tax": "세액", "lot_no": "로트", "expiry_date": "유효기한",
             "po_no": "발주번호", "po_item": "발주품목", "note": "비고"}
SKIP_RE = re.compile(r"^\s*(합\s*계|소\s*계|총\s*계|계|total|subtotal)\s*$", re.IGNORECASE)
MAX_LINES = 200
VAT = 0.1


@dataclass
class Line:
    no: int
    code: str = ""
    name: str = ""
    spec: str = ""
    qty: float = 0.0
    unit_price: float = 0.0
    supply: float | None = None
    tax: float | None = None
    lot_no: str = ""
    expiry_date: str = ""
    po_no: str = ""
    po_item: str = ""
    note: str = ""
    material_id: int | None = None
    material_label: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).replace(",", "").replace("₩", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return float("nan")


def lines_from_frame(df: pd.DataFrame) -> tuple[list[Line], list[str]]:
    """표준 머리글로 바뀐 DataFrame → 줄. (줄, 문제)"""
    reverse = {v: k for k, v in LINE_COLS.items()}
    df = df.rename(columns=lambda c: reverse.get(str(c).strip(), str(c).strip()))
    if "qty" not in df.columns or not ({"code", "name"} & set(df.columns)):
        return [], [f"필수 열이 없습니다: 수량, 그리고 자재코드나 품명 (엑셀 양식 '거래명세서 품목'에서 열 이름을 맞출 수 있습니다)"]
    for col in LINE_COLS:
        if col not in df.columns:
            df[col] = None
    texts = {c: clean_str_series(df[c]) for c in ("name", "spec", "lot_no", "expiry_date", "po_no", "note")}
    codes = code_series(df["code"]).str.upper()
    po_items = code_series(df["po_item"])
    out: list[Line] = []
    for i in range(len(df)):
        name = texts["name"].iloc[i]
        code = codes.iloc[i]
        qty = _num(df["qty"].iloc[i])
        if not code and not name and qty is None:
            continue                                            # 빈 줄
        if SKIP_RE.match(name) or SKIP_RE.match(code):
            continue                                            # 합계·소계 줄
        exp = texts["expiry_date"].iloc[i]
        if exp:
            exp = str(pd.to_datetime(exp, errors="coerce").date()) if pd.notna(pd.to_datetime(exp, errors="coerce")) else exp
        out.append(Line(no=len(out) + 1, code=code, name=name, spec=texts["spec"].iloc[i],
                        qty=qty if qty is not None else float("nan"),
                        unit_price=_num(df["unit_price"].iloc[i]) if _num(df["unit_price"].iloc[i]) is not None else float("nan"),
                        supply=_num(df["supply"].iloc[i]), tax=_num(df["tax"].iloc[i]),
                        lot_no=texts["lot_no"].iloc[i].upper(), expiry_date=exp,
                        po_no=texts["po_no"].iloc[i], po_item=po_items.iloc[i], note=texts["note"].iloc[i]))
    if len(out) > MAX_LINES:
        return [], [f"명세서 한 장은 {MAX_LINES}줄까지 등록할 수 있습니다. 나눠서 올려 주세요."]
    return out, []


def _match(conn, line: Line) -> tuple[int | None, str]:
    """(자재 id, 문제). 자재코드 → SAP 자재번호 → 품명(+규격) 순서. 이름이 같은 자재가 여럿이면 고르게 한다."""
    if line.code:
        for col in ("code", "sap_matnr"):
            row = conn.execute(f"SELECT id FROM materials WHERE UPPER({col}) = ? AND active = 1", (line.code,)).fetchone()
            if row:
                return int(row["id"]), ""
    if line.name:
        rows = conn.execute("SELECT id, spec FROM materials WHERE active = 1 AND name = ?", (line.name,)).fetchall()
        if line.spec and len(rows) > 1:
            rows = [r for r in rows if (r["spec"] or "").strip() == line.spec]
        if len(rows) == 1:
            return int(rows[0]["id"]), ""
        if len(rows) > 1:
            return None, f"품명이 같은 자재가 {len(rows)}개입니다. 자재를 골라 주세요."
    return None, f"자재를 찾지 못했습니다 ({line.code or line.name}). 자재를 고르거나 자재 마스터에 먼저 등록하세요."


def check(kind: str, lines: list[Line], overrides: dict[int, int] | None = None) -> list[Line]:
    """줄마다 자재를 찾고 수량·금액을 확인한다. overrides: {줄 번호: 자재 id} (화면에서 고른 자재)."""
    overrides = overrides or {}
    with db.get_conn() as conn:
        for ln in lines:
            ln.errors, ln.warnings = [], []
            if ln.no in overrides:
                ln.material_id = overrides[ln.no]
                mat = repo.get_material(ln.material_id, conn)
                if mat is None or not mat["active"]:
                    ln.material_id = None
                    ln.errors.append("사용 중인 자재가 아닙니다.")
            else:
                ln.material_id, problem = _match(conn, ln)
                if problem:
                    ln.errors.append(problem)
            if ln.material_id:
                mat = repo.get_material(ln.material_id, conn)
                ln.material_label = f"[{mat['code']}] {mat['name']}"
                if mat["lot_managed"] and kind == "IN" and not ln.lot_no:
                    ln.errors.append("로트 관리 자재입니다. 로트 열을 채워 주세요.")
            if math.isnan(ln.qty):
                ln.errors.append("수량이 숫자가 아닙니다 (단위·글자는 빼고 숫자만).")
                continue
            if ln.qty <= 0:
                ln.errors.append("수량은 0보다 커야 합니다.")
                continue
            if math.isnan(ln.unit_price):
                if ln.supply is not None and not math.isnan(ln.supply):
                    ln.unit_price = round(ln.supply / ln.qty, 4)          # 단가 없이 금액만 적은 명세서
                    ln.warnings.append(f"단가가 없어 공급가액 ÷ 수량으로 계산했습니다 ({ln.unit_price:,.2f}).")
                else:
                    ln.errors.append("단가나 공급가액이 필요합니다.")
                    continue
            if ln.unit_price < 0:
                ln.errors.append("단가는 0 이상이어야 합니다.")
            expected = math.floor(ln.qty * ln.unit_price)
            if ln.supply is not None and not math.isnan(ln.supply):
                if abs(ln.supply - expected) > max(1.0, expected * 0.001):
                    ln.errors.append(f"공급가액 {ln.supply:,.0f} ≠ 수량×단가 {expected:,.0f}")
            else:
                ln.supply = float(expected)
            if ln.tax is not None and not math.isnan(ln.tax) and ln.tax != 0:
                vat = math.floor(ln.supply * VAT)
                if abs(ln.tax - vat) > max(1.0, vat * 0.01):
                    ln.warnings.append(f"세액 {ln.tax:,.0f}이 공급가액의 10%({vat:,.0f})와 다릅니다.")
            if ln.tax is not None and math.isnan(ln.tax):
                ln.errors.append("세액이 숫자가 아닙니다.")
    return lines


def duplicate_of(kind: str, partner: str, statement_no: str) -> int | None:
    if not statement_no:
        return None
    v = db.scalar("SELECT id FROM statements WHERE kind = ? AND partner = ? AND statement_no = ?",
                  (kind, norm_partner(partner), statement_no.strip()))
    return int(v) if v else None


def similar_of(kind: str, partner: str, tx_date: str, supply: float, line_count: int) -> int | None:
    """명세서 번호 없이 올린 명세서가 이미 있는 것 같은지 (같은 거래처·일자·공급가액·줄 수). 막지는 않고 경고만."""
    v = db.scalar("SELECT id FROM statements WHERE kind = ? AND partner = ? AND tx_date = ? AND line_count = ? "
                  "AND ABS(supply_amount - ?) < 1 ORDER BY id DESC LIMIT 1",
                  (kind, norm_partner(partner), tx_date, line_count, supply))
    return int(v) if v else None


def material_ids_by_code(codes: list[str]) -> dict[str, int]:
    """화면에서 입력한 자재코드 → id (사용 중인 자재만)."""
    codes = sorted({c.strip().upper() for c in codes if c and c.strip()})
    if not codes:
        return {}
    frag, wp = db.in_clause(codes)
    df = db.query_df(f"SELECT id, code FROM materials WHERE active = 1 AND code{frag}", wp)
    return {str(c): int(i) for i, c in zip(df["id"], df["code"])}


def norm_partner(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


@dataclass
class Registered:
    ok: bool
    message: str
    statement_id: int = 0
    failed_line: int = 0


class _LineRejected(Exception):
    def __init__(self, no: int, message: str):
        super().__init__(message)
        self.no, self.message = no, message


def register(header: dict, lines: list[Line], *, actor: dict | None, wh_ids=None,
             evidence: documents.Prepared | None = None) -> Registered:
    """확인을 통과한 줄을 한 트랜잭션으로 등록한다. header: kind, warehouse_id, tx_date, partner, partner_biz_no,
    statement_no, cost_center, note."""
    kind = header["kind"]
    if kind not in ("IN", "OUT"):
        return Registered(False, "입고 또는 출고를 고르세요.")
    partner = norm_partner(header.get("partner", ""))
    if not partner:
        return Registered(False, "공급처(입고) 또는 납품처(출고)를 입력하세요.")
    if not lines:
        return Registered(False, "등록할 품목 줄이 없습니다.")
    bad = [ln for ln in lines if ln.errors]
    if bad:
        return Registered(False, f"{bad[0].no}번 줄: {bad[0].errors[0]}", failed_line=bad[0].no)
    st_no = (header.get("statement_no") or "").strip()
    dup = duplicate_of(kind, partner, st_no)
    if dup:
        return Registered(False, f"이미 등록한 거래명세서입니다 (명세서 #{dup}, {partner} · {st_no}).")
    who = services._actor(actor, "")
    wh_id = int(header["warehouse_id"])
    supply = sum(float(ln.supply or 0) for ln in lines)
    tax = sum(float(ln.tax or 0) for ln in lines if ln.tax is not None and not math.isnan(ln.tax))
    first_tx = 0
    try:
        with db.transaction() as conn:
            st_id = conn.execute(
                "INSERT INTO statements (kind, statement_no, partner, partner_biz_no, warehouse_id, tx_date, supply_amount, "
                "tax_amount, line_count, note, created_by_id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, st_no, partner, (header.get("partner_biz_no") or "").strip(), wh_id, header["tx_date"], supply, tax,
                 len(lines), (header.get("note") or "").strip(), who.get("id"), who["name"], now_str())).lastrowid
            for ln in lines:
                out = services._register(
                    conn, who, ln.material_id, kind, float(ln.qty), header["tx_date"], float(ln.unit_price),
                    st_no or f"STMT-{st_id}", partner, (ln.note or header.get("note") or "").strip(),
                    ln.po_no, ln.po_item, header.get("cost_center", "") if kind == "OUT" else "",
                    wh_id, wh_ids, ln.lot_no, ln.expiry_date, statement_id=st_id)
                if isinstance(out, services.Result):            # 거부 → 전체 되돌림
                    raise _LineRejected(ln.no, out.message)
                first_tx = first_tx or out["tx_ids"][0]
            audit.record(conn, who, "STATEMENT_CREATE", "statement", st_id,
                         {"kind": kind, "partner": partner, "statement_no": st_no, "lines": len(lines),
                          "supply": supply, "tax": tax, "warehouse_id": wh_id})
    except _LineRejected as exc:
        return Registered(False, f"{exc.no}번 줄이 거부되어 아무것도 등록하지 않았습니다: {exc.message}", failed_line=exc.no)
    except db.IntegrityError:
        return Registered(False, f"이미 등록한 거래명세서입니다 ({partner} · {st_no}).")

    msg = f"거래명세서 #{st_id} 등록 — {config.TX_LABEL[kind]} {len(lines)}줄, 공급가액 ₩{supply:,.0f}"
    if evidence is not None:
        saved = documents.save(evidence, tx_id=first_tx, actor=actor)
        if saved.ok:
            with db.transaction() as conn:
                conn.execute("UPDATE statements SET doc_id = ? WHERE id = ?", (saved.doc_id, st_id))
            msg += " · 명세서 파일 첨부"
        else:
            msg += f" · 명세서 파일은 저장하지 못했습니다({saved.message}). 증빙 화면에서 거래 #{first_tx}에 다시 올려 주세요."
    return Registered(True, msg, statement_id=st_id)


def list_df(wh_ids=None, limit: int = 200) -> pd.DataFrame:
    frag, wp = db.in_clause(wh_ids)
    return db.query_df(f"""
        SELECT s.id, s.tx_date, s.kind, s.partner, s.statement_no, w.code AS wh_code, s.line_count,
               s.supply_amount, s.tax_amount, s.doc_id, s.created_by, s.created_at
        FROM statements s JOIN warehouses w ON w.id = s.warehouse_id
        {'WHERE s.warehouse_id' + frag if frag else ''}
        ORDER BY s.id DESC LIMIT ?""", (*wp, limit))


def get(statement_id: int) -> dict | None:
    df = db.query_df("SELECT s.*, w.code AS wh_code, w.name AS wh_name FROM statements s "
                     "JOIN warehouses w ON w.id = s.warehouse_id WHERE s.id = ?", (statement_id,))
    return None if df.empty else df.iloc[0].to_dict()


def lines_df(statement_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT t.id, m.code, m.name, m.unit, t.lot_no, t.qty, t.unit_price, t.qty * t.unit_price AS amount,
               t.po_no, t.po_item, t.reversal_of,
               (SELECT r.id FROM transactions r WHERE r.reversal_of = t.id) AS reversed_by
        FROM transactions t JOIN materials m ON m.id = t.material_id
        WHERE t.statement_id = ? AND t.reversal_of IS NULL ORDER BY t.id""", (statement_id,))


def warehouse_ok(statement: dict, wh_ids) -> bool:
    return wh_ids is None or int(statement["warehouse_id"]) in wh_ids

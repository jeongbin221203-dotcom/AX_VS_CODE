"""매출 증빙 — 세금계산서 · 전자세금계산서 · 거래명세서 등록 (이미지/PDF/XML)

원칙
  * 파일 종류는 확장자가 아니라 실제 내용(매직 바이트)으로 판별한다 (위장 파일·스크립트 차단)
  * 원본은 공용 저장소(core.storage — NAS 폴더 또는 S3)의 documents/YYYY/MM/ 에 무작위 이름으로
    저장하고 SHA-256 을 기록한다 (위변조 확인, 서버 여러 대가 같은 파일을 본다)
  * 전자세금계산서 표준 XML 을 올리면 승인번호·일자·금액·사업자번호를 자동으로 읽는다
  * 사업자등록번호 검증번호, 승인번호 24자리, 공급가액+세액=합계, 매출 금액과의 일치,
    발급 기한(공급일이 속한 달의 다음 달 10일)을 확인한다
  * 증빙은 지우지 않는다. 잘못 올린 것은 사유를 남기고 '무효' 처리한다
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime
from typing import Any, Optional

from . import sales_db as db
from .storage import get_storage

COMPANY_BIZ_NO = re.sub(r"\D", "", os.environ.get("SALES_COMPANY_BIZ_NO", ""))   # 우리 회사(공급자)

DOC_TYPES = ["전자세금계산서", "세금계산서", "수정세금계산서", "전자계산서", "계산서", "수정계산서", "거래명세서", "기타"]
EXEMPT_INVOICE_TYPES = {"전자계산서", "계산서", "수정계산서"}          # 면세 매출 (부가세 없음 — 소득세법·법인세법상 계산서)
TAX_INVOICE_TYPES = {"전자세금계산서", "세금계산서", "수정세금계산서"} | EXEMPT_INVOICE_TYPES
ISSUED_INVOICE_TYPES = ("전자세금계산서", "세금계산서", "전자계산서", "계산서")        # 당초 발행분 (수정분 제외)
MAX_BYTES = 10 * 1024 * 1024

# 매직 바이트 → (MIME, 저장 확장자). SVG·HTML 처럼 스크립트를 품을 수 있는 형식은 받지 않는다
SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"%PDF-", "application/pdf", ".pdf"),
]
IMAGE_TYPES = {"image/jpeg", "image/png"}


# ---------------------------------------------------------------------------
# 판별 · 검증 유틸
# ---------------------------------------------------------------------------
def detect_type(data: bytes) -> tuple[str, str]:
    if not data:
        raise ValueError("빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"파일이 너무 큽니다 (최대 {MAX_BYTES // 1024 // 1024}MB).")
    for magic, mime, ext in SIGNATURES:
        if data.startswith(magic):
            if mime in IMAGE_TYPES:
                _verify_image(data)
            return mime, ext
    head = data[:200].lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<"):
        if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data[:2000].upper():
            raise ValueError("DTD·ENTITY 가 포함된 XML 은 받을 수 없습니다.")
        return "application/xml", ".xml"
    raise ValueError("지원하지 않는 파일입니다. JPG · PNG · PDF · 전자세금계산서 XML 만 올릴 수 있습니다.")


def _verify_image(data: bytes) -> None:
    """손상·위장 이미지와 압축 폭탄(거대한 해상도)을 걸러 낸다."""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:          # Pillow 가 없으면 매직 바이트 검사만 한다
        return
    try:
        with Image.open(BytesIO(data)) as img:
            if img.width * img.height > 60_000_000:
                raise ValueError("이미지 해상도가 너무 큽니다.")
            img.verify()
    except ValueError:
        raise
    except Exception as exc:     # noqa: BLE001 - Pillow 는 손상 유형별로 다양한 예외를 던진다
        raise ValueError("이미지 파일이 손상되었거나 올바른 이미지가 아닙니다.") from exc


def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def valid_biz_no(value: Any) -> bool:
    """사업자등록번호 10자리 검증번호 확인 (국세청 가중치 1,3,7,1,3,7,1,3,5)."""
    d = [int(c) for c in digits(value)]
    if len(d) != 10:
        return False
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    total = sum(a * w for a, w in zip(d[:9], weights)) + (d[8] * 5) // 10
    return (10 - total % 10) % 10 == d[9]


def format_biz_no(value: Any) -> str:
    d = digits(value)
    return f"{d[:3]}-{d[3:5]}-{d[5:]}" if len(d) == 10 else str(value or "")


def format_approval_no(value: Any) -> str:
    d = digits(value)
    return f"{d[:8]}-{d[8:16]}-{d[16:]}" if len(d) == 24 else str(value or "")


def _date(value: Any) -> Optional[str]:
    d = digits(value)[:8]
    if len(d) != 8:
        return None
    try:
        return datetime.strptime(d, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _int(value: Any) -> Optional[int]:
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError as exc:
        raise ValueError(f"금액이 숫자가 아닙니다 ('{value}')") from exc


# ---------------------------------------------------------------------------
# 전자세금계산서 표준 XML 읽기
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_etax_xml(data: bytes) -> dict:
    """전자세금계산서 표준 XML(국세청 KEC 표준)에서 주요 항목을 읽는다.

    읽지 못한 항목은 비워 두고, 사용자가 화면에서 확인·보완한다.
    """
    from defusedxml import ElementTree as SafeET   # 외부 엔티티·엔티티 폭탄 차단

    try:
        root = SafeET.fromstring(data)
    except Exception as exc:   # noqa: BLE001
        raise ValueError(f"XML 을 읽지 못했습니다: {exc}") from exc

    def text_under(parent_name: str, child_name: str) -> Optional[str]:
        for parent in root.iter():
            if _local(parent.tag) == parent_name:
                for child in parent.iter():
                    if child is not parent and _local(child.tag) == child_name and (child.text or "").strip():
                        return child.text.strip()
        return None

    found = {
        "approval_no": text_under("TaxInvoiceDocument", "IssueID"),
        "issue_date": _date(text_under("TaxInvoiceDocument", "IssueDateTime")),
        "supplier_biz_no": text_under("InvoicerParty", "ID"),
        "buyer_biz_no": text_under("InvoiceeParty", "ID"),
        "supply_amount": text_under("SpecifiedMonetarySummation", "ChargeTotalAmount"),
        "tax_amount": text_under("SpecifiedMonetarySummation", "TaxTotalAmount"),
        "total_amount": text_under("SpecifiedMonetarySummation", "GrandTotalAmount"),
    }
    if not any(found.values()):
        raise ValueError("전자세금계산서 표준 XML 형식이 아닙니다 (승인번호·금액을 찾지 못함).")
    for key in ("supply_amount", "tax_amount", "total_amount"):
        found[key] = _int(found[key]) if found[key] else None
    for key in ("supplier_biz_no", "buyer_biz_no"):
        found[key] = digits(found[key]) or None
    found["approval_no"] = digits(found["approval_no"]) or None
    return {k: v for k, v in found.items() if v not in (None, "")}


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------
def issue_deadline(supply_date: str) -> date:
    """세금계산서 발급 기한: 공급일이 속한 달의 다음 달 10일 (월합계 발급 특례 기준).
    그날이 토·일요일이나 공휴일(회사 설정)이면 다음 영업일 (국세기본법 제5조 제1항)."""
    from datetime import timedelta

    from . import company
    d = datetime.strptime(supply_date[:10], "%Y-%m-%d").date()
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    due = date(y, m, 10)
    holidays = set(company.get("holidays") or [])
    while due.weekday() >= 5 or due.isoformat() in holidays:
        due += timedelta(days=1)
    return due


def validate(meta: dict, sale: dict, customer: dict) -> tuple[list[str], list[str]]:
    """(오류, 경고). 오류가 있으면 등록하지 않고, 경고는 기록해 두고 등록한다."""
    errors: list[str] = []
    warnings: list[str] = []
    doc_type = meta.get("doc_type")
    if doc_type not in DOC_TYPES:
        errors.append("문서 종류를 선택하세요.")
    is_tax = doc_type in TAX_INVOICE_TYPES

    if doc_type in ("전자세금계산서", "전자계산서"):
        if len(meta.get("approval_no") or "") != 24:
            errors.append(f"{doc_type} 승인번호는 24자리 숫자입니다.")
    elif meta.get("approval_no") and len(meta["approval_no"]) != 24:
        errors.append("승인번호는 24자리 숫자여야 합니다.")
    if is_tax and not meta.get("issue_date"):
        errors.append("작성일자를 입력하세요.")

    for key, label in (("supplier_biz_no", "공급자"), ("buyer_biz_no", "공급받는자")):
        value = meta.get(key)
        if value and not valid_biz_no(value):
            errors.append(f"{label} 사업자등록번호가 올바르지 않습니다 ({format_biz_no(value)}).")
        elif is_tax and not value:
            errors.append(f"{label} 사업자등록번호를 입력하세요.")

    supply, tax, total = meta.get("supply_amount"), meta.get("tax_amount"), meta.get("total_amount")
    exempt = doc_type in EXEMPT_INVOICE_TYPES
    if exempt:
        if tax:
            errors.append("계산서(면세)에는 세액이 없습니다 — 세액을 0 으로 하거나 세금계산서로 등록하세요.")
        tax = meta["tax_amount"] = 0
        if (sale.get("tax_type") or "과세") != "면세":
            warnings.append("과세·영세 매출에 계산서를 붙였습니다 — 세금계산서가 맞는지 확인하세요.")
    elif is_tax and (sale.get("tax_type") or "과세") == "면세":
        warnings.append("면세 매출에는 세금계산서가 아니라 계산서를 발급합니다.")
    if is_tax:
        if supply is None or tax is None:
            errors.append("공급가액과 세액을 입력하세요.")
        else:
            total = total if total is not None else supply + tax
            meta["total_amount"] = total
            if supply + tax != total:
                errors.append(f"공급가액 + 세액({supply + tax:,})이 합계({total:,})와 다릅니다.")
            if tax and not exempt and abs(tax - db.vat_for(supply, "과세")) > 10:      # 매출 부가세와 같은 절사 기준
                warnings.append(f"세액이 공급가액의 10%({db.vat_for(supply, '과세'):,})와 다릅니다 — 영세율·면세·단수 처리를 확인하세요.")
    sale_supply = int(sale.get("amount") or 0)
    sale_total = int(sale.get("total_amount") or sale_supply)
    if supply is not None and supply != sale_supply:
        warnings.append(f"증빙 공급가액({supply:,})이 매출 공급가액({sale_supply:,})과 다릅니다.")
    elif supply is None and total is not None and total not in (sale_total, sale_supply):
        warnings.append(f"증빙 합계({total:,})가 매출 합계({sale_total:,})와 다릅니다.")
    if is_tax and tax is not None and supply == sale_supply and tax != int(sale.get("vat_amount") or 0):
        warnings.append(f"증빙 세액({tax:,})이 매출 부가세({int(sale.get('vat_amount') or 0):,})와 다릅니다.")

    if meta.get("buyer_biz_no") and customer.get("biz_no") and \
            digits(customer["biz_no"]) != meta["buyer_biz_no"]:
        warnings.append(f"공급받는자 사업자번호가 거래처 등록 정보({format_biz_no(customer['biz_no'])})와 다릅니다.")
    from . import entities as ent_mod
    ours = digits(ent_mod.info(sale.get("entity_id"))["biz_no"]) or COMPANY_BIZ_NO   # 매출의 법인 기준
    if ours and meta.get("supplier_biz_no") and meta["supplier_biz_no"] != ours:
        warnings.append("공급자 사업자번호가 우리 회사 번호와 다릅니다 (매입 세금계산서가 아닌지 확인).")
    if is_tax and meta.get("issue_date") and sale.get("sale_date"):
        deadline = issue_deadline(sale["sale_date"])
        if datetime.strptime(meta["issue_date"], "%Y-%m-%d").date() > deadline:
            warnings.append(f"발급 기한({deadline:%Y-%m-%d})을 넘겨 작성되었습니다 — 지연발급 가산세 대상인지 확인하세요.")
    return errors, warnings


# ---------------------------------------------------------------------------
# 등록 · 조회 · 무효
# ---------------------------------------------------------------------------
def normalize_meta(form: dict) -> dict:
    return {
        "doc_type": (form.get("doc_type") or "").strip(),
        "issue_date": _date(form.get("issue_date")) if form.get("issue_date") else None,
        "approval_no": digits(form.get("approval_no")) or None,
        "supplier_biz_no": digits(form.get("supplier_biz_no")) or None,
        "buyer_biz_no": digits(form.get("buyer_biz_no")) or None,
        "supply_amount": _int(form.get("supply_amount")),
        "tax_amount": _int(form.get("tax_amount")),
        "total_amount": _int(form.get("total_amount")),
        "memo": (form.get("memo") or "").strip() or None,
    }


def _store(data: bytes, ext: str, mime: str) -> str:
    today = date.today()
    key = f"documents/{today:%Y}/{today:%m}/{secrets.token_hex(16)}{ext}"
    return get_storage().put(key, data, mime)


def storage_key(file_path: str) -> str:
    """초기 판은 documents/ 아래 상대경로만 저장했다 → 저장소 키로 맞춘다."""
    return file_path if file_path.startswith("documents/") else f"documents/{file_path}"


def read_file(doc: dict) -> bytes:
    return get_storage().get(storage_key(doc["file_path"]))


def add_document(sale_id: int, form: dict, data: bytes, filename: str,
                 uploader: dict) -> tuple[int, list[str]]:
    """증빙 등록. (문서 id, 경고 목록). 전자세금계산서 XML 이면 빈 칸을 XML 값으로 채운다."""
    sale = db.get_sale(sale_id)
    db.check_record_scope(sale, "매출")
    customer = db.get_customer(int(sale["customer_id"])) or {}
    mime, ext = detect_type(data)
    meta = normalize_meta(form)
    if mime == "application/xml":
        parsed = parse_etax_xml(data)
        mismatch = [key for key, value in parsed.items()
                    if value not in (None, "") and meta.get(key) not in (None, "") and str(meta[key]) != str(value)]
        if mismatch:                                 # 파일이 원본 — 다른 매출의 XML 에 손으로 맞춘 값을 적어 넣지 못하게
            raise ValueError("입력한 값이 전자세금계산서 파일과 다릅니다: " + ", ".join(mismatch)
                             + " — 칸을 비우면 파일 값으로 채웁니다.")
        for key, value in parsed.items():
            if value not in (None, ""):
                meta[key] = value
        meta["doc_type"] = meta["doc_type"] or "전자세금계산서"
    errors, warnings = validate(meta, sale, customer)
    if meta.get("approval_no") and db._one(
            "SELECT id FROM sale_documents WHERE approval_no=? AND voided_at IS NULL", [meta["approval_no"]]):
        errors.append(f"승인번호 {format_approval_no(meta['approval_no'])} 는 이미 등록된 증빙입니다.")
    if errors:
        raise ValueError(" / ".join(errors))

    sha = hashlib.sha256(data).hexdigest()
    rel = _store(data, ext, mime)
    safe_name = re.sub(r"[\\/:*?\"<>|\r\n]", "_", os.path.basename(filename or f"document{ext}"))[:120]
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sale_documents (sale_id, doc_type, issue_date, approval_no, supplier_biz_no, "
            "buyer_biz_no, supply_amount, tax_amount, total_amount, memo, file_path, file_name, mime, "
            "file_size, sha256, check_result, uploaded_by, uploaded_by_id, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sale_id, meta["doc_type"], meta["issue_date"], meta["approval_no"], meta["supplier_biz_no"],
             meta["buyer_biz_no"], meta["supply_amount"], meta["tax_amount"], meta["total_amount"],
             meta["memo"], rel, safe_name, mime, len(data), sha,
             json.dumps(warnings, ensure_ascii=False) if warnings else None,
             uploader.get("name"), uploader.get("id"), db._now()))
        doc_id = int(cur.lastrowid)
    db.audit("증빙등록", "매출", sale_id,
             {"문서ID": doc_id, "종류": meta["doc_type"], "승인번호": meta["approval_no"],
              "합계": meta["total_amount"], "파일": safe_name, "sha256": sha, "경고": warnings})
    return doc_id, warnings


def list_documents(sale_id: int, include_void: bool = True) -> list[dict]:
    sql = "SELECT * FROM sale_documents WHERE sale_id=?"
    if not include_void:
        sql += " AND voided_at IS NULL"
    rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}   # NaN → None
            for r in db._df(sql + " ORDER BY id DESC", [sale_id]).to_dict("records")]
    for r in rows:
        r["warnings"] = json.loads(r["check_result"]) if r.get("check_result") else []
        r["approval_fmt"] = format_approval_no(r.get("approval_no"))
        r["is_image"] = r["mime"] in IMAGE_TYPES
    return rows


def get_document(doc_id: int, with_data: bool = False) -> dict:
    """증빙 기록 (with_data=True 면 원본 바이트와 위변조 여부 포함).

    매출이 로그인 사용자의 범위 밖이면 PermissionError.
    """
    doc = db._one("SELECT * FROM sale_documents WHERE id=?", [doc_id])
    if not doc:
        raise ValueError("존재하지 않는 증빙입니다.")
    db.check_record_scope(db.get_sale(int(doc["sale_id"])), "매출")
    if with_data:
        try:
            doc["data"] = read_file(doc)
        except FileNotFoundError:
            doc["data"] = None
        doc["intact"] = doc["data"] is not None and hashlib.sha256(doc["data"]).hexdigest() == doc["sha256"]
    return doc


def void_document(doc_id: int, reason: str, actor: dict) -> None:
    if not (reason or "").strip():
        raise ValueError("무효 사유를 입력하세요.")
    doc = get_document(doc_id)
    if doc.get("voided_at"):
        raise ValueError("이미 무효 처리된 증빙입니다.")
    with db.get_conn() as conn:
        conn.execute("UPDATE sale_documents SET voided_at=?, void_reason=?, voided_by=? WHERE id=?",
                     (db._now(), reason.strip(), actor.get("name"), doc_id))
    db.audit("증빙무효", "매출", int(doc["sale_id"]),
             {"문서ID": doc_id, "승인번호": doc.get("approval_no"), "사유": reason.strip()})


def missing_documents(ym_from: str = "", ym_to: str = "") -> int:
    """세금계산서 증빙이 없는 (취소되지 않은) 매출 건수 — 부가세 신고 전 점검용."""
    scope_sql, scope_params = db._scope_clause("s")
    sql = ("SELECT COUNT(*) FROM sales s WHERE s.status <> '취소' AND NOT EXISTS ("
           "SELECT 1 FROM sale_documents d WHERE d.sale_id = s.id AND d.voided_at IS NULL "
           "AND d.doc_type IN ('전자세금계산서','세금계산서','수정세금계산서','전자계산서','계산서','수정계산서'))")
    params: list[Any] = []
    if ym_from:
        sql += " AND substr(s.sale_date, 1, 7) >= ?"
        params.append(ym_from)
    if ym_to:
        sql += " AND substr(s.sale_date, 1, 7) <= ?"
        params.append(ym_to)
    return int(db._scalar(sql + scope_sql, params + scope_params))

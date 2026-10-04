"""증빙(세금계산서 · 전자세금계산서 · 계산서 · 거래명세서) 이미지 등록 규칙과 파일 보관.

두 단계로 나눈다.
  prepare()  파일 형식 판정 · 입력값 검증 · 중복 확인. DB와 디스크를 건드리지 않는다.
  save()     파일을 쓰고 DB에 기록한다.
입출고 화면은 prepare()가 통과한 뒤에만 거래를 등록하므로, 증빙이 잘못되어 거래만 들어가는 일이 없다.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

import config
from core import audit, db, repository as repo, storage

# 파일 앞부분(매직 바이트)으로 실제 형식을 판정한다. 확장자·브라우저가 보낸 형식은 믿지 않는다.
# SVG·HTML은 스크립트를 품을 수 있어 받지 않는다.
_SIGNATURES = (
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"%PDF-", ".pdf", "application/pdf"),
)
ACCEPT = ".jpg,.jpeg,.png,.webp,.pdf"


@dataclass
class Prepared:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: bytes = b""
    payload: dict = field(default_factory=dict)


@dataclass
class Saved:
    ok: bool
    message: str
    doc_id: int = 0


def sniff(data: bytes) -> tuple[str, str] | None:
    """(확장자, MIME) 또는 허용하지 않는 형식이면 None."""
    for magic, ext, mime in _SIGNATURES:
        if data.startswith(magic):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def valid_biz_no(no: str) -> bool:
    """사업자등록번호 10자리 검증번호 확인 (국세청 가중치 1,3,7,1,3,7,1,3,5)."""
    if len(no) != 10 or not no.isdigit():
        return False
    d = [int(c) for c in no]
    total = sum(a * b for a, b in zip(d, (1, 3, 7, 1, 3, 7, 1, 3, 5))) + d[8] * 5 // 10
    return (10 - total % 10) % 10 == d[9]


def format_biz_no(no: str) -> str:
    return f"{no[:3]}-{no[3:5]}-{no[5:]}" if len(no) == 10 else no


def _amount(raw, label: str, errors: list[str]) -> int:
    text = str(raw or "").replace(",", "").strip()
    if not text:
        return 0
    try:
        value = int(float(text))
    except (ValueError, OverflowError):                  # 'abc', 'nan', '1e309'
        errors.append(f"{label}은(는) 숫자로 입력하세요 ({text[:30]})")
        return 0
    if value > 10 ** 15:
        errors.append(f"{label}이(가) 너무 큽니다 ({text[:30]})")
        return 0
    if value < 0:
        errors.append(f"{label}은(는) 0 이상이어야 합니다.")
    return value


def prepare(data: bytes, filename: str, meta: dict) -> Prepared:
    """meta: doc_type, issue_date, approval_no, supplier_biz_no, supplier_name,
    supply_amount, tax_amount, tx_id, note, created_by (모두 문자열 가능)."""
    errors: list[str] = []
    warnings: list[str] = []

    # ── 파일 ──
    if not data:
        errors.append("증빙 파일을 선택하세요.")
        kind = None
    elif len(data) > config.DOC_MAX_BYTES:
        errors.append(f"증빙 파일은 {config.DOC_MAX_BYTES // 1024 // 1024}MB 이하만 올릴 수 있습니다.")
        kind = None
    else:
        kind = sniff(data)
        if kind is None:
            errors.append("JPG · PNG · WEBP 이미지 또는 PDF만 올릴 수 있습니다. "
                          "(휴대폰 HEIC 사진은 JPG로 바꿔 올려 주세요)")

    # ── 입력값 ──
    doc_type = str(meta.get("doc_type", "")).strip()
    if doc_type not in config.DOC_TYPES:
        errors.append("증빙 종류를 선택하세요.")

    try:
        issue_date = date.fromisoformat(str(meta.get("issue_date", "")).strip()).isoformat()
    except ValueError:
        errors.append("작성일자를 입력하세요.")
        issue_date = ""

    biz_no = digits(meta.get("supplier_biz_no", ""))
    if biz_no and not valid_biz_no(biz_no):
        errors.append(f"공급자 사업자등록번호가 올바르지 않습니다 ({format_biz_no(biz_no)}). 숫자를 다시 확인하세요.")
    elif not biz_no and doc_type in config.DOC_NEED_BIZ_NO:
        errors.append(f"{config.DOC_TYPES[doc_type]}은(는) 공급자 사업자등록번호가 필요합니다.")

    approval_no = digits(meta.get("approval_no", ""))
    if doc_type == "E_TAX_INVOICE" and len(approval_no) != 24:
        errors.append("전자세금계산서 승인번호는 숫자 24자리입니다 (하이픈 제외).")
    elif doc_type != "E_TAX_INVOICE" and approval_no:
        warnings.append("승인번호는 전자세금계산서에만 있습니다. 입력값은 그대로 저장합니다.")

    supply = _amount(meta.get("supply_amount"), "공급가액", errors)
    tax = _amount(meta.get("tax_amount"), "세액", errors)
    if doc_type in config.DOC_NEED_BIZ_NO and supply <= 0:
        errors.append("공급가액을 입력하세요.")
    if doc_type in config.DOC_VAT and tax and abs(tax - supply // 10) > 10:
        warnings.append(f"세액 {tax:,}원이 공급가액의 10%({supply // 10:,}원)와 다릅니다. 증빙과 대조해 주세요.")
    if doc_type == "INVOICE" and tax:
        warnings.append("계산서(면세)는 보통 세액이 0원입니다. 증빙 종류를 확인해 주세요.")

    tx_raw = str(meta.get("tx_id") or "").strip()
    tx_id = int(tx_raw) if tx_raw.isascii() and tx_raw.isdigit() and len(tx_raw) <= 18 else None
    if tx_raw and tx_id is None:
        errors.append("연결할 거래 ID가 올바르지 않습니다.")

    sha256 = hashlib.sha256(data).hexdigest() if data else ""

    # ── DB 확인 (중복 · 연결 거래 존재) ──
    if not errors:
        with db.get_conn() as conn:
            dup = repo.find_document_id(conn, sha256=sha256, approval_no=approval_no)
            if dup is not None:
                errors.append(f"이미 등록된 증빙입니다 (증빙 ID {dup}). 같은 파일이거나 같은 승인번호입니다.")
            if tx_id is not None and repo.get_transaction(conn, tx_id) is None:
                errors.append(f"거래 ID {tx_id}가 없습니다.")
            elif tx_id is not None and biz_no:
                try:
                    p = conn.execute("SELECT p.name, p.biz_no FROM transactions t JOIN partners p ON p.id = t.partner_id "
                                     "WHERE t.id = ?", (tx_id,)).fetchone()
                except db.DBError:
                    p = None
                if p is not None and p["biz_no"] and digits(p["biz_no"]) != biz_no:
                    warnings.append(f"계산서 공급자({format_biz_no(biz_no)})가 이 거래의 거래처 {p['name']}"
                                    f"({format_biz_no(digits(p['biz_no']))})와 다릅니다. 맞는 거래에 연결했는지 확인하세요.")

    if errors:
        return Prepared(False, errors, warnings)

    ext, mime = kind
    safe_name = Path(filename or f"증빙{ext}").name[:200]
    return Prepared(True, [], warnings, data, {
        "tx_id": tx_id, "doc_type": doc_type, "issue_date": issue_date,
        "approval_no": approval_no, "supplier_biz_no": biz_no,
        "supplier_name": str(meta.get("supplier_name", "")).strip(),
        "supply_amount": supply, "tax_amount": tax,
        "file_name": safe_name, "stored_name": f"{secrets.token_hex(16)}{ext}",
        "mime": mime, "size": len(data), "sha256": sha256,
        "note": str(meta.get("note", "")).strip(),
        "created_by": str(meta.get("created_by", "")).strip(),
    })


def key(doc: dict) -> str:
    """저장소 키. stored_name은 서버가 만든 무작위 이름이라 경로 조작 걱정이 없다."""
    return f"attachments/{Path(doc['stored_name']).name}"


def save(prepared: Prepared, tx_id: int | None = None, actor: dict | None = None) -> Saved:
    """prepare()를 통과한 증빙을 저장한다. tx_id를 주면 그 거래에 연결한다.

    파일을 먼저 저장소에 올리고 DB에 기록한다. DB 기록이 실패하면 올린 파일을 지운다
    (반대 순서면 'DB에는 있는데 파일이 없는' 증빙이 생길 수 있다).
    """
    if not prepared.ok:
        return Saved(False, " / ".join(prepared.errors))
    payload = dict(prepared.payload)
    if tx_id is not None:
        payload["tx_id"] = tx_id
    if actor:
        payload["created_by"] = actor["name"]
        payload["created_by_id"] = actor.get("id")
    store = storage.get()
    k = key(payload)
    store.put(k, prepared.data)
    try:
        with db.transaction() as conn:
            doc_id = repo.insert_document(conn, payload)
            audit.record(conn, actor, "DOC_CREATE", "document", doc_id,
                         {f: payload[f] for f in ("tx_id", "doc_type", "issue_date", "approval_no",
                                                  "supplier_biz_no", "supply_amount", "tax_amount",
                                                  "file_name", "sha256")})
    except Exception:
        store.delete(k)
        # prepare() 이후 같은 파일이 먼저 등록된 경우(동시 등록) 등
        return Saved(False, "증빙 저장에 실패했습니다. 이미 등록된 증빙인지 확인하세요.")
    return Saved(True, f"{config.DOC_TYPES[payload['doc_type']]} 증빙 등록 (증빙 ID {doc_id})", doc_id)


def read(doc: dict, actor: dict | None, download: bool = False) -> bytes | None:
    """증빙 파일 내용. 열람·다운로드를 감사로그에 남긴다(세금계산서는 민감 자료)."""
    data = storage.get().get(key(doc))
    audit.log(actor, "DOC_DOWNLOAD" if download else "DOC_VIEW", "document", doc["id"],
              {"file_name": doc["file_name"], "found": data is not None})
    return data


def exists(doc: dict) -> bool:
    return storage.get().exists(key(doc))


def link(doc_id: int, tx_id: int | None, actor: dict | None = None, wh_ids=None) -> Saved:
    doc = repo.get_document(doc_id)
    if doc is None:
        return Saved(False, "증빙이 없습니다.")
    with db.transaction() as conn:
        if tx_id is not None:
            tx = repo.get_transaction(conn, tx_id)
            if tx is None or (wh_ids is not None and tx["warehouse_id"] not in wh_ids):
                return Saved(False, f"거래 ID {tx_id}가 없거나 권한 밖입니다.")
        repo.set_document_tx(conn, doc_id, tx_id)
        before = None if pd.isna(doc["tx_id"]) else int(doc["tx_id"])
        audit.record(conn, actor, "DOC_LINK", "document", doc_id, {"tx_id": [before, tx_id]})
    return Saved(True, f"거래 #{tx_id}에 연결했습니다." if tx_id else "거래 연결을 해제했습니다.", doc_id)


def delete(doc_id: int, actor: dict | None = None) -> Saved:
    doc = repo.get_document(doc_id)
    if doc is None:
        return Saved(False, "이미 삭제된 증빙입니다.")
    if config.SOD_ENFORCE and actor and pd.notna(doc.get("created_by_id")) and int(doc["created_by_id"]) == actor.get("id"):
        return Saved(False, "본인이 올린 증빙은 다른 관리자가 삭제해야 합니다(직무 분리).")
    with db.transaction() as conn:
        repo.delete_document(conn, doc_id)
        audit.record(conn, actor, "DOC_DELETE", "document", doc_id,
                     {f: doc[f] for f in ("doc_type", "issue_date", "approval_no", "supplier_biz_no",
                                          "supply_amount", "tax_amount", "file_name", "sha256")})
    storage.get().delete(key(doc))
    return Saved(True, f"증빙 ID {doc_id} 삭제 완료")

"""영업기회·영업활동 첨부 — 제안서·계약서·회의록·도면

  받는 형식(파일 내용으로 판별, 확장자만 바꾼 파일은 거부): PDF, JPG, PNG,
  워드·엑셀·파워포인트(docx·xlsx·pptx, 매크로 포함 파일은 거부), 한글(hwp·hwpx)
  파일은 공용 저장소(attachments/연/월/무작위이름)에 두고 SHA-256 을 기록한다.
  볼 수 있는 사람 = 그 영업기회·활동의 데이터 범위 안에 있는 사람. 삭제 대신 사유를 남겨 '무효'.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import zipfile
from datetime import date
from typing import Optional

from . import documents as docs
from . import sales_db as db
from .storage import get_storage

MAX_BYTES = 20 * 1024 * 1024
KINDS = ["제안서", "견적서", "계약서", "회의록", "기술자료", "기타"]
ENTITIES = {"deal": "영업기회", "activity": "영업활동"}
OOXML = {"word/": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
         "xl/": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
         "ppt/": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx")}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect(data: bytes, filename: str) -> tuple[str, str]:
    if not data:
        raise ValueError("빈 파일입니다.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"파일이 너무 큽니다 (최대 {MAX_BYTES // 1024 // 1024}MB).")
    name = (filename or "").lower()
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                    raise ValueError("압축을 풀면 너무 큰 파일입니다.")
        except zipfile.BadZipFile as exc:
            raise ValueError("손상된 문서 파일입니다.") from exc
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise ValueError("매크로가 포함된 문서는 올릴 수 없습니다. 매크로 없는 파일(docx·xlsx·pptx)로 저장해 주세요.")
        if "mimetype" in names and name.endswith(".hwpx"):
            return "application/hwp+zip", ".hwpx"
        if "[Content_Types].xml" in names:
            for prefix, kind in OOXML.items():
                if any(n.startswith(prefix) for n in names):
                    return kind
        raise ValueError("지원하지 않는 압축 파일입니다.")
    if data.startswith(OLE):
        if name.endswith(".hwp"):
            return "application/x-hwp", ".hwp"
        raise ValueError("옛 형식(doc·xls·ppt)은 매크로를 숨길 수 있어 받지 않습니다. docx·xlsx·pptx 로 저장해 주세요.")
    mime, ext = docs.detect_type(data)              # PDF·JPG·PNG (XML 은 첨부로 받지 않음)
    if mime == "application/xml":
        raise ValueError("XML 은 첨부로 받지 않습니다. 전자세금계산서는 매출 증빙에 올리세요.")
    return mime, ext


def _owner_record(entity: str, entity_id: int) -> dict:
    if entity == "deal":
        row = db.get_deal(int(entity_id))
    elif entity == "activity":
        row = db._one("SELECT * FROM activities WHERE id=?", [int(entity_id)])
    else:
        raise ValueError("첨부할 수 없는 대상입니다.")
    db.check_record_scope(row, ENTITIES[entity])
    return row


def add(entity: str, entity_id: int, data: bytes, filename: str, kind: str = "기타", memo: str = "",
        actor: Optional[dict] = None) -> int:
    _owner_record(entity, entity_id)
    mime, ext = detect(data, filename)
    kind = kind if kind in KINDS else "기타"
    key = f"attachments/{date.today():%Y/%m}/{secrets.token_hex(16)}{ext}"
    get_storage().put(key, data, mime)
    safe_name = "".join(ch for ch in (filename or "file") if ch not in '\\/:*?"<>|')[:150] or f"file{ext}"
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO attachments (entity, entity_id, kind, file_key, file_name, mime, size, sha256, "
                           "memo, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (entity, int(entity_id), kind, key, safe_name, mime, len(data),
                            hashlib.sha256(data).hexdigest(), memo or None, (actor or {}).get("name") or db.current_actor(),
                            db._now()))
        aid = int(cur.lastrowid)
    db.audit("첨부", ENTITIES[entity], int(entity_id), {"파일": safe_name, "종류": kind, "크기": len(data)})
    return aid


def list_for(entity: str, entity_ids: list[int]) -> list[dict]:
    if not entity_ids:
        return []
    marks = ",".join("?" * len(entity_ids))
    return db._df(f"SELECT * FROM attachments WHERE entity=? AND entity_id IN ({marks}) ORDER BY id DESC",
                  [entity, *map(int, entity_ids)]).to_dict("records")


def get(attachment_id: int, with_data: bool = False) -> dict:
    row = db._one("SELECT * FROM attachments WHERE id=?", [int(attachment_id)])
    if not row:
        raise ValueError("첨부 파일이 없습니다.")
    _owner_record(row["entity"], row["entity_id"])
    if with_data:
        data = get_storage().get(row["file_key"])
        row["intact"] = hashlib.sha256(data).hexdigest() == row["sha256"]
        row["data"] = data
    return row


def void(attachment_id: int, reason: str, actor: dict) -> dict:
    if not str(reason or "").strip():
        raise ValueError("무효 사유를 입력하세요.")
    row = get(attachment_id)
    with db.get_conn() as conn:
        conn.execute("UPDATE attachments SET voided_at=?, voided_by=?, void_reason=? WHERE id=? AND voided_at IS NULL",
                     (db._now(), actor.get("name"), reason.strip(), int(attachment_id)))
    db.audit("첨부무효", ENTITIES[row["entity"]], int(row["entity_id"]), {"파일": row["file_name"], "사유": reason.strip()})
    return row

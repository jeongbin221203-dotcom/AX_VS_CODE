"""증빙·첨부 파일 백업 (자재관리 core/backup.py 의 _mirror_attachments 와 같은 생각).

  DB 백업만 복원하면 sale_documents·attachments 에는 sha256 만 남고 파일(세금계산서 이미지·XML·견적 PDF·계약서)이 없어
  위변조 점검이 '원본 없음' 으로 끝난다. 증빙은 법정 보관 대상이라 DB 백업을 할 때마다 파일도 백업 폴더로 복사한다.

  <백업 폴더>/files/<저장소 키>   새 파일만 복사 (이미 있고 sha256 이 같으면 건너뜀) — 지운 파일은 백업에서 지우지 않는다
  files.json                       복사한 파일의 sha256 목록 (복원 때 검증)
  복원: python manage.py restore-files <백업 폴더>  — 저장소에 없는 파일만 되돌리고 sha256 을 확인한다
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from . import sales_db as db
from .storage import get_storage

LOG = logging.getLogger("sales.backup")


def _targets() -> list[tuple[str, str]]:
    """(저장소 키, 기대 sha256) — 무효 처리한 증빙·첨부도 포함 (기록은 보존 대상)."""
    out: list[tuple[str, str]] = []
    from .documents import storage_key
    for r in db._df("SELECT file_path, sha256 FROM sale_documents WHERE file_path IS NOT NULL").to_dict("records"):
        out.append((storage_key(r["file_path"]), r["sha256"]))
    for r in db._df("SELECT file_key, sha256 FROM attachments WHERE file_key IS NOT NULL").to_dict("records"):
        out.append((r["file_key"], r["sha256"]))
    return out


def mirror_files(folder: str | os.PathLike) -> dict:
    """저장소의 증빙·첨부를 백업 폴더로 복사한다. {'복사','이미있음','없음','불일치'}."""
    root = Path(folder) / "files"
    root.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    manifest_path = Path(folder) / "files.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    result = {"복사": 0, "이미있음": 0, "없음": 0, "불일치": 0}
    for key, sha in _targets():
        dest = root / key
        if dest.exists() and manifest.get(key) == sha:
            result["이미있음"] += 1
            continue
        try:
            data = storage.get(key)
        except (FileNotFoundError, KeyError, OSError):
            result["없음"] += 1                      # 원본이 이미 없다 — 점검 화면에서 알린다
            continue
        if sha and hashlib.sha256(data).hexdigest() != sha:
            result["불일치"] += 1                    # 변조된 파일은 백업에 덮어쓰지 않는다
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        manifest[key] = sha
        result["복사"] += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return result


def missing_in_backup(folder: str | os.PathLike) -> int:
    """DB 에는 있는데 백업 폴더에 없는 파일 수 (운영 점검용)."""
    root = Path(folder) / "files"
    return sum(1 for key, _sha in _targets() if not (root / key).exists())


def restore_files(folder: str | os.PathLike) -> dict:
    """백업 폴더의 파일을 저장소로 되돌린다 (이미 있는 파일은 건드리지 않고, sha256 이 다르면 거부)."""
    root = Path(folder) / "files"
    storage = get_storage()
    result = {"복원": 0, "이미있음": 0, "백업에없음": 0, "불일치": 0}
    for key, sha in _targets():
        try:
            storage.get(key)
            result["이미있음"] += 1
            continue
        except (FileNotFoundError, KeyError, OSError):
            pass
        src = root / key
        if not src.exists():
            result["백업에없음"] += 1
            continue
        data = src.read_bytes()
        if sha and hashlib.sha256(data).hexdigest() != sha:
            result["불일치"] += 1
            continue
        storage.put(key, data)
        result["복원"] += 1
    return result


def zip_files() -> bytes:
    """모든 증빙·첨부 + 목록(sha256)을 zip 하나로 (관리자 내려받기 — 백그라운드 작업에서)."""
    import io
    import zipfile
    storage = get_storage()
    buf = io.BytesIO()
    lines = ["저장소키\tsha256"]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for key, sha in _targets():
            try:
                data = storage.get(key)
            except (FileNotFoundError, KeyError, OSError):
                continue
            zf.writestr(f"files/{key}", data)
            lines.append(f"{key}\t{sha}")
        zf.writestr("files.tsv", "\n".join(lines))
    return buf.getvalue()

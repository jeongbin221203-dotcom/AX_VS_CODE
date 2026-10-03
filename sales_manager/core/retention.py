"""보관기간 — 감사로그 이관 · 백업 세대 관리

감사로그 이관
  보관기간(회사 설정 audit_retention_years)이 지난 감사로그를 압축 파일(JSONL.gz)로 저장소에 옮기고 DB 에서 지운다.
  * 옮기기 전에 그 구간의 해시 체인을 다시 계산해 끊긴 곳이 있으면 이관하지 않는다(변조 증거를 지우지 않도록)
  * 파일의 SHA-256 과 마지막 해시를 audit_archives 에 먼저 기록하고, 그 범위의 행만 지울 수 있다(DB 트리거)
  * audit_archives 는 고치거나 지울 수 없다. 이관 파일은 verify_archive 로 언제든 다시 검증한다
  * DB 에 남은 체인은 마지막 이관의 해시에서 이어지므로 무결성 검증이 그대로 동작한다

백업 세대 관리 (회사 설정)
  일 백업 최근 N개 + 월말 백업 M개월 + 연말 백업 Y년. 나머지는 지운다.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from . import sales_db as db
from .storage import get_storage

AUDIT_COLUMNS = ["id", "ts", "actor", "actor_id", "action", "entity", "entity_id", "detail", "prev_hash", "hash"]


def last_archive() -> Optional[dict]:
    return db._one("SELECT * FROM audit_archives ORDER BY last_id DESC LIMIT 1")


def list_archives():
    return db._df("SELECT id, first_id AS 시작번호, last_id AS 끝번호, row_count AS 건수, created_at AS 이관일시, "
                  "created_by AS 처리자, file_key AS 파일, sha256 FROM audit_archives ORDER BY id DESC")


def _chain_ok(rows: list[dict], prev: str) -> tuple[bool, str, Optional[int]]:
    for r in rows:
        if not r["hash"]:
            if prev:
                return False, "해시 없는 기록이 끼어 있음", int(r["id"])
            continue
        expect = db._audit_hash(prev, r["ts"], r["actor"], r["actor_id"], r["action"], r["entity"],
                                r["entity_id"], r["detail"])
        if (r["prev_hash"] or "") != prev:
            return False, "앞 기록과 연결이 끊김", int(r["id"])
        if expect != r["hash"]:
            return False, "내용이 기록 당시와 다름", int(r["id"])
        prev = r["hash"]
    return True, prev, None


def archive_audit(years: Optional[int] = None, now: Optional[datetime] = None, actor: str = "batch") -> dict:
    from . import company
    years = int(company.get("audit_retention_years") if years is None else years)
    if years <= 0:
        return {"archived": 0, "message": "감사로그 보관기간이 0(계속 보관)입니다."}
    cutoff = ((now or datetime.now()) - timedelta(days=365 * years)).strftime("%Y-%m-%d %H:%M:%S")
    anchor = last_archive()
    start_after = int(anchor["last_id"]) if anchor else 0
    prev = (anchor or {}).get("last_hash") or ""
    last_id = db._scalar("SELECT MAX(id) FROM audit_log WHERE id > ? AND ts < ?", [start_after, cutoff])
    if not last_id:
        return {"archived": 0, "message": "이관할 감사로그가 없습니다."}
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(AUDIT_COLUMNS)} FROM audit_log WHERE id > ? AND id <= ? ORDER BY id",
            (start_after, int(last_id))).fetchall()]
    for r in rows:
        r["actor_id"] = None if r["actor_id"] is None else int(r["actor_id"])
        r["entity_id"] = None if r["entity_id"] is None else int(r["entity_id"])
    ok, last_hash, broken = _chain_ok(rows, prev)
    if not ok:
        db.audit("감사로그이관실패", "시스템", broken, {"사유": last_hash})
        raise ValueError(f"감사로그 {broken}번에서 해시 체인이 끊겨 이관하지 않았습니다({last_hash}). 먼저 원인을 조사하세요.")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in rows:
            gz.write((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    first = int(rows[0]["id"])
    key = f"audit_archive/audit_{first:010d}_{int(last_id):010d}.jsonl.gz"
    get_storage().put(key, data, "application/gzip")
    if get_storage().get(key) != data:                      # 저장소에 제대로 남았는지 확인한 뒤에만 지운다
        raise RuntimeError("이관 파일을 다시 읽어 보니 내용이 다릅니다. DB 기록은 지우지 않았습니다.")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO audit_archives (first_id, last_id, row_count, last_hash, file_key, sha256, "
                     "created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (first, int(last_id), len(rows), last_hash or None, key, digest, actor, db._now()))
        conn.execute("DELETE FROM audit_log WHERE id >= ? AND id <= ?", (first, int(last_id)))
    result = {"archived": len(rows), "first_id": first, "last_id": int(last_id), "file": key, "sha256": digest,
              "cutoff": cutoff}
    db.audit("감사로그이관", "시스템", None, result)
    return result


def verify_archive(archive_id: int) -> dict:
    """이관 파일을 다시 읽어 SHA-256 과 해시 체인을 검증한다."""
    arc = db._one("SELECT * FROM audit_archives WHERE id=?", [int(archive_id)])
    if not arc:
        raise ValueError("이관 기록이 없습니다.")
    data = get_storage().get(arc["file_key"])
    if hashlib.sha256(data).hexdigest() != arc["sha256"]:
        return {"ok": False, "reason": "파일이 이관 당시와 다름(SHA-256 불일치)"}
    prev_arc = db._one("SELECT last_hash FROM audit_archives WHERE last_id < ? ORDER BY last_id DESC LIMIT 1",
                       [arc["first_id"]])
    rows = [json.loads(line) for line in gzip.decompress(data).decode("utf-8").splitlines() if line]
    ok, last_hash, broken = _chain_ok(rows, (prev_arc or {}).get("last_hash") or "")
    if not ok:
        return {"ok": False, "reason": f"{broken}번: {last_hash}"}
    if (last_hash or None) != arc["last_hash"]:
        return {"ok": False, "reason": "마지막 해시가 이관 기록과 다름"}
    return {"ok": True, "rows": len(rows)}


# ---------------------------------------------------------------------------
# 백업 세대 관리
# ---------------------------------------------------------------------------
_STAMP = re.compile(r"sales_(\d{8})_(\d{6})")


def prune_backups(folder: str | os.PathLike, suffixes: tuple[str, ...]) -> dict:
    from . import company
    daily = int(company.get("backup_keep_daily"))
    monthly = int(company.get("backup_keep_monthly"))
    yearly = int(company.get("backup_keep_yearly"))
    files = []
    for name in os.listdir(folder):
        m = _STAMP.match(name)
        if m and name.endswith(suffixes):
            files.append((datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"), name))
    files.sort(reverse=True)
    keep: dict[str, str] = {}
    for _ts, name in files[:daily]:
        keep[name] = "일"
    months, years = {}, {}
    for ts, name in files:                                  # 최신순 → 각 달·해의 마지막 백업이 먼저 나온다
        months.setdefault(ts.strftime("%Y-%m"), name)
        years.setdefault(ts.strftime("%Y"), name)
    for ym in sorted(months, reverse=True)[:monthly]:
        keep.setdefault(months[ym], "월말")
    for y in sorted(years, reverse=True)[:yearly]:
        keep.setdefault(years[y], "연말")
    removed = [name for _ts, name in files if name not in keep]
    for name in removed:
        os.remove(os.path.join(folder, name))
    return {"kept": len(keep), "removed": len(removed),
            "monthly": sum(1 for v in keep.values() if v == "월말"), "yearly": sum(1 for v in keep.values() if v == "연말")}

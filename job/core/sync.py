"""내 PC(원본) ↔ Render(사본) 동기화.

- 원본은 내 PC의 data/job.db. 수집도 내 PC에서만 한다.
- Render 는 사본: JOB_MIRROR=1. 화면에서 바꾼 것(저장·숨김·지원 기록·회사 평균연봉·내 조건·확인함)만
  mirror_changes 에 쌓는다.
- 내 PC가 한 시간마다(수집이 끝날 때마다) ① Render 의 변경을 받아 원본에 반영하고 ② 원본 DB 스냅샷을
  올린다. Render 는 받은 DB 로 바꾼 뒤, 그사이(①과 ② 사이) 생긴 변경을 다시 얹는다.
- 내 PC 쪽 설정은 data/sync.json {"url": "https://…onrender.com", "token": "…"} (깃에 안 올라감).
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import requests

import config

from . import applications, db, postings, profile

KINDS = ("save", "hide", "app_upsert", "app_remove", "company_avg", "profile", "seen")


# ── Render(사본) 쪽 ─────────────────────────────────────

def note(kind: str, **payload) -> None:
    """사본에서 사용자가 바꾼 것을 기록 (원본에 옮기려고). 원본에서는 아무것도 안 함."""
    if not config.MIRROR:
        return
    with db.connect() as con:
        con.execute("INSERT INTO mirror_changes(id, at, kind, payload) VALUES(?, ?, ?, ?)",
                    (time.time_ns() // 1000, db.now(), kind, json.dumps(payload, ensure_ascii=False)))


def changes_after(after: int) -> list[dict]:
    with db.connect() as con:
        return [{"id": r[0], "at": r[1], "kind": r[2], "payload": json.loads(r[3])} for r in con.execute(
            "SELECT id, at, kind, payload FROM mirror_changes WHERE id > ? ORDER BY id", (after,))]


def receive(data: bytes, last_change: int) -> dict:
    """바이트로 받은 원본 DB(gzip) — 테스트·작은 파일용. 큰 파일은 receive_stream 으로(메모리 한계)."""
    import io
    return receive_stream(io.BytesIO(data), last_change)


def receive_stream(stream, last_change: int) -> dict:
    """원본 DB(gzip)를 흘려받아 사본 DB 를 바꾼다 — 받은 것도 푼 것도 메모리에 통째로 올리지 않고 파일로만 쓴다
    (무료 서버 메모리 512MB, DB 200MB 넘으면 통째로 올리다 죽음). 원본이 아직 못 받아 간 변경(last_change 뒤)은 새 DB 에 다시 얹는다."""
    pending = changes_after(last_change)
    target = db.path()
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".upload")
    os.close(fd)
    size = 0
    try:
        with gzip.GzipFile(fileobj=stream, mode="rb") as gz, open(tmp, "wb") as out:
            while chunk := gz.read(1 << 20):
                out.write(chunk)
                size += len(chunk)
    except (OSError, EOFError, ValueError) as e:      # gzip 이 아니거나 중간에 끊김
        os.remove(tmp)
        raise ValueError(f"받은 파일을 풀지 못했습니다: {e}") from e
    try:
        con = sqlite3.connect(tmp)
        n = con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        con.close()
    except sqlite3.DatabaseError as e:
        os.remove(tmp)
        raise ValueError(f"받은 파일이 DB 가 아닙니다: {e}") from e
    os.replace(tmp, target)
    db.configure(target)                          # 표·열 맞추기 (mirror_changes 포함)
    with db.connect() as con:
        con.execute("DELETE FROM mirror_changes")
        con.executemany("INSERT INTO mirror_changes(id, at, kind, payload) VALUES(?, ?, ?, ?)",
                        [(c["id"], c["at"], c["kind"], json.dumps(c["payload"], ensure_ascii=False)) for c in pending])
    for c in pending:                             # 사본 화면에도 그대로 보이게
        apply(c)
    db.set_setting("mirror_synced_at", db.now())
    return {"postings": n, "reapplied": len(pending), "bytes": size}


# ── 변경 적용 (원본에서, 그리고 사본에서 다시 얹을 때) ────────

def apply(c: dict) -> bool:
    k, p = c["kind"], c["payload"]
    pid = postings.find_id(p.get("source", ""), p.get("source_id", "")) if "source" in p else None
    if k in ("save", "hide", "app_upsert", "app_remove", "company_avg") and not pid:
        return False                              # 원본에서 이미 지워진 공고
    if k == "save":
        postings.set_saved(pid, bool(p["value"]))
    elif k == "hide":
        postings.set_hidden(pid, bool(p["value"]))
    elif k == "company_avg":
        postings.set_company_avg(pid, p.get("value"))
    elif k == "app_upsert":
        applications.upsert(pid, p["status"], memo=p.get("memo"), applied_at=p.get("applied_at"),
                            next_at=p.get("next_at"), note=p.get("note", ""))
    elif k == "app_remove":
        applications.remove(pid)
    elif k == "profile":
        profile.save(p["data"])
    elif k == "seen":
        db.set_setting("seen_at", p["value"])
    else:
        return False
    return True


# ── 내 PC(원본) 쪽 ─────────────────────────────────────

def settings() -> dict | None:
    path = db.path().parent / "sync.json"
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return s if s.get("url") and s.get("token") else None


def snapshot_file() -> str:
    """쓰는 중에도 안전하게 원본 DB 를 복사해 gzip 파일로 (경로를 돌려줌, 쓴 뒤 지울 것). 메모리에 통째로 올리지 않음."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    out = tmp + ".gz"
    try:
        src = sqlite3.connect(db.path())
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.execute("DELETE FROM settings WHERE key IN ('crawl_lock', 'crawl_progress')")   # 사본엔 필요 없음
        dst.commit()
        dst.close()
        src.close()
        with open(tmp, "rb") as f, gzip.open(out, "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g, 1 << 20)
        return out
    finally:
        os.remove(tmp)


def snapshot_gz() -> bytes:
    """snapshot_file 을 바이트로 (테스트용)."""
    path = snapshot_file()
    try:
        return Path(path).read_bytes()
    finally:
        os.remove(path)


def push() -> dict:
    """① 사본의 변경 받기 → 원본에 반영 ② 원본 스냅샷 올리기. 결과는 settings.sync_last 에 남긴다."""
    cfg = settings()
    if not cfg:
        return {"ok": False, "error": "data/sync.json 이 없어 동기화하지 않음"}
    base = cfg["url"].rstrip("/")
    h = {"Authorization": f"Bearer {cfg['token']}", "User-Agent": "jobfit-sync"}
    last = int(db.get_setting("sync_last_change") or 0)
    started = db.now()
    try:
        r = requests.get(f"{base}/api/sync/changes", params={"after": last}, headers=h, timeout=180)
        r.raise_for_status()
        changes = r.json()["changes"]
        applied = sum(1 for c in changes if apply(c))
        if changes:
            last = max(last, max(c["id"] for c in changes))
            db.set_setting("sync_last_change", str(last))
        path = snapshot_file()
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:                # 파일을 흘려 보냄
                r = requests.post(f"{base}/api/sync/upload", data=f, timeout=600,
                                  headers={**h, "Content-Type": "application/gzip", "X-Last-Change": str(last)})
        finally:
            os.remove(path)
        r.raise_for_status()
        result = {"ok": True, "at": started, "pulled": len(changes), "applied": applied, "uploaded_mb":
                  round(size / 1e6, 2), **r.json()}
    except (requests.RequestException, KeyError, ValueError) as e:
        result = {"ok": False, "at": started, "error": f"{e.__class__.__name__}: {str(e)[:200]}"}
    db.set_setting("sync_last", json.dumps(result, ensure_ascii=False))
    return result


def last_result() -> dict | None:
    raw = db.get_setting("sync_last")
    return json.loads(raw) if raw else None

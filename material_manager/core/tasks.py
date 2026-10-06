"""백그라운드 작업 — 오래 걸리는 일(큰 업로드 반영 · 큰 엑셀 추출)을 요청 안에서 기다리지 않고 따로 돌린다.

왜: 자재 1만 건 업로드나 거래 수만 건 엑셀은 호스팅(Render 등)의 요청 제한(약 100초)에 걸린다. 요청은 바로 '작업 번호'를
돌려주고, 이 서버의 스레드가 일을 한다. 진행 상황·결과는 bg_tasks 표에 있어 다른 서버나 새로 고침 뒤에도 볼 수 있다.

- 결과 파일(엑셀 등)은 저장소(local/s3)의 uploads/task<번호>_… 에 두고 24시간 뒤 배치(cleanup_uploads)가 지운다.
- 작업은 만든 사람(과 시스템관리자)만 본다.
- 서버가 중간에 죽으면 하트비트가 끊긴 작업을 STALE_MINUTES 뒤 '중단됨'으로 표시한다 — 올린 파일은 다시 올려야 한다.
- 일 하나는 한 트랜잭션으로 끝나므로(반영은 전부 또는 아무것도) 중단돼도 반쯤 들어간 상태가 남지 않는다.
- 이 서버에서 동시에 도는 작업은 BG_WORKERS 개까지, 나머지는 줄을 선다.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import config
from core import audit, db, storage
from core.utils import now_str

log = logging.getLogger(__name__)

STALE_MINUTES = 10
BEAT_SECONDS = 30
STATUS_LABEL = {"QUEUED": "대기", "RUNNING": "진행 중", "DONE": "완료", "ERROR": "실패"}
_slots = threading.BoundedSemaphore(max(int(config.BG_WORKERS), 1))
_threads: dict[int, threading.Thread] = {}


@dataclass
class Output:
    """작업 함수가 돌려주는 결과. data 가 있으면 내려받을 파일."""
    message: str
    data: bytes | None = None
    filename: str = ""
    mime: str = ""
    ok: bool = True


class Progress:
    """작업 함수에 넘기는 진행 표시 (선택). total 을 알면 퍼센트가 보인다. 갱신은 1초에 한 번만 DB 에 쓴다."""

    def __init__(self, task_id: int):
        self.id, self._total, self._done, self._last = task_id, 0, 0, 0.0

    def total(self, n: int) -> None:
        self._total = int(n)
        self._flush(force=True)

    def advance(self, n: int = 1) -> None:
        self._done += int(n)
        self._flush()

    def _flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < 1.0:
            return
        self._last = now
        try:
            db.execute("UPDATE bg_tasks SET total = ?, done = ?, heartbeat_at = ? WHERE id = ?",
                       (self._total, min(self._done, self._total) if self._total else self._done, now_str(), self.id))
        except db.DBError:
            pass


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "result.bin"


def _beat(task_id: int, stop: threading.Event) -> None:
    while not stop.wait(BEAT_SECONDS):
        try:
            db.execute("UPDATE bg_tasks SET heartbeat_at = ? WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
                       (now_str(), task_id))
        except Exception:        # 한 번 못 써도 다음에 다시
            pass


def start(kind: str, title: str, fn: Callable[[Progress], "Output | object"], actor: dict | None,
          inline: bool | None = None) -> int:
    """작업을 만들고 바로 돌린다. 작업 번호. fn(progress) → Output 또는 services.Result(ok, message)."""
    actor = actor or audit.SYSTEM
    with db.transaction() as conn:
        tid = conn.execute(
            "INSERT INTO bg_tasks (kind, title, status, user_id, user_name, request_id, created_at, heartbeat_at) "
            "VALUES (?, ?, 'QUEUED', ?, ?, ?, ?, ?)",
            (kind, title[:200], actor.get("id"), actor.get("name", ""), audit.request_id(), now_str(), now_str())).lastrowid
        audit.record(conn, actor, "BULK_TASK", "bg_task", tid, {"kind": kind, "title": title[:200], "start": True})
    if config.BG_INLINE if inline is None else inline:
        _run(tid, fn, actor)
    else:
        t = threading.Thread(target=_run, args=(tid, fn, actor), daemon=True, name=f"mm-task-{tid}")
        _threads[tid] = t
        t.start()
    return tid


def wait(task_id: int, timeout: float = 60) -> None:
    """테스트용: 스레드가 끝나길 기다린다."""
    t = _threads.get(task_id)
    if t is not None:
        t.join(timeout)


def _run(tid: int, fn, actor: dict) -> None:
    stop = threading.Event()
    beat = threading.Thread(target=_beat, args=(tid, stop), daemon=True)
    beat.start()
    status, message, key, name, mime = "ERROR", "", "", "", ""
    try:
        with _slots:                                           # 동시에 도는 수 제한 — 나머지는 '대기'
            db.execute("UPDATE bg_tasks SET status = 'RUNNING', started_at = ?, heartbeat_at = ? WHERE id = ?",
                       (now_str(), now_str(), tid))
            out = fn(Progress(tid))
            ok = getattr(out, "ok", True)
            message = str(getattr(out, "message", "") or "")
            data = getattr(out, "data", None)
            if ok and data:
                name = _safe_name(getattr(out, "filename", "") or f"작업{tid}.bin")
                mime = getattr(out, "mime", "") or "application/octet-stream"
                ext = re.sub(r"[^A-Za-z0-9]", "", name.rsplit(".", 1)[-1])[:8] if "." in name else "bin"
                key = f"uploads/task{tid}_{secrets.token_hex(6)}.{ext or 'bin'}"      # 저장소 키는 영문·숫자만 (한글 이름은 result_name 에)
                storage.get().put(key, data)
            status = "DONE" if ok else "ERROR"
    except Exception as exc:                                    # 작업 하나의 실패가 서버를 멈추지 않는다
        log.warning("백그라운드 작업 %s 실패", tid, exc_info=True)
        text = str(exc)
        message = text if any("가" <= ch <= "힣" for ch in text) else f"처리 중 오류가 발생했습니다 ({type(exc).__name__}). 관리자에게 작업 번호 {tid}를 알려 주세요."
        status = "ERROR"
    finally:
        stop.set()
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE bg_tasks SET status = ?, message = ?, result_key = ?, result_name = ?, result_mime = ?, "
                         "finished_at = ?, heartbeat_at = ?, done = CASE WHEN total > 0 AND ? = 'DONE' THEN total ELSE done END "
                         "WHERE id = ?", (status, message[:1000], key, name, mime, now_str(), now_str(), status, tid))
            audit.record(conn, actor, "BULK_TASK", "bg_task", tid, {"status": status, "message": message[:300], "file": name or None})
    finally:
        _threads.pop(tid, None)
        try:
            from core import cache
            cache.bump()                                        # 반영한 데이터가 화면에 바로 보이게
        except Exception:
            pass


# ── 조회 ─────────────────────────────────────────────────────
def mark_stale() -> int:
    """하트비트가 끊긴 진행·대기 작업을 중단됨으로 (서버가 죽었거나 재시작). 표시한 건수."""
    cutoff = (datetime.now() - timedelta(minutes=STALE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with db.transaction() as conn:
            return conn.execute(
                "UPDATE bg_tasks SET status = 'ERROR', finished_at = ?, message = ? WHERE status IN ('QUEUED', 'RUNNING') "
                "AND COALESCE(NULLIF(heartbeat_at, ''), created_at) < ?",
                (now_str(), "서버가 중간에 멈춰 끝내지 못했습니다. 파일을 다시 올려 주세요 (반영은 전부 또는 아무것도 되지 않으므로 일부만 들어간 데이터는 없습니다).",
                 cutoff)).rowcount
    except db.DBError:
        return 0


def get(task_id: int) -> dict | None:
    df = db.query_df("SELECT * FROM bg_tasks WHERE id = ?", (task_id,))
    return None if df.empty else {k: (None if v != v else v) for k, v in df.iloc[0].to_dict().items()}


def visible(task: dict | None, user: dict) -> bool:
    return bool(task) and (task["user_id"] == user.get("id") or user.get("role") == "ADMIN")


def list_for(user: dict, limit: int = 50):
    mark_stale()
    if user.get("role") == "ADMIN":
        return db.query_df("SELECT * FROM bg_tasks ORDER BY id DESC LIMIT ?", (limit,))
    return db.query_df("SELECT * FROM bg_tasks WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user.get("id"), limit))


def result_bytes(task: dict) -> bytes | None:
    return storage.get().get(task["result_key"]) if task.get("result_key") else None


def active_count(user_id) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM bg_tasks WHERE user_id = ? AND status IN ('QUEUED', 'RUNNING')", (user_id,)) or 0)


def cleanup(file_hours: int = 24, row_days: int = 14) -> str:
    """결과 파일은 24시간 뒤, 기록은 14일 뒤 정리 (배치 cleanup_uploads 에서)."""
    cut_files = (datetime.now() - timedelta(hours=file_hours)).strftime("%Y-%m-%d %H:%M:%S")
    cut_rows = (datetime.now() - timedelta(days=row_days)).strftime("%Y-%m-%d %H:%M:%S")
    removed = 0
    old = db.query_df("SELECT id, result_key FROM bg_tasks WHERE result_key <> '' AND finished_at < ? AND finished_at <> ''", (cut_files,))
    store = storage.get()
    for tid, key in old.itertuples(index=False):
        try:
            store.delete(key)
            removed += 1
        except Exception:
            log.warning("작업 결과 파일 정리 실패: %s", key, exc_info=True)
            continue
        db.execute("UPDATE bg_tasks SET result_key = '' WHERE id = ?", (int(tid),))
    with db.transaction() as conn:
        rows = conn.execute("DELETE FROM bg_tasks WHERE created_at < ? AND status IN ('DONE', 'ERROR')", (cut_rows,)).rowcount
    mark_stale()
    return f"작업 파일 {removed}건 · 기록 {rows}건 정리"

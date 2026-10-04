"""오래 걸리는 엑셀 업로드·내려받기를 웹 요청 밖에서 처리한다.

  웹 요청은 길어야 수십 초에 끊긴다(nginx proxy_read_timeout 120초, Render 100초). 매출 2만 행 업로드나
  15MB 백업 엑셀은 몇 분 걸리므로 요청은 바로 돌려주고, 받은 서버의 백그라운드 스레드가 처리한다.
  진행률 · 결과 · 행별 오류 · 만든 파일은 bulk_tasks 에 남겨 어느 서버에서 열어도 보인다.

  * 별도 워커 없이 동작한다 (시연 서버처럼 웹 서버 하나뿐이어도)
  * 서버가 처리 도중 다시 시작되면 STALE_MINUTES 뒤 '중단' 으로 바뀐다. 업로드는 같은 행을 건너뛰므로 다시 올려도 된다
  * 만든 파일은 저장소(bulk/<id>/…)에 두고, 만든 사람(또는 관리자)만 내려받는다 — 내려받기는 감사로그에 남는다
"""
from __future__ import annotations

import contextvars
import json
import socket
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import pandas as pd

from . import sales_db as db

STALE_MINUTES = 10
SYNC = False                     # True 면 같은 스레드에서 바로 실행 (테스트)
INLINE_ROWS = 300                # 이보다 작은 업로드는 요청 안에서 바로 처리


class Progress:
    """처리한 행 수를 1초에 한 번 정도만 DB 에 쓴다."""

    def __init__(self, task_id: int):
        self.task_id, self.last = task_id, 0.0

    def __call__(self, done: int, total: Optional[int] = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last < 1.0:
            return
        self.last = now
        with db.get_conn() as conn:
            conn.execute("UPDATE bulk_tasks SET done=?, total=COALESCE(?, total), updated_at=? WHERE id=?",
                         (int(done), total, db._now(), self.task_id))


def start(kind: str, title: str, fn: Callable[[Progress], dict], user: dict, total: int = 0,
          inline: bool = False) -> int:
    """작업을 만들고 실행한다. fn(progress) 는 {'ok','skipped','errors','file': (이름, bytes), 'pii', 'message'} 를 돌려준다."""
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO bulk_tasks (kind, title, status, total, server, created_by, created_by_id, "
                           "created_at, updated_at) VALUES (?,?, '진행중', ?,?,?,?,?,?)",
                           (kind, title[:200], int(total), socket.gethostname(), user.get("name"), user.get("id"),
                            db._now(), db._now()))
        task_id = int(cur.lastrowid)
    db.audit(f"{kind}시작", "대량작업", task_id, {"작업": title, "행수": total})

    def run() -> None:
        try:
            result = fn(Progress(task_id)) or {}
            _finish(task_id, result)
        except Exception as exc:   # noqa: BLE001 - 어떤 실패든 작업에 남긴다
            with db.get_conn() as conn:
                conn.execute("UPDATE bulk_tasks SET status='실패', message=?, updated_at=?, finished_at=? WHERE id=?",
                             (f"{type(exc).__name__}: {exc}"[:1000], db._now(), db._now(), task_id))

    if inline or SYNC:
        run()
    else:                          # 요청을 보낸 사람의 권한 범위·감사 주체를 그대로 가지고 간다
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(run,), name=f"bulk-{task_id}", daemon=True).start()
    return task_id


def _finish(task_id: int, result: dict) -> None:
    key = name = None
    if result.get("file"):
        from .storage import get_storage
        name, data = result["file"]
        key = get_storage().put(f"bulk/{task_id}/{name}", data)
    errors = result.get("errors") or []
    with db.get_conn() as conn:
        conn.execute("UPDATE bulk_tasks SET status='완료', ok=?, skipped=?, error_count=?, errors=?, message=?, "
                     "file_key=?, file_name=?, pii=?, done=CASE WHEN total > 0 THEN total ELSE done END, "
                     "updated_at=?, finished_at=? WHERE id=?",
                     (int(result.get("ok") or 0), int(result.get("skipped") or 0), len(errors),
                      json.dumps(errors[:5000], ensure_ascii=False) if errors else None, result.get("message"),
                      key, name, 1 if result.get("pii") else 0, db._now(), db._now(), task_id))
    db.audit("대량작업완료", "대량작업", task_id, {"등록": result.get("ok"), "건너뜀": result.get("skipped"),
                                                "오류": len(errors), "파일": name})


def mark_stale() -> int:
    """처리하던 서버가 꺼져 진행이 멈춘 작업."""
    limit = (datetime.now() - timedelta(minutes=STALE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        return conn.execute("UPDATE bulk_tasks SET status='중단', message=?, finished_at=? "
                            "WHERE status='진행중' AND updated_at < ?",
                            ("처리하던 서버가 다시 시작되어 멈췄습니다. 업로드는 같은 파일을 다시 올리면 이미 등록된 행은 건너뜁니다.",
                             db._now(), limit)).rowcount


def get(task_id: int, user: dict) -> dict:
    from . import enterprise as ent
    row = db._one("SELECT * FROM bulk_tasks WHERE id=?", [int(task_id)])
    if not row or (int(row.get("created_by_id") or 0) != int(user["id"]) and not ent.has_role(user, "ADMIN")):
        raise PermissionError("내가 만든 작업만 볼 수 있습니다.")
    return row


def recent(user: dict, limit: int = 10) -> list[dict]:
    mark_stale()
    rows = db._df("SELECT * FROM bulk_tasks WHERE created_by_id=? ORDER BY id DESC LIMIT ?",
                  [int(user["id"]), int(limit)]).to_dict("records")
    for r in rows:
        total = int(r.get("total") or 0)
        r["percent"] = min(100, int(int(r.get("done") or 0) * 100 / total)) if total else (100 if r["status"] == "완료" else 0)
    return rows


def errors_frame(row: dict) -> pd.DataFrame:
    errors: list[Any] = json.loads(row.get("errors") or "[]")
    return pd.DataFrame(errors, columns=["엑셀 행", "오류 내용"]) if errors else pd.DataFrame(columns=["엑셀 행", "오류 내용"])


def file_bytes(row: dict) -> bytes:
    from .storage import get_storage
    if not row.get("file_key"):
        raise ValueError("이 작업에는 만든 파일이 없습니다.")
    return get_storage().get(row["file_key"])


def running(user: dict) -> bool:
    return bool(db._scalar("SELECT COUNT(*) FROM bulk_tasks WHERE created_by_id=? AND status='진행중'", [int(user["id"])]))

"""배치 작업. 서버가 여러 대여도 한 작업은 한 번에 한 서버만 돌린다.

잠금(임대, lease) 방식:
  job_locks 행을 '비어 있거나 임대가 끝났고, 실행 주기가 됐을 때'만 내 이름으로 바꾼다(한 문장 UPDATE).
  바뀐 행이 1개면 내가 잡은 것, 0개면 다른 서버가 돌리는 중이거나 아직 때가 아닌 것.
  서버가 작업 중 죽어도 lease_until이 지나면 다른 서버가 이어받는다.

실행: `flask --app app batch --loop` (모든 서버에서 켜 두어도 된다) 또는 화면의 '지금 실행'.
"""

import logging
import os
import socket
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

import config
from core import backup, db, once, sap, storage
from core.utils import now_str


@dataclass
class Job:
    name: str
    label: str
    interval: int                 # 초
    func: Callable[[], str]       # 결과 메시지를 돌려준다
    enabled: Callable[[], bool] = lambda: True


def _sap_sync() -> str:
    counts = sap.process_outbox()
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def _cleanup_uploads() -> str:
    """반영하지 않고 남은 업로드 미리보기 파일 정리."""
    store = storage.get()
    cutoff = time.time() - config.UPLOAD_KEEP_HOURS * 3600
    removed = 0
    for key in store.keys("uploads/"):
        try:
            if store.modified(key) < cutoff:
                store.delete(key)
                removed += 1
        except Exception:                          # 한 파일 실패로 정리를 멈추지 않는다 (다음 주기에 다시)
            logging.getLogger(__name__).warning("업로드 임시파일 정리 실패: %s", key, exc_info=True)
            continue
    removed_once = once.cleanup()
    return f"삭제 {removed}건 · 중복 제출 기록 정리 {removed_once}건"


def _storage_flush() -> str:
    return storage.get().flush()


def _backup() -> str:
    return backup.run()


def _master_sync() -> str:
    from core import master_sync
    counts = master_sync.sync()
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def _master_enabled() -> bool:
    from core import master_sync
    return master_sync.enabled()


JOBS = {
    "sap_sync": Job("sap_sync", "ERP·SAP 전송", 60, _sap_sync, enabled=sap.enabled),
    "sap_master_sync": Job("sap_master_sync", "ERP·SAP 마스터 동기화", 3600, _master_sync, enabled=_master_enabled),
    "cleanup_uploads": Job("cleanup_uploads", "업로드 임시파일 정리", 3600, _cleanup_uploads),
    "db_backup": Job("db_backup", "DB 자동 백업", max(config.BACKUP_HOURS, 1) * 3600, _backup, enabled=backup.enabled),
    "storage_flush": Job("storage_flush", "S3 임시 보관 파일 올리기", 300, _storage_flush,
                         enabled=lambda: config.STORAGE == "s3"),
}


def worker_id() -> str:
    """배치 잠금·실행 기록에 남는 서버 이름. MM_WORKER_ID로 정할 수 있다(컨테이너는 이름이 매번 바뀌므로 'app-01'처럼)."""
    return f"{os.getenv('MM_WORKER_ID') or socket.gethostname()}:{os.getpid()}"


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _acquire(name: str, interval: int, holder: str, force: bool) -> bool:
    now = datetime.now()
    with db.transaction() as conn:
        conn.execute("INSERT INTO job_locks (name) VALUES (?) ON CONFLICT (name) DO NOTHING", (name,))
        due = "" if force else " AND (last_started = '' OR last_started <= ?)"
        params = [holder, _ts(now + timedelta(seconds=config.JOB_LEASE_SECONDS)), _ts(now), name, _ts(now)]
        if not force:
            params.append(_ts(now - timedelta(seconds=interval)))
        cur = conn.execute(
            "UPDATE job_locks SET holder = ?, lease_until = ?, last_started = ? "
            f"WHERE name = ? AND (holder = '' OR lease_until < ?){due}", params)
        return cur.rowcount == 1


def _release(name: str, holder: str, started: str, status: str, message: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE job_locks SET holder = '', lease_until = '', last_finished = ?, last_status = ?, "
                     "last_message = ? WHERE name = ? AND holder = ?",
                     (now_str(), status, message[:500], name, holder))
        conn.execute("INSERT INTO job_runs (name, holder, started_at, finished_at, status, message) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (name, holder, started, now_str(), status, message[:2000]))


def run(name: str, force: bool = False, holder: str | None = None) -> tuple[bool, str]:
    """(실행했는지, 메시지). force=True면 주기와 상관없이(단, 다른 서버가 돌리는 중이면 건너뜀)."""
    job = JOBS[name]
    if not job.enabled():
        return False, "사용하지 않는 작업입니다."
    holder = holder or worker_id()
    if not _acquire(name, job.interval, holder, force):
        return False, "다른 서버가 실행 중이거나 아직 실행 주기가 아닙니다."
    started = now_str()
    try:
        message = job.func() or ""
        _release(name, holder, started, "OK", message)
        return True, message
    except Exception as exc:                       # 작업 하나가 실패해도 배치 루프는 계속 돈다
        _release(name, holder, started, "ERROR", f"{exc}\n{traceback.format_exc(limit=3)}")
        return True, f"오류: {exc}"


def run_due(holder: str | None = None) -> dict[str, str]:
    """주기가 된 작업을 모두 한 번씩."""
    return {name: msg for name in JOBS for ran, msg in [run(name, holder=holder)] if ran}


def status_df() -> pd.DataFrame:
    df = db.query_df("SELECT * FROM job_locks")
    rows = []
    for name, job in JOBS.items():
        r = df[df["name"] == name].iloc[0].to_dict() if not df.empty and name in set(df["name"]) else {}
        rows.append({"name": name, "label": job.label, "interval": job.interval, "enabled": job.enabled(),
                     "holder": r.get("holder", ""), "last_started": r.get("last_started", ""),
                     "last_finished": r.get("last_finished", ""), "last_status": r.get("last_status", ""),
                     "last_message": r.get("last_message", "")})
    return pd.DataFrame(rows)


def runs_df(limit: int = 50) -> pd.DataFrame:
    return db.query_df("SELECT id, name, holder, started_at, finished_at, status, message FROM job_runs "
                       "ORDER BY id DESC LIMIT ?", (limit,))

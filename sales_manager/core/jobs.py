"""작업 큐 · 워커 · 스케줄러 (DB 테이블 기반 — 별도 메시지 서버 불필요)

  enqueue("erp.send")               작업 등록 (dedupe_key 로 중복 등록 방지)
  python manage.py worker           워커 실행 — 여러 대 띄워도 같은 작업을 두 번 처리하지 않는다
                                    (PostgreSQL: FOR UPDATE SKIP LOCKED)
  스케줄러                          워커가 30초마다 tick() — 여러 대 중 잠금을 얻은 한 곳만 예약 작업을 올린다
                                    (PostgreSQL advisory lock)

실패한 작업은 1·2·4·8…분 간격으로 max_attempts 까지 다시 시도하고, 끝내 실패하면 관리자에게 알린다.
실행 중인 작업은 LEASE_SECONDS 마다 잠금 시각을 갱신한다 → 오래 걸리는 작업(백업 등)을 다른 서버가 '죽은 작업'으로 보고
두 번 실행하지 않는다. 워커가 죽어 갱신이 멈춘 작업만 STALE_MINUTES 뒤 다시 대기로 돌린다.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from . import database
from . import sales_db as db

LOG = logging.getLogger("sales.jobs")
STALE_MINUTES = 10                  # 잠금 갱신이 이만큼 멈추면 워커가 죽은 것으로 본다
LEASE_SECONDS = 60                  # 실행 중 잠금 갱신 간격
HANDLERS: dict[str, Callable[[dict], Any]] = {}
FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    return datetime.now()


def _ts(dt: datetime) -> str:
    return dt.strftime(FMT)


def handler(kind: str):
    """작업 처리 함수 등록."""
    def wrap(fn):
        HANDLERS[kind] = fn
        return fn
    return wrap


# ---------------------------------------------------------------------------
# 등록 · 조회
# ---------------------------------------------------------------------------
def enqueue(kind: str, payload: Optional[dict] = None, run_after: Optional[datetime] = None,
            dedupe_key: Optional[str] = None, max_attempts: int = 5, conn=None) -> Optional[int]:
    """작업 등록. 같은 dedupe_key 가 이미 있으면 등록하지 않고 None."""
    sql = ("INSERT OR IGNORE INTO jobs (kind, payload, status, max_attempts, run_after, dedupe_key, created_at) "
           "VALUES (?, ?, '대기', ?, ?, ?, ?)")
    params = (kind, json.dumps(payload or {}, ensure_ascii=False, default=str), int(max_attempts),
              _ts(run_after or _now()), dedupe_key, _ts(_now()))
    if conn is not None:
        cur = conn.execute(sql, params)
    else:
        with db.get_conn() as c:
            cur = c.execute(sql, params)
    new_id = cur.lastrowid if cur.rowcount else None
    return int(new_id) if new_id else None


def list_jobs(status: str = "", limit: int = 200):
    sql = ("SELECT id, kind AS 작업, status AS 상태, attempts AS 시도, max_attempts AS 최대, "
           "run_after AS 실행예정, started_at AS 시작, finished_at AS 종료, locked_by AS 처리서버, "
           "last_error AS 오류, created_at AS 등록 FROM jobs WHERE 1=1")
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db._df(sql + " ORDER BY id DESC LIMIT ?", [*params, int(limit)])


def summary() -> dict:
    df = db._df("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    return {r.status: int(r.n) for r in df.itertuples()}


def retry(job_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET status='대기', attempts=0, run_after=?, last_error=NULL "
                     "WHERE id=? AND status IN ('실패','취소')", (_ts(_now()), job_id))
    db.audit("작업재시도", "작업", job_id)


# ---------------------------------------------------------------------------
# 워커
# ---------------------------------------------------------------------------
def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim(worker: str) -> Optional[dict]:
    """실행할 작업 하나를 가져온다. 다른 워커가 잡은 작업은 건너뛴다."""
    now = _ts(_now())
    with db.get_conn() as conn:
        if conn.pg:
            row = conn.execute(
                "UPDATE jobs SET status='실행중', locked_by=?, locked_at=?, started_at=?, attempts=attempts+1 "
                "WHERE id = (SELECT id FROM jobs WHERE status='대기' AND run_after <= ? "
                "ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
                (worker, now, now, now)).fetchone()
            return dict(row) if row else None
        database.lock(conn, "jobs")
        row = conn.execute("SELECT id FROM jobs WHERE status='대기' AND run_after <= ? "
                           "ORDER BY run_after, id LIMIT 1", (now,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE jobs SET status='실행중', locked_by=?, locked_at=?, started_at=?, "
                     "attempts=attempts+1 WHERE id=?", (worker, now, now, row["id"]))
        return dict(conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())


def _finish(job: dict, status: str, result: Any = None, error: str | None = None,
            run_after: Optional[datetime] = None) -> bool:
    """잠금을 가진 워커만 결과를 쓴다 (잠금을 잃었는데 덮어쓰면 다른 워커의 실행 상태가 지워진다)."""
    with db.get_conn() as conn:
        done = conn.execute("UPDATE jobs SET status=?, result=?, last_error=?, finished_at=?, run_after=?, "
                            "locked_by=NULL, locked_at=NULL WHERE id=? AND status='실행중' AND locked_by=?",
                            (status, json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                             error, _ts(_now()) if status in ("완료", "실패") else None,
                             _ts(run_after or _now()), job["id"], job["locked_by"])).rowcount
    if not done:
        LOG.warning("job %s %s: 잠금을 잃어 결과를 기록하지 않음", job["id"], job["kind"])
    return bool(done)


class _Lease:
    """실행하는 동안 LEASE_SECONDS 마다 locked_at 을 갱신한다."""

    def __init__(self, job: dict):
        self.job, self.stop = job, threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"job-lease-{job['id']}", daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(LEASE_SECONDS):
            try:
                with db.get_conn() as conn:
                    kept = conn.execute("UPDATE jobs SET locked_at=? WHERE id=? AND status='실행중' AND locked_by=?",
                                        (_ts(_now()), self.job["id"], self.job["locked_by"])).rowcount
                if not kept:
                    return
            except Exception:   # noqa: BLE001 - 갱신 실패는 다음 간격에 다시
                LOG.exception("job lease renew failed")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()


def run_one(worker: Optional[str] = None) -> bool:
    """작업 하나를 처리했으면 True."""
    job = claim(worker or worker_id())
    if not job:
        return False
    fn = HANDLERS.get(job["kind"])
    db.set_context("batch", None)
    db.set_ip(None)
    from . import company
    company.refresh()                               # 관리자가 바꾼 회사 설정을 워커도 따른다
    try:
        if fn is None:
            raise LookupError(f"등록되지 않은 작업 종류입니다: {job['kind']}")
        with _Lease(job):
            result = fn(json.loads(job["payload"] or "{}"))
        _finish(job, "완료", result)
        LOG.info("job %s %s done", job["id"], job["kind"])
    except Exception as exc:   # noqa: BLE001 - 어떤 실패든 기록하고 다음 작업으로 넘어간다
        error = f"{type(exc).__name__}: {exc}"[:1000]
        LOG.warning("job %s %s failed: %s\n%s", job["id"], job["kind"], error, traceback.format_exc())
        if int(job["attempts"]) < int(job["max_attempts"]):
            delay = min(2 ** (int(job["attempts"]) - 1), 60)
            _finish(job, "대기", error=error, run_after=_now() + timedelta(minutes=delay))
        else:
            if not _finish(job, "실패", error=error):
                return True
            from . import notify
            notify.notify_role("ADMIN", "작업실패", f"배치 작업 실패: {job['kind']}",
                               f"{job['attempts']}번 시도했지만 실패했습니다. {error}", "/admin/jobs?status=실패")
    return True


def recover_stale() -> int:
    """워커가 죽어 '실행중' 으로 남은 작업을 다시 대기로 돌린다."""
    limit = _ts(_now() - timedelta(minutes=STALE_MINUTES))
    with db.get_conn() as conn:
        return conn.execute("UPDATE jobs SET status='대기', locked_by=NULL, locked_at=NULL "
                            "WHERE status='실행중' AND locked_at < ?", (limit,)).rowcount


def run_pending(limit: int = 100, worker: Optional[str] = None) -> int:
    """지금 실행 가능한 작업을 limit 개까지 처리한다 (관리자 화면의 '지금 실행', 테스트용)."""
    done = 0
    while done < limit and run_one(worker):
        done += 1
    return done


def run_worker(poll_seconds: float = 2.0, tick_seconds: float = 30.0, stop: Optional[Callable[[], bool]] = None):
    """워커 루프: 스케줄러 tick → 작업 처리 → 잠시 대기."""
    me = worker_id()
    LOG.info("worker %s started", me)
    last_tick = 0.0
    from .observability import read_only
    while not (stop and stop()):
        if read_only():                       # 점검(읽기 전용) 중에는 쓰기 작업을 하지 않는다
            if time.monotonic() - last_tick >= tick_seconds:
                try:
                    heartbeat(me)             # 살아 있다는 신호는 남긴다 (점검 중 '워커 응답 없음' 오경보 방지)
                except Exception:   # noqa: BLE001
                    LOG.exception("heartbeat failed")
                last_tick = time.monotonic()
            time.sleep(poll_seconds)
            continue
        if time.monotonic() - last_tick >= tick_seconds:
            try:
                heartbeat(me)
                tick()
                recover_stale()
            except Exception:   # noqa: BLE001
                LOG.exception("scheduler tick failed")
            last_tick = time.monotonic()
        if not run_one(me):
            time.sleep(poll_seconds)


def heartbeat(worker: str) -> None:
    """워커가 살아 있다는 신호 (지표 sales_worker_heartbeat_age_seconds 로 감시)."""
    now = _ts(_now())
    with db.get_conn() as conn:
        name = f"worker:{worker}"
        if conn.execute("UPDATE scheduler_state SET last_run_at=? WHERE name=?", (now, name)).rowcount == 0:
            conn.execute("INSERT INTO scheduler_state (name, last_slot, last_run_at) VALUES (?,?,?)",
                         (name, "heartbeat", now))
        # 하루 넘게 신호가 없는 워커 기록은 지운다 (재배포로 호스트명이 바뀌는 경우)
        conn.execute("DELETE FROM scheduler_state WHERE name LIKE 'worker:%' AND last_run_at < ?",
                     (_ts(_now() - timedelta(days=1)),))


# ---------------------------------------------------------------------------
# 스케줄러
# ---------------------------------------------------------------------------
@dataclass
class Schedule:
    kind: str
    every_minutes: int = 0              # N분마다
    daily: str = ""                     # "HH:MM" 매일
    weekday: Optional[int] = None       # 0=월 … (daily 와 함께 쓰면 매주)
    enabled: Callable[[], bool] = lambda: True
    description: str = ""

    def slot(self, now: datetime) -> Optional[str]:
        """지금 실행해야 하는 구간 이름. 같은 구간은 한 번만 실행한다."""
        if self.every_minutes:
            minute = (now.hour * 60 + now.minute) // self.every_minutes * self.every_minutes
            return f"{now:%Y-%m-%d} {minute // 60:02d}:{minute % 60:02d}"
        hh, mm = (int(x) for x in self.daily.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return None
        if self.weekday is not None and now.weekday() != self.weekday:
            return None
        return f"{now:%Y-%m-%d}"


def _setting_on(key: str) -> bool:
    from . import company
    return int(company.get(key) or 0) > 0


def _setting_bool(key: str) -> bool:
    from . import company
    return bool(company.get(key))


def _pii_on() -> bool:
    from . import company
    return int(company.get("pii_retention_years") or 0) > 0


def _env_on(name: str) -> Callable[[], bool]:
    return lambda: bool(os.environ.get(name))


SCHEDULES: list[Schedule] = [
    Schedule("erp.send", every_minutes=5, description="ERP 전송 대기열 처리"),
    Schedule("notify.digest", daily="08:30", description="미수·마감 임박 요약 알림"),
    Schedule("approval.escalate", every_minutes=60, description="결재 기한 경과 독촉·상위 보고"),
    Schedule("forecast.snapshot", daily="07:00", weekday=0, description="주간 파이프라인 스냅샷"),
    Schedule("backup.db", daily="02:00", description="DB 백업"),
    Schedule("hr.sync", daily="03:00", enabled=_env_on("SALES_HR_SOURCE"), description="인사 시스템 동기화"),
    Schedule("jobs.cleanup", daily="04:00", description="오래된 완료 작업 정리"),
    Schedule("storage.flush", every_minutes=5, enabled=lambda: os.environ.get("SALES_STORAGE") == "s3",
             description="저장소(S3) 장애 때 임시 보관한 파일 다시 올리기"),
    Schedule("audit.archive", daily="01:45", enabled=lambda: _setting_on("audit_retention_years"),
             description="보관기간 지난 감사로그를 파일로 이관 (회사 설정)"),
    Schedule("credit.autoblock", daily="06:00", enabled=lambda: _setting_on("auto_block_overdue_days") or
             _setting_bool("auto_block_over_credit"), description="연체·여신초과 거래처 자동 거래정지 (회사 설정)"),
    Schedule("privacy.purge", daily="01:30", enabled=lambda: _pii_on(),
             description="종료 거래처 고객 연락처 파기 (회사 설정의 보관기간)"),
]


def tick(now: Optional[datetime] = None) -> list[str]:
    """예약 시각이 된 작업을 큐에 올린다. 서버 여러 대 중 잠금을 얻은 한 곳만 실행한다."""
    now = now or _now()
    queued: list[str] = []
    with db.get_conn() as conn:
        if not database.try_lock(conn, "scheduler"):
            return queued
        state = {r["name"]: r["last_slot"] for r in
                 conn.execute("SELECT name, last_slot FROM scheduler_state").fetchall()}
        for sched in SCHEDULES:
            if not sched.enabled():
                continue
            slot = sched.slot(now)
            if not slot or state.get(sched.kind) == slot:
                continue
            enqueue(sched.kind, {"slot": slot}, dedupe_key=f"{sched.kind}@{slot}", conn=conn)
            if sched.kind in state:
                conn.execute("UPDATE scheduler_state SET last_slot=?, last_run_at=? WHERE name=?",
                             (slot, _ts(now), sched.kind))
            else:
                conn.execute("INSERT INTO scheduler_state (name, last_slot, last_run_at) VALUES (?,?,?)",
                             (sched.kind, slot, _ts(now)))
            queued.append(sched.kind)
    return queued


def workers():
    """워커별 마지막 신호. 2분 넘게 신호가 없으면 '응답 없음'."""
    import pandas as pd
    df = db._df("SELECT name, last_run_at FROM scheduler_state WHERE name LIKE 'worker:%' ORDER BY last_run_at DESC")
    if df.empty:
        return pd.DataFrame(columns=["워커", "마지막 신호", "상태"])
    limit = _ts(_now() - timedelta(minutes=2))
    return pd.DataFrame({"워커": df["name"].str[7:], "마지막 신호": df["last_run_at"],
                         "상태": ["정상" if t >= limit else "응답 없음" for t in df["last_run_at"]]})


def schedule_table():
    import pandas as pd
    state = {r["name"]: r for r in db._df("SELECT * FROM scheduler_state").to_dict("records")}
    rows = []
    for s in SCHEDULES:
        when = (f"{s.every_minutes}분마다" if s.every_minutes else
                (f"매주 {'월화수목금토일'[s.weekday]} {s.daily}" if s.weekday is not None else f"매일 {s.daily}"))
        st = state.get(s.kind, {})
        rows.append({"작업": s.kind, "설명": s.description, "주기": when,
                     "사용": "예" if s.enabled() else "아니오(설정 없음)",
                     "최근 실행": st.get("last_run_at") or ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 기본 작업 처리기
# ---------------------------------------------------------------------------
@handler("etax.issue")
def _etax_issue(payload: dict):
    from . import etax
    return etax.process(int(payload["id"]))


@handler("credit.autoblock")
def _credit_autoblock(payload: dict):
    from . import credit
    return credit.auto_block()


@handler("audit.archive")
def _audit_archive(payload: dict):
    from . import retention
    return retention.archive_audit()


@handler("privacy.purge")
def _privacy_purge(payload: dict):
    from . import company
    return company.purge_pii()


@handler("erp.send")
def _erp_send(payload: dict):
    from . import erp
    stuck = erp.recover_stuck()
    from . import etax                          # 같은 5분 주기에 멈춘 전자세금계산서 전송도 되살린다
    etax_stuck = etax.recover_stuck() if etax.enabled() else 0
    return {**erp.process_outbox(), "stuck": stuck, "etax_stuck": etax_stuck}


@handler("forecast.snapshot")
def _snapshot(payload: dict):
    from . import enterprise as ent
    return {"rows": ent.take_snapshot(_now().strftime("%Y-%m"))}


@handler("backup.db")
def _backup(payload: dict):
    folder = os.environ.get("SALES_BACKUP_DIR", os.path.join(database.BASE_DIR, "data", "backups"))
    return {"file": os.path.basename(db.backup_database(folder))}


@handler("storage.flush")
def _storage_flush(payload: dict) -> dict:
    from .storage import get_storage
    st = get_storage()
    return st.flush() if hasattr(st, "flush") else {"uploaded": 0, "left": 0}


@handler("jobs.cleanup")
def _cleanup(payload: dict):
    limit = _ts(_now() - timedelta(days=int(os.environ.get("SALES_JOB_KEEP_DAYS", "30"))))
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM jobs WHERE status='완료' AND finished_at < ?", (limit,)).rowcount
        # API 호출 수(분 단위)는 하루, 멱등키 응답은 7일 보관
        usage = conn.execute("DELETE FROM api_usage WHERE win < ?",
                             ((_now() - timedelta(days=1)).strftime("%Y%m%d%H%M"),)).rowcount
        idem = conn.execute("DELETE FROM api_idempotency WHERE created_at < ?",
                            (_ts(_now() - timedelta(days=7)),)).rowcount
        idem += conn.execute("DELETE FROM form_submissions WHERE created_at < ?",
                             (_ts(_now() - timedelta(days=7)),)).rowcount
    return {"deleted": n, "api_usage": usage, "idempotency": idem}


@handler("hr.sync")
def _hr_sync(payload: dict):
    from . import hr
    return hr.sync_from_source(apply=True)


@handler("notify.deliver")
def _deliver(payload: dict):
    from . import notify
    return notify.deliver(payload.get("ids", []))


@handler("notify.digest")
def _digest(payload: dict):
    from . import notify
    return notify.daily_digest()


@handler("approval.escalate")
def _escalate(payload: dict):
    from . import enterprise as ent
    return ent.escalate_overdue_steps()

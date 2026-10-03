"""시연 서버 데이터 — 첫 화면이 몇 초 안에 열리고, 매일 새벽 처음 샘플로 돌아간다.

샘플을 서버가 켜질 때 만들면 무료 서버에서 2분 넘게 걸려 첫 방문자가 기다린다. 그래서
  1) 배포(빌드) 때 `python manage.py demo-build` 로 샘플 DB(data/demo_template.db)를 미리 만들어 두고
  2) 서버가 켜질 때 DB 가 비어 있으면 그 파일 내용을 넣어 바로 연다 (prepare)
  3) 샘플 기준일이 '오늘'(새벽 RESET_HOUR 시 기준)이 아니면, 별도 프로세스가 오늘 기준 샘플을 새 파일로 만든 뒤
     DB 경로를 바꿔 끼운다 (서버는 요청마다 연결을 새로 여므로 다음 요청부터 새 DB). 켜져 있는 동안 매일 새벽에도 같다.
  4) '샘플로 되돌리기'(reset) — 처음 샘플(PRISTINE, 손대지 않은 원본)을 지금 DB 에 다시 넣는다.
시연 서버(SALES_DEMO=1) 전용. 바꿔 끼우거나 되돌리면 방문자가 바꾼 내용은 사라진다.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

from . import database

log = logging.getLogger("sales.demo")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.environ.get("SALES_DEMO_TEMPLATE") or os.path.join(HERE, "data", "demo_template.db")
RESET_HOUR = int(os.environ.get("SALES_DEMO_RESET_HOUR", "4"))   # 매일 이 시각 뒤 첫 확인 때 새 샘플 (서버 시간대, Render 는 TZ=Asia/Seoul)
CHECK_SECONDS = 300
PRISTINE = TEMPLATE                     # 지금 DB 의 처음 상태 (되돌리기용 원본)


def _marker(path: str) -> str:
    return path + ".date"


def seeded_on(path: str) -> str:
    """그 DB 샘플의 기준일 (YYYY-MM-DD). 모르면 ''."""
    try:
        with open(_marker(path), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def due_day(now: datetime | None = None) -> str:
    """지금 있어야 할 샘플 기준일 — 새벽 RESET_HOUR 시 전이면 어제 것으로 충분하다."""
    now = now or datetime.now()
    return (now.date() - timedelta(days=1 if now.hour < RESET_HOUR else 0)).isoformat()


def _remove_db(path: str) -> None:
    for p in (path, path + "-wal", path + "-shm", _marker(path)):
        if os.path.exists(p):
            os.remove(p)


def _copy_db(src: str, dst: str) -> None:
    """src 내용을 dst 에 통째로 넣는다 (dst 가 열려 있어도 됨 — 파일을 지우고 복사하지 않음)."""
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    if os.path.exists(_marker(src)):
        shutil.copyfile(_marker(src), _marker(dst))


def build(out: str) -> float:
    """오늘 기준 시연 DB 를 out 에 만든다 (다른 프로세스에서 — 이 프로세스의 DB 연결·전역 상태를 건드리지 않음)."""
    started = time.time()
    tmp = out + ".building"
    _remove_db(tmp)
    env = {**os.environ, "SALES_DB_PATH": tmp, "SALES_DEMO": "1", "PYTHONIOENCODING": "utf-8"}
    manage = os.path.join(HERE, "manage.py")
    for step in (["db", "upgrade"], ["demo-init"]):
        subprocess.run([sys.executable, manage, *step], env=env, cwd=HERE, check=True)
    conn = sqlite3.connect(tmp)
    try:                                                # WAL 내용을 본 파일에 합쳐 파일 하나로 만든다
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    with open(_marker(tmp), "w", encoding="utf-8") as f:
        f.write(date.today().isoformat())
    _remove_db(out)
    os.replace(tmp, out)
    os.replace(_marker(tmp), _marker(out))
    for p in (tmp + "-wal", tmp + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    return time.time() - started


def _has_data(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            return bool(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def prepare() -> str:
    """서버 시작 직전: 빈 DB 면 미리 만든 샘플을 넣는다. 반환: 지금 샘플의 기준일."""
    path = database.DB_PATH
    if not _has_data(path):
        if not os.path.exists(TEMPLATE):                # 미리 만든 파일이 없으면 여기서 만든다 (느림 — 배포 빌드에서 demo-build 권장)
            log.info("시연: 미리 만든 샘플이 없어 지금 만듭니다")
            build(TEMPLATE)
        _copy_db(TEMPLATE, path)
        log.info("시연: 미리 만든 샘플로 시작 (기준일 %s)", seeded_on(path) or "모름")
    return seeded_on(path)


_lock = threading.Lock()


def reset() -> str:
    """샘플로 되돌리기: 처음 샘플을 지금 DB 에 다시 넣는다. 반환: 샘플 기준일."""
    with _lock:
        _copy_db(PRISTINE, database.DB_PATH)
    return seeded_on(database.DB_PATH)


def refresh_if_stale(now: datetime | None = None) -> bool:
    """샘플 기준일이 지났으면(매일 새벽 RESET_HOUR 시) 새로 만들어 바꿔 끼운다. 바꿨으면 True."""
    global PRISTINE
    if seeded_on(database.DB_PATH) >= due_day(now):
        return False
    if not _lock.acquire(blocking=False):
        return False
    try:
        today = date.today().isoformat()
        folder = os.path.dirname(os.path.abspath(database.DB_PATH))
        pristine = os.path.join(folder, f"demo_{today}.db")            # 원본 (되돌리기용, 손대지 않음)
        seconds = build(pristine)
        live = os.path.join(folder, f"live_{today}.db")                # 방문자가 쓰는 사본
        _remove_db(live)
        _copy_db(pristine, live)
        old, old_pristine = database.DB_PATH, PRISTINE
        PRISTINE, database.DB_PATH = pristine, live                     # 다음 요청부터 새 DB
        print(f"시연 데이터 새로 만듦 {seconds:.1f}초 (기준일 {today}) → {live}", flush=True)
        time.sleep(30)                                  # 진행 중이던 요청이 끝난 뒤 옛 파일 정리
        for p in (old, old_pristine):
            if p != TEMPLATE and os.path.basename(p).startswith(("demo_", "live_")) and p not in (live, pristine):
                try:
                    _remove_db(p)
                except OSError:
                    pass
        return True
    except Exception:                                   # noqa: BLE001 - 실패해도 지금 샘플로 계속 서비스
        log.exception("시연 데이터 새로 만들기 실패 — 기존 샘플로 계속")
        return False
    finally:
        _lock.release()


def start_background() -> threading.Thread:
    """뒤에서: 지금 샘플이 지났으면 새로 만들고, 그 뒤로도 매일 새벽 RESET_HOUR 시에 다시 만든다."""
    def loop():
        while True:
            refresh_if_stale()
            time.sleep(CHECK_SECONDS)
    t = threading.Thread(target=loop, name="demo-refresh", daemon=True)
    t.start()
    return t

"""시연 서버 데이터 — 첫 화면이 몇 초 안에 열리도록.

샘플을 서버가 켜질 때 만들면 무료 서버에서 2분 넘게 걸려 첫 방문자가 기다린다. 그래서
  1) 배포(빌드) 때 `python manage.py demo-build` 로 샘플 DB(data/demo_template.db)를 미리 만들어 두고
  2) 서버가 켜질 때 DB 가 비어 있으면 그 파일을 복사해 바로 연다 (prepare)
  3) 샘플 기준일이 오늘이 아니면, 별도 프로세스가 오늘 기준 샘플을 새 파일로 만든 뒤 DB 경로를 바꿔 끼운다
     (서버는 요청마다 연결을 새로 여므로 경로만 바꾸면 다음 요청부터 새 DB). 켜져 있는 동안 날짜가 바뀌어도 같다.
시연 서버(SALES_DEMO=1) 전용. 바꿔 끼우면 방문자가 그사이 바꾼 내용은 사라진다 (시연 데이터는 원래 초기화된다).
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
from datetime import date

from . import database

log = logging.getLogger("sales.demo")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.environ.get("SALES_DEMO_TEMPLATE") or os.path.join(HERE, "data", "demo_template.db")
CHECK_SECONDS = 600                     # 날짜가 바뀌었는지 보는 간격


def _marker(path: str) -> str:
    return path + ".date"


def seeded_on(path: str) -> str:
    """그 DB 샘플의 기준일 (YYYY-MM-DD). 모르면 ''."""
    try:
        with open(_marker(path), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _remove_db(path: str) -> None:
    for p in (path, path + "-wal", path + "-shm", _marker(path)):
        if os.path.exists(p):
            os.remove(p)


def build(out: str) -> float:
    """오늘 기준 시연 DB 를 out 에 만든다 (다른 프로세스에서 — 이 프로세스의 DB 연결·전역 상태를 건드리지 않음)."""
    started = time.time()
    tmp = out + ".building"
    _remove_db(tmp)
    env = {**os.environ, "SALES_DB_PATH": tmp, "SALES_DEMO": "1", "PYTHONIOENCODING": "utf-8"}
    manage = os.path.join(HERE, "manage.py")
    for step in (["db", "upgrade"], ["demo-init"]):
        subprocess.run([sys.executable, manage, *step], env=env, cwd=HERE, check=True)
    with sqlite3.connect(tmp) as conn:                  # WAL 내용을 본 파일에 합쳐 파일 하나로 만든다
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
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
        with sqlite3.connect(path) as conn:
            return bool(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
    except sqlite3.Error:
        return False


def prepare() -> str:
    """서버 시작 직전: 빈 DB 면 미리 만든 샘플을 복사한다. 반환: 지금 샘플의 기준일."""
    path = database.DB_PATH
    if not _has_data(path):
        if not os.path.exists(TEMPLATE):                # 미리 만든 파일이 없으면 여기서 만든다 (느림 — 배포 빌드에서 demo-build 권장)
            log.info("시연: 미리 만든 샘플이 없어 지금 만듭니다")
            build(TEMPLATE)
        src, dst = sqlite3.connect(TEMPLATE), sqlite3.connect(path)
        try:
            src.backup(dst)                             # 열려 있는 파일이어도 내용을 통째로 바꾼다 (지우고 복사하지 않음)
        finally:
            src.close()
            dst.close()
        if os.path.exists(_marker(TEMPLATE)):
            shutil.copyfile(_marker(TEMPLATE), _marker(path))
        log.info("시연: 미리 만든 샘플로 시작 (기준일 %s)", seeded_on(path) or "모름")
    return seeded_on(path)


_lock = threading.Lock()


def refresh_if_stale() -> bool:
    """샘플 기준일이 오늘이 아니면 새로 만들어 바꿔 끼운다. 바꿨으면 True."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        today = date.today().isoformat()
        if seeded_on(database.DB_PATH) == today:
            return False
        folder = os.path.dirname(os.path.abspath(database.DB_PATH))
        new = os.path.join(folder, f"demo_{today}.db")
        seconds = build(new)
        old = database.DB_PATH
        database.DB_PATH = new                          # 다음 요청부터 새 DB
        print(f"시연 데이터 새로 만듦 {seconds:.1f}초 (기준일 {today}) → {new}", flush=True)
        if os.path.basename(old).startswith("demo_") and old != new:
            time.sleep(30)                              # 진행 중이던 요청이 끝난 뒤 옛 파일 정리
            try:
                _remove_db(old)
            except OSError:
                pass
        return True
    except Exception:                                   # noqa: BLE001 - 실패해도 지금 샘플로 계속 서비스
        log.exception("시연 데이터 새로 만들기 실패 — 기존 샘플로 계속")
        return False
    finally:
        _lock.release()


def start_background() -> threading.Thread:
    """뒤에서: 지금 샘플이 오늘 것이 아니면 새로 만들고, 그 뒤로도 날짜가 바뀌면 다시 만든다."""
    def loop():
        while True:
            refresh_if_stale()
            time.sleep(CHECK_SECONDS)
    t = threading.Thread(target=loop, name="demo-refresh", daemon=True)
    t.start()
    return t

"""앱 안에서 도는 예약 실행 스레드. 1분마다 '할 때가 됐는지'만 보고, 실행은 crawler.run_once 에 맡긴다.

앱을 여러 개 띄워도 DB 잠금 때문에 한 번만 실행된다. 앱이 꺼져 있으면 돌지 않으므로,
PC를 켜 두지 않는 경우에는 `python crawl.py` 를 작업 스케줄러에 등록한다.
"""
from __future__ import annotations

import logging
import threading
import time

from . import crawler, postings, power

log = logging.getLogger("job.scheduler")
_started = False
_manual: threading.Thread | None = None
CHECK_SECONDS = 60


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="job-crawl-scheduler", daemon=True).start()


PURGE_SECONDS = 3600
SYNC_SECONDS = 3600            # Render 사본으로 올리는 주기 (수집이 끝날 때도 올림)


def _sync() -> None:
    from . import sync
    if sync.settings():
        r = sync.push()
        log.info("Render 동기화 %s", r)


def _should_stay_awake() -> bool:
    s = crawler.load_settings()
    if not s["enabled"]:
        return False
    if crawler.due():
        return True                                   # 이번 확인에서 바로 실행됨
    return crawler.backlog_status()["left"] > 0       # 밀린 공고가 있으면 다음 실행까지 깨어 있게


def _loop() -> None:
    stop = threading.Event()
    last_purge = 0.0
    last_sync = time.monotonic() - SYNC_SECONDS + 120     # 켜고 2분 뒤 첫 동기화
    while not stop.wait(CHECK_SECONDS):
        try:
            # 자동 수집을 꺼 두어도 마감된 미저장 공고는 한 시간마다 지운다
            if time.monotonic() - last_purge >= PURGE_SECONDS:
                n = postings.purge_closed() + postings.purge_stale()
                last_purge = time.monotonic()
                if n:
                    log.info("마감된 미저장 공고 %d건 삭제", n)
            # 수집 중이거나(곧 실행될 때 포함) 밀린 공고가 남아 있으면 PC 가 잠들지 않게, 아니면 원래 절전 설정대로
            awake = _should_stay_awake()
            if power.keep_awake(awake):
                from . import db
                db.set_setting("keep_awake", "1" if awake else "0")       # 수집 현황 화면에 보여 줌
            if crawler.run_once() is not None or time.monotonic() - last_sync >= SYNC_SECONDS:
                _sync()
                last_sync = time.monotonic()
        except Exception:  # 한 번 실패해도 다음 주기에 다시 시도
            log.exception("예약 작업 실패")


def run_now() -> bool:
    """'지금 실행' 버튼: 요청을 붙잡지 않도록 따로 스레드에서 돌린다. 이미 도는 중이면 False."""
    global _manual
    if (_manual and _manual.is_alive()) or crawler.status()["running"]:
        return False
    _manual = threading.Thread(target=_safe_run, name="job-crawl-now", daemon=True)
    _manual.start()
    return True


def _safe_run() -> None:
    try:
        if power.keep_awake(True):                     # '지금 실행'도 끝날 때까지 잠들지 않게
            from . import db
            db.set_setting("keep_awake", "1")
        crawler.run_once(force=True)
    except Exception:
        log.exception("수동 크롤링 실패")
    finally:
        power.keep_awake(False)
        from . import db
        db.set_setting("keep_awake", "0")

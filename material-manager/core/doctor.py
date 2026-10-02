"""운영 점검: 회사에 설치한 뒤(그리고 설정을 바꿀 때마다) 실제 DB·저장소·ERP·사내 로그인·백업·배치가 붙는지 한 번에 확인한다.

    flask --app app doctor          (실패가 있으면 종료 코드 1 → 배포 스크립트에서 멈출 수 있다)
    시스템관리자 → 배치 화면의 '운영 점검'

이 저장소의 테스트는 가짜 SAP·IdP·S3로만 확인했다. 실제 시스템 연결은 설치한 곳에서 이 점검으로 확인한다.
각 항목은 (이름, ok | warn | fail | off, 설명). 거래는 보내지 않는다(ERP는 연결 확인만, 저장소는 시험 파일을 쓰고 지운다).
"""

import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config
from core import backup, db, erp, jobs, sso, storage


@dataclass
class Check:
    name: str
    status: str          # ok | warn | fail | off
    message: str


LABEL = {"ok": "정상", "warn": "주의", "fail": "실패", "off": "사용 안 함"}


def _run(name: str, fn) -> Check:
    started = time.monotonic()
    try:
        status, message = fn()
    except Exception as exc:                                     # 점검은 끝까지 돈다
        status, message = "fail", f"{type(exc).__name__}: {str(exc)[:300]}"
    took = time.monotonic() - started
    return Check(name, status, message + (f" ({took:.1f}초)" if took >= 1 else ""))


def _db():
    db.scalar("SELECT 1")
    if db.is_pg():
        return "ok", "PostgreSQL 연결됨"
    path = str(config.DB_PATH)
    if path.startswith(("\\\\", "//")):
        return "warn", f"SQLite가 네트워크 폴더에 있습니다({path}) — 로컬 디스크 또는 PostgreSQL 권장"
    mode = db.scalar("PRAGMA journal_mode")
    return "ok", f"SQLite {config.DB_PATH.name} (저널 {mode})"


def _storage():
    store = storage.get()
    key = f"uploads/doctor-{secrets.token_hex(6)}.json"
    store.put(key, b"{}")
    try:
        if store.get(key) != b"{}":
            return "fail", "시험 파일을 쓰고 다시 읽지 못했습니다"
    finally:
        store.delete(key)
    state = store.check() or "ok"
    if state == "degraded":
        return "warn", f"S3에 연결되지 않아 이 서버 임시 폴더로 일하는 중 (올릴 파일 {store.pending()}건)"
    return "ok", f"{store.name} 읽기·쓰기·지우기 확인"


def _erp():
    if not erp.enabled():
        return "off", "MM_ERP_MODE=off"
    if config.SAP_MODE == "mock":
        return "warn", "모의 전송 중 — 실제 ERP에 전기되지 않습니다"
    result = erp.ping()
    return ("ok" if result.ok else "fail"), f"{config.SAP_MODE}: {result.message}"


def _sso():
    if not sso.enabled():
        return "off", "MM_SSO_ENABLED=0"
    sso._cache.clear()
    doc = sso.discovery()
    keys = sso._jwks(force=True)
    note = " · SSO 장애 모드 켜짐" if sso.outage_active() else ""
    return ("warn" if note else "ok"), (f"IdP {doc.get('issuer', '')} 발견 문서·공개키 {len(keys.get('keys', []))}개 받음"
                                        f"{note}")


def _backup():
    if not backup.enabled():
        if db.is_pg():
            return "warn", "pg_dump가 없어 자동 백업이 꺼져 있습니다 — DB 서버 백업을 쓰는지 확인 (MM_PG_DUMP)"
        return "warn", "자동 백업이 꺼져 있습니다 (MM_BACKUP_HOURS=0)"
    Path(config.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    probe = Path(config.BACKUP_DIR) / ".doctor"
    probe.write_text("ok")
    probe.unlink()
    found = backup.files()
    problems = [w for w in [backup.same_disk_warning()] if w]
    if not found:
        problems.append("아직 백업 파일이 없습니다 — 배치가 돌고 있는지 확인하거나 `flask --app app backup`")
    else:
        age_h = (time.time() - found[-1].stat().st_mtime) / 3600
        if age_h > config.BACKUP_HOURS * 2:
            problems.append(f"마지막 백업이 {age_h:,.0f}시간 전입니다")
    head = f"{config.BACKUP_DIR} · {len(found)}개 보관"
    return ("warn", head + " · " + " / ".join(problems)) if problems else ("ok", head)


def _batch():
    status = jobs.status_df()
    late = []
    for r in status.itertuples():
        if not r.enabled:
            continue
        last = str(getattr(r, "last_finished", "") or "")
        if not last:
            late.append(f"{r.name}(한 번도 안 돎)")
            continue
        age = (datetime.now() - datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
        if age > max(r.interval * 3, 900):
            late.append(f"{r.name}({age / 3600:,.1f}시간 전)")
        elif str(getattr(r, "last_status", "")) == "ERROR":
            late.append(f"{r.name}(마지막 실행 오류)")
    if late:
        return "warn", "배치가 돌지 않거나 오류: " + ", ".join(late) + " — `flask --app app batch --loop`를 서비스로 등록"
    return "ok", "배치 작업이 주기대로 돌고 있습니다"


def _ocr():
    from core import statement_reader
    if statement_reader.ocr_available():
        return "ok", f"Tesseract 있음 (언어 {config.OCR_LANG}) — 거래명세서 스캔 읽기 가능"
    return "off", "Tesseract 없음 — 스캔 이미지는 읽지 않음 (PDF·엑셀·직접 입력은 가능)"


def _security():
    problems = []
    if not config.SECRET_KEY_FROM_ENV:
        problems.append("MM_SECRET_KEY 없음(재시작하면 모두 로그아웃, 서버 여러 대면 로그인 풀림)")
    if not config.SESSION_COOKIE_SECURE:
        problems.append("MM_COOKIE_SECURE=0 (HTTPS 운영이면 1)")
    if os.getenv("MM_DEBUG", "0") == "1":
        problems.append("디버그 모드 켜짐")
    return ("warn", " · ".join(problems)) if problems else ("ok", "세션 키·쿠키 설정 정상")


CHECKS = [("DB", _db), ("파일 저장소", _storage), ("ERP·SAP", _erp), ("사내 로그인(SSO)", _sso),
          ("백업", _backup), ("배치", _batch), ("명세서 스캔(OCR)", _ocr), ("보안 설정", _security)]


def run() -> list[Check]:
    return [_run(name, fn) for name, fn in CHECKS]

"""운영 점검 — 회사에 설치한 뒤(그리고 설정을 바꿀 때마다) 실제 DB·저장소·ERP·사내 로그인·메일·백업·배치가 붙는지 한 번에 확인한다.

    python manage.py doctor         (실패가 있으면 종료 코드 1 → 배포 스크립트에서 멈출 수 있다)
    관리자 → ⏱️ 배치 작업 · ⚙️ 데이터 관리 화면의 '🩺 운영 점검'

이 저장소의 테스트는 가짜 SAP·IdP·S3·메신저로만 확인했다. 실제 시스템 연결은 설치한 곳에서 이 점검으로 확인한다.
각 항목은 (영역, 이름, ok | warn | fail | off, 설명). 업무 데이터는 보내지 않는다(ERP·메일은 연결만, 저장소는 시험 파일을 쓰고 지운다).
"""
from __future__ import annotations

import os
import secrets
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config

from . import database

LABEL = {"ok": "정상", "warn": "주의", "fail": "실패", "off": "사용 안 함"}
BACKUP_MAX_AGE_HOURS = 26              # 매일 02:00 백업 → 하루 넘게 새 백업이 없으면 주의


@dataclass
class Check:
    area: str
    name: str
    status: str
    message: str

    @property
    def label(self) -> str:
        return LABEL[self.status]


def _run(area: str, name: str, fn) -> Check:
    try:
        status, message = fn()
    except Exception as exc:                         # noqa: BLE001 - 점검은 실패 내용을 그대로 보여 준다
        status, message = "fail", f"{type(exc).__name__}: {str(exc)[:300]}"
    return Check(area, name, status, message)


# ---------------------------------------------------------------------------
# 항목별
# ---------------------------------------------------------------------------
def _db():
    t = time.perf_counter()
    database.scalar("SELECT 1")
    ms = (time.perf_counter() - t) * 1000
    return ("ok" if ms < 500 else "warn"), f"{database.describe()} · 응답 {ms:.0f}ms"


def _schema():
    current, head = database.current_revision(), database.head_revision()
    if current == head:
        return "ok", f"최신 ({head})"
    return "fail", f"현재 {current} / 최신 {head} → python manage.py db upgrade"


def _sqlite():
    if database.is_pg():
        return "off", "PostgreSQL 사용"
    if database.on_network_share():
        return "fail", "SQLite 파일이 네트워크 공유 폴더에 있습니다 — 파일 잠금이 보장되지 않아 DB 가 깨질 수 있습니다. 로컬 디스크 또는 PostgreSQL 로"
    with database.get_conn() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
    ok = str(mode).lower() == "wal" and int(sync) >= 2
    return ("ok" if ok else "warn"), f"journal={mode} · synchronous={'FULL' if int(sync) >= 2 else sync}"


def _storage():
    from .storage import get_storage
    st = get_storage()
    key = f"_doctor/{secrets.token_hex(6)}.txt"
    st.put(key, b"ok", "text/plain")
    data = st.get(key)
    st.delete(key)
    spooled = getattr(st, "spooled_count", lambda: 0)()
    if data != b"ok":
        return "fail", f"{st.describe()} · 쓴 값과 읽은 값이 다릅니다"
    if spooled:
        return "warn", f"{st.describe()} · 저장소 장애 때 임시 보관한 파일 {spooled}개가 아직 올라가지 않았습니다 (배치 storage.flush)"
    return "ok", f"{st.describe()} · 쓰기·읽기·지우기 정상"


def _disk():
    usage = shutil.disk_usage(config.DATA_DIR if Path(config.DATA_DIR).exists() else ".")
    free_gb = usage.free / 1024 ** 3
    return ("ok" if free_gb >= 2 else "warn" if free_gb >= 0.5 else "fail"), f"데이터 디스크 여유 {free_gb:,.1f}GB"


def _erp():
    from . import erp
    adapter = erp.settings()["adapter"]
    if adapter in ("", "none", "off"):
        return "off", "ERP 연동 안 함"
    ok, message = erp.test_connection()
    return ("ok" if ok else "fail"), f"{adapter} · {message}"


def _login():
    from . import auth
    mode = auth.AUTH_MODE
    if mode == "simple":
        return ("fail" if config.PRODUCTION else "warn"), "간편 로그인(목록에서 선택) — 개발·시연 전용"
    if mode == "password":
        return "ok", "사번 + 비밀번호"
    if mode == "sso":
        proxies = ", ".join(sorted(config.TRUSTED_PROXIES))
        return "ok", f"앞단 SSO 헤더 · 믿는 프록시 {proxies}"
    from . import oidc
    missing = oidc.problems()
    if missing:
        return "fail", " / ".join(missing)
    meta = oidc.client().load_server_metadata()
    return ("ok" if meta.get("authorization_endpoint") else "fail"), f"OIDC · IdP 응답 정상 ({oidc.settings()['metadata_url']})"


def _sso_outage():
    from . import auth
    until = auth.sso_outage_until()
    if auth.AUTH_MODE not in ("sso", "oidc"):
        return "off", "사내 로그인을 쓰지 않음"
    if until:
        return "warn", f"SSO 장애 모드 켜짐 — {until:%m-%d %H:%M}까지 비밀번호 로그인 허용"
    breakglass = sorted(auth.breakglass_users())
    return ("ok" if breakglass else "warn"), (f"비상 계정 {', '.join(breakglass)}" if breakglass
                                               else "비상 계정(SALES_BREAKGLASS_USERS)이 없습니다 — 사내 로그인 장애 때 들어올 방법은 SSO 장애 모드뿐")


def _maintenance():
    from .observability import read_only
    return ("warn", "점검(읽기 전용) 모드 켜짐 — 저장·변경이 막혀 있습니다") if read_only() else ("ok", "꺼짐")


def _mail():
    if not os.environ.get("SALES_SMTP_HOST"):
        return "off", "메일 서버(SALES_SMTP_HOST) 없음 — 알림은 화면·메신저로만"
    import smtplib
    host, port = os.environ["SALES_SMTP_HOST"], int(os.environ.get("SALES_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.ehlo()
        if os.environ.get("SALES_SMTP_STARTTLS", "1") == "1":
            smtp.starttls()
        if os.environ.get("SALES_SMTP_USER"):
            smtp.login(os.environ["SALES_SMTP_USER"], os.environ.get("SALES_SMTP_PASSWORD", ""))
    return "ok", f"{host}:{port} 연결·인증 정상 (메일은 보내지 않음)"


def _messenger():
    from . import messenger
    channels = messenger.list_channels(active_only=True)
    if not channels:
        return "off", "등록된 메신저 채널 없음 (관리자 → 🔔 알림 채널)"
    failed = database.scalar("SELECT COUNT(*) FROM notify_log WHERE status='실패' AND sent_at >= ?",
                             [(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")])
    names = ", ".join(c["name"] for c in channels)
    return ("warn" if failed else "ok"), f"{names}" + (f" · 최근 24시간 실패 {int(failed)}건" if failed else "")


def _backup():
    if config.DEMO:
        return "off", "시연 서버 — 매일 새벽 샘플로 초기화하므로 백업하지 않음"
    folder = Path(config.BACKUP_DIR)
    files = sorted([*folder.glob("sales_*.db"), *folder.glob("sales_*.dump")], key=lambda f: f.stat().st_mtime) \
        if folder.exists() else []
    if not files:
        return "fail", f"백업 파일이 없습니다 ({folder}) → python manage.py backup"
    age = (time.time() - files[-1].stat().st_mtime) / 3600
    notes = [f"최근 {files[-1].name} · {age:.0f}시간 전 · {len(files)}개"]
    status = "ok" if age <= BACKUP_MAX_AGE_HOURS else "warn"
    if database.same_disk(folder):
        status = "warn"
        notes.append("DB 와 같은 디스크 — 다른 디스크·NAS 로 (SALES_BACKUP_DIR)")
    return status, " · ".join(notes)


def _worker():
    if config.DEMO:
        return "off", "시연 서버 — 배치 워커를 띄우지 않음 (알림·ERP 전송은 대기열에만 쌓임)"
    from . import jobs
    w = jobs.workers()
    if w.empty:
        return "warn", "배치 워커 신호 없음 — python manage.py worker 를 띄우세요 (알림·ERP 전송·백업이 멈춤)"
    alive = int((w["상태"] == "정상").sum())
    failed = database.scalar("SELECT COUNT(*) FROM jobs WHERE status='실패' AND finished_at >= ?",
                             [(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")])
    status = "ok" if alive else "fail"
    if alive and failed:
        status = "warn"
    return status, f"살아 있는 워커 {alive}/{len(w)}" + (f" · 최근 24시간 실패 작업 {int(failed)}건" if failed else "")


def _security():
    notes, status = [], "ok"
    if config.PRODUCTION and not config.SESSION_COOKIE_SECURE:
        status = "warn"
        notes.append("쿠키 Secure 꺼짐")
    if not os.environ.get("SALES_SECRET_KEY"):
        status = "fail" if config.PRODUCTION else "warn"
        notes.append("SALES_SECRET_KEY 없음")
    ip = os.environ.get("SALES_CLIENT_IP_HEADER") or ("X-Forwarded-For" if os.environ.get("SALES_PROXY_FIX") == "1" else "")
    notes.append(f"접속 IP: {ip or '직접 연결 주소'}")
    return status, " · ".join(notes)


CHECKS = [
    ("DB", "연결", _db), ("DB", "구조(리비전)", _schema), ("DB", "SQLite 안전 설정", _sqlite),
    ("파일", "저장소", _storage), ("파일", "디스크 여유", _disk), ("파일", "백업", _backup),
    ("연동", "ERP", _erp), ("로그인", "인증 방식", _login), ("로그인", "장애 대비", _sso_outage),
    ("알림", "메일", _mail), ("알림", "메신저", _messenger),
    ("운영", "배치 워커", _worker), ("운영", "점검 모드", _maintenance), ("보안", "설정", _security),
]


def run() -> list[Check]:
    return [_run(area, name, fn) for area, name, fn in CHECKS]


def summary(checks: list[Check]) -> dict:
    return {s: sum(1 for c in checks if c.status == s) for s in LABEL}

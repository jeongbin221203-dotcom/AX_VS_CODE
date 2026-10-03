"""포트폴리오 시연 모드 (MM_DEMO=1 일 때만).

- 서버가 뜰 때 시연용 시스템관리자(demo)와 샘플 데이터(4개 플랜트·1년 치 거래, core/seed_demo.py)를 만든다. 빈 DB일 때만 넣는다.
- 로그인하지 않은 방문자는 이 계정으로 자동 로그인된다 → 어느 컴퓨터에서 열어도 바로 모든 화면을 볼 수 있다.
- 방문자가 시연 계정을 중지·강등해도 다음 자동 로그인 때 되돌린다.
- 사이드바 '다른 역할로 보기': 샘플 사용자(core/seed_demo.py)로 바꿔 역할·창고 범위 차이를 볼 수 있다.
- 관리자 설정(사용자·플랜트·창고·배치·엑셀 양식·비밀번호·샘플 추가)은 저장을 막는다 — 업무 데이터는 저장된다.
- '샘플로 되돌리기'(reset) 또는 매일 새벽(MM_DEMO_RESET_HOUR, 한국 시간) 첫 요청 때 DB를 비우고 샘플을 다시 만든다.
  방문자가 바꾼 내용은 감사로그 표준출력(MM_AUDIT_STDOUT)으로 서버 로그에 남아 초기화 뒤에도 확인할 수 있다.
운영 서버에서는 켜지 말 것 (누구나 시스템관리자가 된다).
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone

import config
from core import audit, auth, db, repository as repo

USERNAME = "demo"
NAME = "시연 관리자"
ROLE = "ADMIN"


def enabled() -> bool:
    return config.DEMO


def ensure_user() -> dict:
    """시연 계정을 찾거나 만들고, 시스템관리자·전체 창고·사용 중 상태로 맞춘다."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (USERNAME,)).fetchone()
    if row is None:
        # 비밀번호는 아무도 모르는 임의 값 — 이 계정은 자동 로그인으로만 쓴다
        result = auth.create_user(USERNAME, NAME, ROLE, "Demo1!" + secrets.token_urlsafe(18), audit.SYSTEM,
                                  must_change_pw=False)
        uid = result.user["id"] if result.ok else None
        if uid is None:                                  # 서버 여러 대가 동시에 만든 경우
            with db.get_conn() as conn:
                uid = conn.execute("SELECT id FROM users WHERE username = ?", (USERNAME,)).fetchone()[0]
    else:
        uid = row[0]
    user = auth.get_user(uid)
    if (user["role"] != ROLE or not user["active"] or not user["all_warehouses"] or user["must_change_pw"]
            or user.get("locked_until")):
        db.execute("UPDATE users SET role = ?, active = 1, all_warehouses = 1, must_change_pw = 0, "
                   "failed_count = 0, locked_until = '' WHERE id = ?", (ROLE, uid))
        user = auth.get_user(uid)
    return user


# 다른 역할로 보기: (역할, 아이디, 표시) — 아이디는 core/seed_demo.py 의 샘플 사용자
ROLE_VIEWS = [("ADMIN", USERNAME, "시스템관리자"), ("MANAGER", "park.jh", "관리자"),
              ("CLERK", "kim.mj", "담당자(인천 창고)"), ("VIEWER", "kang.dy", "조회")]
# 시연에서 저장을 막는 관리자 설정: admin 블루프린트 전체 + 아래 (시연 초기화 /demo/reset 은 막지 않는다)
LOCKED_ENDPOINTS = {"auth.password", "data_admin.make_seed", "data_admin.make_seed_mfg"}


def locked(blueprint: str | None, endpoint: str | None) -> bool:
    return blueprint == "admin" or endpoint in LOCKED_ENDPOINTS


def role_user(role: str) -> dict | None:
    """역할 바꿔 보기 계정. 시스템관리자는 시연 계정, 나머지는 샘플 사용자를 사용 중 상태로 되돌려 쓴다."""
    view = next((v for v in ROLE_VIEWS if v[0] == role), None)
    if not view:
        return None
    if view[1] == USERNAME:
        return ensure_user()
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (view[1],)).fetchone()
    if row is None:
        return None
    user = auth.get_user(row[0])
    if user["role"] != role or not user["active"] or user["must_change_pw"] or user.get("locked_until"):
        db.execute("UPDATE users SET role = ?, active = 1, must_change_pw = 0, failed_count = 0, locked_until = '' "
                   "WHERE id = ?", (role, row[0]))
        user = auth.get_user(row[0])
    return user


KST = timezone(timedelta(hours=9))
RESET_KEY = "demo_reset_on"           # app_settings: 마지막으로 샘플을 만든 날 (한국 날짜)
MIN_GAP = 60                          # 버튼 연타 방지 (초)
_lock = threading.Lock()
_last = {"at": 0.0}


def _today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def _mark_today() -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (RESET_KEY, _today_kst()))


def reset(actor: dict | None, reason: str) -> bool:
    """DB를 비우고 샘플을 다시 만든다. 방금(1분 안) 했거나 다른 요청이 하는 중이면 False."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        if time.time() - _last["at"] < MIN_GAP:
            return False
        audit._emit(actor or audit.SYSTEM, "DEMO_RESET", "demo", "", {"reason": reason, "phase": "start"})
        db.wipe_database()
        prepare()
        audit.log(actor, "DEMO_RESET", "demo", "", {"reason": reason})
        _last["at"] = time.time()
        return True
    finally:
        _lock.release()


def maybe_daily_reset() -> None:
    """매일 정해진 시각이 지난 뒤 첫 요청에서 한 번 초기화 (서버가 잠들어 있던 날은 깨어날 때)."""
    if datetime.now(KST).hour < config.DEMO_RESET_HOUR:
        return
    done = db.scalar("SELECT value FROM app_settings WHERE key = ?", (RESET_KEY,))
    if done != _today_kst():
        reset(None, f"매일 {config.DEMO_RESET_HOUR}시 자동 초기화")


def prepare() -> None:
    """서버 시작 때: 시연 계정 + (빈 DB면) 샘플 데이터."""
    ensure_user()
    if not repo.count_materials():
        from core import seed, seed_demo, seed_mfg
        with audit.quiet():                      # 샘플 생성 기록은 서버 로그에 쏟지 않는다
            seed.seed(history=True)              # 부산 포장·고박 자재
            seed_mfg.seed_manufacturing()        # 창원 제조공장
            seed_demo.seed_large()               # 인천·평택 + 담당자 · 1년 치 거래 · 구매 · 월 마감
            _extras()                            # 거래처 마스터 · BOM·생산 투입 · 바코드 · 알림 메일 주소
        _mark_today()


# 샘플 자재 바코드 (스캔 시연용 — 카메라나 스캐너로 찍으면 그 자재)
DEMO_BARCODES = {"PKG-001": "8801234500017", "PKG-002": "8801234500024", "PKG-003": "8801234500031",
                 "PT-BLT-001": "8809876500016", "PT-NUT-001": "8809876500023", "PT-MTR-001": "8809876500030",
                 "PT-PCB-001": "8809876500047", "PK-BOX-001": "8809876500054"}


def _extras() -> None:
    """시연에서 보여 줄 새 기능의 샘플: 거래처 마스터(+표기만 다른 이름 1개는 미등록으로 남김), BOM·생산 투입,
    자재 바코드, 결재 알림을 받을 메일 주소(시연은 '기록만' — 실제로 보내지 않음)."""
    from core import partners, production, services
    production.seed_sample()
    partners.seed_from_data(audit.SYSTEM)
    for code, bc in DEMO_BARCODES.items():
        db.execute("UPDATE materials SET barcode = ? WHERE code = ? AND barcode = ''", (bc, code))
    db.execute("UPDATE users SET email = username || '@example.com' WHERE email = '' AND role IN ('MANAGER', 'ADMIN', 'CLERK')")
    # 같은 회사를 다르게 적은 입고 1건 → '미등록 이름 정리'에서 기존 거래처에 연결해 볼 수 있다
    mid = db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'")
    wh = db.scalar("SELECT id FROM warehouses ORDER BY id LIMIT 1")
    if mid and wh:
        services.register_transaction(int(mid), "IN", 10, date_today(), 18000, ref_no="DEMO-ALIAS",
                                      partner="대한팔레트 부산지점", actor=audit.SYSTEM, warehouse_id=int(wh))


def date_today() -> str:
    return date.today().isoformat()

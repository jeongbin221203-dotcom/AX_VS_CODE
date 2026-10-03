"""포트폴리오 시연 모드 (MM_DEMO=1 일 때만).

- 서버가 뜰 때 시연용 시스템관리자(demo)와 샘플 데이터(지난 11개월 거래)를 만든다. 빈 DB일 때만 샘플을 넣는다.
- 로그인하지 않은 방문자는 이 계정으로 자동 로그인된다 → 어느 컴퓨터에서 열어도 바로 모든 화면을 볼 수 있다.
- 방문자가 시연 계정을 중지·강등해도 다음 자동 로그인 때 되돌린다.
운영 서버에서는 켜지 말 것 (누구나 시스템관리자가 된다).
"""
from __future__ import annotations

import secrets

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
        result = auth.create_user(USERNAME, NAME, ROLE, "Demo!" + secrets.token_urlsafe(18), audit.SYSTEM,
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


def prepare() -> None:
    """서버 시작 때: 시연 계정 + (빈 DB면) 샘플 데이터."""
    ensure_user()
    if not repo.count_materials():
        from core import seed
        seed.seed(history=True)

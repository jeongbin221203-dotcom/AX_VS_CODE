"""인증 로직 (화면 의존성 없음)

  simple   : 사용자 목록에서 선택 — 개발·시연 전용. 운영(SALES_ENV=production)에서는 기동을 거부한다
  password : 사번 + 비밀번호 (PBKDF2, 실패 5회 잠금, 90일 만료, 복잡도 정책)
  sso      : 사내 SSO 를 앞단 리버스 프록시(SAML/OIDC)가 처리하고, 인증된 사번을 헤더로 넘긴다.
             신뢰하는 프록시 IP 에서 온 요청의 헤더만 믿는다
  oidc     : 이 앱이 직접 사내 IdP 와 OpenID Connect 로 통신한다 (core/oidc.py)

사내 표준 인증으로 교체할 때는 이 파일만 바꾸면 되고, 화면 코드는 손대지 않아도 된다.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from . import enterprise as ent
from . import sales_db as db

AUTH_MODE = os.environ.get("SALES_AUTH_MODE", "simple")        # simple | password | sso | oidc
SSO_MATCH = os.environ.get("SALES_SSO_MATCH", "emp_no")        # SSO 헤더 값을 사번/이메일 중 무엇과 맞출지
DEV_SSO_USER_ENV = "SALES_SSO_USER"                             # 개발용: 헤더 없이 이 이름으로 로그인

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
PASSWORD_MAX_AGE_DAYS = 90
PASSWORD_MIN_LENGTH = 10
PBKDF2_ROUNDS = 310_000


# ---------------------------------------------------------------------------
# 비밀번호
# ---------------------------------------------------------------------------
def hash_password(raw: str, salt: str | None = None, rounds: int = PBKDF2_ROUNDS) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), rounds).hex()
    return f"pbkdf2${rounds}${salt}${digest}"


def verify_password(raw: str, stored: str | None) -> bool:
    if not stored or stored.count("$") != 3:
        return False
    _algo, rounds, salt, digest = stored.split("$")
    calc = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), int(rounds)).hex()
    return hmac.compare_digest(calc, digest)


def password_problems(raw: str, user: dict | None = None) -> list[str]:
    """비밀번호 정책 위반 사항 (빈 목록이면 통과)."""
    problems = []
    if len(raw) < PASSWORD_MIN_LENGTH:
        problems.append(f"{PASSWORD_MIN_LENGTH}자 이상")
    classes = sum(bool(re.search(p, raw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        problems.append("영문 대문자·소문자·숫자·특수문자 중 3종류 이상")
    if user and user.get("emp_no") and str(user["emp_no"]) in raw:
        problems.append("사번을 포함하지 않을 것")
    return problems


def set_password(user_id: int, raw: str, must_change: bool = False, current: str | None = None) -> None:
    """비밀번호 설정. current 를 넘기면(본인 변경) 현재 비밀번호를 먼저 확인한다."""
    user = ent.get_user(user_id=user_id)
    if not user:
        raise ValueError("사용자를 찾을 수 없습니다.")
    if current is not None and not verify_password(current, user.get("pw_hash")):
        raise ValueError("현재 비밀번호가 맞지 않습니다.")
    problems = password_problems(raw, user)
    if problems:
        raise ValueError("비밀번호 규칙: " + ", ".join(problems))
    if user.get("pw_hash") and verify_password(raw, user["pw_hash"]):
        raise ValueError("이전과 다른 비밀번호를 사용하세요.")
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET pw_hash=?, pw_changed_at=?, must_change_pw=?, failed_logins=0, "
                     "locked_until=NULL WHERE id=?",
                     (hash_password(raw), db._now(), int(must_change), user_id))
    db.audit("비밀번호설정" if must_change else "비밀번호변경", "사용자", user_id,
             {"임시비밀번호": bool(must_change)})


def password_expired(user: dict) -> bool:
    if AUTH_MODE != "password":
        return False
    if user.get("must_change_pw"):
        return True
    changed = user.get("pw_changed_at")
    if not changed:
        return True
    return datetime.strptime(changed[:19], "%Y-%m-%d %H:%M:%S") < datetime.now() - timedelta(
        days=PASSWORD_MAX_AGE_DAYS)


def unlock(user_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (user_id,))
    db.audit("잠금해제", "사용자", user_id)


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------
def authenticate_password(emp_no: str, password: str) -> tuple[Optional[dict], str]:
    """사번+비밀번호 인증. (사용자, 실패 메시지). 계정 존재 여부는 메시지로 드러내지 않는다."""
    generic = "사번 또는 비밀번호가 맞지 않습니다."
    user = ent.get_user(emp_no=str(emp_no).strip()) if emp_no else None
    if not user or not user.get("active"):
        db.audit("로그인실패", "사용자", None, {"사번": emp_no, "사유": "없는 계정/비활성"})
        return None, generic
    now = datetime.now()
    if user.get("locked_until") and datetime.strptime(user["locked_until"][:19], "%Y-%m-%d %H:%M:%S") > now:
        db.audit("로그인차단", "사용자", user["id"], {"사유": "잠금 중"})
        return None, f"로그인 실패가 반복되어 잠긴 계정입니다. {LOCK_MINUTES}분 뒤 다시 시도하거나 관리자에게 문의하세요."
    if not verify_password(password or "", user.get("pw_hash")):
        fails = int(user.get("failed_logins") or 0) + 1
        locked = fails >= MAX_FAILED_LOGINS
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                         (0 if locked else fails,
                          (now + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                          if locked else None, user["id"]))
        db.audit("계정잠금" if locked else "로그인실패", "사용자", user["id"], {"연속실패": fails})
        return None, (f"로그인 실패 {MAX_FAILED_LOGINS}회로 계정이 {LOCK_MINUTES}분간 잠겼습니다."
                      if locked else generic)
    if user.get("failed_logins"):
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (user["id"],))
    return user, ""


def authenticate_sso(identity: str) -> Optional[dict]:
    """프록시가 넘긴 인증된 식별자(사번 또는 이메일)로 사용자를 찾는다."""
    identity = (identity or "").strip()
    if not identity:
        return None
    column = "email" if SSO_MATCH == "email" else "emp_no"
    return db._one(f"SELECT * FROM users WHERE {column}=? AND active=1", [identity])


def authenticate_dev_sso() -> Optional[dict]:
    """개발·시연용: 환경변수 SALES_SSO_USER(이름)로 로그인. 운영에서는 쓰지 않는다."""
    name = os.environ.get(DEV_SSO_USER_ENV, "")
    return ent.get_user(name=name) if name else None


def authenticate_simple(user_id: int) -> Optional[dict]:
    """개발·시연용 간편 선택 로그인."""
    user = ent.get_user(user_id=user_id)
    return user if user and user.get("active") else None

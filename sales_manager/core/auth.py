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


def breakglass_users() -> set[str]:
    """사내 인증(SSO·OIDC)이 멈췄을 때 사번+비밀번호로 들어올 수 있는 비상 계정 (SALES_BREAKGLASS_USERS=9999,2001).
    인터넷이나 IdP 가 끊겨도 관리자가 들어와 점검 모드 전환·조치를 할 수 있게 한다. 모든 사용은 감사로그에 남는다."""
    return {e.strip() for e in os.environ.get("SALES_BREAKGLASS_USERS", "").split(",") if e.strip()}


def sso_outage_until() -> Optional[datetime]:
    """SSO 장애 모드가 켜져 있으면 끝나는 시각. (관리자가 화면·명령으로 최대 24시간 켬)"""
    from . import company
    raw = str(company.get("sso_outage_until") or "")
    try:
        until = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S") if raw else None
    except ValueError:
        return None
    return until if until and until > datetime.now() else None


def set_sso_outage(hours: float, actor: str, reason: str = "") -> Optional[datetime]:
    """hours > 0 이면 그 시간 동안 비밀번호가 있는 사용자 누구나 비밀번호로 로그인(사내 로그인 장애 대비). 0 이면 끔."""
    from . import company
    if hours and not 0 < float(hours) <= SSO_OUTAGE_MAX_HOURS:
        raise ValueError(f"SSO 장애 모드는 최대 {SSO_OUTAGE_MAX_HOURS}시간까지 켤 수 있습니다.")
    until = datetime.now() + timedelta(hours=float(hours)) if hours else None
    company.set_state("sso_outage_until", until.strftime("%Y-%m-%d %H:%M:%S") if until else "", actor,
                      {"사유": reason or None, "시간": hours or 0})
    return until


def breakglass_enabled() -> bool:
    return AUTH_MODE in ("sso", "oidc") and (bool(breakglass_users()) or sso_outage_until() is not None)


def authenticate_breakglass(emp_no: str, password: str) -> tuple[Optional[dict], str]:
    allowed = (emp_no or "").strip() in breakglass_users() or sso_outage_until() is not None
    if not breakglass_enabled() or not allowed:
        db.audit("로그인실패", "사용자", None, {"비상로그인": emp_no, "사유": "비상 계정 아님"})
        return None, "사번 또는 비밀번호가 올바르지 않습니다."
    return authenticate_password(emp_no, password)

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
IP_MAX_FAILURES = int(os.environ.get("SALES_IP_MAX_FAILURES", "20"))    # 한 IP 가 15분 동안 이만큼 실패하면 그 IP 차단
IP_WINDOW_MINUTES = 15
SSO_OUTAGE_MAX_HOURS = 24
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


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password("dummy-password-for-timing", "00" * 16))
    return _DUMMY[0]


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
                     "locked_until=NULL, session_version=COALESCE(session_version, 0) + 1 WHERE id=?",   # 다른 세션 모두 끊김
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
        verify_password(password or "", _dummy_hash())          # 없는 계정도 같은 시간이 걸리게 (계정 존재 추측 방지)
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


# ---------------------------------------------------------------------------
# 세션 판 — 비밀번호 변경·로그아웃·관리자 강제 종료 때 올려 다른 기기의 세션(복사된 쿠키 포함)을 끊는다
# ---------------------------------------------------------------------------
def session_version(user: dict) -> int:
    return int((user or {}).get("session_version") or 0)


def end_all_sessions(user_id: int, actor_reason: str = "") -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET session_version = COALESCE(session_version, 0) + 1 WHERE id=?", (int(user_id),))
    if actor_reason:
        db.audit("세션종료", "사용자", int(user_id), {"사유": actor_reason})


# ---------------------------------------------------------------------------
# IP 단위 로그인 차단 — 여러 계정을 돌아가며 시도하는 공격(계정 잠금만으로는 못 막음)
# ---------------------------------------------------------------------------
def _ip_exempt(ip: str) -> bool:
    """SALES_IP_ALLOWLIST(쉼표, CIDR 가능 — 예: 10.0.0.0/8,121.130.1.2): 회사 NAT 처럼 여러 사람이 한 IP 를 쓰면
    오타 몇 번으로 회사 전체가 막히지 않게 IP 차단에서 뺀다 (계정 잠금은 그대로)."""
    import ipaddress
    raw = os.environ.get("SALES_IP_ALLOWLIST", "").strip()
    if not raw:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for part in raw.split(","):
        try:
            if addr in ipaddress.ip_network(part.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def ip_blocked(ip: str) -> bool:
    if not ip or _ip_exempt(ip):
        return False
    since = (datetime.now() - timedelta(minutes=IP_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    return int(db._scalar("SELECT COUNT(*) FROM login_ip_failures WHERE ip=? AND at>=?", [ip, since]) or 0) >= IP_MAX_FAILURES


def record_ip_failure(ip: str) -> None:
    if not ip or _ip_exempt(ip):
        return
    now = datetime.now()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO login_ip_failures (ip, at) VALUES (?, ?)", (ip, now.strftime("%Y-%m-%d %H:%M:%S")))
        conn.execute("DELETE FROM login_ip_failures WHERE at < ?", ((now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),))

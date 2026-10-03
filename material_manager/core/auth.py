"""사용자 · 비밀번호 · 권한.

비밀번호는 PBKDF2-SHA256(표준 라이브러리)으로 해시한다. 형식: pbkdf2_sha256$반복횟수$소금$해시
반복 횟수를 해시에 함께 저장하므로 설정을 바꿔도 기존 비밀번호는 그대로 검증된다.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

import config
from core import audit, db, version
from core.utils import now_str

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
# 없는 아이디로 로그인할 때도 같은 시간만큼 해시를 계산해, 응답 시간으로 계정 존재를 알아낼 수 없게 한다
_DUMMY_HASH = None


@dataclass
class AuthResult:
    ok: bool
    message: str
    user: dict | None = None


# ── 비밀번호 ─────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), config.PW_ITERATIONS)
    return f"pbkdf2_sha256${config.PW_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(calc.hex(), digest)


def session_stamp(user: dict) -> str:
    """세션에 넣는 도장. 비밀번호가 바뀌거나 로그아웃하면(session_ver) 달라져
    그 전에 발급된 세션(탈취된 쿠키 포함)이 모두 무효가 된다."""
    raw = f"{user['id']}:{user['password_hash']}:{int(user.get('session_ver') or 0)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def end_sessions(user_id: int) -> None:
    """이 사용자의 모든 세션을 서버에서 끝낸다 (쿠키만 지우는 것이 아니라 도장을 바꾼다)."""
    db.execute("UPDATE users SET session_ver = COALESCE(session_ver, 0) + 1 WHERE id = ?", (user_id,))


def _dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    verify_password(password, _DUMMY_HASH)


def ip_blocked(conn, ip: str) -> bool:
    """한 IP에서 최근 로그인 실패가 너무 많으면 막는다 (여러 계정에 같은 비밀번호를 넣어 보는 공격 방어)."""
    if not ip:
        return False
    since = (datetime.now() - timedelta(minutes=config.LOGIN_LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    fails = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'LOGIN_FAIL' AND ip = ? AND at >= ?",
                         (ip, since)).fetchone()[0]
    return fails >= config.LOGIN_IP_MAX_FAILS


def password_problem(password: str) -> str:
    """비밀번호 정책 위반 사유. 문제없으면 ''."""
    if len(password) < config.PW_MIN_LENGTH:
        return f"비밀번호는 {config.PW_MIN_LENGTH}자 이상이어야 합니다."
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "비밀번호에는 영문과 숫자가 모두 들어가야 합니다."
    return ""


# ── 권한 ─────────────────────────────────────────────────────
def level(role: str) -> int:
    return config.ROLES.get(role, (0, ""))[0]


def has_role(user: dict | None, minimum: str) -> bool:
    return bool(user) and level(user.get("role", "")) >= level(minimum)


def role_label(role: str) -> str:
    return config.ROLES.get(role, (0, role))[1]


# ── 조회 ─────────────────────────────────────────────────────
def _row(conn, sql: str, params) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with db.get_conn() as conn:
        return _row(conn, "SELECT * FROM users WHERE id = ?", (user_id,))


def count_users() -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def users_df() -> pd.DataFrame:
    return db.query_df(
        "SELECT id, username, name, role, active, email, messenger_id, auth_source, all_warehouses, must_change_pw, "
        "failed_count, locked_until, "
        "last_login_at, created_at FROM users ORDER BY active DESC, role DESC, username"
    )


# ── 로그인 ───────────────────────────────────────────────────
def authenticate(username: str, password: str, ip: str = "") -> AuthResult:
    """연속 실패가 한도를 넘으면 일정 시간 잠근다. 실패 사유는 '아이디 또는 비밀번호'로 뭉뚱그린다.

    - 없는 아이디도 해시 계산을 해서 응답 시간이 같다.
    - 중지된 계정이라는 사실은 비밀번호가 맞을 때만 알려 준다.
    - 한 IP의 실패가 많으면 계정과 상관없이 막는다.
    """
    generic = "아이디 또는 비밀번호가 올바르지 않습니다."
    now = datetime.now()
    with db.transaction() as conn:
        anon = {"id": None, "name": username.strip()[:30] or "(빈 아이디)", "ip": ip}
        if ip_blocked(conn, ip):
            audit.record(conn, anon, "LOGIN_FAIL", "user", "", {"reason": "IP 차단"})
            return AuthResult(False, f"로그인 실패가 너무 많아 이 PC에서의 로그인을 {config.LOGIN_LOCK_MINUTES}분간 막았습니다.")
        user = _row(conn, "SELECT * FROM users WHERE username = ?", (username.strip().lower(),))
        if user is None:
            _dummy_verify(password)
            audit.record(conn, anon, "LOGIN_FAIL", "user", "", {"reason": "없는 아이디"})
            return AuthResult(False, generic)
        actor = {"id": user["id"], "name": user["name"], "role": user["role"], "ip": ip}
        if user["locked_until"] and user["locked_until"] > now.strftime("%Y-%m-%d %H:%M:%S"):
            audit.record(conn, actor, "LOGIN_FAIL", "user", user["id"], {"reason": "잠김"})
            _dummy_verify(password)
            return AuthResult(False, generic + f" 실패가 반복되면 {config.LOGIN_LOCK_MINUTES}분간 로그인할 수 없습니다.")
        if verify_password(password, user["password_hash"]) and not user["active"]:
            audit.record(conn, actor, "LOGIN_FAIL", "user", user["id"], {"reason": "비활성 계정"})
            return AuthResult(False, "사용이 중지된 계정입니다. 관리자에게 문의하세요.")
        if not verify_password(password, user["password_hash"]):
            fails = user["failed_count"] + 1
            locked = ""
            if fails >= config.LOGIN_MAX_FAILS:
                locked = (now + timedelta(minutes=config.LOGIN_LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                fails = 0
            conn.execute("UPDATE users SET failed_count = ?, locked_until = ? WHERE id = ?",
                         (fails, locked, user["id"]))
            audit.record(conn, actor, "LOGIN_FAIL", "user", user["id"],
                         {"reason": "비밀번호 불일치", "locked_until": locked or None})
            return AuthResult(False, generic)
        conn.execute("UPDATE users SET failed_count = 0, locked_until = '', last_login_at = ? WHERE id = ?",
                     (now_str(), user["id"]))
        audit.record(conn, actor, "LOGIN", "user", user["id"])
    return AuthResult(True, f"{user['name']}님 환영합니다.", user)


# ── 사용자 관리 ──────────────────────────────────────────────
def create_user(username: str, name: str, role: str, password: str,
                actor: dict | None, must_change_pw: bool = True) -> AuthResult:
    username, name = username.strip().lower(), name.strip()    # 아이디는 소문자로 저장(DB와 무관하게 대소문자 무시)
    if not USERNAME_RE.match(username):
        return AuthResult(False, "아이디는 영문·숫자·._- 3~30자로 입력하세요.")
    if not name:
        return AuthResult(False, "이름을 입력하세요.")
    if role not in config.ROLES:
        return AuthResult(False, "역할을 선택하세요.")
    problem = password_problem(password)
    if problem:
        return AuthResult(False, problem)
    ts = now_str()
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            return AuthResult(False, f"아이디 '{username}'는 이미 있습니다.")
        uid = conn.execute(
            """
            INSERT INTO users (username, name, role, password_hash, must_change_pw, all_warehouses,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (username, name, role, hash_password(password), 1 if must_change_pw else 0,
             1 if config.NEW_USER_ALL_WAREHOUSES else 0, ts, ts),
        ).lastrowid
        audit.record(conn, actor, "USER_CREATE", "user", uid,
                     {"username": username, "name": name, "role": role})
    return AuthResult(True, f"사용자 {name}({username}) 등록 완료", get_user(uid))


def _active_admins(conn, excluding: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND active = 1 AND id <> ?",
                        (excluding,)).fetchone()[0]


def update_user(user_id: int, name: str, role: str, active: bool, actor: dict | None,
                expected: str | None = None) -> AuthResult:
    if role not in config.ROLES or not name.strip():
        return AuthResult(False, "이름과 역할을 확인하세요.")
    if actor and actor.get("id") == user_id:
        return AuthResult(False, "본인 계정의 역할·상태는 다른 시스템관리자가 바꿔야 합니다(직무 분리).")
    with db.transaction() as conn:
        before = _row(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        if before is None:
            return AuthResult(False, "사용자가 없습니다.")
        if version.stale("users", before, expected):
            return AuthResult(False, version.STALE)
        after = {"name": name.strip(), "role": role, "active": 1 if active else 0}
        losing_admin = before["role"] == "ADMIN" and before["active"] and (role != "ADMIN" or not active)
        if losing_admin and _active_admins(conn, user_id) == 0:
            return AuthResult(False, "마지막 시스템관리자는 역할을 낮추거나 중지할 수 없습니다.")
        diff = audit.changes(before, after, after.keys())
        if not diff:
            return AuthResult(True, "변경된 내용이 없습니다.")
        conn.execute("UPDATE users SET name = ?, role = ?, active = ?, suspended_by = ?, updated_at = ? WHERE id = ?",
                     (after["name"], role, after["active"], "" if active else "admin", now_str(), user_id))
        audit.record(conn, actor, "USER_UPDATE", "user", user_id, diff)
    return AuthResult(True, "사용자 정보를 저장했습니다.")


def set_email(user_id: int, email: str, actor: dict | None, messenger_id: str | None = None) -> AuthResult:
    """결재 알림을 받을 메일 주소·메신저 아이디(네이버웍스, 비우면 메일 주소로). 둘 다 비우면 알림을 받지 않는다."""
    email = email.strip()
    if email and (len(email) > 200 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
        return AuthResult(False, "메일 주소 형식을 확인하세요.")
    with db.transaction() as conn:
        before = _row(conn, "SELECT email, messenger_id FROM users WHERE id = ?", (user_id,))
        if before is None:
            return AuthResult(False, "사용자가 없습니다.")
        mid = (before["messenger_id"] or "") if messenger_id is None else messenger_id.strip()[:100]
        if (before["email"] or "") == email and (before["messenger_id"] or "") == mid:
            return AuthResult(True, "변경된 내용이 없습니다.")
        conn.execute("UPDATE users SET email = ?, messenger_id = ?, updated_at = ? WHERE id = ?", (email, mid, now_str(), user_id))
        audit.record(conn, actor, "USER_EMAIL", "user", user_id,
                     {"email": [before["email"] or "", email], "messenger_id": [before["messenger_id"] or "", mid]})
    return AuthResult(True, "알림 받을 곳을 저장했습니다." if email or mid else "알림 받을 곳을 지웠습니다.")


def reset_password(user_id: int, new_password: str, actor: dict | None) -> AuthResult:
    """관리자가 임시 비밀번호로 초기화 → 다음 로그인 때 본인이 바꾸게 한다. 잠금도 푼다."""
    if actor and actor.get("id") == user_id:
        return AuthResult(False, "본인 비밀번호는 '비밀번호 변경' 화면에서 바꾸세요.")
    problem = password_problem(new_password)
    if problem:
        return AuthResult(False, problem)
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            return AuthResult(False, "사용자가 없습니다.")
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_pw = 1, failed_count = 0, locked_until = '', "
            "updated_at = ? WHERE id = ?", (hash_password(new_password), now_str(), user_id))
        audit.record(conn, actor, "PASSWORD_RESET", "user", user_id)
    return AuthResult(True, "임시 비밀번호로 초기화했습니다. 다음 로그인 때 변경하도록 안내하세요.")


def change_password(user_id: int, current: str, new: str, actor: dict | None) -> AuthResult:
    user = get_user(user_id)
    if user is None or not verify_password(current, user["password_hash"]):
        return AuthResult(False, "현재 비밀번호가 올바르지 않습니다.")
    if current == new:
        return AuthResult(False, "새 비밀번호는 현재 비밀번호와 달라야 합니다.")
    problem = password_problem(new)
    if problem:
        return AuthResult(False, problem)
    with db.transaction() as conn:
        conn.execute("UPDATE users SET password_hash = ?, must_change_pw = 0, updated_at = ? WHERE id = ?",
                     (hash_password(new), now_str(), user_id))
        audit.record(conn, actor, "PASSWORD_CHANGE", "user", user_id)
    return AuthResult(True, "비밀번호를 변경했습니다.", get_user(user_id))

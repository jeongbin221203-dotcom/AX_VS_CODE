"""2단계 인증 (TOTP, RFC 6238). Google Authenticator · Microsoft Authenticator 등 인증 앱과 호환.

- 비밀키는 DB에 암호화해 저장한다(Fernet, 키는 MM_MFA_KEY 또는 MM_SECRET_KEY에서 만든다).
  → MM_SECRET_KEY를 바꾸면 등록된 2단계 인증을 다시 등록해야 하므로, 운영에서는 MM_MFA_KEY를 따로 둔다.
- 한 번 쓴 코드(같은 30초 구간)는 다시 쓸 수 없다(재전송 공격 방어).
- 복구 코드 10개(한 번씩만 사용)는 해시로만 저장한다.
- config.MFA_REQUIRED_ROLES 역할은 2단계 인증을 등록해야만 쓸 수 있다. SSO 사용자는 IdP의 2단계 인증을 따른다.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
import urllib.parse

from cryptography.fernet import Fernet, InvalidToken

import config
from core import audit, db
from core.utils import now_str

STEP = 30
DIGITS = 6


def _fernet() -> Fernet:
    raw = os.getenv("MM_MFA_KEY") or config.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("mfa:" + raw).encode()).digest()))


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS
    return str(value).zfill(DIGITS)


def now_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def provisioning_uri(secret: str, username: str) -> str:
    label = urllib.parse.quote(f"{config.TOTP_ISSUER}:{username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={urllib.parse.quote(config.TOTP_ISSUER)}"
            f"&digits={DIGITS}&period={STEP}")


def required(user: dict) -> bool:
    return user.get("auth_source", "local") != "sso" and user.get("role") in config.MFA_REQUIRED_ROLES


def _match_step(secret: str, code: str, last_step: int) -> int | None:
    """맞는 30초 구간 번호 (앞뒤 1구간 허용, 이미 쓴 구간은 제외)."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now = int(time.time() // STEP)
    for step in (now - 1, now, now + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def _norm_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def _hash_code(code: str, salt: str | None = None) -> str:
    """복구 코드 저장값 '소금$해시'. 코드가 64비트라 DB가 새어도 대입으로 찾을 수 없다."""
    salt = salt if salt is not None else secrets.token_hex(8)
    return salt + "$" + hashlib.sha256((salt + ":" + _norm_code(code)).encode()).hexdigest()


def _code_matches(code: str, stored: str) -> bool:
    if "$" in stored:
        salt = stored.split("$", 1)[0]
        return hmac.compare_digest(_hash_code(code, salt), stored)
    return hmac.compare_digest(hashlib.sha256(_norm_code(code).encode()).hexdigest(), stored)   # 예전 형식


def enable(user_id: int, secret: str, code: str, actor: dict) -> tuple[bool, str, list[str]]:
    """등록 확인: 인증 앱에 뜬 코드가 맞으면 켜고 복구 코드를 돌려준다(한 번만 보여 줌)."""
    step = _match_step(secret, code, 0)
    if step is None:
        return False, "인증 앱의 6자리 코드가 맞지 않습니다. 휴대폰 시간이 맞는지 확인하세요.", []
    codes = ["-".join(secrets.token_hex(2).upper() for _ in range(4)) for _ in range(10)]   # 64비트
    with db.transaction() as conn:
        conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 1, totp_last_step = ?, recovery_codes = ?, "
                     "updated_at = ? WHERE id = ?",
                     (_fernet().encrypt(secret.encode()).decode(), step,
                      json.dumps([_hash_code(c) for c in codes]), now_str(), user_id))
        audit.record(conn, actor, "MFA_ENABLE", "user", user_id)
    return True, "2단계 인증을 켰습니다. 복구 코드를 안전한 곳에 보관하세요(다시 볼 수 없습니다).", codes


def disable(user_id: int, actor: dict, reason: str = "") -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE users SET totp_secret = '', totp_enabled = 0, totp_last_step = 0, recovery_codes = '', "
                     "updated_at = ? WHERE id = ?", (now_str(), user_id))
        audit.record(conn, actor, "MFA_DISABLE", "user", user_id, {"reason": reason} if reason else None)


def verify(user_id: int, code: str, ip: str = "") -> tuple[bool, str]:
    """로그인 2단계. 인증 앱 코드 또는 복구 코드. 실패는 비밀번호 실패와 같은 잠금 횟수에 더한다."""
    with db.transaction() as conn:
        db.lock(conn, f"mfa:{user_id}")
        u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if u is None or not u["totp_enabled"]:
            return False, "2단계 인증이 설정되지 않았습니다."
        actor = {"id": u["id"], "name": u["name"], "role": u["role"], "ip": ip}
        if u["locked_until"] and u["locked_until"] > now_str():
            audit.record(conn, actor, "MFA_FAIL", "user", user_id, {"reason": "잠김"})
            return False, f"인증 실패가 반복되어 {u['locked_until'][11:16]}까지 잠겼습니다."
        try:
            secret = _fernet().decrypt(u["totp_secret"].encode()).decode()
        except InvalidToken:
            audit.record(conn, actor, "MFA_FAIL", "user", user_id, {"reason": "비밀키 복호화 실패(키 변경?)"})
            return False, "2단계 인증 정보를 읽을 수 없습니다. 시스템관리자에게 재등록을 요청하세요."
        step = _match_step(secret, code, int(u["totp_last_step"] or 0))
        if step is not None:
            conn.execute("UPDATE users SET totp_last_step = ?, failed_count = 0 WHERE id = ?", (step, user_id))
            return True, ""
        hashes = json.loads(u["recovery_codes"] or "[]")
        h = next((x for x in hashes if _code_matches(code, x)), None)
        if h is not None:
            hashes.remove(h)
            conn.execute("UPDATE users SET recovery_codes = ?, failed_count = 0 WHERE id = ?",
                         (json.dumps(hashes), user_id))
            audit.record(conn, actor, "MFA_RECOVERY", "user", user_id, {"left": len(hashes)})
            return True, f"복구 코드를 사용했습니다. 남은 복구 코드 {len(hashes)}개."
        fails = int(u["failed_count"] or 0) + 1
        locked = ""
        if fails >= config.LOGIN_MAX_FAILS:
            from datetime import datetime, timedelta
            locked = (datetime.now() + timedelta(minutes=config.LOGIN_LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
            fails = 0
        conn.execute("UPDATE users SET failed_count = ?, locked_until = ? WHERE id = ?", (fails, locked, user_id))
        audit.record(conn, actor, "MFA_FAIL", "user", user_id, {"locked_until": locked or None})
    return False, "인증 코드가 맞지 않습니다." + (" 반복 실패로 계정이 잠겼습니다." if locked else "")

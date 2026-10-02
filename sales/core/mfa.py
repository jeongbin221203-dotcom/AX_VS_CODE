"""2단계 인증 — TOTP(RFC 6238, Google·Microsoft Authenticator 호환) + 일회용 복구 코드

  적용 대상: 이 시스템이 직접 비밀번호를 확인하는 로그인(password 모드, 비상 계정).
            SSO·OIDC 로그인은 회사 IdP 의 2단계 인증을 따른다.
  필수 역할: 회사 설정 mfa_required_roles (기본: 시스템관리자). 등록하지 않으면 다른 화면으로 가지 못한다.
  비밀키는 SALES_MFA_KEY(없으면 SALES_SECRET_KEY)로 암호화해 저장하고, 같은 코드는 두 번 쓸 수 없다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

from . import sales_db as db

STEP, DIGITS, WINDOW = 30, 6, 1          # 30초, 6자리, 앞뒤 1구간 허용(시계 오차)
RECOVERY_COUNT = 8


def _fernet():
    from cryptography.fernet import Fernet
    raw = os.environ.get("SALES_MFA_KEY") or os.environ.get("SALES_SECRET_KEY") or "dev-only-mfa-key"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("sales-mfa:" + raw).encode()).digest()))


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def totp(secret: str, counter: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def match(secret: str, code: str, now: Optional[float] = None) -> Optional[int]:
    """맞으면 그 시간 구간 번호, 틀리면 None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((now or time.time()) // STEP)
    for counter in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(totp(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_data_uri(uri: str) -> str:
    import segno
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


# ---------------------------------------------------------------------------
# 사용자별
# ---------------------------------------------------------------------------
def enabled(user: dict) -> bool:
    return bool(user and user.get("totp_enabled_at") and user.get("totp_secret"))


def required(user: dict) -> bool:
    from . import company
    return (user or {}).get("role") in set(company.get("mfa_required_roles") or [])


def _secret_of(user: dict) -> str:
    return _fernet().decrypt(user["totp_secret"].encode()).decode()


def _hash_code(code: str) -> str:
    return hashlib.sha256(("sales-recovery:" + code.replace("-", "").upper()).encode()).hexdigest()


def enable(user_id: int, secret: str, code: str) -> list[str]:
    """첫 코드를 확인한 뒤 켠다. 복구 코드(한 번만 보여 줌)를 돌려준다."""
    counter = match(secret, code)
    if counter is None:
        raise ValueError("인증 코드가 맞지 않습니다. 앱에 표시된 6자리 숫자를 입력하세요(휴대폰 시간이 맞는지도 확인).")
    codes = [f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}" for _ in range(RECOVERY_COUNT)]
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret=?, totp_enabled_at=?, totp_last_counter=?, recovery_codes=? WHERE id=?",
                     (_fernet().encrypt(secret.encode()).decode(), db._now(), counter,
                      json.dumps([_hash_code(c) for c in codes]), int(user_id)))
    db.audit("2단계인증등록", "사용자", int(user_id))
    return codes


def disable(user_id: int, actor: dict, reason: str = "") -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret=NULL, totp_enabled_at=NULL, totp_last_counter=NULL, "
                     "recovery_codes=NULL WHERE id=?", (int(user_id),))
    db.audit("2단계인증해제", "사용자", int(user_id), {"처리자": actor.get("name"), "사유": reason or None})


def verify(user_id: int, code: str) -> tuple[bool, str]:
    """로그인 2단계. (성공 여부, 방식 'TOTP'|'복구코드'). 같은 코드·복구 코드는 다시 쓸 수 없다."""
    from . import enterprise as ent
    user = ent.get_user(user_id=int(user_id))
    if not enabled(user):
        return False, ""
    raw = str(code or "").strip()
    if "-" in raw or len(raw) == 8 and not raw.isdigit():
        hashes = json.loads(user.get("recovery_codes") or "[]")
        digest = _hash_code(raw)
        if digest in hashes:
            hashes.remove(digest)
            with db.get_conn() as conn:
                conn.execute("UPDATE users SET recovery_codes=? WHERE id=?", (json.dumps(hashes), user_id))
            db.audit("복구코드사용", "사용자", int(user_id), {"남은코드": len(hashes)})
            return True, "복구코드"
        return False, ""
    counter = match(_secret_of(user), raw)
    if counter is None:
        return False, ""
    with db.get_conn() as conn:      # 같은 구간 코드 재사용(엿본 코드로 다시 로그인) 차단
        used = conn.execute("UPDATE users SET totp_last_counter=? WHERE id=? AND COALESCE(totp_last_counter, -1) < ?",
                            (counter, user_id, counter)).rowcount
    return (bool(used), "TOTP") if used else (False, "")

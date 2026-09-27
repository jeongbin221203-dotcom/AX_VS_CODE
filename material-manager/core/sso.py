"""사내 SSO (OpenID Connect). Microsoft Entra ID(Azure AD) · Okta · Keycloak · Google Workspace 등.

흐름: /sso/login → IdP 로그인(2단계 인증은 IdP 정책) → /sso/callback?code=...
  - 인가 코드 + PKCE(S256), state(위조 요청 방지), nonce(토큰 재사용 방지)
  - ID 토큰은 IdP 공개키(JWKS)로 서명을 검증하고 iss·aud·exp·nonce를 확인한다(joserfc, RS256/ES256만 허용).
  - 그룹 클레임 → 역할(MM_SSO_ROLE_MAP, 가장 높은 역할). 맞는 그룹이 없으면 로그인 거부.
  - 처음 로그인하면 계정을 만든다(JIT). 역할은 로그인할 때마다 IdP 그룹으로 다시 맞춘다 → 퇴사·부서이동이 IdP에서 반영.
  - 같은 아이디의 로컬 계정이 있으면 자동으로 합치지 않는다(계정 탈취 방지) — 관리자가 정리해야 한다.
SAML만 되는 IdP는 IdP 쪽 OIDC 브리지(Entra·Okta·Keycloak 모두 지원)를 쓴다.
"""

import base64
import hashlib
import json
import re
import secrets
import time
import urllib.parse
import urllib.request

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

import config
from core import audit, auth, db
from core.utils import now_str

_cache: dict[str, tuple[float, dict]] = {}


def enabled() -> bool:
    return config.SSO_ENABLED and bool(config.SSO_ISSUER and config.SSO_CLIENT_ID)


# ── HTTP (테스트에서 바꿔 끼운다) ────────────────────────────
def _require_https(url: str) -> str:
    """IdP와의 통신(발견 문서·공개키·토큰)은 https만. 같은 PC의 테스트 IdP만 예외."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"IdP 주소는 https여야 합니다: {parts.scheme}://{parts.hostname}")
    return url


def _http_get(url: str) -> dict:
    with urllib.request.urlopen(_require_https(url), timeout=10) as res:  # noqa: S310 (https만 허용)
        return json.loads(res.read().decode())


def _http_post(url: str, data: dict) -> dict:
    req = urllib.request.Request(_require_https(url), data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read().decode())


def _cached(key: str, loader, ttl: int = 3600) -> dict:
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    value = loader()
    _cache[key] = (time.time() + ttl, value)
    return value


def discovery() -> dict:
    url = config.SSO_ISSUER.rstrip("/") + "/.well-known/openid-configuration"
    return _cached("discovery:" + url, lambda: _http_get(url))


def _jwks(force: bool = False) -> dict:
    uri = discovery()["jwks_uri"]
    if force:
        _cache.pop("jwks:" + uri, None)                # 키 교체(rotation) 대응
    return _cached("jwks:" + uri, lambda: _http_get(uri))


# ── 로그인 시작 ─────────────────────────────────────────────
def start() -> tuple[str, dict]:
    """(IdP로 보낼 주소, 세션에 보관할 값)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {"response_type": "code", "client_id": config.SSO_CLIENT_ID, "redirect_uri": config.SSO_REDIRECT_URI,
              "scope": "openid profile email", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    url = discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    return url, {"state": state, "nonce": nonce, "verifier": verifier, "at": int(time.time())}


# ── 콜백 ────────────────────────────────────────────────────
def _decode(id_token: str, nonce: str) -> dict:
    registry = JWTClaimsRegistry(leeway=60,
                                 iss={"essential": True, "value": discovery().get("issuer", config.SSO_ISSUER)},
                                 aud={"essential": True, "value": config.SSO_CLIENT_ID},
                                 exp={"essential": True}, sub={"essential": True},
                                 nonce={"essential": True, "value": nonce})
    for force in (False, True):                      # 모르는 키(kid)면 JWKS를 한 번 새로 받아 다시 시도
        try:
            token = jwt.decode(id_token, KeySet.import_key_set(_jwks(force)), algorithms=["RS256", "ES256"])
            registry.validate(token.claims)
            return dict(token.claims)
        except (JoseError, ValueError):
            if force:
                raise
    raise JoseError("ID 토큰 검증 실패")


def role_from_groups(groups) -> str | None:
    if isinstance(groups, str):
        groups = [groups]
    roles = [config.SSO_ROLE_MAP[g] for g in (groups or []) if g in config.SSO_ROLE_MAP]
    roles = [r for r in roles if r in config.ROLES]
    return max(roles, key=auth.level) if roles else None


def finish(args: dict, stash: dict, ip: str = "") -> tuple[dict | None, str]:
    """(로그인할 사용자, 오류 메시지)."""
    anon = {"id": None, "name": "(SSO)", "ip": ip}
    if not stash or args.get("state") != stash.get("state") or time.time() - stash.get("at", 0) > 600:
        audit.log(anon, "SSO_FAIL", "user", "", {"reason": "state 불일치 또는 만료"})
        return None, "로그인 요청이 만료되었거나 올바르지 않습니다. 다시 시도하세요."
    if args.get("error"):
        audit.log(anon, "SSO_FAIL", "user", "", {"reason": args.get("error")})
        return None, "사내 로그인이 취소되었거나 거부되었습니다."
    try:
        tokens = _http_post(discovery()["token_endpoint"], {
            "grant_type": "authorization_code", "code": args.get("code", ""), "redirect_uri": config.SSO_REDIRECT_URI,
            "client_id": config.SSO_CLIENT_ID, "client_secret": config.SSO_CLIENT_SECRET,
            "code_verifier": stash["verifier"]})
        claims = _decode(tokens["id_token"], stash["nonce"])
    except (JoseError, KeyError, OSError, ValueError) as exc:
        audit.log(anon, "SSO_FAIL", "user", "", {"reason": f"토큰 검증 실패: {type(exc).__name__}"})
        return None, "사내 로그인 응답을 확인하지 못했습니다. 관리자에게 문의하세요."
    if config.SSO_REQUIRE_MFA and "mfa" not in (claims.get("amr") or []):
        audit.log({**anon, "name": str(claims.get("preferred_username", ""))[:30]}, "SSO_FAIL", "user", "",
                  {"reason": "IdP 2단계 인증 없음(amr)"})
        return None, "사내 로그인에서 2단계 인증을 거쳐야 합니다."
    return _upsert_user(claims, ip)


def _username(claims: dict) -> str:
    raw = str(claims.get("preferred_username") or claims.get("email") or claims.get("upn") or claims["sub"])
    name = re.sub(r"[^a-z0-9._-]", "", raw.lower().split("@")[0])[:30]
    return name if len(name) >= 3 else f"sso-{hashlib.sha1(claims['sub'].encode(), usedforsecurity=False).hexdigest()[:8]}"


def _upsert_user(claims: dict, ip: str) -> tuple[dict | None, str]:
    sub = str(claims["sub"])
    role = role_from_groups(claims.get(config.SSO_GROUPS_CLAIM))
    display = str(claims.get("name") or claims.get("preferred_username") or sub)[:50]
    who = {"id": None, "name": display, "ip": ip}
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE sso_subject = ?", (sub,)).fetchone()
        if role is None:
            audit.record(conn, who, "SSO_FAIL", "user", user["id"] if user else "", {"reason": "매핑된 그룹 없음"})
            if user is not None and user["active"]:
                conn.execute("UPDATE users SET active = 0, suspended_by = 'sso', updated_at = ? WHERE id = ?",
                             (now_str(), user["id"]))
            return None, "이 시스템을 쓸 권한(사내 그룹)이 없습니다. 담당 부서에 권한을 요청하세요."
        ts = now_str()
        if user is None:
            username = _username(claims)
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                audit.record(conn, who, "SSO_FAIL", "user", "", {"reason": "아이디 충돌", "username": username})
                return None, f"아이디 '{username}'가 로컬 계정과 겹칩니다. 시스템관리자에게 계정 정리를 요청하세요."
            uid = conn.execute(
                "INSERT INTO users (username, name, role, password_hash, auth_source, sso_subject, all_warehouses, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'sso', ?, ?, ?, ?)",
                (username, display, role, "sso$" + secrets.token_hex(16), sub,
                 1 if config.NEW_USER_ALL_WAREHOUSES else 0, ts, ts)).lastrowid   # 비밀번호 로그인 불가
            audit.record(conn, {**who, "id": uid}, "USER_CREATE", "user", uid,
                         {"username": username, "role": role, "source": "sso"})
        else:
            uid = user["id"]
            if not user["active"] and (user["suspended_by"] or "") != "sso":
                audit.record(conn, {**who, "id": uid}, "SSO_FAIL", "user", uid, {"reason": "관리자가 중지한 계정"})
                return None, "사용이 중지된 계정입니다. 관리자에게 문의하세요."
            changes = {}
            if user["role"] != role:
                changes["role"] = [user["role"], role]
            if user["name"] != display:
                changes["name"] = [user["name"], display]
            conn.execute("UPDATE users SET role = ?, name = ?, active = 1, suspended_by = '', last_login_at = ?, "
                         "updated_at = ? WHERE id = ?",
                         (role, display, ts, ts, uid))
            if changes:
                audit.record(conn, {**who, "id": uid}, "USER_UPDATE", "user", uid, {**changes, "source": "sso"})
        audit.record(conn, {**who, "id": uid, "role": role}, "SSO_LOGIN", "user", uid)
    return auth.get_user(uid), ""

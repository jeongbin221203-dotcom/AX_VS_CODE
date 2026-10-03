"""OIDC(OpenID Connect) 로그인 — 사내 IdP(Azure AD/Entra ID, Okta, Keycloak 등)에 직접 붙는다.

  SALES_AUTH_MODE=oidc
  SALES_OIDC_METADATA_URL  https://login.example.com/.well-known/openid-configuration
  SALES_OIDC_CLIENT_ID / SALES_OIDC_CLIENT_SECRET
  SALES_OIDC_SCOPE         기본 "openid profile email"
  SALES_OIDC_CLAIM         사용자 식별 클레임 (기본 preferred_username). 값은 SALES_SSO_MATCH(사번/이메일)와 맞춘다
  SALES_OIDC_REDIRECT_URL  프록시 뒤에서 콜백 주소를 고정할 때 (예: https://sales.example.com/login/oidc/callback)
  SALES_OIDC_LOGOUT=1      로그아웃 때 IdP 세션도 끝낸다 (end_session_endpoint)

  Authorization Code + PKCE(S256), state·nonce 검증, ID 토큰 서명(jwks)·발급자·대상·만료 검증은 authlib 가 한다.
  IdP 로그인에 성공해도 이 시스템에 등록된 활성 사용자가 아니면 들어올 수 없다(권한은 이 시스템이 관리).
"""
from __future__ import annotations

import os
from typing import Optional

from flask import Flask, current_app


def settings() -> dict:
    env = os.environ.get
    return {"metadata_url": env("SALES_OIDC_METADATA_URL", ""), "client_id": env("SALES_OIDC_CLIENT_ID", ""),
            "client_secret": env("SALES_OIDC_CLIENT_SECRET", ""),
            "scope": env("SALES_OIDC_SCOPE", "openid profile email"),
            "claim": env("SALES_OIDC_CLAIM", "preferred_username"),
            "redirect_url": env("SALES_OIDC_REDIRECT_URL", ""), "logout": env("SALES_OIDC_LOGOUT", "0") == "1"}


def problems() -> list[str]:
    s = settings()
    return [f"{name} 를 지정하세요 (OIDC 로그인)." for name, key in
            (("SALES_OIDC_METADATA_URL", "metadata_url"), ("SALES_OIDC_CLIENT_ID", "client_id"),
             ("SALES_OIDC_CLIENT_SECRET", "client_secret")) if not s[key]]


def client(app: Optional[Flask] = None):
    """설정별로 한 번 만들어 둔 authlib 클라이언트."""
    from authlib.integrations.flask_client import OAuth
    app = app or current_app
    s = settings()
    key = (s["metadata_url"], s["client_id"])
    cache = app.extensions.setdefault("sales_oidc", {})
    if key not in cache:
        oauth = OAuth(app)
        cache[key] = oauth.register(
            name=f"idp{len(cache)}", server_metadata_url=s["metadata_url"], client_id=s["client_id"],
            client_secret=s["client_secret"],
            client_kwargs={"scope": s["scope"], "code_challenge_method": "S256"})
    return cache[key]


def identity(token: dict) -> Optional[str]:
    claims = token.get("userinfo") or {}
    value = claims.get(settings()["claim"])
    return str(value).strip() if value else None

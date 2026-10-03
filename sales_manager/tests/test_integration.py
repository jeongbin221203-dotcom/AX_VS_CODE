"""외부 시스템 연동: 인사(HR) 동기화 · OIDC 로그인(모의 IdP) · REST API."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import threading
import time
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from conftest import TMP, login, post, user

from core import api_keys
from core import auth as core_auth
from core import hr
from core import jobs
from core import sales_db as db


# ============================================================================
# 인사(HR) 연동
# ============================================================================
FEED1 = {
    "orgs": [{"code": "H100", "name": "HR본부", "parent": None, "type": "본부"},
             {"code": "H110", "name": "HR영업팀", "parent": "H100", "type": "팀"}],
    "employees": [
        {"emp_no": "5001", "name": "홍길동", "email": "5001@corp.kr", "org": "H110", "position": "사원", "status": "재직"},
        {"emp_no": "5002", "name": "이연동", "email": "5002@corp.kr", "org": "H110", "position": "팀장", "status": "재직"},
        {"emp_no": "5003", "name": "박이직", "email": "5003@corp.kr", "org": "H110", "position": "대리", "status": "재직"},
    ],
}


def test_hr_sync_preview_apply_and_leavers(app, monkeypatch):
    admin = login(app, "시스템관리자")
    res = post(admin, "/admin/hr/preview",
               {"file": (io.BytesIO(json.dumps(FEED1, ensure_ascii=False).encode()), "hr.json")},
               content_type="multipart/form-data")
    assert res.status_code == 302
    page = admin.get(res.headers["Location"]).get_data(as_text=True)
    assert "홍길동" in page and "H110" in page and "반영" in page
    assert db._one("SELECT id FROM users WHERE emp_no='5001'") is None            # 미리보기는 반영하지 않는다
    token = parse_qs(urlparse(res.headers["Location"]).query)["hr_token"][0]
    assert post(admin, "/admin/hr/apply", {"hr_token": token}).status_code == 302

    hong, lee = user(emp_no="5001"), user(emp_no="5002")
    assert hong["source"] == "hr" and hong["role"] == "REP" and lee["role"] == "MANAGER"   # 직위 → 역할
    team = db._one("SELECT * FROM orgs WHERE code='H110'")
    assert team["parent_id"] == db._one("SELECT id FROM orgs WHERE code='H100'")["id"]
    assert hong["org_id"] == team["id"]
    assert db._one("SELECT id FROM audit_log WHERE action='인사연동'")

    # 박이직에게 거래처, 이연동에게 대결 지정이 있는 상태에서 퇴직·누락
    db.set_context("system", None)
    park = user(emp_no="5003")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO customers (name, owner, owner_id, status, created_at, updated_at) "
                     "VALUES ('이직자거래처', ?, ?, '활성', ?, ?)", (park["name"], park["id"], db._now(), db._now()))
        conn.execute("INSERT INTO delegations (from_user_id, to_user_id, start_date, end_date, reason, created_at) "
                     "VALUES (?, ?, '2026-01-01', '2099-12-31', '테스트', ?)", (lee["id"], hong["id"], db._now()))
    feed2 = {"orgs": FEED1["orgs"], "employees": [
        {**FEED1["employees"][0], "name": "홍길순", "email": "hong@corp.kr"},
        {**FEED1["employees"][1], "status": "퇴직"}]}
    # 활성 인사 인원 3명 → 피드 재직 1명: 잘린 파일로 보고 멈춘다
    blocked = hr.sync(feed2, apply=False)
    assert blocked.get("blocked")
    with pytest.raises(ValueError, match="잘렸을"):
        hr.sync(feed2, apply=True)
    monkeypatch.setenv("SALES_HR_MIN_RATIO", "0.3")
    result = hr.sync(feed2, apply=True)
    assert result["applied"]["users_leave"] == 2 and result["applied"]["handovers"] == 1
    assert user(emp_no="5001")["name"] == "홍길순"
    assert not db._one("SELECT active FROM users WHERE emp_no='5002'")["active"]
    assert not db._one("SELECT active FROM users WHERE emp_no='5003'")["active"]
    assert db._one("SELECT revoked_at FROM delegations WHERE from_user_id=?", [lee["id"]])["revoked_at"]
    assert db._one("SELECT id FROM notifications WHERE kind='담당이관' AND user_id=?", [user("시스템관리자")["id"]])
    # 로컬 계정(시드 사용자)은 피드에 없어도 그대로
    assert user("김영업")["active"]


def test_hr_never_removes_last_admin_and_runs_as_job(app, monkeypatch):
    feed = {"orgs": [], "employees": [{"emp_no": "9999", "name": "시스템관리자", "status": "퇴직"},
                                      {"emp_no": "5001", "name": "홍길순", "org": "H110"}]}
    monkeypatch.setenv("SALES_HR_MIN_RATIO", "0")
    p = hr.plan(hr.parse_feed(feed))
    assert not any(u["emp_no"] == "9999" for u in p["users_leave"])
    assert any("마지막 활성 관리자" in w for w in p["warnings"])

    path = TMP / "hr_feed.json"
    path.write_text(json.dumps({"orgs": FEED1["orgs"], "employees": [FEED1["employees"][0]]}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setenv("SALES_HR_SOURCE", str(path))
    job_id = jobs.enqueue("hr.sync", {}, dedupe_key=f"hr-test-{secrets.token_hex(3)}")
    jobs.run_pending()
    assert db._one("SELECT status FROM jobs WHERE id=?", [job_id])["status"] == "완료"


# ============================================================================
# OIDC — 모의 IdP 를 실제 HTTP 서버로 띄워 authlib 가 메타데이터·토큰·jwks 를 받아 가게 한다
# ============================================================================
@pytest.fixture
def idp():
    import warnings
    with warnings.catch_warnings():                 # 모의 IdP 에서만 쓰는 옛 API
        warnings.simplefilter("ignore")
        from authlib.jose import JsonWebKey, jwt
    from flask import Flask, jsonify, redirect, request
    from werkzeug.serving import make_server

    key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
    server_app = Flask("mock_idp")
    state = {"codes": {}, "client_id": "sales-app", "secret": "s3cret", "base": ""}

    @server_app.get("/.well-known/openid-configuration")
    def meta():
        b = state["base"]
        return jsonify(issuer=b, authorization_endpoint=b + "/authorize", token_endpoint=b + "/token",
                       jwks_uri=b + "/jwks", end_session_endpoint=b + "/logout",
                       response_types_supported=["code"], subject_types_supported=["public"],
                       id_token_signing_alg_values_supported=["RS256"], code_challenge_methods_supported=["S256"])

    @server_app.get("/authorize")
    def authorize():
        a = request.args
        assert a["client_id"] == state["client_id"] and a["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        state["codes"][code] = {"user": a["login_hint"], "nonce": a["nonce"], "challenge": a["code_challenge"],
                                "redirect_uri": a["redirect_uri"]}
        return redirect(a["redirect_uri"] + "?" + urlencode({"code": code, "state": a["state"]}))

    @server_app.post("/token")
    def token():
        auth = request.authorization
        if not auth or (auth.username, auth.password) != (state["client_id"], state["secret"]):
            return jsonify(error="invalid_client"), 401
        info = state["codes"].pop(request.form["code"], None)
        verifier = request.form.get("code_verifier", "")
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not info or digest != info["challenge"] or request.form["redirect_uri"] != info["redirect_uri"]:
            return jsonify(error="invalid_grant"), 400
        now = int(time.time())
        claims = {"iss": state["base"], "aud": state["client_id"], "sub": "u-" + info["user"], "iat": now,
                  "exp": now + 300, "nonce": info["nonce"], "preferred_username": info["user"]}
        id_token = jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key).decode()
        return jsonify(access_token="at-" + secrets.token_hex(8), token_type="Bearer", expires_in=300,
                       id_token=id_token)

    @server_app.get("/jwks")
    def jwks():
        return jsonify(keys=[key.as_dict(is_private=False)])

    server = make_server("127.0.0.1", 0, server_app)
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, server_app.test_client()
    server.shutdown()


def _oidc_login(app, idp_client, login_hint: str, next_path: str = "/quotes"):
    client = app.test_client()
    res = client.get(f"/login/oidc?next={next_path}")
    assert res.status_code == 302, res.get_data(as_text=True)[:300]
    auth_url = urlparse(res.headers["Location"])
    query = parse_qs(auth_url.query)
    assert query["code_challenge_method"] == ["S256"] and query.get("nonce") and query.get("state")
    back = idp_client.get(auth_url.path + "?" + auth_url.query + "&" + urlencode({"login_hint": login_hint}))
    cb = urlparse(back.headers["Location"])
    return client, client.get(cb.path + "?" + cb.query), cb


def test_oidc_login_flow(app, idp, monkeypatch):
    state, idp_client = idp
    monkeypatch.setattr(core_auth, "AUTH_MODE", "oidc")
    monkeypatch.setenv("SALES_OIDC_METADATA_URL", state["base"] + "/.well-known/openid-configuration")
    monkeypatch.setenv("SALES_OIDC_CLIENT_ID", state["client_id"])
    monkeypatch.setenv("SALES_OIDC_CLIENT_SECRET", state["secret"])

    anon = app.test_client()
    assert "사내 계정으로 로그인" in anon.get("/login").get_data(as_text=True)

    kim = user("김영업")
    client, res, _cb = _oidc_login(app, idp_client, kim["emp_no"])
    assert res.status_code == 302 and res.headers["Location"].endswith("/quotes")
    assert client.get("/quotes").status_code == 200
    assert db._one("SELECT detail FROM audit_log WHERE action='로그인' ORDER BY id DESC")["detail"].count("OIDC")

    # IdP 에서는 인증됐지만 이 시스템에 없는 사용자 → 403
    _c, res, _cb = _oidc_login(app, idp_client, "0000")
    assert res.status_code == 403 and "등록된 활성 사용자가 아닙니다" in res.get_data(as_text=True)

    # state 위조 → 거부
    client, _res, cb = _oidc_login(app, idp_client, kim["emp_no"])
    fresh = app.test_client()
    fresh.get("/login/oidc")
    q = parse_qs(cb.query)
    res = fresh.get(cb.path + "?" + urlencode({"code": q["code"][0], "state": "forged"}))
    assert res.status_code == 401

    # 운영 설정 검사: OIDC 필수 값 누락
    monkeypatch.delenv("SALES_OIDC_CLIENT_SECRET")
    from core import oidc
    assert any("CLIENT_SECRET" in p for p in oidc.problems())


# ============================================================================
# REST API
# ============================================================================
def _issue(admin, name: str, scopes: list[str], acting: str = "김영업", **extra) -> str:
    res = post(admin, "/admin/api/create", {"name": name, "user_id": user(acting)["id"], "scopes": scopes, **extra})
    assert res.status_code == 200, res.get_data(as_text=True)[:500]
    return re.search(r"(sk_[0-9a-f]{8}_[A-Za-z0-9_\-]+)", res.get_data(as_text=True)).group(1)


def _h(key: str, **extra) -> dict:
    return {"Authorization": f"Bearer {key}", **extra}


def test_api_auth_scopes_and_reads(app):
    client = app.test_client()
    assert client.get("/api/v1/openapi.json").get_json()["paths"]["/sales"]["post"]
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers=_h("sk_deadbeef_nope")).status_code == 401

    admin = login(app, "시스템관리자")
    key = _issue(admin, "BI", ["customers:read", "sales:read", "products:read"])
    assert key not in admin.get("/admin/api").get_data(as_text=True)          # 목록에는 원문 키가 없다
    stored = db._one("SELECT key_hash FROM api_clients WHERE name='BI'")["key_hash"]
    assert stored == hashlib.sha256(key.encode()).hexdigest()

    me = client.get("/api/v1/me", headers=_h(key)).get_json()
    assert me["acting_user"] == "김영업" and "sales:read" in me["scopes"]
    res = client.get("/api/v1/customers?limit=5", headers=_h(key))
    body = res.get_json()
    assert res.status_code == 200 and body["page"]["limit"] == 5 and res.headers["X-RateLimit-Limit"] == "120"
    kim_id = user("김영업")["id"]
    all_rows = client.get("/api/v1/customers?limit=500", headers=_h(key)).get_json()["data"]
    assert all_rows and all(r["owner_id"] == kim_id for r in all_rows)        # 대리 사용자 범위만
    sales = client.get("/api/v1/sales?limit=3", headers=_h(key)).get_json()["data"]
    assert sales and {"supply_amount", "vat_amount", "total_amount"} <= set(sales[0])
    assert isinstance(sales[0]["customer_id"], int)
    denied = client.get("/api/v1/deals", headers=_h(key))
    assert denied.status_code == 403 and denied.get_json()["error"]["code"] == "insufficient_scope"
    assert client.get("/api/v1/products", headers=_h(key)).get_json()["page"]["total"] >= 5

    # 폐기 즉시 거부
    cid = db._one("SELECT id FROM api_clients WHERE name='BI'")["id"]
    post(admin, f"/admin/api/{cid}/revoke")
    assert client.get("/api/v1/me", headers=_h(key)).status_code == 401

    # 허용 IP 밖에서 호출
    key_ip = _issue(admin, "사내망전용", ["customers:read"], allowed_ips="10.0.0.0/8")
    assert client.get("/api/v1/me", headers=_h(key_ip)).status_code == 401


def test_api_create_sale_idempotent_and_scoped(app):
    admin = login(app, "시스템관리자")
    key = _issue(admin, "그룹웨어", ["sales:write", "sales:read"])
    client = app.test_client()
    kim_cust = db._one("SELECT id FROM customers WHERE owner_id=? ORDER BY id LIMIT 1", [user("김영업")["id"]])["id"]
    body = {"customer_id": kim_cust, "item": "API 매출", "qty": 2, "unit_price": 500000, "tax_type": "과세"}
    first = client.post("/api/v1/sales", json=body, headers=_h(key, **{"Idempotency-Key": "gw-001"}))
    assert first.status_code == 201, first.get_data(as_text=True)
    sale = first.get_json()
    assert (sale["supply_amount"], sale["vat_amount"], sale["total_amount"]) == (1_000_000, 100_000, 1_100_000)
    again = client.post("/api/v1/sales", json=body, headers=_h(key, **{"Idempotency-Key": "gw-001"}))
    assert again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true"
    assert again.get_json()["id"] == sale["id"]
    assert len(db._df("SELECT id FROM sales WHERE item='API 매출'")) == 1
    changed = client.post("/api/v1/sales", json={**body, "qty": 3}, headers=_h(key, **{"Idempotency-Key": "gw-001"}))
    assert changed.status_code == 422
    audit = db._one("SELECT actor FROM audit_log WHERE entity='매출' AND entity_id=? ORDER BY id LIMIT 1", [sale["id"]])
    assert audit["actor"] == "API:그룹웨어"

    # 다른 담당자의 거래처는 등록 불가, 필수값 누락은 422
    other = db._one("SELECT id FROM customers WHERE owner_id=? LIMIT 1", [user("박고객")["id"]])["id"]
    res = client.post("/api/v1/sales", json={**body, "customer_id": other}, headers=_h(key))
    assert res.status_code == 403
    res = client.post("/api/v1/sales", json={"item": "x"}, headers=_h(key))
    assert res.status_code == 422 and "customer_id" in res.get_json()["error"]["message"]


def test_api_rate_limit_shared_counter(app, monkeypatch):
    admin = login(app, "시스템관리자")
    key = _issue(admin, "한도3", ["customers:read"], rate_limit="3")
    fixed = datetime(2026, 9, 27, 10, 15, 20)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(api_keys, "datetime", Frozen)
    client = app.test_client()
    codes = [client.get("/api/v1/me", headers=_h(key)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    res = client.get("/api/v1/me", headers=_h(key))
    assert res.headers["Retry-After"] == "40" and res.get_json()["error"]["code"] == "rate_limited"

"""고급 기능: 2단계 인증 · 사내 SSO(OIDC) · 재고 평가 · 로트/유효기한 · 구매요청/발주 · SAP 마스터 동기화.

SQLite(기본)와 PostgreSQL(MM_DATABASE_URL=...mm_test) 모두에서 돌린다.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_adv_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
from core import (auth, db, master_sync, mfa, org, periods, purchasing, repository as repo, sap,  # noqa: E402
                  seed, services, sso, valuation)
from test_app import DOC, PNG, PW, app, client, csrf, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()
CLERK = {"id": 11, "name": "담당자", "role": "CLERK", "ip": ""}
M1 = {"id": 21, "name": "팀장1", "role": "MANAGER", "ip": ""}
M2 = {"id": 22, "name": "팀장2", "role": "MANAGER", "ip": ""}
ADMIN = {"id": 31, "name": "관리자", "role": "ADMIN", "ip": ""}


@pytest.fixture()
def fresh():
    config.SAP_MODE = "off"
    db.reset_database()
    seed.seed()
    yield
    config.SAP_MODE = "off"
    config.SAP_MASTER_SYNC = False


def mid(code):
    return int(db.scalar("SELECT id FROM materials WHERE code = ?", (code,)))


def wh(code="WH1"):
    return int(db.scalar("SELECT id FROM warehouses WHERE code = ?", (code,)))


def stock(code, wh_code=None, lot=None):
    with db.get_conn() as conn:
        return repo.current_stock(conn, mid(code), None if wh_code is None else wh(wh_code), lot)


def lot_material(code="CHM-1", expiry=True) -> int:
    assert services.create_material({"code": code, "name": "방청제", "unit": "L", "unit_price": 12000,
                                     "lot_managed": 1, "expiry_managed": 1 if expiry else 0}).ok
    return mid(code)


# ── 2단계 인증 ───────────────────────────────────────────────
def test_totp_enable_verify_replay_recovery(fresh):
    u = auth.create_user("otp", "오티피", "CLERK", PW, None, must_change_pw=False).user
    secret = mfa.new_secret()
    assert not mfa.enable(u["id"], secret, "000000", CLERK)[0]
    ok, _, codes = mfa.enable(u["id"], secret, mfa.now_code(secret), CLERK)
    assert ok and len(codes) == 10
    assert not mfa.verify(u["id"], mfa.now_code(secret))[0], "등록에 쓴 코드는 다시 못 쓴다(재전송 방어)"
    assert mfa.verify(u["id"], codes[0])[0], "복구 코드"
    assert not mfa.verify(u["id"], codes[0])[0], "복구 코드는 한 번만"
    stored = db.scalar("SELECT totp_secret FROM users WHERE id = ?", (u["id"],))
    assert secret not in stored, "비밀키는 암호화해 저장"
    for _ in range(config.LOGIN_MAX_FAILS - 1):
        mfa.verify(u["id"], "123456")
    assert "잠겼" in mfa.verify(u["id"], "123456")[1]


def test_login_with_mfa_and_required_role(app):
    admin = auth.get_user(int(db.scalar("SELECT id FROM users WHERE username = 'admin'")))
    secret = mfa.new_secret()
    mfa.enable(admin["id"], secret, mfa.now_code(secret, time.time() - 30), ADMIN)
    c = app.test_client()
    res = post(c, "/login", {"username": "admin", "password": PW})
    assert res.headers["Location"].endswith("/login/mfa")
    assert c.get("/stock/").status_code == 302, "2단계 전에는 로그인 안 됨"
    res = post(c, "/login/mfa", {"code": mfa.now_code(secret)})
    assert res.status_code == 302 and c.get("/stock/").status_code == 200

    config.MFA_REQUIRED_ROLES = {"MANAGER"}
    try:
        m = login(app.test_client(), "manager")
        assert m.get("/stock/").headers["Location"].endswith("/mfa"), "필수 역할은 등록 전까지 등록 화면만"
        assert m.get("/mfa").status_code == 200
    finally:
        config.MFA_REQUIRED_ROLES = set()


# ── 사내 SSO (가짜 IdP) ──────────────────────────────────────
@pytest.fixture()
def idp(monkeypatch):
    from joserfc import jwt
    from joserfc.jwk import KeySet, RSAKey
    key = RSAKey.generate_key(2048, parameters={"kid": "k1", "alg": "RS256", "use": "sig"})
    issuer = "https://idp.test"
    state = {"claims": {}}
    monkeypatch.setattr(config, "SSO_ENABLED", True)
    monkeypatch.setattr(config, "SSO_ISSUER", issuer)
    monkeypatch.setattr(config, "SSO_CLIENT_ID", "mm-app")
    monkeypatch.setattr(config, "SSO_REDIRECT_URI", "http://localhost/sso/callback")
    monkeypatch.setattr(config, "SSO_ROLE_MAP", {"grp-mm-clerk": "CLERK", "grp-mm-manager": "MANAGER"})
    sso._cache.clear()

    def http_get(url):
        if url.endswith("openid-configuration"):
            return {"issuer": issuer, "authorization_endpoint": issuer + "/auth", "token_endpoint": issuer + "/token",
                    "jwks_uri": issuer + "/jwks"}
        return KeySet([key]).as_dict(private=False)

    def http_post(url, data):
        claims = {"iss": issuer, "aud": "mm-app", "exp": int(time.time()) + 300, "iat": int(time.time()),
                  "nonce": state["nonce"], **state["claims"]}
        return {"id_token": jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key)}

    monkeypatch.setattr(sso, "_http_get", http_get)
    monkeypatch.setattr(sso, "_http_post", http_post)
    return state


def sso_login(c, idp_state, claims):
    res = post(c, "/sso/login")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(res.headers["Location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    idp_state["nonce"] = q["nonce"][0]
    idp_state["claims"] = claims
    return c.get(f"/sso/callback?code=abc&state={q['state'][0]}")


def test_sso_login_provisions_and_maps_roles(app, idp):
    c = app.test_client()
    res = sso_login(c, idp, {"sub": "u-1", "preferred_username": "hong@corp.com", "name": "홍길동",
                             "groups": ["grp-mm-clerk", "grp-mm-manager"]})
    assert res.status_code == 302 and c.get("/stock/").status_code == 200
    u = db.query_df("SELECT username, role, auth_source FROM users WHERE sso_subject = 'u-1'").iloc[0]
    assert (u["username"], u["role"], u["auth_source"]) == ("hong", "MANAGER", "sso"), "가장 높은 역할"

    c2 = app.test_client()
    res = sso_login(c2, idp, {"sub": "u-1", "preferred_username": "hong", "name": "홍길동", "groups": ["other"]})
    assert "권한" in c2.get("/login").get_data(as_text=True)
    assert db.scalar("SELECT active FROM users WHERE sso_subject = 'u-1'") == 0, "그룹에서 빠지면 계정 중지"
    assert post(app.test_client(), "/login", {"username": "hong", "password": "x"}).status_code == 200, \
        "SSO 계정은 비밀번호 로그인 불가"


def test_sso_rejects_bad_token_and_collisions(app, idp):
    c = app.test_client()
    res = c.get("/sso/callback?code=abc&state=forged")
    assert res.status_code == 302 and c.get("/stock/").status_code == 302, "state 위조"
    c = app.test_client()
    res = post(c, "/sso/login")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(res.headers["Location"]).query)
    idp["nonce"] = "다른-nonce"
    idp["claims"] = {"sub": "u-2", "preferred_username": "kim", "groups": ["grp-mm-clerk"]}
    c.get(f"/sso/callback?code=abc&state={q['state'][0]}")
    assert c.get("/stock/").status_code == 302, "nonce 불일치 토큰 거부"
    c = app.test_client()
    sso_login(c, idp, {"sub": "u-3", "preferred_username": "clerk", "groups": ["grp-mm-clerk"]})
    assert "겹칩니다" in c.get("/login").get_data(as_text=True), "로컬 계정과 자동으로 합치지 않는다"


def test_sso_only_blocks_password_except_admin(app, monkeypatch):
    monkeypatch.setattr(config, "SSO_ONLY", True)
    res = post(app.test_client(), "/login", {"username": "clerk", "password": PW})
    assert "SSO" in res.get_data(as_text=True) and res.status_code == 200
    assert post(app.test_client(), "/login", {"username": "admin", "password": PW}).status_code == 302, "비상용 관리자"


# ── 재고 평가 ────────────────────────────────────────────────
def test_valuation_moving_average_and_fifo(fresh):
    m = mid("PKG-001")                                        # 샘플: 120@18,000 입고, 85 출고 → 35
    services.register_transaction(m, "IN", 10, TODAY, 20000)
    out = services.register_transaction(m, "OUT", 20, TODAY, 0)
    mavg = valuation.report("2000-01-01", TODAY, "MAVG")[0].set_index("code").loc["PKG-001"]
    fifo = valuation.report("2000-01-01", TODAY, "FIFO")[0].set_index("code").loc["PKG-001"]
    assert round(mavg["close_value"]) == 461111 and round(mavg["issues"]) == 1898889
    assert round(fifo["close_value"]) == 470000 and round(fifo["issues"]) == 1890000

    services.reverse_transaction(out.tx_id, "오입력")
    fifo = valuation.report("2000-01-01", TODAY, "FIFO")[0].set_index("code").loc["PKG-001"]
    assert round(fifo["close_value"]) == 35 * 18000 + 10 * 20000, "출고 취소는 꺼낸 층을 되돌린다"


def test_valuation_cross_plant_transfer_and_close(fresh):
    assert org.create_plant("P2", "2공장", "2000", None).ok
    assert org.create_warehouse(int(db.scalar("SELECT id FROM plants WHERE code='P2'")), "WH2", "2창고", "", None).ok
    services.transfer(mid("PKG-003"), wh("WH1"), wh("WH2"), 70, TODAY, actor=CLERK)
    df = valuation.report("2000-01-01", TODAY, "MAVG")[0]
    p2 = df[(df["code"] == "PKG-003") & (df["plant"] == "P2")].iloc[0]
    assert p2["close_qty"] == 70 and round(p2["close_value"]) == 70 * 1200, "보낸 쪽 원가 그대로"
    total = df[df["code"] == "PKG-003"]["close_value"].sum()
    assert round(total) == 170 * 1200, "플랜트 간 이동은 회사 전체 금액을 바꾸지 않는다"

    first = db.scalar("SELECT MIN(tx_date) FROM transactions")[:7]
    if first < TODAY[:7]:
        before = valuation.report(f"{TODAY[:7]}-01", TODAY, "FIFO")[0]
        assert periods.close_month(first, None).ok
        assert int(db.scalar("SELECT COUNT(*) FROM valuation_snapshots WHERE ym = ?", (first,))) > 0
        after = valuation.report(f"{TODAY[:7]}-01", TODAY, "FIFO")[0]
        assert before["close_value"].round(2).tolist() == after["close_value"].round(2).tolist()


# ── 로트 · 유효기한 ──────────────────────────────────────────
def test_lots_fefo_expiry_and_transfer(fresh):
    m = lot_material()
    soon = (date.today() + timedelta(days=10)).isoformat()
    later = (date.today() + timedelta(days=200)).isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()
    assert not services.register_transaction(m, "IN", 5, TODAY, 12000).ok, "로트 필수"
    assert not services.register_transaction(m, "IN", 5, TODAY, 12000, lot_no="L0").ok, "새 로트는 유효기한 필수"
    assert not services.register_transaction(m, "IN", 5, TODAY, 12000, lot_no="LX", expiry_date=past).ok, "지난 로트 입고 불가"
    assert services.register_transaction(m, "IN", 10, TODAY, 12000, lot_no="LB", expiry_date=later).ok
    assert services.register_transaction(m, "IN", 6, TODAY, 12000, lot_no="LA", expiry_date=soon).ok
    assert not services.register_transaction(m, "IN", 1, TODAY, 12000, lot_no="LA", expiry_date=later).ok, \
        "같은 로트에 다른 유효기한"

    r = services.register_transaction(m, "OUT", 8, TODAY, 0)
    assert r.ok and "LA 6.00" in r.message and "LB 2.00" in r.message, "유효기한 빠른 로트부터"
    assert (stock("CHM-1", lot="LA"), stock("CHM-1", lot="LB")) == (0, 8)

    with db.transaction() as conn:                               # LB 기한이 지났다고 치자
        conn.execute("UPDATE lots SET expiry_date = ? WHERE lot_no = 'LB'", (past,))
    assert not services.register_transaction(m, "OUT", 1, TODAY, 0).ok, "기한 지난 로트는 자동 배정에서 빠짐"
    assert "유효기한" in services.register_transaction(m, "OUT", 1, TODAY, 0, lot_no="LB").message
    adj = services.register_transaction(m, "ADJ", 0, TODAY, 1, lot_no="LB")         # 폐기(1원 단가 → 결재 없음)
    assert adj.ok and stock("CHM-1") == 0

    services.register_transaction(m, "IN", 4, TODAY, 12000, lot_no="LC", expiry_date=later)
    org.create_warehouse(int(db.scalar("SELECT id FROM plants LIMIT 1")), "WH9", "9창고", "", None)
    assert services.transfer(m, wh("WH1"), wh("WH9"), 4, TODAY, actor=CLERK).ok
    assert stock("CHM-1", "WH9", "LC") == 4, "이동해도 로트 유지"
    assert "바꿀 수 없" in services.update_material(m, {"name": "방청제", "lot_managed": 0}).message


# ── 구매요청 → 결재 → 발주 → 입고 ─────────────────────────────
def test_purchase_flow_with_sod_and_three_way_match(fresh):
    m = mid("PKG-002")
    r = purchasing.create_pr(wh(), [(m, 100, 15000)], TODAY, "성수기 대비", CLERK)       # 150만 원 → 2단계
    assert r.ok and "2단계" in r.message
    pr = r.id
    assert not purchasing.decide_pr(pr, True, "", CLERK).ok, "요청자 결재 불가"
    assert purchasing.decide_pr(pr, True, "", M1).ok
    assert not purchasing.decide_pr(pr, True, "", M1).ok, "같은 사람이 두 단계 불가"
    assert not purchasing.create_po(pr, "한국필름", {}, "", "", M1).ok, "승인 전 발주 불가"
    assert purchasing.decide_pr(pr, True, "", M2).ok
    assert not purchasing.create_po(pr, "한국필름", {}, "", "", CLERK).ok, "요청자는 발주 불가"
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr,)))
    po = purchasing.create_po(pr, "한국필름", {item: 17000}, "", "", M1)          # +13% → 발주 결재
    assert po.ok and "결재" in po.message
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    blocked = services.register_transaction(m, "IN", 10, TODAY, 17000, po_no=po_no, po_item="10")
    assert not blocked.ok, "결재 전 입고 불가"
    assert not purchasing.approve_po(po.id, M1).ok, "발주자는 결재 불가"
    assert purchasing.approve_po(po.id, M2).ok

    assert services.register_transaction(m, "IN", 60, TODAY, 17000, po_no=po_no, po_item="10", actor=CLERK).ok
    assert db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po.id,)) == "PARTIAL"
    assert not services.register_transaction(m, "IN", 41, TODAY, 17000, po_no=po_no, po_item="10").ok, "초과 입고"
    last = services.register_transaction(m, "IN", 40, TODAY, 17000, po_no=po_no, po_item="10", actor=CLERK)
    assert db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po.id,)) == "CLOSED"
    services.reverse_transaction(last.tx_id, "수량 착오", actor=M1)
    assert db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po.id,)) == "PARTIAL", "입고 취소 반영"

    from core import documents
    tx60 = int(db.scalar("SELECT MIN(id) FROM transactions WHERE po_no = ?", (po_no,)))
    saved = documents.save(documents.prepare(PNG, "inv.png", {
        "doc_type": "E_TAX_INVOICE", "issue_date": TODAY, "approval_no": "202609274100000099999999",
        "supplier_biz_no": "1248100998", "supply_amount": "1020000", "tax_amount": "102000", "tx_id": str(tx60)}))
    assert saved.ok
    _, items, match = purchasing.po_detail(po.id)
    assert match["received"] == 60 * 17000 and match["invoiced"] == 1020000 and abs(match["gap"]) < 1


def test_purchase_three_steps_needs_admin_and_sap_po_number(fresh):
    pr = purchasing.create_pr(wh(), [(mid("LSH-001"), 1000, 15000)], TODAY, "대량", CLERK).id    # 1500만 → 3단계
    purchasing.decide_pr(pr, True, "", M1)
    purchasing.decide_pr(pr, True, "", M2)
    assert "시스템관리자" in purchasing.decide_pr(pr, True, "", {**M1, "id": 23}).message
    assert purchasing.decide_pr(pr, True, "", ADMIN).ok
    po = purchasing.create_po(pr, "세이프라싱", {}, "", "", M1)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    config.SAP_MODE = "mock"
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = REPLACE(code, '-', '')")
        conn.execute("UPDATE plants SET sap_plant = '1000'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
    services.register_transaction(mid("LSH-001"), "IN", 10, TODAY, 15000, po_no=po_no, po_item="10", actor=CLERK)
    assert sap.process_outbox()["failed"] == 1, "SAP PO 번호가 없으면 전기 못 함"
    purchasing.set_sap_po_no(po.id, "4500009999", M1)
    sap.retry(int(db.scalar("SELECT MAX(id) FROM sap_outbox")), M1)
    assert sap.process_outbox()["sent"] == 1
    assert '"purchaseOrder": "4500009999"' in db.scalar("SELECT payload FROM sap_outbox ORDER BY id DESC LIMIT 1")


# ── SAP 마스터 동기화 ────────────────────────────────────────
def test_master_sync_mock(fresh):
    config.SAP_MODE, config.SAP_MASTER_SYNC = "mock", True
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = 'PKG001' WHERE code = 'PKG-001'")
    counts = master_sync.sync()
    assert counts["created"] == 1 and counts["updated"] == 1 and counts["cost_centers"] == 3
    new = db.query_df("SELECT code, lot_managed, expiry_managed, unit FROM materials WHERE sap_matnr = 'CHM001'").iloc[0]
    assert (new["code"], new["lot_managed"], new["expiry_managed"], new["unit"]) == ("CHM001", 1, 1, "L")
    assert db.scalar("SELECT unit_price FROM materials WHERE code = 'PKG-001'") == 18500
    assert not db.scalar("SELECT COUNT(*) FROM materials WHERE sap_matnr = 'OLD999'"), "삭제표시 자재는 만들지 않음"

    r = services.update_material(mid("PKG-001"), {"name": "바꿈", "unit": "EA", "category": "포장재",
                                                  "unit_price": 18500, "sap_matnr": "PKG001"})
    assert not r.ok and "SAP" in r.message, "SAP가 원본인 항목은 못 고침"
    assert services.update_material(mid("PKG-001"), {"name": "수출용 목재 팔레트 1100x1100", "unit": "EA",
                                                     "category": "포장재", "unit_price": 18500, "sap_matnr": "PKG001",
                                                     "safety_stock": 70}).ok, "이 시스템 항목(안전재고)은 고칠 수 있음"
    with db.transaction() as conn:
        conn.execute("UPDATE plants SET sap_plant = '1000'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
    out = services.register_transaction(mid("PKG-001"), "OUT", 1, TODAY, 0, cost_center="CC-0000")
    assert not out.ok and "원가센터" in out.message
    assert "사용이 막혔" in services.register_transaction(mid("PKG-001"), "OUT", 1, TODAY, 0, cost_center="CC-9000").message
    assert services.register_transaction(mid("PKG-001"), "OUT", 1, TODAY, 0, cost_center="cc-4100").ok


# ── 화면 ─────────────────────────────────────────────────────
def test_new_screens_render(client):
    seed.seed()
    lot_material()
    pr = purchasing.create_pr(wh(), [(mid("PKG-002"), 5, 1000)], TODAY, "테스트", CLERK).id
    for url in ("/purchase/", "/purchase/?tab=new", "/purchase/?tab=po", f"/purchase/pr/{pr}",
                f"/reports/valuation?ym={TODAY[:7]}", f"/reports/valuation?ym={TODAY[:7]}&method=FIFO",
                "/stock/?view=lot", "/mfa", "/sap/", "/admin/users",
                f"/transactions/?type=IN&material={mid('CHM-1')}", f"/transactions/?type=OUT&material={mid('CHM-1')}"):
        res = client.get(url)
        assert res.status_code == 200, url
    html = client.get("/mfa").get_data(as_text=True)
    assert "otpauth://totp/" in html and "data-qr" in html


def test_purchase_ui_flow(app, client):
    seed.seed()
    clerk = login(app.test_client(), "clerk")
    res = post(clerk, "/purchase/pr", {"warehouse_id": wh(), "need_date": TODAY, "reason": "화면 테스트",
                                       "material_id": [mid("PKG-002"), ""], "qty": ["10", ""], "price": ["9500", ""]})
    pr = int(re.search(r"/purchase/pr/(\d+)", res.headers["Location"]).group(1))
    assert "결재 중" in clerk.get(f"/purchase/pr/{pr}").get_data(as_text=True)
    manager = login(app.test_client(), "manager")
    post(manager, f"/purchase/pr/{pr}/decide", {"decision": "approve"})
    res = post(client, f"/purchase/pr/{pr}/po", {"supplier": "한국필름"})         # 관리자(admin)가 발주
    po = int(re.search(r"/purchase/po/(\d+)", res.headers["Location"]).group(1))
    html = client.get(f"/purchase/po/{po}").get_data(as_text=True)
    assert "3자 대조" in html and "발주" in html

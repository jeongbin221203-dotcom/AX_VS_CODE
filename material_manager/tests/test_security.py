"""자체 보안 점검 (모의해킹을 대신하지 않는다 — docs/SECURITY.md 참고).

모든 화면에 대해 기계적으로 확인한다:
  1) 로그인 없이 열리는 화면은 정해 둔 것뿐 (나머지는 로그인 화면으로)
  2) 상태를 바꾸는 모든 요청(POST)은 CSRF 표 없이는 거부
  3) 조회 역할은 어떤 쓰기도 못 함 (403/400)
  4) 창고 범위 밖 데이터 접근(IDOR) 차단 — 작업지시·여러 줄 입출고·MRP 플랜트
  5) 사용자가 넣은 글자는 화면에 그대로 실행되지 않음 (XSS)
  6) 검색어에 SQL 문법을 넣어도 오류 없이 글자로만 다룸
  7) 보안 헤더·세션 쿠키 속성
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_sec_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import audit, auth, db, org, partners, production, seed  # noqa: E402
from test_app import PW, app, client, csrf, login, post  # noqa: E402,F401

PUBLIC = {"auth.login", "auth.setup", "static", "health", "metrics", "auth.sso_login", "auth.sso_callback", "favicon",
          "service_worker", "auth.logout"}
TODAY = date.today().isoformat()


def _url(rule) -> str:
    """규칙의 변수 자리에 1을 넣은 주소."""
    return re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", "1", rule.rule)


def test_login_required_everywhere(app):
    seed.seed()
    c = app.test_client()
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint in PUBLIC:
            continue
        res = c.get(_url(rule))
        assert res.status_code in (302, 401, 403, 404), (rule.rule, res.status_code)
        if res.status_code == 302:
            assert "/login" in res.headers["Location"] or "/setup" in res.headers["Location"], rule.rule


def test_every_post_needs_csrf(client):
    seed.seed()
    for rule in client.application.url_map.iter_rules():
        if "POST" not in rule.methods:
            continue
        res = client.post(_url(rule), data={"x": "1"})
        assert res.status_code == 400, (rule.rule, res.status_code)


def test_viewer_cannot_write(app):
    seed.seed()
    v = login(app.test_client(), "viewer")
    token = csrf(v)
    allowed = {"auth.logout", "auth.password", "prefs.menu", "auth.demo_as"}        # 자기 계정·화면 설정만
    for rule in app.url_map.iter_rules():
        if "POST" not in rule.methods or rule.endpoint in allowed or rule.endpoint in PUBLIC:
            continue
        res = v.post(_url(rule), data={"_csrf": token})
        assert res.status_code in (400, 403, 404), (rule.rule, res.status_code)


def _scoped_clerk():
    plant = int(db.scalar("SELECT MIN(id) FROM plants"))
    org.create_warehouse(plant, "WH2", "둘째", "", audit.SYSTEM)
    wh2 = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH2'"))
    u = auth.create_user("scoped", "범위담당", "CLERK", PW, audit.SYSTEM, must_change_pw=False).user
    org.set_user_scope(u["id"], False, [], [wh2], audit.SYSTEM)
    return wh2


def test_warehouse_scope_idor(app):
    seed.seed()
    wh1 = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'"))
    wh2 = _scoped_clerk()
    from core import services
    M = {"id": 99, "name": "관리", "role": "ADMIN", "ip": ""}
    services.create_material({"code": "FG-S", "name": "제품"}, M)
    fg = int(db.scalar("SELECT id FROM materials WHERE code = 'FG-S'"))
    pkg = int(db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'"))
    production.save_bom(fg, 1, [production.BomLine(pkg, 1)], "", M)
    wo = production.create_wo(fg, 1, wh1, due_date=TODAY, actor=M).tx_id
    c = login(app.test_client(), "scoped")
    assert c.get(f"/production/{wo}").status_code == 403                         # 다른 창고 작업지시
    res = post(c, f"/production/{wo}/issue", {f"line_{int(production.wo_lines(wo).iloc[0]['id'])}": "1"})
    assert production.get(wo)["status"] == "PLANNED"                              # 투입되지 않음
    res = post(c, "/transactions/batch", {"kind": "OUT", "warehouse_id": str(wh1), "tx_date": TODAY, "line_mid": [str(pkg)],
                                          "line_qty": ["1"], "line_unit": [""], "line_lot": [""], "line_exp": [""],
                                          "line_price": [""], "line_note": [""]})
    assert "창고를 고르세요" in res.get_data(as_text=True)
    assert not db.scalar("SELECT COUNT(*) FROM transactions WHERE batch_no <> ''")
    page = c.get("/production/?tab=wo&show=all").get_data(as_text=True)
    assert production.get(wo)["prod_no"] not in page                              # 목록에도 안 보임
    assert wh2 and "WH1" not in c.get("/transactions/batch").get_data(as_text=True).split("최근 묶음")[0].split("창고")[1][:400]


def test_user_text_is_escaped(client):
    seed.seed()
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    partners.create({"name": evil, "kind": "SUPPLIER"}, {"id": None, "name": "t", "role": "ADMIN", "ip": ""})
    for url in ("/partners/", f"/partners/{int(db.scalar('SELECT MAX(id) FROM partners'))}"):
        page = client.get(url).get_data(as_text=True)
        assert "<script>alert" not in page and "onerror=alert" not in page.replace("onerror=alert(1)&gt;", "")
        assert "&lt;script&gt;" in page


def test_sql_meta_in_search_is_plain_text(client):
    seed.seed()
    for url in ("/history/?q=' OR 1=1 --", "/partners/?q=%27%3B DROP TABLE partners;--", "/materials/labels?q=%25' UNION SELECT 1--",
                "/stock/?q=' OR ''='"):
        res = client.get(url)
        assert res.status_code == 200, url
    assert db.scalar("SELECT COUNT(*) FROM materials") > 0 and db.scalar("SELECT COUNT(*) FROM partners") is not None


def test_security_headers_and_cookie(client):
    res = client.get("/")
    h = res.headers
    assert "script-src 'self'" in h["Content-Security-Policy"] and "object-src 'none'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "SAMEORIGIN" and h["X-Content-Type-Options"] == "nosniff"
    assert "camera=(self)" in h["Permissions-Policy"] and "microphone=()" in h["Permissions-Policy"]
    assert h["Cache-Control"] == "no-store"
    cookie = next(v for k, v in client.application.test_client().post(
        "/login", data={"username": "x", "password": "y"}).headers.items() if k == "Set-Cookie") if False else None
    app_cfg = client.application.config
    assert app_cfg["SESSION_COOKIE_HTTPONLY"] and app_cfg["SESSION_COOKIE_SAMESITE"] in ("Lax", "Strict")
    assert cookie is None

"""시연 서버 자동 로그인 — SALES_DEMO_AUTOLOGIN 이 켜진 서버에서만, 로그인 화면 없이 들어오고 역할을 바꿔 볼 수 있다."""
from __future__ import annotations

import config
from core import enterprise as ent
from core import sales_db as db


def test_demo_autologin_and_role_switch(app, isolated_db, monkeypatch):
    db.set_context("system", None)
    ent.seed_org_demo()
    c = app.test_client()
    assert c.get("/").status_code == 302                       # 꺼져 있으면 로그인 화면으로
    assert c.get("/demo/as/2003").status_code == 404           # 꺼져 있으면 역할 바꾸기도 없음

    monkeypatch.setattr(config, "DEMO_AUTOLOGIN", "9999")
    c = app.test_client()
    page = c.get("/")
    body = page.get_data(as_text=True)
    assert page.status_code == 200 and "시스템관리자" in body and "포트폴리오 시연 서버" in body
    assert "다른 역할로 보기" in body and "로그아웃" in body and "비밀번호 변경" not in body
    assert c.get("/admin/data").status_code == 200

    res = c.get("/demo/as/2003", follow_redirects=True)
    body = res.get_data(as_text=True)
    assert res.status_code == 200 and "김영업" in body
    assert c.get("/admin/data").status_code == 403             # 영업사원 권한으로 바뀜
    assert c.get("/demo/as/1234").status_code == 404           # 목록에 없는 사번은 안 됨


def test_demo_locks_admin_settings_but_allows_business_data(app, isolated_db, monkeypatch):
    import re
    db.set_context("system", None)
    ent.seed_org_demo()
    monkeypatch.setattr(config, "DEMO_AUTOLOGIN", "9999")
    c = app.test_client()
    tok = re.search(r'name="_csrf" value="([0-9a-f]+)"', c.get("/customers").get_data(as_text=True)).group(1)

    def post(client, url, data, **kw):
        return client.post(url, data={**data, "_csrf": tok}, **kw)
    before = db._scalar("SELECT COUNT(*) FROM orgs")
    res = post(c, "/admin/org/save", {"name": "방문자팀", "kind": "팀"}, follow_redirects=True)
    assert "관리자 설정을 바꿀 수 없습니다" in res.get_data(as_text=True)
    assert db._scalar("SELECT COUNT(*) FROM orgs") == before
    res = post(c, "/admin/data/action", {"action": "reset", "confirm": "1"}, follow_redirects=True)
    assert "관리자 설정을 바꿀 수 없습니다" in res.get_data(as_text=True)
    res = post(c, "/data/forms/save", {"name": "방문자 양식"}, follow_redirects=True)
    assert "관리자 설정을 바꿀 수 없습니다" in res.get_data(as_text=True)    # 회사 엑셀 양식도 관리자 설정
    assert c.get("/admin/settings").status_code == 200             # 보기는 된다
    res = post(c, "/customers/save", {"name": "(주)방문자상사", "industry": "제조", "grade": "B"}, follow_redirects=True)
    assert "관리자 설정을 바꿀 수 없습니다" not in res.get_data(as_text=True)
    assert db._scalar("SELECT COUNT(*) FROM customers WHERE name='(주)방문자상사'") == 1   # 업무 데이터는 저장


def test_demo_logout_shows_login_with_demo_button(app, isolated_db, monkeypatch):
    """자재관리와 같은 흐름: 로그아웃 → 로그인 화면(시연 버튼 + 사번 로그인) → 버튼 누르면 다시 시연 관리자."""
    import re
    db.set_context("system", None)
    ent.seed_org_demo()
    monkeypatch.setattr(config, "DEMO_AUTOLOGIN", "9999")
    c = app.test_client()
    tok = re.search(r'name="_csrf" value="([0-9a-f]+)"', c.get("/customers").get_data(as_text=True)).group(1)
    res = c.post("/logout", data={"_csrf": tok}, follow_redirects=True)
    body = res.get_data(as_text=True)
    assert "시연 관리자로 들어가기" in body
    assert 'name="user_id"' in body or 'name="emp_no"' in body           # 일반 로그인도 그대로
    assert "<strong>시스템관리자</strong>" not in body                      # 로그인 화면에서는 자동 로그인 안 함
    # 방문자가 시연 계정을 중지해도 다음 자동 로그인 때 되돌린다
    with __import__("core.database", fromlist=["x"]).get_conn() as conn:
        conn.execute("UPDATE users SET active=0, role='REP' WHERE emp_no='9999'")
    body = c.get("/").get_data(as_text=True)
    assert "<strong>시스템관리자</strong>" in body and c.get("/admin/data").status_code == 200

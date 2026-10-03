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
    assert "다른 역할로 보기" in body and "로그아웃" not in body
    assert c.get("/admin/data").status_code == 200

    res = c.get("/demo/as/2003", follow_redirects=True)
    body = res.get_data(as_text=True)
    assert res.status_code == 200 and "김영업" in body
    assert c.get("/admin/data").status_code == 403             # 영업사원 권한으로 바뀜
    assert c.get("/demo/as/1234").status_code == 404           # 목록에 없는 사번은 안 됨

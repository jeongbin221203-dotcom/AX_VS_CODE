"""사이드바 메뉴: 기본 순서(자주 쓰는 순, 대시보드 고정) · 사용자별 순서·즐겨찾기 저장 (자재관리와 같은 방식)."""
from __future__ import annotations

import re

from conftest import csrf, login, user

import config
from core import enterprise as ent
from core import database
from core import sales_db as db


def menu_keys(html: str) -> dict:
    nav = re.search(r'<nav class="menu".*?</nav>', html, re.S).group(0)
    fav = re.search(r'data-group="fav">(.*?)</div>', nav, re.S).group(1)
    others = re.search(r'data-group="others">(.*?)</div>', nav, re.S).group(1)
    return {"first": re.search(r'<a href="([^"]+)"', nav).group(1),
            "fav": re.findall(r'data-key="(\w+)"', fav), "others": re.findall(r'data-key="(\w+)"', others)}


def prefs_count(name: str) -> int:
    return int(database.scalar("SELECT COUNT(*) FROM user_prefs WHERE user_id = ?", (user(name)["id"],)))


def test_default_order_most_used_with_dashboard_first(app):
    keys = menu_keys(login(app, "김영업").get("/customers").get_data(as_text=True))
    assert keys["first"] == "/" and keys["fav"] == []
    assert keys["others"][:5] == ["deals", "activities", "customers", "quotes", "orders"]
    assert "dashboard" not in keys["others"] and "audit" not in keys["others"]      # 관리자 메뉴는 권한대로


def test_user_order_and_favorites_saved_per_user(app):
    c = login(app, "시스템관리자")
    token = csrf(c)
    res = c.post("/prefs/menu", data={"_csrf": token, "order": "audit,sales,bogus,sales,dashboard",
                                      "fav": "audit,targets,bogus"})
    assert res.status_code == 200 and res.get_json()["fav"] == ["audit", "targets"]
    keys = menu_keys(c.get("/").get_data(as_text=True))
    assert keys["fav"] == ["audit", "targets"]                          # 즐겨찾기는 위 묶음, 정한 순서대로
    assert keys["others"][0] == "sales" and "audit" not in keys["others"]
    assert keys["first"] == "/"                                         # 대시보드는 그대로 맨 위
    assert menu_keys(login(app, "한팀장").get("/").get_data(as_text=True))["fav"] == []   # 다른 사용자는 기본
    c.post("/prefs/menu", data={"_csrf": token, "reset": "1"})
    assert menu_keys(c.get("/").get_data(as_text=True))["others"][0] == "deals"
    assert prefs_count("시스템관리자") == 0


def test_rep_cannot_favorite_admin_menu(app):
    c = login(app, "김영업")
    res = c.post("/prefs/menu", data={"_csrf": csrf(c), "order": "", "fav": "audit,sales"})
    assert res.get_json()["fav"] == ["sales"]
    c.post("/prefs/menu", data={"_csrf": csrf(c), "reset": "1"})


def test_prefs_requires_login_and_csrf(app):
    assert app.test_client().post("/prefs/menu", data={"fav": "sales"}).status_code in (302, 400, 403)
    c = login(app, "김영업")
    assert c.post("/prefs/menu", data={"fav": "sales"}).status_code in (400, 403)
    assert prefs_count("김영업") == 0


def test_demo_mode_keeps_menu_in_session(app, isolated_db, monkeypatch):
    db.set_context("system", None)
    ent.seed_org_demo()
    monkeypatch.setattr(config, "DEMO_AUTOLOGIN", "9999")
    v1, v2 = app.test_client(), app.test_client()
    v1.get("/"), v2.get("/")
    res = v1.post("/prefs/menu", data={"_csrf": csrf(v1), "order": "", "fav": "targets"})
    assert res.status_code == 200
    assert menu_keys(v1.get("/").get_data(as_text=True))["fav"] == ["targets"]
    assert menu_keys(v2.get("/").get_data(as_text=True))["fav"] == []   # 같은 시연 계정이어도 방문자마다 따로
    assert database.scalar("SELECT COUNT(*) FROM user_prefs") == 0

"""사이드바 메뉴: 기본 순서(자주 쓰는 순, 대시보드 고정) · 사용자별 순서·즐겨찾기 저장."""
from __future__ import annotations

import re

from test_app import app, client, csrf, login  # noqa: F401,I001  (먼저 — 임시 DB 경로를 정한다)

import config  # noqa: E402
from core import db  # noqa: E402


def menu_keys(html: str) -> dict:
    nav = re.search(r'<nav class="menu".*?</nav>', html, re.S).group(0)
    fav = re.search(r'data-group="fav">(.*?)</div>', nav, re.S).group(1)
    others = re.search(r'data-group="others">(.*?)</div>', nav, re.S).group(1)
    return {"first": re.search(r'<a href="([^"]+)"', nav).group(1),
            "fav": re.findall(r'data-key="(\w+)"', fav), "others": re.findall(r'data-key="(\w+)"', others)}


def test_default_order_most_used_with_dashboard_first(client):          # noqa: F811
    keys = menu_keys(client.get("/stock/").get_data(as_text=True))
    assert keys["first"] == "/" and keys["fav"] == []
    assert keys["others"][:6] == ["transactions", "batch", "stock", "history", "purchase", "approvals"]
    assert "dashboard" not in keys["others"]


def test_user_order_and_favorites_saved_per_user(app):                  # noqa: F811
    c = login(app.test_client(), "admin")
    token = csrf(c)
    res = c.post("/prefs/menu", data={"_csrf": token, "order": "audit,stock,bogus,stock,dashboard",
                                      "fav": "audit,ledger,bogus"})
    assert res.status_code == 200 and res.get_json()["fav"] == ["audit", "ledger"]
    keys = menu_keys(c.get("/").get_data(as_text=True))
    assert keys["fav"] == ["audit", "ledger"]                          # 즐겨찾기는 위 묶음, 정한 순서대로
    assert keys["others"][0] == "stock" and "audit" not in keys["others"]
    assert keys["first"] == "/"                                         # 대시보드는 그대로 맨 위
    other = login(app.test_client(), "manager")                         # 다른 사용자는 기본 순서
    assert menu_keys(other.get("/").get_data(as_text=True))["fav"] == []
    c.post("/prefs/menu", data={"_csrf": token, "reset": "1"})
    assert menu_keys(c.get("/").get_data(as_text=True))["others"][0] == "transactions"
    assert db.scalar("SELECT COUNT(*) FROM user_prefs") == 0


def test_viewer_cannot_favorite_hidden_menu(app):                       # noqa: F811
    c = login(app.test_client(), "viewer")
    res = c.post("/prefs/menu", data={"_csrf": csrf(c), "order": "", "fav": "audit,stock"})
    assert res.get_json()["fav"] == ["stock"]


def test_demo_mode_keeps_menu_in_session(monkeypatch):
    from app import create_app
    monkeypatch.setattr(config, "DEMO", True)
    db.reset_database()
    a = create_app({"TESTING": True, "DEMO": True})
    v1, v2 = a.test_client(), a.test_client()
    v1.get("/"), v2.get("/")
    v1.post("/prefs/menu", data={"_csrf": csrf(v1), "order": "", "fav": "ledger"})
    assert menu_keys(v1.get("/").get_data(as_text=True))["fav"] == ["ledger"]
    assert menu_keys(v2.get("/").get_data(as_text=True))["fav"] == []   # 같은 시연 계정이어도 방문자마다 따로
    assert db.scalar("SELECT COUNT(*) FROM user_prefs") == 0

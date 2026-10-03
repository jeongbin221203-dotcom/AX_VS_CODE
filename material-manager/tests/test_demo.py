"""포트폴리오 시연 모드(MM_DEMO=1): 자동 로그인 · 샘플 데이터 · 시연 계정 복구."""
from __future__ import annotations

from test_app import app, client, csrf, login  # noqa: F401,I001  (먼저 — 임시 DB 경로를 정한다)

import config  # noqa: E402
from core import auth, db, demo, repository as repo  # noqa: E402


def _demo_app(monkeypatch):
    from app import create_app
    monkeypatch.setattr(config, "DEMO", True)
    db.reset_database()
    application = create_app({"TESTING": True, "DEMO": True})
    return application


def test_demo_auto_login_and_seed(monkeypatch):
    application = _demo_app(monkeypatch)
    assert repo.count_materials() > 0                    # 빈 DB에 샘플이 들어감
    c = application.test_client()
    res = c.get("/")
    assert res.status_code == 200                        # 로그인·최초 설정 화면으로 가지 않는다
    html = res.get_data(as_text=True)
    assert "시연 관리자" in html and "포트폴리오 시연" in html
    assert c.get("/admin/users").status_code == 200      # 시스템관리자 화면도 열린다


def test_demo_sample_is_large_and_consistent(monkeypatch):
    application = _demo_app(monkeypatch)
    assert repo.count_materials() >= 70
    assert db.scalar("SELECT COUNT(*) FROM transactions") > 5000
    assert db.scalar("SELECT COUNT(*) FROM warehouses") >= 9
    # 재고는 (자재, 창고, 로트) 어디서도 음수가 아니다
    assert db.scalar("""SELECT COUNT(*) FROM (SELECT SUM(CASE WHEN tx_type = 'OUT' THEN -qty ELSE qty END) s
                        FROM transactions GROUP BY material_id, warehouse_id, lot_no) x WHERE s < -0.001""") == 0
    statuses = set(db.query_df("SELECT DISTINCT status FROM purchase_requests")["status"])
    assert {"PENDING", "APPROVED", "REJECTED", "ORDERED", "CANCELLED"} <= statuses
    assert db.scalar("SELECT COUNT(*) FROM period_closes") >= 6
    c = application.test_client()
    for url in ("/", "/stock/", "/history/", "/history/?page=20", "/purchase/", "/purchase/?tab=po", "/approvals/",
                "/periods/", "/reports/ledger", "/reports/valuation", "/reports/reconcile", "/admin/users", "/admin/audit", "/materials/", "/statements/"):
        assert c.get(url).status_code in (200, 302), url


def test_demo_logout_shows_button_and_user_is_repaired(monkeypatch):
    application = _demo_app(monkeypatch)
    c = application.test_client()
    c.get("/")
    token = csrf(c)
    res = c.post("/logout", data={"_csrf": token})
    assert res.status_code == 302 and "/login" in res.headers["Location"]
    assert "시연 관리자로 들어가기" in c.get("/login").get_data(as_text=True)
    # 방문자가 시연 계정을 중지·강등해도 다음 자동 로그인 때 되돌린다
    uid = demo.ensure_user()["id"]
    db.execute("UPDATE users SET role = 'VIEWER', active = 0 WHERE id = ?", (uid,))
    assert application.test_client().get("/admin/users").status_code == 200
    assert auth.get_user(uid)["role"] == "ADMIN"


def test_demo_off_by_default(client):                   # noqa: F811
    assert config.DEMO is False
    assert client.application.test_client().get("/").status_code == 302

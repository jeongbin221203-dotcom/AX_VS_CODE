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


def test_demo_sample_is_clean_small_qty_big_amount(monkeypatch):
    application = _demo_app(monkeypatch)
    assert 15 <= repo.count_materials() <= 40
    assert db.scalar("SELECT COUNT(*) FROM transactions") < 600
    # 재고는 (자재, 창고, 로트) 어디서도 음수가 아니다
    assert db.scalar("""SELECT COUNT(*) FROM (SELECT SUM(CASE WHEN tx_type = 'OUT' THEN -qty ELSE qty END) s
                        FROM transactions GROUP BY material_id, warehouse_id, lot_no) x WHERE s < -0.001""") == 0
    # 수량은 적고(자재·창고별 재고 100 이하) 금액은 크다(재고 평가 수천만 원 이상)
    assert db.scalar("""SELECT MAX(s) FROM (SELECT SUM(CASE WHEN tx_type = 'OUT' THEN -qty ELSE qty END) s
                        FROM transactions GROUP BY material_id, warehouse_id) x""") <= 100
    assert db.scalar("""SELECT SUM(m.unit_price * s.q) FROM materials m JOIN (
                        SELECT material_id, SUM(CASE WHEN tx_type = 'OUT' THEN -qty ELSE qty END) q FROM transactions
                        GROUP BY material_id) s ON s.material_id = m.id""") > 50_000_000
    statuses = set(db.query_df("SELECT DISTINCT status FROM purchase_requests")["status"])
    assert {"PENDING", "APPROVED", "REJECTED", "ORDERED", "CANCELLED"} <= statuses
    assert db.scalar("SELECT COUNT(*) FROM period_closes") >= 3
    assert db.scalar("SELECT COUNT(*) FROM boms") >= 2
    c = application.test_client()
    for url in ("/", "/stock/", "/history/", "/purchase/", "/purchase/?tab=po", "/approvals/", "/periods/", "/reports/ledger",
                "/reports/valuation", "/reports/reconcile", "/admin/users", "/admin/audit", "/materials/", "/statements/",
                "/production/", "/mrp/", "/data/"):
        assert c.get(url).status_code in (200, 302), url


def test_demo_packs_add_complex_data_once(monkeypatch):
    application = _demo_app(monkeypatch)
    from core import seed_packs
    c = application.test_client()
    c.get("/")
    token = csrf(c)
    before = db.scalar("SELECT COUNT(*) FROM transactions")
    for key in ("lots", "multiplant", "procurement"):
        assert not seed_packs.done(key)
        res = c.post(f"/data/pack/{key}", data={"_csrf": token}, follow_redirects=True)
        assert res.status_code == 200 and "추가했습니다" in res.get_data(as_text=True), key
        assert seed_packs.done(key)
        again = c.post(f"/data/pack/{key}", data={"_csrf": token}, follow_redirects=True)
        assert "이미 추가" in again.get_data(as_text=True)
    assert db.scalar("SELECT COUNT(*) FROM transactions") > before + 150
    assert db.scalar("""SELECT COUNT(*) FROM (SELECT SUM(CASE WHEN tx_type = 'OUT' THEN -qty ELSE qty END) s
                        FROM transactions GROUP BY material_id, warehouse_id, lot_no) x WHERE s < -0.001""") == 0
    assert db.scalar("SELECT COUNT(*) FROM purchase_orders WHERE status = 'PARTIAL'") >= 3
    assert db.scalar("SELECT COUNT(*) FROM lots") >= 40
    assert c.get("/data/").status_code == 200 and c.get("/").status_code == 200
    assert c.post("/data/pack/nothing", data={"_csrf": token}).status_code == 302


def test_large_pack_after_clean_sample(monkeypatch):
    application = _demo_app(monkeypatch)
    from core import seed_packs
    c = application.test_client()
    c.get("/")
    token = csrf(c)
    assert "추가 데이터 넣기" in c.get("/").get_data(as_text=True)          # 시연 사이드바 링크
    res = c.post("/data/pack/large", data={"_csrf": token}, follow_redirects=True)
    assert "백그라운드" in res.get_data(as_text=True)                        # 시연 서버는 오래 걸리는 팩을 백그라운드로
    import time
    for _ in range(300):
        if seed_packs.done("large") and not seed_packs.running():
            break
        time.sleep(0.5)
    assert seed_packs.done("large")
    assert "이미 추가" in c.post("/data/pack/large", data={"_csrf": token}, follow_redirects=True).get_data(as_text=True)
    assert repo.count_materials() >= 60 and db.scalar("SELECT COUNT(*) FROM transactions") > 5000
    assert db.scalar("""SELECT COUNT(*) FROM (SELECT SUM(CASE WHEN tx_type = 'OUT' THEN -qty ELSE qty END) s
                        FROM transactions GROUP BY material_id, warehouse_id, lot_no) x WHERE s < -0.001""") == 0
    assert db.scalar("SELECT COUNT(*) FROM period_closes WHERE action = 'CLOSE'") >= 6
    assert seed_packs.done("large")


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


def test_forward_to_live_server_else_serve_here(monkeypatch):
    from core import forward
    monkeypatch.setattr(config, "FORWARD_URL", "https://pc.example")
    application = _demo_app(monkeypatch)
    monkeypatch.setattr(config, "FORWARD_URL", "https://pc.example")
    c = application.test_client()
    monkeypatch.setattr(forward, "target_up", lambda: True)
    res = c.get("/stock/?q=1")
    assert res.status_code == 302 and res.headers["Location"] == "https://pc.example/stock/?q=1"
    assert c.get("/health").status_code == 200                   # 상태 확인은 전달하지 않는다
    monkeypatch.setattr(forward, "target_up", lambda: False)
    html = c.get("/").get_data(as_text=True)                       # 꺼져 있으면 여기서 시연
    assert "임시 서버" in html


def test_demo_reset_button_restores_sample_and_logs(monkeypatch, capsys):
    from core import demo
    application = _demo_app(monkeypatch)
    monkeypatch.setattr(config, "AUDIT_STDOUT", True)
    monkeypatch.setattr(demo, "MIN_GAP", 0)
    c = application.test_client()
    c.get("/")
    before = db.scalar("SELECT COUNT(*) FROM transactions")
    mid = db.scalar("SELECT id FROM materials WHERE code = 'PKG-002'")
    wh = db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'")
    token = csrf(c)
    res = c.post("/transactions/", data={"_csrf": token, "material_id": mid, "warehouse_id": wh, "tx_type": "OUT",
                                          "qty": "1", "tx_date": TODAY_STR(), "partner": "방문자"})
    assert res.status_code in (200, 302)
    assert db.scalar("SELECT COUNT(*) FROM transactions") == before + 1
    out = capsys.readouterr().out
    assert "AUDIT " in out and "TX_CREATE" in out                  # 방문자 변경이 서버 로그에 남는다
    res = c.post("/demo/reset", data={"_csrf": token})
    assert res.status_code == 302
    assert db.scalar("SELECT COUNT(*) FROM transactions") == before   # 처음 샘플로 돌아감
    assert "DEMO_RESET" in capsys.readouterr().out
    res = c.get("/")                                                # 초기화 뒤에도 바로 자동 로그인 + 안내 유지
    assert res.status_code == 200 and "처음 샘플로 되돌렸습니다" in res.get_data(as_text=True)


def test_demo_daily_reset_once_per_day(monkeypatch):
    from core import demo
    application = _demo_app(monkeypatch)
    monkeypatch.setattr(config, "DEMO_RESET_HOUR", 0)
    monkeypatch.setattr(demo, "MIN_GAP", 0)
    calls = []
    monkeypatch.setattr(demo, "reset", lambda actor, reason: calls.append(reason))
    c = application.test_client()
    c.get("/")
    assert calls == []                                              # 오늘 이미 만든 샘플
    db.execute("UPDATE app_settings SET value = '2000-01-01' WHERE key = ?", (demo.RESET_KEY,))
    c.get("/")
    assert len(calls) == 1 and "자동 초기화" in calls[0]


def TODAY_STR():
    from datetime import date
    return date.today().isoformat()


def test_month_close_ignores_float_noise(monkeypatch):
    """소수 수량 입출고(0.1 + 0.2 - 0.3 같은)의 부동소수 오차를 음수 재고로 보고하지 않는다."""
    _demo_app(monkeypatch)
    rows = db.query_df("SELECT entity_id, detail FROM audit_log WHERE action = 'PERIOD_CLOSE'")
    assert not rows.empty and not rows["detail"].str.contains("negative_stock\": \[", regex=False).any()
    assert db.scalar("SELECT COUNT(*) FROM inventory_snapshots WHERE qty < 0") == 0


def test_client_ip_header(monkeypatch):
    from app import _ClientIpHeader
    seen = {}
    mw = _ClientIpHeader(lambda env, sr: seen.update(ip=env["REMOTE_ADDR"]), "True-Client-IP")
    mw({"REMOTE_ADDR": "127.0.0.1", "HTTP_TRUE_CLIENT_IP": "203.0.113.7"}, None)
    assert seen["ip"] == "203.0.113.7"
    mw({"REMOTE_ADDR": "127.0.0.1", "HTTP_TRUE_CLIENT_IP": "not-an-ip"}, None)
    assert seen["ip"] == "127.0.0.1"


def test_demo_locks_admin_settings_keeps_business_and_reset(monkeypatch):
    """시연: 관리자 설정 저장은 막고(화면은 열림), 업무 저장과 '샘플로 되돌리기'는 막지 않는다."""
    application = _demo_app(monkeypatch)
    c = application.test_client()
    c.get("/")
    token = csrf(c)
    users_before = db.scalar("SELECT COUNT(*) FROM users")
    res = c.post("/admin/users", data={"_csrf": token, "username": "visitor1", "name": "방문자", "role": "ADMIN",
                                       "password": "Visitor123!"}, follow_redirects=True)
    assert "관리자 설정을 바꿀 수 없습니다" in res.get_data(as_text=True)
    assert db.scalar("SELECT COUNT(*) FROM users") == users_before
    res = c.post("/data/seed", data={"_csrf": token}, follow_redirects=True)
    assert "관리자 설정을 바꿀 수 없습니다" in res.get_data(as_text=True)
    assert c.get("/admin/users").status_code == 200 and c.get("/admin/org").status_code == 200   # 보기는 된다
    from core import demo as demo_mod
    assert not demo_mod.locked(None, "demo_reset") and not demo_mod.locked("transactions", "transactions.create")


def test_demo_role_switch_uses_sample_users(monkeypatch):
    application = _demo_app(monkeypatch)
    c = application.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "다른 역할로 보기" in html
    res = c.get("/demo/as/CLERK", follow_redirects=True)
    assert "김민준" in res.get_data(as_text=True)
    assert c.get("/admin/users").status_code == 403                 # 담당자는 사용자 화면 불가
    res = c.get("/demo/as/VIEWER", follow_redirects=True)
    assert "강도윤" in res.get_data(as_text=True)
    assert c.get("/demo/as/ADMIN", follow_redirects=True).status_code == 200
    assert c.get("/admin/users").status_code == 200
    assert c.get("/demo/as/NOPE").status_code == 404
    # 샘플 사용자가 중지돼 있어도 바꿔 보기 때 되돌린다
    db.execute("UPDATE users SET active = 0 WHERE username = 'park.jh'")
    c.get("/demo/as/MANAGER")
    assert auth.get_user(db.scalar("SELECT id FROM users WHERE username = 'park.jh'"))["active"]


def test_demo_as_off_by_default(client):                            # noqa: F811
    assert client.get("/demo/as/ADMIN").status_code in (302, 404)


def test_login_page_redirects_when_signed_in(client):     # noqa: F811
    res = client.get("/login")
    assert res.status_code == 302 and res.headers["Location"].endswith("/")
    res = client.get("/login?next=/stock/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/stock/")


def test_demo_guide_links_follow_role(monkeypatch):
    """시연 안내의 바로 가기는 지금 역할이 열 수 있는 화면만 링크 (조회 역할이면 글자만)."""
    application = _demo_app(monkeypatch)
    c = application.test_client()
    admin_page = c.get("/").get_data(as_text=True)
    assert 'href="/transactions/batch"' in admin_page
    c.get("/demo/as/VIEWER")
    page = c.get("/").get_data(as_text=True)
    assert "조회만 됩니다" in page and 'href="/transactions/batch"' not in page and 'href="/admin/jobs"' not in page


def test_demo_role_users_have_data_scope(monkeypatch):
    """'다른 역할로 보기'의 관리자·담당자·조회가 빈 화면이 아니도록 샘플 계정에 데이터 범위를 준다 (범위 없는 계정은 아무것도 못 봄)."""
    from core import org
    application = _demo_app(monkeypatch)
    c = application.test_client()
    uid = {r: db.scalar("SELECT id FROM users WHERE username = ?", (n,)) for r, n in
           (("MANAGER", "park.jh"), ("VIEWER", "kang.dy"), ("CLERK", "kim.mj"))}
    assert org.allowed_warehouses(auth.get_user(uid["MANAGER"])) is None
    assert org.allowed_warehouses(auth.get_user(uid["VIEWER"])) is None
    clerk = org.allowed_warehouses(auth.get_user(uid["CLERK"]))
    busan = {int(r.id) for r in db.query_df("SELECT w.id FROM warehouses w JOIN plants p ON p.id = w.plant_id WHERE p.code = 'P1'").itertuples()}
    assert clerk == busan and clerk                         # 담당자 = 부산 플랜트의 창고들
    for role in ("MANAGER", "CLERK", "VIEWER"):
        html = c.get(f"/demo/as/{role}", follow_redirects=True).get_data(as_text=True)
        assert "데이터 범위 없음" not in html


def test_demo_guide_matches_sample_data(monkeypatch):
    """대시보드의 시연 안내가 말하는 값이 실제 샘플과 같아야 한다."""
    application = _demo_app(monkeypatch)
    c = application.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "볼베어링 1 BOX = 20 EA" in html and "브래킷" not in html
    row = db.query_df("SELECT m.name, u.factor FROM material_units u JOIN materials m ON m.id = u.material_id "
                      "WHERE u.barcode = '8809876500993'")
    assert row.iloc[0]["name"] == "깊은홈 볼베어링" and float(row.iloc[0]["factor"]) == 20
    qty = sorted(float(v) for v in db.query_df("SELECT qty FROM mrp_demands").iloc[:, 0])
    assert qty == [6.0, 12.0]

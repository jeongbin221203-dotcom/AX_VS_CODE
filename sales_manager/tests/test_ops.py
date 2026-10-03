"""운영: 준비 상태 · 지표 · 요청 ID · 읽기 전용 점검 모드 · 워커 신호."""
from __future__ import annotations

from conftest import login, post

from core import jobs
from core import sales_db as db


def test_readyz_and_request_id(app):
    client = app.test_client()
    res = client.get("/readyz")
    body = res.get_json()
    assert res.status_code == 200 and body["checks"] == {"db": "ok", "schema": "ok", "storage": "ok",
                                                          "read_only": "off"}
    assert len(res.headers["X-Request-ID"]) == 32
    res = client.get("/healthz", headers={"X-Request-ID": "lb-trace-12345678"})
    assert res.headers["X-Request-ID"] == "lb-trace-12345678"
    res = client.get("/healthz", headers={"X-Request-ID": "bad id<script>"})       # 형식이 틀리면 새로 만든다
    assert res.headers["X-Request-ID"] != "bad id<script>"


def test_metrics_restricted_and_business_gauges(app, monkeypatch):
    client = app.test_client()
    client.get("/healthz")
    local = client.get("/metrics")                                   # 테스트 클라이언트는 127.0.0.1
    text = local.get_data(as_text=True)
    assert local.status_code == 200
    for name in ("sales_http_requests_total", "sales_db_up 1.0", "sales_schema_current 1.0", "sales_jobs",
                 "sales_worker_heartbeat_age_seconds", "sales_erp_documents", "sales_approval_steps_overdue",
                 "sales_login_failures_15m", "sales_read_only 0.0"):
        assert name in text, name
    remote = client.get("/metrics", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert remote.status_code == 403
    monkeypatch.setenv("SALES_METRICS_TOKEN", "prom-token")
    ok = client.get("/metrics", environ_base={"REMOTE_ADDR": "203.0.113.9"},
                    headers={"Authorization": "Bearer prom-token"})
    assert ok.status_code == 200


def test_read_only_mode_blocks_writes(app, monkeypatch):
    rep = login(app, "김영업")
    monkeypatch.setenv("SALES_READ_ONLY", "1")
    page = rep.get("/customers")
    assert page.status_code == 200 and "점검 중" in page.get_data(as_text=True)
    res = post(rep, "/customers/save", {"name": "점검중거래처"})
    assert res.status_code == 503 and db._one("SELECT id FROM customers WHERE name='점검중거래처'") is None
    assert app.test_client().get("/readyz").get_json()["checks"]["read_only"] == "on"
    api = app.test_client().post("/api/v1/sales", json={}, headers={"Authorization": "Bearer sk_x_y"})
    assert api.status_code == 503 and api.get_json()["error"]["code"] == "read_only"
    assert post(app.test_client(), "/logout").status_code in (302, 303)     # 로그아웃은 허용
    monkeypatch.setenv("SALES_READ_ONLY", "0")
    assert post(rep, "/customers/save", {"name": "점검후거래처", "grade": "B", "industry": "제조"}).status_code == 302


def test_worker_heartbeat_shown_to_admin(app):
    jobs.heartbeat("test-host:1")
    admin = login(app, "시스템관리자")
    page = admin.get("/admin/jobs").get_data(as_text=True)
    assert "test-host:1" in page and "정상" in page

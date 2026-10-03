"""영업관리와 맞춘 기능: 알림함 · 내 결재 대기(대결·독촉) · 회사 설정 · 점검 모드 · readyz · 데이터 점검 · 거래처 병합 · API 키 ·
화면(행 누르기·결재 단계 미리 보기·보드)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r3_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
from core import (api_keys, audit, auth, company, db, delegation, maintenance, notify, partners, purchasing,  # noqa: E402
                  quality, services, workflow)
from test_advanced import M1, fresh, mid, stock, wh  # noqa: E402,F401
from test_app import PW, app, client, csrf, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()


def _users():
    out = {}
    for u, role in (("req", "CLERK"), ("m1", "MANAGER"), ("m2", "MANAGER"), ("adm", "ADMIN"), ("sub", "CLERK")):
        r = auth.create_user(f"user.{u}", f"{u}님", role, PW, audit.SYSTEM, must_change_pw=False)
        db.execute("UPDATE users SET all_warehouses = 1 WHERE id = ?", (r.user["id"],))     # 모든 창고 (기본값과 무관하게)
        out[u] = {**auth.get_user(r.user["id"]), "ip": ""}
    return out


def _pr(u, amount):
    r = purchasing.create_pr(wh(), [(mid("PKG-001"), 1, amount)], TODAY, "보충", u["req"])
    assert r.ok
    return r.id


# ── 알림함 ───────────────────────────────────────────────────
def test_inbox_without_email_and_read(fresh, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_MODE", "off")                  # 메일·메신저를 꺼도 알림함은 남는다
    u = _users()
    _pr(u, 1000)
    assert notify.unread(u["m1"]["id"]) == 1 and notify.unread(u["req"]["id"]) == 0
    rows = notify.inbox_df(u["m1"]["id"])
    assert "구매요청" in rows.iloc[0]["subject"] and rows.iloc[0]["link"].startswith("/purchase/pr/")
    assert notify.mark_read(u["m1"]["id"]) == 1 and notify.unread(u["m1"]["id"]) == 0
    assert not db.scalar("SELECT COUNT(*) FROM notifications WHERE channel <> 'inbox'")


def test_inbox_screens(app):
    from core import seed
    seed.seed()
    u = _users()
    _pr(u, 1000)
    c = login(app.test_client(), "user.m1")
    page = c.get("/").get_data(as_text=True)
    assert "🔔 알림" in page and "has-unread" in page
    nid = int(db.scalar("SELECT id FROM notifications WHERE channel = 'inbox' AND to_user_id = ?", (u["m1"]["id"],)))
    res = c.get(f"/notifications/{nid}/open")
    assert res.status_code == 302 and "/purchase/pr/" in res.headers["Location"]
    assert notify.unread(u["m1"]["id"]) == 0
    other = login(app.test_client(), "user.m2")
    assert other.get(f"/notifications/{nid}/open").status_code == 404          # 남의 알림은 못 연다


# ── 내 결재 대기 · 대결 · 독촉 ───────────────────────────────
def test_my_queue_follows_steps(fresh):
    u = _users()
    pr = _pr(u, 2_000_000)                                              # 2단계
    assert [x["no"] for x in workflow.queue(u["m1"])] and workflow.count(u["req"]) == 0
    assert workflow.queue(u["m1"])[0]["step"] == "1/2단계"
    assert workflow.decide(u["m1"], "PR", pr, True, "").ok
    assert workflow.count(u["m1"]) == 0                                 # 이미 결재한 사람은 다음 단계에서 빠짐
    assert workflow.queue(u["m2"])[0]["step"] == "2/2단계"
    assert not workflow.decide(u["req"], "PR", pr, True, "").ok          # 요청자 본인은 불가
    assert workflow.decide(u["m2"], "PR", pr, True, "").ok
    assert db.scalar("SELECT status FROM purchase_requests WHERE id = ?", (pr,)) == "APPROVED"
    req = workflow.my_requests(u["req"]["id"])
    assert req.iloc[0]["status"] == "승인" and req.iloc[0]["step"] == "2/2"
    assert len(workflow.history(u["m2"])) == 1


def test_delegation(fresh):
    u = _users()
    pr = _pr(u, 1000)
    assert not delegation.create(u["m1"]["id"], u["m1"]["id"], TODAY, TODAY, "", u["m1"]).ok
    assert not delegation.create(u["m2"]["id"], u["sub"]["id"], TODAY, TODAY, "", u["m1"]).ok       # 남의 것은 관리자만
    assert delegation.create(u["m1"]["id"], u["sub"]["id"], TODAY, (date.today() + timedelta(days=3)).isoformat(), "휴가", u["m1"]).ok
    assert not delegation.create(u["m1"]["id"], u["adm"]["id"], TODAY, TODAY, "", u["m1"]).ok        # 겹치는 기간
    q = workflow.queue(u["sub"])
    assert q and q[0]["via"] == "m1님"
    r = workflow.decide(u["sub"], "PR", pr, True, "대신 승인")
    assert r.ok
    assert db.scalar("SELECT approver FROM pr_approvals WHERE pr_id = ?", (pr,)) == "sub님 (대결: m1님)"
    # 요청자가 맡긴 대결로 자기 요청을 승인할 수는 없다
    pr2 = _pr(u, 1000)
    delegation.create(u["req"]["id"], u["m2"]["id"], TODAY, TODAY, "", u["req"])
    assert all(x["via"] != "req님" for x in workflow.queue(u["m2"]) if x["id"] == pr2)
    did = int(db.scalar("SELECT id FROM approval_delegations WHERE from_user_id = ?", (u["m1"]["id"],)))
    assert delegation.cancel(did, u["sub"]).ok and not any(x["via"] for x in workflow.queue(u["sub"]))


def test_reminder_once_a_day(fresh, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_SLA_HOURS", 1)
    u = _users()
    pr = _pr(u, 1000)
    db.execute("UPDATE purchase_requests SET requested_at = '2020-01-01 09:00:00', updated_at = '2020-01-01 09:00:00' WHERE id = ?", (pr,))
    assert workflow.remind_overdue() == "독촉 1건"
    assert workflow.remind_overdue() == "독촉 0건"                      # 같은 날은 한 번
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE subject LIKE '%독촉%' AND to_user_id = ?", (u["m1"]["id"],)) == 1
    assert any(x["late"] for x in workflow.queue(u["m1"]))


def test_approvals_screen_decide(app):
    from core import seed
    seed.seed()
    u = _users()
    pr = _pr(u, 1000)
    c = login(app.test_client(), "user.m1")
    page = c.get("/approvals/").get_data(as_text=True)
    assert "📥 내 결재 대기" in page and "구매요청" in page
    assert post(c, "/approvals/decide", {"kind": "PR", "id": str(pr), "decision": "approve"}).status_code == 302
    assert db.scalar("SELECT status FROM purchase_requests WHERE id = ?", (pr,)) == "APPROVED"
    for tab in ("requests", "history", "delegate", "adj"):
        assert c.get(f"/approvals/?tab={tab}").status_code == 200
    assert "내 결재 대기" in c.get("/").get_data(as_text=True)


# ── 회사 설정 · 점검 모드 · readyz ─────────────────────────────
def test_company_settings_change_rules(fresh):
    company.refresh(force=True)
    base = company.values()
    form = {**{k: str(v) for k, v in base.items()}, "PR_TIER1": "5000", "PR_TIER2": "9000", "PARTNER_REQUIRED": "",
            "COMPANY_NAME": "대한제조(주)"}
    ok, msg = company.save(form, M1)
    assert ok and "저장" in msg
    assert purchasing.required_steps(6000) == 2 and purchasing.required_steps(10000) == 3 and config.COMPANY_NAME == "대한제조(주)"
    assert not company.save({**form, "PR_TIER2": "100"}, M1)[0]             # 2단계 < 1단계
    assert not company.save({**form, "ADJ_APPROVAL_AMOUNT": "abc"}, M1)[0]
    assert len(company.history()) == 1
    ok, _ = company.save({**form, "PR_TIER1": str(base["PR_TIER1"]), "PR_TIER2": str(base["PR_TIER2"]), "COMPANY_NAME": ""}, M1)
    assert ok and purchasing.required_steps(6000) == 1


def test_read_only_mode_and_readyz(client):
    from core import seed
    seed.seed()
    assert client.get("/readyz").get_json()["ready"] is True
    maintenance.set_mode(True, "서버 이전", M1)
    page = client.get("/").get_data(as_text=True)
    assert "시스템 점검 중" in page and "서버 이전" in page
    before = stock("PKG-001")
    res = post(client, "/transactions/", {"tx_type": "IN", "material_id": str(mid("PKG-001")), "warehouse_id": str(wh()),
                                          "qty": "1", "unit_price": "1", "tx_date": TODAY})
    assert res.status_code == 302 and stock("PKG-001") == before
    q = client.post("/transactions/queue", data={"_csrf": csrf(client), "kind": "TX"}, headers={"X-MM-Queue": "1"})
    assert q.status_code == 503 and q.get_json()["retry"]
    assert post(client, "/admin/read-only", {"on": "0"}).status_code == 302       # 끄기는 허용
    assert maintenance.state(cache_seconds=0) is None
    assert client.get("/").headers.get("X-Request-ID")


def test_download_is_audited(client):
    from core import seed
    seed.seed()
    client.get("/partners/?export=xlsx")
    client.get("/partners/import/template.xlsx")
    actions = db.query_df("SELECT action, entity FROM audit_log WHERE action IN ('EXPORT', 'DOWNLOAD') ORDER BY id")
    assert list(actions["action"]) == ["EXPORT", "DOWNLOAD"]                      # 같은 내려받기를 두 번 남기지 않음


# ── 데이터 점검 ──────────────────────────────────────────────
def test_quality_checks(fresh):
    from core import repository as repo
    with db.transaction() as conn:                                      # 규칙을 우회한 음수 재고 (직접 입력 가정)
        repo.insert_transaction(conn, {"material_id": mid("PKG-002"), "tx_type": "OUT", "qty": 999, "unit_price": 0,
                                       "tx_date": TODAY, "created_by": "x", "warehouse_id": wh(), "ref_no": "", "partner": "",
                                       "note": ""})
    partners.create({"name": "갑상사", "biz_no": "1248100998"}, M1)
    db.execute("INSERT INTO partners (code, name, name_key, biz_no, kind, active, created_at, updated_at) "
               "VALUES ('PX', '갑상사 2', '갑상사2', '1248100998', 'BOTH', 1, ?, ?)", (TODAY, TODAY))
    checks = {c["key"]: c for c in quality.run()}
    assert checks["negative"]["count"] == 1 and checks["negative"]["severity"] == "high"
    assert checks["partner_dup"]["count"] == 1
    assert quality.run()[0]["count"] > 0                                # 문제 있는 항목이 먼저


# ── 거래처 병합 ──────────────────────────────────────────────
def test_partner_merge(fresh):
    a = partners.create({"name": "대한팔레트", "kind": "SUPPLIER"}, M1).id
    b = partners.create({"name": "대한 팔레트 공업", "kind": "SUPPLIER", "biz_no": "1248100998"}, M1).id
    services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 1000, partner="대한 팔레트 공업", actor=M1)
    services.register_transaction(mid("PKG-001"), "IN", 3, TODAY, 1000, partner="대한팔레트", actor=M1)
    db.execute("UPDATE materials SET supplier = '대한 팔레트 공업' WHERE code = 'PKG-003'")
    assert any(c["why"] == "이름 비슷함" for c in partners.merge_candidates())
    assert not partners.merge(a, a, M1).ok
    r = partners.merge(b, a, M1)
    assert r.ok
    stats = partners.list_df().set_index("id")
    assert b not in stats.index and stats.loc[a, "tx_cnt"] == 2 and stats.loc[a, "in_amt"] == 8000
    assert db.scalar("SELECT biz_no FROM partners WHERE id = ?", (a,)) == "1248100998"
    assert db.scalar("SELECT supplier FROM materials WHERE code = 'PKG-003'") == "대한팔레트"
    assert len(partners.recent_tx(a)) == 2
    res = services.register_transaction(mid("PKG-001"), "IN", 1, TODAY, 1000, partner="대한 팔레트 공업", actor=M1)
    assert db.scalar("SELECT partner_id FROM transactions WHERE id = ?", (res.tx_id,)) == a
    assert not partners.merge(b, a, M1).ok                              # 두 번은 안 됨


# ── API ──────────────────────────────────────────────────────
def _key(scopes, whs=(), ips=""):
    r, raw = api_keys.create("MES", list(scopes), ips, list(whs), {"id": None, "name": "관리", "role": "ADMIN", "ip": ""})
    assert r.ok and raw.startswith("mmk_")
    return r.tx_id, raw


def test_api_read_write_idempotent(app, monkeypatch):
    from core import seed
    seed.seed()
    c = app.test_client()
    kid, raw = _key(["materials:read", "stock:read", "transactions:write"])
    h = {"Authorization": f"Bearer {raw}"}
    assert c.get("/api/v1/materials").status_code == 401
    assert c.get("/api/v1/materials", headers={"Authorization": "Bearer mmk_wrong"}).status_code == 401
    items = c.get("/api/v1/materials?q=팔레트", headers=h).get_json()["items"]
    assert items[0]["code"] == "PKG-001"
    st = c.get("/api/v1/stock?material=PKG-001", headers=h).get_json()["items"]
    assert st[0]["qty"] == stock("PKG-001")
    before = stock("PKG-001")
    body = {"type": "OUT", "material": "PKG-001", "warehouse": "WH1", "qty": 2, "ref_no": "MES-1"}
    r1 = c.post("/api/v1/transactions", json=body, headers={**h, "Idempotency-Key": "abc-1"})
    r2 = c.post("/api/v1/transactions", json=body, headers={**h, "Idempotency-Key": "abc-1"})
    assert r1.get_json()["ok"] and r2.get_json()["duplicate"] and stock("PKG-001") == before - 2
    assert db.scalar("SELECT created_by FROM transactions WHERE id = ?", (r1.get_json()["tx_id"],)) == "API:MES"
    big = c.post("/api/v1/transactions", json={**body, "qty": 999999}, headers=h)
    assert big.status_code == 422 and "재고 부족" in big.get_json()["error"]
    assert c.post("/api/v1/transactions", json={**body, "type": "X"}, headers=h).status_code == 400
    assert api_keys.revoke(kid, M1).ok
    assert c.get("/api/v1/materials", headers=h).status_code == 401


def test_api_scope_warehouse_ip_rate(app, monkeypatch):
    from core import seed
    seed.seed()
    c = app.test_client()
    _, read_only = _key(["stock:read"])
    assert c.get("/api/v1/materials", headers={"Authorization": f"Bearer {read_only}"}).status_code == 403
    assert c.post("/api/v1/transactions", json={}, headers={"Authorization": f"Bearer {read_only}"}).status_code == 403
    other = db.scalar("SELECT id FROM warehouses ORDER BY id DESC LIMIT 1")
    _, scoped = _key(["transactions:write"], whs=[int(other) + 999])
    res = c.post("/api/v1/transactions", json={"type": "IN", "material": "PKG-001", "warehouse": "WH1", "qty": 1},
                 headers={"Authorization": f"Bearer {scoped}"})
    assert res.status_code == 403
    _, ip_key = _key(["stock:read"], ips="10.9.0.0/16")
    assert c.get("/api/v1/stock", headers={"Authorization": f"Bearer {ip_key}"}).status_code == 403
    assert c.get("/api/v1/stock", headers={"Authorization": f"Bearer {ip_key}"},
                 environ_base={"REMOTE_ADDR": "10.9.1.2"}).status_code == 200
    monkeypatch.setattr(api_keys, "MAX_PER_MINUTE", 2)
    _, fast = _key(["stock:read"])
    codes = [c.get("/api/v1/stock", headers={"Authorization": f"Bearer {fast}"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_api_key_admin_screen(client):
    res = post(client, "/admin/api-keys", {"name": "WMS", "scope": ["stock:read"], "allowed_ips": "", "warehouse": []})
    page = res.get_data(as_text=True)
    assert "지금 한 번만 보입니다" in page and "mmk_" in page
    assert db.scalar("SELECT COUNT(*) FROM api_keys WHERE key_hash NOT LIKE 'mmk_%'") == 1      # 원래 키는 저장하지 않음


# ── 화면 ─────────────────────────────────────────────────────
def test_ui_row_click_preview_board(client):
    from core import seed
    seed.seed()
    page = client.get("/materials/").get_data(as_text=True)
    assert 'data-href="/materials/?tab=edit&amp;id=' in page and "📋 목록" in page
    page = client.get("/purchase/?tab=new").get_data(as_text=True)
    assert 'data-pr-tiers="' in page and "data-pr-preview" in page
    assert "▦ 보드" in client.get("/production/?tab=wo").get_data(as_text=True)
    assert client.get("/production/?tab=wo&view=board").status_code == 200
    assert "접근 권한 없음" in login(client.application.test_client(), "viewer").get("/admin/users").get_data(as_text=True)

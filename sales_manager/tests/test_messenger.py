"""사내 메신저 알림 — 잔디 · 네이버웍스 · 카카오워크 · Slack · Teams (가짜 서버로 요청 형식 확인)."""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import login, post, user

from core import database
from core import messenger
from core import notify
from core import sales_db as db

CALLS: list[dict] = []
FAIL_PATHS: set[str] = set()


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):          # 조용히
        pass

    def do_POST(self):                  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        CALLS.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path in FAIL_PATHS:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        out = b"{}"
        if self.path == "/nw/token":
            out = json.dumps({"access_token": "tok-123", "expires_in": 86400}).encode()
        if self.path.startswith("/kw/"):
            out = json.dumps({"success": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture(scope="module")
def fake():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def _clear_channels():
    db.set_context("system", None)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM notify_channels")


def test_channels_send_in_each_format_once(app, fake, monkeypatch):
    monkeypatch.setattr(messenger, "NAVERWORKS_TOKEN_URL", fake + "/nw/token")
    monkeypatch.setattr(messenger, "NAVERWORKS_API", fake + "/nw/api")
    monkeypatch.setattr(messenger, "KAKAOWORK_API", fake + "/kw")
    messenger._nw_tokens.clear()
    _clear_channels()
    ids = [
        messenger.save_channel({"kind": "jandi", "name": "영업 토픽", "webhook_url": fake + "/jandi"}, "관리자"),
        messenger.save_channel({"kind": "naverworks", "name": "웍스", "client_id": "cid", "client_secret": "csecret",
                                "service_account": "sa@corp", "private_key": _pem(), "bot_id": "777",
                                "channel_id": "ch-1", "targets": ["room", "user"]}, "관리자"),
        messenger.save_channel({"kind": "kakaowork", "name": "카카오워크", "app_key": "kw-app-key"}, "관리자"),
        messenger.save_channel({"kind": "teams", "name": "Teams", "webhook_url": fake + "/teams",
                                "kinds": ["요약"]}, "관리자"),                     # 결재요청은 안 보냄
    ]
    raw = database.rows("SELECT config FROM notify_channels")
    assert all("csecret" not in r["config"] and "kw-app-key" not in r["config"] and "BEGIN" not in r["config"]
               for r in raw)                                                   # 비밀값은 암호화

    rep, mgr = user("김영업"), user("한팀장")
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET email='kim@corp.kr', notify_messenger=1 WHERE id=?", (rep["id"],))
        conn.execute("UPDATE users SET email='han@corp.kr', notify_messenger=0 WHERE id=?", (mgr["id"],))
    CALLS.clear()
    nids = notify.notify([rep["id"], mgr["id"]], "결재요청", "할인 결재 요청", "제일테크 22%", "/approvals", deliver=False)
    out = notify.deliver(nids)
    paths = [c["path"] for c in CALLS]
    jandi = next(c for c in CALLS if c["path"] == "/jandi")
    assert jandi["headers"]["Accept"] == "application/vnd.tosslab.jandi-v2+json"
    jb = json.loads(jandi["body"])
    assert jb["connectInfo"][0]["title"] == "할인 결재 요청" and "외 1명" in jb["connectInfo"][0]["description"]
    tok = next(c for c in CALLS if c["path"] == "/nw/token")
    form = urllib.parse.parse_qs(tok["body"])
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"] and form["scope"] == ["bot"]
    assert form["assertion"][0].count(".") == 2                               # RS256 JWT
    assert "/nw/api/bots/777/channels/ch-1/messages" in paths                  # 채널방
    assert "/nw/api/bots/777/users/kim%40corp.kr/messages" in paths            # 개인 (이메일)
    assert not any("han%40corp.kr" in p for p in paths)                        # 메신저 끈 사람에게는 안 감
    kw = [json.loads(c["body"]) for c in CALLS if c["path"] == "/kw/messages.send_by_email"]
    assert [k["email"] for k in kw] == ["kim@corp.kr"] and "할인 결재 요청" in kw[0]["text"]
    assert "/teams" not in paths                                               # 고른 알림 종류만
    assert out["messenger"] == 4                                               # 잔디 1 + 웍스 방 1 + 웍스 개인 1 + 카카오 1

    CALLS.clear()
    with db.get_conn() as conn:
        conn.execute("UPDATE notifications SET email_status='대기' WHERE id IN (%s)" % ",".join(map(str, nids)))
    notify.deliver(nids)                                                       # 작업 재시도 → 메신저는 다시 안 보냄
    assert CALLS == []
    for cid in ids:
        messenger.delete_channel(cid)


def test_failed_channel_is_logged_and_others_still_sent(app, fake):
    _clear_channels()
    bad = messenger.save_channel({"kind": "slack", "name": "슬랙", "webhook_url": fake + "/slack"}, "관리자")
    good = messenger.save_channel({"kind": "webhook", "name": "사내", "webhook_url": fake + "/hook"}, "관리자")
    FAIL_PATHS.add("/slack")
    CALLS.clear()
    nids = notify.notify([user("김영업")["id"]], "작업실패", "배치 실패", "erp.send", "/admin/jobs", deliver=False)
    with pytest.raises(RuntimeError, match="슬랙"):
        notify.deliver(nids)
    assert "/hook" in [c["path"] for c in CALLS]
    log = messenger.recent_log(5)
    assert set(log["결과"]) >= {"실패", "발송"}
    FAIL_PATHS.clear()
    CALLS.clear()
    notify.deliver(nids)                                                       # 재시도: 실패했던 슬랙만 다시
    assert [c["path"] for c in CALLS] == ["/slack"]
    for cid in (bad, good):
        messenger.delete_channel(cid)


def test_channel_admin_pages_and_user_prefs(app, fake):
    _clear_channels()
    admin = login(app, "시스템관리자")
    assert "잔디 (JANDI)" in admin.get("/admin/channels?kind=naverworks").get_data(as_text=True)
    res = post(admin, "/admin/channels/save", {"kind": "jandi", "name": "토픽", "webhook_url": "http://evil.example/x"})
    assert res.status_code == 302 and messenger.list_channels() == []                     # https 아니면 거부
    post(admin, "/admin/channels/save", {"kind": "jandi", "name": "토픽", "webhook_url": fake + "/jandi", "active": "1"})
    ch = messenger.list_channels()[0]
    html = admin.get(f"/admin/channels?cid={ch['id']}").get_data(as_text=True)
    assert fake + "/jandi" not in html                                                     # 비밀값은 가려서
    CALLS.clear()
    post(admin, f"/admin/channels/{ch['id']}/test")
    assert [c["path"] for c in CALLS] == ["/jandi"]
    post(admin, "/admin/channels/save", {"id": ch["id"], "kind": "jandi", "name": "토픽2", "webhook_url": "", "active": "1"})
    assert messenger.get_channel(ch["id"])["config"]["webhook_url"] == fake + "/jandi"   # 비워 두면 기존 값 유지
    messenger.delete_channel(ch["id"])
    assert login(app, "김영업").get("/admin/channels").status_code == 403

    rep = login(app, "김영업")
    assert "내 알림 받기" in rep.get("/notifications").get_data(as_text=True)
    post(rep, "/notifications/prefs", {"notify_email": "1"})
    me = database.rows("SELECT notify_email, notify_messenger FROM users WHERE name='김영업'")[0]
    assert me == {"notify_email": 1, "notify_messenger": 0}

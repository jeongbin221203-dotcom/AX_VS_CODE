"""다중 서버 운영 기반 — 작업 큐 · 스케줄러 · 알림 · 운영 명령."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from conftest import login, post, user

from core import database, jobs, notify
from core import sales_db as db


def _cancel_pending():
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET status='취소' WHERE status IN ('대기','실행중')")


def test_job_retry_then_success_and_final_failure(app):
    _cancel_pending()
    calls = {"flaky": 0}

    @jobs.handler("test.flaky")
    def flaky(payload):
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            raise RuntimeError("일시 장애")
        return {"ok": True}

    @jobs.handler("test.broken")
    def broken(payload):
        raise RuntimeError("계속 실패")

    jid = jobs.enqueue("test.flaky", {"n": 1})
    assert jobs.run_pending() == 1
    job = db._one("SELECT * FROM jobs WHERE id=?", [jid])
    assert job["status"] == "대기" and job["attempts"] == 1 and "일시 장애" in job["last_error"]
    assert job["run_after"] > datetime.now().strftime("%Y-%m-%d %H:%M:%S")      # 1분 뒤 재시도
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET run_after='2000-01-01 00:00:00' WHERE id=?", (jid,))
    jobs.run_pending()
    assert db._one("SELECT status FROM jobs WHERE id=?", [jid])["status"] == "완료"

    before = db._scalar("SELECT COUNT(*) FROM notifications WHERE kind='작업실패'")
    bid = jobs.enqueue("test.broken", max_attempts=2)
    for _ in range(2):
        with db.get_conn() as conn:
            conn.execute("UPDATE jobs SET run_after='2000-01-01 00:00:00' WHERE id=?", (bid,))
        jobs.run_one("t")
    assert db._one("SELECT status FROM jobs WHERE id=?", [bid])["status"] == "실패"
    assert db._scalar("SELECT COUNT(*) FROM notifications WHERE kind='작업실패'") > before   # 관리자에게 알림
    _cancel_pending()


def test_dedupe_and_scheduler_once_per_slot(app):
    _cancel_pending()
    assert jobs.enqueue("erp.send", dedupe_key="dup-1") is not None
    assert jobs.enqueue("erp.send", dedupe_key="dup-1") is None
    monday_8am = datetime(2031, 1, 6, 8, 45)                  # 월요일
    queued = jobs.tick(monday_8am)
    assert {"erp.send", "notify.digest", "approval.escalate", "forecast.snapshot", "backup.db"} <= set(queued)
    assert "hr.sync" not in queued                            # 인사 연동 설정이 없으면 건너뜀
    assert jobs.tick(monday_8am) == []                        # 같은 구간은 한 번만
    assert jobs.tick(monday_8am + timedelta(minutes=5)) == ["erp.send"]
    _cancel_pending()


def test_workers_never_process_a_job_twice(app):
    _cancel_pending()
    seen: list[int] = []
    lock = threading.Lock()

    @jobs.handler("test.count")
    def count(payload):
        with lock:
            seen.append(payload["n"])

    for n in range(40):
        jobs.enqueue("test.count", {"n": n})
    threads = [threading.Thread(target=jobs.run_pending, kwargs={"limit": 100, "worker": f"w{i}"}) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(seen) == list(range(40))                   # 40건 모두, 한 번씩만
    workers = set(db._df("SELECT locked_by FROM jobs WHERE kind='test.count'")["locked_by"].dropna())
    assert not workers                                        # 끝난 작업은 잠금이 풀려 있다


class _Hook(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        _Hook.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()


def test_notifications_email_webhook_and_inbox(app, monkeypatch):
    _cancel_pending()
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent.append((msg["To"], msg["Subject"], msg.get_content()))

    server = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
        monkeypatch.setenv("SALES_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SALES_NOTIFY_WEBHOOK_URL", f"http://127.0.0.1:{server.server_port}/hook")
        monkeypatch.setenv("SALES_BASE_URL", "https://sales.example.com")
        rep = user("김영업")
        notify.notify([rep["id"]], "테스트", "견적 승인됨", "견적 Q-1 이 승인되었습니다", "/quotes/1")
        jobs.run_pending()
        assert sent and sent[-1][0] == rep["email"] and "견적 승인됨" in sent[-1][1]
        assert "https://sales.example.com/quotes/1" in sent[-1][2]
        assert _Hook.received and "견적 승인됨" in _Hook.received[-1]["text"]

        client = login(app, "김영업")
        assert "🔔 알림" in client.get("/").get_data(as_text=True)
        assert "견적 승인됨" in client.get("/notifications").get_data(as_text=True)
        assert notify.unread_count(rep["id"]) >= 1
        post(client, "/notifications/read", {})
        assert notify.unread_count(rep["id"]) == 0
    finally:
        server.shutdown()


def test_admin_jobs_page_and_manage_check(app, capsys):
    admin = login(app, "시스템관리자")
    assert admin.get("/admin/jobs").status_code == 200
    res = post(admin, "/admin/jobs/action", {"action": "tick"}, follow_redirects=True)
    assert "예약 시각 점검" in res.get_data(as_text=True)
    _cancel_pending()
    import manage
    assert manage.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "DB        OK" in out and "저장소    OK" in out
    assert database.current_revision() == database.head_revision()

"""알림 — 화면 알림함 + 메일(SMTP) + 사내 메신저 웹훅

  notify([user_id], "결재요청", "제목", "내용", "/approvals")  → 알림함에 쌓고 발송 작업을 큐에 올린다
  실제 발송은 워커(notify.deliver)가 한다 → 메일 서버가 느려도 화면이 기다리지 않는다

설정
  SALES_SMTP_HOST / SALES_SMTP_PORT(587) / SALES_SMTP_USER / SALES_SMTP_PASSWORD
  SALES_SMTP_FROM / SALES_SMTP_STARTTLS(1)          메일 (사용자별로 끌 수 있다: users.notify_email)
  SALES_NOTIFY_WEBHOOK_URL                           Teams·Slack·사내 메신저 수신 웹훅 (JSON {"text": …})
  관리자 > 🔔 알림 채널                              잔디·네이버웍스·카카오워크·Slack·Teams 채널 등록 (core/messenger.py)
  SALES_BASE_URL                                     메일·메신저에 넣을 링크 주소 (https://sales.example.com)
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Iterable, Optional

from . import sales_db as db


def _base_url() -> str:
    return os.environ.get("SALES_BASE_URL", "http://127.0.0.1:5001").rstrip("/")


def notify(user_ids: Iterable[int], kind: str, title: str, body: str = "", link: str = "",
           deliver: bool = True) -> list[int]:
    """알림함에 기록하고(중복 사용자 제거) 메일·메신저 발송 작업을 큐에 올린다."""
    from . import jobs
    ids: list[int] = []
    targets = sorted({int(u) for u in user_ids if u})
    if not targets:
        return ids
    with db.get_conn() as conn:
        for uid in targets:
            cur = conn.execute(
                "INSERT INTO notifications (user_id, kind, title, body, link, created_at, email_status) "
                "VALUES (?,?,?,?,?,?, '대기')", (uid, kind, title[:200], body[:2000], link, db._now()))
            ids.append(int(cur.lastrowid))
    if deliver:
        jobs.enqueue("notify.deliver", {"ids": ids}, max_attempts=3)
    return ids


def notify_role(role: str, kind: str, title: str, body: str = "", link: str = "") -> list[int]:
    """해당 역할 이상의 활성 사용자 전원."""
    level = db.ROLES[role]
    users = db._df("SELECT id, role FROM users WHERE active=1")
    return notify([int(r.id) for r in users.itertuples() if db.ROLES.get(r.role, 0) >= level],
                  kind, title, body, link)


# ---------------------------------------------------------------------------
# 알림함
# ---------------------------------------------------------------------------
def unread_count(user_id: int) -> int:
    return int(db._scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", [user_id]))


def list_for(user_id: int, limit: int = 100):
    return db._df("SELECT id, created_at AS 시각, kind AS 종류, title AS 제목, body AS 내용, link, "
                  "read_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?", [user_id, int(limit)])


def mark_read(user_id: int, notification_id: Optional[int] = None) -> int:
    sql = "UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL"
    params: list = [db._now(), user_id]
    if notification_id:
        sql += " AND id=?"
        params.append(int(notification_id))
    with db.get_conn() as conn:
        return conn.execute(sql, params).rowcount


# ---------------------------------------------------------------------------
# 발송 (워커에서 실행)
# ---------------------------------------------------------------------------
def _smtp_configured() -> bool:
    return bool(os.environ.get("SALES_SMTP_HOST"))


def send_email(to: str, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = os.environ.get("SALES_SMTP_FROM", "sales-noreply@localhost")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    host, port = os.environ["SALES_SMTP_HOST"], int(os.environ.get("SALES_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if os.environ.get("SALES_SMTP_STARTTLS", "1") == "1":
            smtp.starttls()
        if os.environ.get("SALES_SMTP_USER"):
            smtp.login(os.environ["SALES_SMTP_USER"], os.environ.get("SALES_SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def post_webhook(text: str) -> None:
    url = os.environ.get("SALES_NOTIFY_WEBHOOK_URL")
    if not url:
        return
    req = urllib.request.Request(url, data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        if res.status >= 300:
            raise RuntimeError(f"웹훅 응답 {res.status}")


def deliver(ids: list[int]) -> dict:
    """메일(사용자 설정에 따라)과 메신저 웹훅으로 보낸다. 실패하면 예외 → 작업 재시도."""
    if not ids:
        return {"email": 0}
    marks = ",".join("?" * len(ids))
    rows = db._df(f"SELECT n.id, n.kind, n.title, n.body, n.link, n.email_status, u.email, "
                  f"COALESCE(u.notify_email, 1) AS notify_email, COALESCE(u.notify_messenger, 1) AS notify_messenger, u.messenger_id, u.name "
                  f"FROM notifications n JOIN users u ON u.id = n.user_id WHERE n.id IN ({marks})", ids)
    sent = skipped = 0
    for r in rows.to_dict("records"):
        if r["email_status"] in ("발송", "생략"):
            continue
        link = f"{_base_url()}{r['link']}" if r.get("link") else _base_url()
        status = "생략"
        if _smtp_configured() and r.get("email") and int(r["notify_email"] or 0):
            send_email(r["email"], f"[영업관리] {r['title']}", f"{r['name']}님,\n\n{r['body'] or ''}\n\n{link}")
            status, sent = "발송", sent + 1
        else:
            skipped += 1
        with db.get_conn() as conn:
            conn.execute("UPDATE notifications SET email_status=?, sent_at=? WHERE id=?", (status, db._now(), r["id"]))
    if os.environ.get("SALES_NOTIFY_WEBHOOK_URL") and not rows.empty:
        first = rows.iloc[0]
        extra = f" 외 {len(rows) - 1}명" if len(rows) > 1 else ""
        post_webhook(f"[영업관리] {first['title']} → {first['name']}{extra}\n{first['body'] or ''}\n"
                     f"{_base_url()}{first['link'] or ''}")
    from . import messenger                         # 잔디·네이버웍스·카카오워크·Slack·Teams (관리자 > 알림 채널)
    chat = messenger.deliver(rows.to_dict("records"), _base_url()) if not rows.empty else {"sent": 0}
    return {"email": sent, "skipped": skipped, "messenger": chat.get("sent", 0)}


# ---------------------------------------------------------------------------
# 매일 아침 요약 (워커 스케줄 notify.digest)
# ---------------------------------------------------------------------------
def daily_digest() -> dict:
    """담당자별: 결제기일 지난 미수 · 7일 안에 마감 예정인 기회."""
    db.set_context("batch", None)
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=7)).isoformat()
    overdue = db._df("SELECT owner_id, COUNT(*) AS n, SUM(COALESCE(total_amount, amount) - COALESCE(paid_amount,0)) AS amt "
                     "FROM sales WHERE status NOT IN ('입금완료','취소') AND due_date < ? "
                     "AND owner_id IS NOT NULL GROUP BY owner_id", [today])
    closing = db._df("SELECT owner_id, COUNT(*) AS n FROM deals WHERE stage NOT IN ('수주','실주') "
                     "AND expected_close IS NOT NULL AND expected_close <= ? AND owner_id IS NOT NULL "
                     "GROUP BY owner_id", [soon])
    per_user: dict[int, list[str]] = {}
    for r in overdue.itertuples():
        per_user.setdefault(int(r.owner_id), []).append(f"결제기일이 지난 미수 {int(r.n)}건 ({int(r.amt or 0):,}원)")
    for r in closing.itertuples():
        per_user.setdefault(int(r.owner_id), []).append(f"7일 안에 마감 예정인 영업기회 {int(r.n)}건")
    for uid, lines in per_user.items():
        notify([uid], "요약", "오늘 확인할 항목", "\n".join(lines), "/")
    return {"users": len(per_user)}


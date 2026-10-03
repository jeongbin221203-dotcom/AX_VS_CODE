"""결재 알림 메일.

- 결재가 필요한 일이 생기면(구매요청·다음 결재 단계·발주 결재·큰 실사 조정) 결재할 수 있는 사람에게,
  결재가 끝나면(승인·반려) 요청한 사람에게 알린다.
- 알림은 업무와 같은 트랜잭션에서 notifications 표에 쌓는다 → 업무가 취소되면 알림도 없다(유령 메일 없음).
  실제 발송은 배치(notify_send)가 한다 → 메일 서버가 느리거나 멈춰도 화면은 기다리지 않고, 실패하면 다시 보낸다.
- 받는 사람: 사용 중이고 메일 주소가 있으며, 역할이 충분하고, 그 창고 권한이 있는 사용자. 요청자 본인은 빼고.
- 방식(config.NOTIFY_MODE): off(만들지 않음) | log(보낼 목록에만 남김 — 메일 서버 없이 확인) | smtp(메일 발송).
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import pandas as pd

import config
from core import auth, db
from core.utils import now_str

log = logging.getLogger(__name__)
STATUS = {"PENDING": "보낼 예정", "SENT": "보냄", "LOGGED": "기록만(메일 서버 없음)", "FAILED": "실패"}


def enabled() -> bool:
    return config.NOTIFY_MODE in ("log", "smtp")


def _link(path: str) -> str:
    return f"{config.BASE_URL}{path}" if config.BASE_URL else path


def recipients(conn, min_role: str, warehouse_id: int | None, exclude_ids=()) -> list[dict]:
    """역할이 min_role 이상이고 그 창고 권한이 있는, 메일 주소가 있는 사용 중인 사용자."""
    exclude = {int(i) for i in exclude_ids if i is not None}
    out = []
    for r in conn.execute("SELECT id, name, email, role, all_warehouses FROM users WHERE active = 1 AND email <> ''"):
        r = dict(r)
        if r["id"] in exclude or not auth.has_role(r, min_role):
            continue
        if warehouse_id is not None and r["role"] != "ADMIN" and not int(r["all_warehouses"] or 0):
            hit = conn.execute("SELECT 1 FROM user_scopes s JOIN warehouses w ON w.id = s.warehouse_id OR w.plant_id = s.plant_id "
                               "WHERE s.user_id = ? AND w.id = ?", (r["id"], warehouse_id)).fetchone()
            if not hit:
                continue
        out.append(r)
    return out


def user(conn, user_id) -> list[dict]:
    if user_id is None:
        return []
    row = conn.execute("SELECT id, name, email FROM users WHERE id = ? AND active = 1 AND email <> ''", (user_id,)).fetchone()
    return [dict(row)] if row else []


def queue(conn, to: list[dict], subject: str, lines: list[str], path: str, ref: str) -> int:
    """알림을 쌓는다 (호출하는 쪽 트랜잭션 안에서). 쌓은 건수."""
    if not enabled() or not to:
        return 0
    body = "\n".join([*lines, "", f"바로 가기: {_link(path)}", "", f"— {config.APP_TITLE} (이 메일은 자동 발송입니다)"])
    seen = set()
    for r in to:
        addr = (r.get("email") or "").strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        conn.execute("INSERT INTO notifications (ref, to_user_id, to_addr, subject, body, status, created_at) "
                     "VALUES (?, ?, ?, ?, ?, 'PENDING', ?)",
                     (ref, r.get("id"), addr, f"[{config.APP_TITLE}] {subject}", body, now_str()))
    return len(seen)


# ── 보내기 (배치) ─────────────────────────────────────────────
def _smtp():
    if config.SMTP_SECURITY == "ssl":
        s = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20, context=ssl.create_default_context())
    else:
        s = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        if config.SMTP_SECURITY == "starttls":
            s.starttls(context=ssl.create_default_context())
    if config.SMTP_USER:
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
    return s


def send_pending(limit: int = 100) -> str:
    """보낼 알림을 보낸다. log 방식이면 '기록만'으로 표시한다. 실패하면 횟수를 올려 다음 주기에 다시."""
    rows = db.query_df("SELECT * FROM notifications WHERE status = 'PENDING' OR (status = 'FAILED' AND tries < ?) "
                       "ORDER BY id LIMIT ?", (config.NOTIFY_MAX_TRIES, limit))
    if rows.empty:
        return "보낼 알림 없음"
    if config.NOTIFY_MODE != "smtp" or not config.SMTP_HOST:
        ids = [int(i) for i in rows["id"]]
        frag, params = db.in_clause(ids)
        db.execute(f"UPDATE notifications SET status = 'LOGGED', sent_at = ? WHERE id{frag}", (now_str(), *params))
        for r in rows.itertuples():
            log.info("알림(기록만) → %s: %s", r.to_addr, r.subject)
        return f"기록만 {len(ids)}건 (메일 서버 설정 없음: MM_NOTIFY_MODE=smtp, MM_SMTP_HOST)"
    sent = failed = 0
    try:
        server = _smtp()
    except Exception as exc:                                    # 메일 서버 연결 실패 → 모두 다음에 다시
        _fail(rows["id"].tolist(), f"메일 서버 연결 실패: {exc}")
        return f"메일 서버 연결 실패 ({len(rows)}건은 다음에 다시): {exc}"
    try:
        for r in rows.itertuples():
            msg = EmailMessage()
            msg["Subject"] = r.subject
            msg["From"] = formataddr((config.APP_TITLE, config.SMTP_FROM))
            msg["To"] = r.to_addr
            msg.set_content(r.body)
            try:
                server.send_message(msg)
                db.execute("UPDATE notifications SET status = 'SENT', tries = tries + 1, sent_at = ?, last_error = '' "
                           "WHERE id = ?", (now_str(), int(r.id)))
                sent += 1
            except Exception as exc:
                _fail([r.id], str(exc))
                failed += 1
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return f"보냄 {sent} · 실패 {failed}"


def _fail(ids, error: str) -> None:
    for i in ids:
        db.execute("UPDATE notifications SET status = 'FAILED', tries = tries + 1, last_error = ? WHERE id = ?",
                   (error[:500], int(i)))


def recent_df(limit: int = 50) -> pd.DataFrame:
    return db.query_df("SELECT id, created_at, to_addr, subject, status, tries, last_error, sent_at FROM notifications "
                       "ORDER BY id DESC LIMIT ?", (limit,))

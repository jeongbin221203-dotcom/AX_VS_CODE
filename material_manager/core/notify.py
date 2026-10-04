"""결재 알림: 메일 · 잔디(JANDI) · 네이버웍스(NAVER WORKS).

- 결재가 필요한 일이 생기면(구매요청·다음 결재 단계·발주 결재·큰 실사 조정) 결재할 수 있는 사람에게,
  결재가 끝나면(승인·반려) 요청한 사람에게 알린다.
- 알림은 업무와 같은 트랜잭션에서 notifications 표에 쌓는다 → 업무가 취소되면 알림도 없다(유령 알림 없음).
  실제 발송은 배치(notify_send)가 한다 → 메일·메신저 서버가 느리거나 멈춰도 화면은 기다리지 않고, 실패하면 다시 보낸다.
- 받는 사람: 사용 중이고 역할이 충분하고 그 창고 권한이 있는 사용자(요청자 본인 제외).
- 알림함(channel 'inbox'): 받는 사람마다 화면 알림 한 줄 — 메일·메신저 설정과 상관없이 늘 남고, 사이드바 🔔 에 안 읽은 수.
- 보낼 길(channel)
    email       사용자 메일 주소가 있으면 (MM_SMTP_*)
    jandi       잔디 토픽의 '들어오는 웹훅'(MM_JANDI_WEBHOOK_URL) — 사람마다가 아니라 토픽에 한 번
    naverworks  네이버웍스 봇(MM_NAVERWORKS_*) — 사용자의 메신저 아이디(없으면 메일)로 1:1, 채널 번호가 있으면 채널에도
- 알림 채널(관리자 → 🔔 알림 채널, core/messenger.py): 잔디·네이버웍스·카카오워크·Slack·Teams·웹훅을 화면에서 등록 —
  채널마다 보낼 알림 종류(결재 요청·결과·독촉)와 받는 곳(그룹방·개인)을 고른다. 위의 환경변수 방식도 그대로 동작한다.
- 방식(config.NOTIFY_MODE): off(만들지 않음) | log(보낼 목록에만 남김 — 서버 없이 확인) | send(실제로 보냄, 예전 이름 smtp).
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

import pandas as pd

import config
from core import auth, db
from core.utils import now_str

log = logging.getLogger(__name__)
STATUS = {"PENDING": "보낼 예정", "SENT": "보냄", "LOGGED": "기록만(보내지 않음)", "FAILED": "실패"}
CHANNELS = {"email": "메일", "jandi": "잔디", "naverworks": "네이버웍스", "kakaowork": "카카오워크", "slack": "Slack",
            "teams": "Teams", "webhook": "웹훅"}
NW_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
NW_API = "https://www.worksapis.com/v1.0"


def enabled() -> bool:
    return config.NOTIFY_MODE in ("log", "send", "smtp")


def sending() -> bool:
    return config.NOTIFY_MODE in ("send", "smtp")


def jandi_on() -> bool:
    return bool(config.JANDI_WEBHOOK_URL)


def naverworks_on() -> bool:
    return all((config.NW_BOT_ID, config.NW_CLIENT_ID, config.NW_CLIENT_SECRET, config.NW_SERVICE_ACCOUNT,
                config.NW_PRIVATE_KEY))


def _link(path: str) -> str:
    return f"{config.BASE_URL}{path}" if config.BASE_URL else path


def recipients(conn, min_role: str, warehouse_id: int | None, exclude_ids=()) -> list[dict]:
    """역할이 min_role 이상이고 그 창고 권한이 있는 사용 중인 사용자 (+ 그 사람의 대결자). 연락처가 없으면 알림함에만."""
    exclude = {int(i) for i in exclude_ids if i is not None}
    out = []
    for r in conn.execute("SELECT id, name, email, messenger_id, role, all_warehouses FROM users WHERE active = 1"):
        r = dict(r)
        if r["id"] in exclude or not auth.has_role(r, min_role):
            continue
        if warehouse_id is not None and r["role"] != "ADMIN" and not int(r["all_warehouses"] or 0):
            hit = conn.execute("SELECT 1 FROM user_scopes s JOIN warehouses w ON w.id = s.warehouse_id OR w.plant_id = s.plant_id "
                               "WHERE s.user_id = ? AND w.id = ?", (r["id"], warehouse_id)).fetchone()
            if not hit:
                continue
        out.append(r)
    # 대결: 오늘 위임받은 사람에게도 (위임한 사람 대신 결재할 수 있으므로)
    from core import delegation
    ids = {r["id"] for r in out}
    for d in delegation.active_for_froms(conn, list(ids)):
        if d["to_user_id"] not in ids | exclude:
            row = conn.execute("SELECT id, name, email, messenger_id FROM users WHERE id = ? AND active = 1",
                               (d["to_user_id"],)).fetchone()
            if row:
                out.append({**dict(row), "delegate_for": d["from_name"]})
                ids.add(row["id"])
    return out


def user(conn, user_id) -> list[dict]:
    if user_id is None:
        return []
    row = conn.execute("SELECT id, name, email, messenger_id FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
    return [dict(row)] if row else []


def event_of(subject: str) -> str:
    """알림 종류(채널별로 고를 수 있음): 결재 독촉 · 결재 결과(승인·반려) · 결재 요청."""
    if "독촉" in subject:
        return "REMIND"
    if subject.rstrip().endswith(("승인", "반려")) or " 승인" in subject or " 반려" in subject:
        return "RESULT"
    return "REQUEST"


def queue(conn, to: list[dict], subject: str, lines: list[str], path: str, ref: str, event: str = "") -> int:
    """알림을 쌓는다 (호출하는 쪽 트랜잭션 안에서). 알림함은 늘, 메일·메신저는 켜져 있을 때. 쌓은 건수."""
    event = event or event_of(subject)
    for r in to:                                         # 화면 알림함 (사람마다 한 줄)
        if r.get("id") is not None:
            extra = f" (대결: {r['delegate_for']} 대신)" if r.get("delegate_for") else ""
            conn.execute("INSERT INTO notifications (ref, to_user_id, to_addr, subject, body, status, created_at, channel, link, "
                         "read_at) VALUES (?, ?, '', ?, ?, 'INBOX', ?, 'inbox', ?, '')",
                         (ref, r["id"], subject + extra, "\n".join(lines), now_str(), path))
    if not enabled():
        return 0
    link = _link(path)
    body = "\n".join([*lines, "", f"바로 가기: {link}", "", f"— {config.APP_TITLE} (자동 알림)"])
    subject = f"[{config.APP_TITLE}] {subject}"
    rows: list[tuple] = []
    seen = set()
    for r in to:
        addr = (r.get("email") or "").strip()
        if addr and ("email", addr.lower()) not in seen:
            seen.add(("email", addr.lower()))
            rows.append(("email", r.get("id"), addr))
        if naverworks_on() and config.NW_TO_USERS:
            nid = (r.get("messenger_id") or "").strip() or addr
            if nid and ("naverworks", nid.lower()) not in seen:
                seen.add(("naverworks", nid.lower()))
                rows.append(("naverworks", r.get("id"), nid))
    if to and jandi_on():
        names = ", ".join(r["name"] for r in to[:5]) + (" 외" if len(to) > 5 else "")
        rows.append(("jandi", None, f"토픽 (받을 사람: {names})"))
    if to and naverworks_on() and config.NW_CHANNEL_ID:
        rows.append(("naverworks", None, f"channel:{config.NW_CHANNEL_ID}"))
    for channel, uid, addr in rows:
        conn.execute("INSERT INTO notifications (ref, to_user_id, to_addr, subject, body, status, created_at, channel) "
                     "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)", (ref, uid, addr, subject, body, now_str(), channel))
    from core import messenger                           # 화면에서 등록한 알림 채널 (채널마다 알림 종류·받는 곳)
    ch_rows = messenger.rows_for(conn, to, event)
    for kind, cid, addr in ch_rows:
        conn.execute("INSERT INTO notifications (ref, to_user_id, to_addr, subject, body, status, created_at, channel, "
                     "channel_id, event, link) VALUES (?, NULL, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)",
                     (ref, addr, subject, "\n".join(lines), now_str(), kind, cid, event, path))
    return len(rows) + len(ch_rows)


# ── 보내기 (배치) ─────────────────────────────────────────────
def _post(url: str, data: bytes, headers: dict, timeout: int = 15) -> tuple[int, bytes]:
    """HTTP POST (테스트에서 바꿔 끼운다)."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:          # noqa: S310 — 설정한 주소(https)만
        return res.status, res.read()


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


def _send_jandi(row) -> None:
    title, _, rest = row.body.partition("\n")
    payload = {"body": row.subject, "connectColor": "#2747A3",
               "connectInfo": [{"title": title, "description": rest.strip()[:1500]}]}
    status, _ = _post(config.JANDI_WEBHOOK_URL, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      {"Accept": "application/vnd.tosslab.jandi-v2+json", "Content-Type": "application/json"})
    if status >= 300:
        raise RuntimeError(f"잔디 응답 {status}")


_nw_token = {"value": "", "until": 0.0}


def _nw_access_token() -> str:
    """네이버웍스 서비스 계정(JWT) 인증으로 봇 접근 토큰을 받는다 (만료 전까지 재사용)."""
    if _nw_token["value"] and time.time() < _nw_token["until"] - 60:
        return _nw_token["value"]
    from joserfc import jwt
    from joserfc.jwk import RSAKey
    pem = config.NW_PRIVATE_KEY
    if "BEGIN" not in pem:                                    # 파일 경로로 준 경우
        with open(pem, encoding="utf-8") as f:
            pem = f.read()
    now = int(time.time())
    assertion = jwt.encode({"alg": "RS256", "typ": "JWT"},
                           {"iss": config.NW_CLIENT_ID, "sub": config.NW_SERVICE_ACCOUNT, "iat": now, "exp": now + 3600},
                           RSAKey.import_key(pem))
    form = urllib.parse.urlencode({"assertion": assertion, "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "client_id": config.NW_CLIENT_ID, "client_secret": config.NW_CLIENT_SECRET,
                                   "scope": "bot"}).encode()
    status, body = _post(NW_TOKEN_URL, form, {"Content-Type": "application/x-www-form-urlencoded"})
    data = json.loads(body or b"{}")
    if status >= 300 or not data.get("access_token"):
        raise RuntimeError(f"네이버웍스 토큰 발급 실패 ({status})")
    _nw_token.update(value=data["access_token"], until=time.time() + int(data.get("expires_in", 3600)))
    return _nw_token["value"]


def _send_naverworks(row) -> None:
    token = _nw_access_token()
    target = (f"channels/{urllib.parse.quote(row.to_addr[8:])}" if row.to_addr.startswith("channel:")
              else f"users/{urllib.parse.quote(row.to_addr)}")
    payload = {"content": {"type": "text", "text": f"{row.subject}\n{row.body}"[:2000]}}
    status, _ = _post(f"{NW_API}/bots/{config.NW_BOT_ID}/{target}/messages",
                      json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if status >= 300:
        raise RuntimeError(f"네이버웍스 응답 {status}")


def send_pending(limit: int = 100) -> str:
    """보낼 알림을 보낸다. log 방식이면 '기록만'으로 표시한다. 실패하면 횟수를 올려 다음 주기에 다시."""
    rows = db.query_df("SELECT * FROM notifications WHERE channel <> 'inbox' AND (status = 'PENDING' "
                       "OR (status = 'FAILED' AND tries < ?)) ORDER BY id LIMIT ?", (config.NOTIFY_MAX_TRIES, limit))
    if rows.empty:
        return "보낼 알림 없음"
    rows["channel"] = rows["channel"].fillna("email").replace("", "email")
    if not sending():
        _mark(rows["id"].tolist(), "LOGGED")
        for r in rows.itertuples():
            log.info("알림(기록만) %s → %s: %s", r.channel, r.to_addr, r.subject)
        return f"기록만 {len(rows)}건 (실제로 보내려면 MM_NOTIFY_MODE=send)"
    counts = {"sent": 0, "failed": 0}
    by_channel = rows["channel_id"].notna() if "channel_id" in rows.columns else pd.Series(False, index=rows.index)
    if by_channel.any():
        from core import messenger
        for r in rows[by_channel].itertuples():           # 화면에서 등록한 알림 채널
            _try(r, lambda r=r: messenger.send_row(r), counts)
    rows = rows[~by_channel]
    mail = rows[rows["channel"] == "email"]
    if len(mail):
        if not config.SMTP_HOST:
            _fail(mail["id"].tolist(), "메일 서버 설정 없음 (MM_SMTP_HOST)")
            counts["failed"] += len(mail)
        else:
            try:
                server = _smtp()
            except Exception as exc:                            # 메일 서버 연결 실패 → 모두 다음에 다시
                _fail(mail["id"].tolist(), f"메일 서버 연결 실패: {exc}")
                counts["failed"] += len(mail)
                server = None
            if server is not None:
                try:
                    for r in mail.itertuples():
                        msg = EmailMessage()
                        msg["Subject"] = r.subject
                        msg["From"] = formataddr((config.APP_TITLE, config.SMTP_FROM))
                        msg["To"] = r.to_addr
                        msg.set_content(r.body)
                        _try(r, lambda r=r, m=msg: server.send_message(m), counts)
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass
    for r in rows[rows["channel"] == "jandi"].itertuples():
        if not jandi_on():
            _fail([r.id], "잔디 웹훅 설정 없음 (MM_JANDI_WEBHOOK_URL)")
            counts["failed"] += 1
            continue
        _try(r, lambda r=r: _send_jandi(r), counts)
    for r in rows[rows["channel"] == "naverworks"].itertuples():
        if not naverworks_on():
            _fail([r.id], "네이버웍스 봇 설정 없음 (MM_NAVERWORKS_*)")
            counts["failed"] += 1
            continue
        _try(r, lambda r=r: _send_naverworks(r), counts)
    return f"보냄 {counts['sent']} · 실패 {counts['failed']}"


def _try(row, fn, counts: dict) -> None:
    try:
        fn()
        db.execute("UPDATE notifications SET status = 'SENT', tries = tries + 1, sent_at = ?, last_error = '' WHERE id = ?",
                   (now_str(), int(row.id)))
        counts["sent"] += 1
    except Exception as exc:
        _fail([row.id], str(exc))
        counts["failed"] += 1


def _mark(ids, status: str) -> None:
    frag, params = db.in_clause([int(i) for i in ids])
    db.execute(f"UPDATE notifications SET status = ?, sent_at = ? WHERE id{frag}", (status, now_str(), *params))


def _fail(ids, error: str) -> None:
    for i in ids:
        db.execute("UPDATE notifications SET status = 'FAILED', tries = tries + 1, last_error = ? WHERE id = ?",
                   (error[:500], int(i)))


def test_message(actor: dict) -> int:
    """설정 확인용 알림 한 건 (보내는 사람 자신에게 + 잔디·네이버웍스 채널)."""
    with db.transaction() as conn:
        me = conn.execute("SELECT id, name, email, messenger_id FROM users WHERE id = ?", (actor.get("id"),)).fetchone()
        return queue(conn, [dict(me)] if me else [], "알림 시험", [f"{actor['name']}님이 보낸 알림 시험입니다."],
                     "/admin/jobs", "test")


def recent_df(limit: int = 50) -> pd.DataFrame:
    return db.query_df("SELECT id, created_at, channel, to_addr, subject, status, tries, last_error, sent_at FROM notifications "
                       "WHERE channel <> 'inbox' ORDER BY id DESC LIMIT ?", (limit,))


# ── 알림함 ───────────────────────────────────────────────────
def unread(user_id) -> int:
    if not user_id:
        return 0
    return int(db.scalar("SELECT COUNT(*) FROM notifications WHERE channel = 'inbox' AND to_user_id = ? AND read_at = ''",
                         (user_id,)) or 0)


def inbox_df(user_id, only_unread: bool = False, limit: int = 200) -> pd.DataFrame:
    return db.query_df("SELECT id, created_at, subject, body, link, read_at FROM notifications WHERE channel = 'inbox' "
                       "AND to_user_id = ?" + (" AND read_at = ''" if only_unread else "") + " ORDER BY id DESC LIMIT ?",
                       (user_id, limit))


def mark_read(user_id, ids: list[int] | None = None) -> int:
    """읽음 표시 (ids 가 None 이면 모두). 바꾼 건수."""
    sql = "UPDATE notifications SET read_at = ? WHERE channel = 'inbox' AND to_user_id = ? AND read_at = ''"
    params: list = [now_str(), user_id]
    if ids is not None:
        frag, p2 = db.in_clause([int(i) for i in ids])
        sql += f" AND id{frag}"
        params += p2
    with db.transaction() as conn:
        return conn.execute(sql, params).rowcount

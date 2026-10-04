"""알림 채널(사내 메신저) — 관리자 화면에서 등록·수정·삭제·시험 보내기.

  종류          보내는 곳                                   필요한 값
  jandi         잔디 토픽 (들어오는 웹훅, 커넥트 카드)       webhook_url
  naverworks    네이버웍스 봇 → 채널방 또는 개인 메시지     client_id · client_secret · service_account · private_key · bot_id (+ channel_id)
  kakaowork     카카오워크 봇 → 개인 메시지(사용자 메일)     app_key
  slack         Slack 들어오는 웹훅                          webhook_url
  teams         Microsoft Teams (Workflows 웹훅)            webhook_url
  webhook       기타 사내 메신저 웹훅 (JSON {"text": …})     webhook_url

받는 곳
  room   그룹방에 한 번 ("결재 요청 → 박팀장 외 1명")       잔디·Slack·Teams·웹훅·네이버웍스(채널 ID 가 있을 때)
  user   알림 받는 사람마다 개인 메시지                     네이버웍스(메신저 아이디, 없으면 메일)·카카오워크(메일)

- 알림은 업무와 같은 트랜잭션에서 notifications 에 쌓이고(core/notify.queue), 배치 notify_send 가 채널별로 보낸다.
  채널마다 보낼 알림 종류(결재 요청·결과·독촉)를 고를 수 있다.
- 비밀값(웹훅 주소·키)은 DB 에 암호화해 두고(키: MM_NOTIFY_KEY, 없으면 MM_SECRET_KEY) 화면에는 끝 4자리만 보인다.
  키가 바뀌면 비밀값을 다시 입력해야 한다.
- 예전 방식(환경변수 MM_JANDI_* · MM_NAVERWORKS_*)도 그대로 동작한다.
- 실제 잔디·네이버웍스·카카오워크·Teams 서버로는 검증하지 않았다(각 서비스 공개 문서 형식, 테스트는 가짜 서버).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse

import config
from core import audit, db
from core.utils import now_str

KINDS = {   # 종류: (이름, [(키, 칸 이름, 비밀값?, 필수?)], 받는 곳)
    "jandi": ("잔디 (JANDI)", [("webhook_url", "들어오는 웹훅 URL", True, True)], ("room",)),
    "naverworks": ("네이버웍스 (NAVER WORKS)", [
        ("client_id", "Client ID", False, True), ("client_secret", "Client Secret", True, True),
        ("service_account", "Service Account", False, True), ("private_key", "Private Key (PEM)", True, True),
        ("bot_id", "Bot ID", False, True), ("channel_id", "채널 ID (채널방으로 보낼 때)", False, False)], ("room", "user")),
    "kakaowork": ("카카오워크 (Kakao Work)", [("app_key", "봇 App Key", True, True)], ("user",)),
    "slack": ("Slack", [("webhook_url", "Incoming Webhook URL", True, True)], ("room",)),
    "teams": ("Microsoft Teams", [("webhook_url", "Workflows 웹훅 URL", True, True)], ("room",)),
    "webhook": ("기타 웹훅 (JSON text)", [("webhook_url", "웹훅 URL", True, True)], ("room",)),
}
TARGETS = {"room": "그룹방에 한 번", "user": "받는 사람마다 개인 메시지"}
EVENTS = {"REQUEST": "결재 요청", "RESULT": "결재 결과(승인·반려)", "REMIND": "결재 독촉"}
NW_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
NW_API = "https://www.worksapis.com/v1.0"
KAKAOWORK_API = "https://api.kakaowork.com/v1"


# ── 비밀값 암호화 ────────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    secret = os.getenv("MM_NOTIFY_KEY") or config.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("mm-notify:" + secret).encode()).digest()))


def _secret_keys(kind: str) -> set[str]:
    return {k for k, _l, secret, _r in KINDS[kind][1] if secret}


def _encrypt(kind: str, cfg: dict) -> str:
    f = _fernet()
    return json.dumps({k: ("enc:" + f.encrypt(str(v).encode()).decode()) if k in _secret_keys(kind) and v else v
                       for k, v in cfg.items()}, ensure_ascii=False)


def _decrypt(raw: str) -> tuple[dict, bool]:
    """(설정, 키가 바뀌어 못 읽은 비밀값이 있나)."""
    cfg, lost = json.loads(raw or "{}"), False
    f = _fernet()
    for k, v in list(cfg.items()):
        if isinstance(v, str) and v.startswith("enc:"):
            try:
                cfg[k] = f.decrypt(v[4:].encode()).decode()
            except Exception:                        # 암호 키가 바뀜 → 다시 입력해야 한다
                cfg[k], lost = "", True
    return cfg, lost


def masked(kind: str, cfg: dict) -> dict:
    """화면 표시용: 비밀값은 끝 4자리만."""
    return {k: (("•" * 6 + str(v)[-4:]) if v and k in _secret_keys(kind) else v) for k, v in cfg.items()}


# ── 채널 관리 ────────────────────────────────────────────────
def list_channels(active_only: bool = False) -> list[dict]:
    try:
        df = db.query_df("SELECT * FROM messenger_channels" + (" WHERE active = 1" if active_only else "") + " ORDER BY id")
    except db.DBError:                               # 리비전 0005 전
        return []
    out = []
    for r in df.to_dict("records"):
        r["config"], r["lost"] = _decrypt(r["config"])
        r["events"] = [e for e in (r["events"] or "").split(",") if e]
        r["kind_label"] = KINDS.get(r["kind"], (r["kind"],))[0]
        r["shown"] = masked(r["kind"], r["config"]) if r["kind"] in KINDS else {}
        out.append(r)
    return out


def get(cid: int) -> dict | None:
    return next((c for c in list_channels() if int(c["id"]) == int(cid)), None)


def save(data: dict, actor: dict) -> tuple[bool, str, int | None]:
    """등록·수정. 비밀값 칸을 비워 두면 지금 값을 그대로 둔다. (성공, 메시지, 채널 번호)"""
    kind = data.get("kind") or ""
    old = get(int(data["id"])) if str(data.get("id") or "").isdigit() else None
    if old:
        kind = old["kind"]                            # 종류는 바꾸지 않는다 (필요한 값이 다름)
    if kind not in KINDS:
        return False, "메신저 종류를 고르세요.", None
    name = str(data.get("name") or "").strip()[:60] or KINDS[kind][0]
    cfg: dict = {}
    for key, label, secret, required in KINDS[kind][1]:
        val = str(data.get(key) or "").strip()
        if not val and secret and old:
            val = old["config"].get(key, "")
        if required and not val:
            return False, f"{label}을(를) 입력하세요.", None
        if key == "webhook_url" and val and not val.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            return False, "웹훅 URL 은 https:// 로 시작해야 합니다.", None
        cfg[key] = val
    targets = [t for t in (data.get("targets") or []) if t in KINDS[kind][2]] or [KINDS[kind][2][0]]
    if kind == "naverworks" and "room" in targets and not cfg.get("channel_id"):
        targets = [t for t in targets if t != "room"] or ["user"]
    cfg["targets"] = targets
    events = [e for e in (data.get("events") or []) if e in EVENTS]
    active = 1 if str(data.get("active", "1")) in ("1", "on", "true", "True") else 0
    ts = now_str()
    with db.transaction() as conn:
        if old:
            conn.execute("UPDATE messenger_channels SET name = ?, config = ?, events = ?, active = ?, updated_at = ? WHERE id = ?",
                         (name, _encrypt(kind, cfg), ",".join(events), active, ts, old["id"]))
            cid = int(old["id"])
        else:
            cid = conn.execute("INSERT INTO messenger_channels (kind, name, config, events, active, created_by, created_at, "
                               "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (kind, name, _encrypt(kind, cfg), ",".join(events), active, actor["name"], ts, ts)).lastrowid
            if cid is None:
                cid = conn.execute("SELECT MAX(id) FROM messenger_channels").fetchone()[0]
        audit.record(conn, actor, "CHANNEL_SAVE", "channel", cid,
                     {"kind": kind, "name": name, "events": events or "전체", "targets": targets, "active": active,
                      "new": not old})                # 비밀값은 감사로그에 남기지 않는다
    return True, f"알림 채널 '{name}'을(를) {'저장' if old else '등록'}했습니다.", int(cid)


def delete(cid: int, actor: dict) -> tuple[bool, str]:
    ch = get(cid)
    if ch is None:
        return False, "채널이 없습니다."
    with db.transaction() as conn:
        conn.execute("DELETE FROM messenger_channels WHERE id = ?", (cid,))
        conn.execute("UPDATE notifications SET status = 'FAILED', last_error = '채널 삭제됨' "
                     "WHERE channel_id = ? AND status IN ('PENDING', 'FAILED')", (cid,))
        audit.record(conn, actor, "CHANNEL_DELETE", "channel", cid, {"kind": ch["kind"], "name": ch["name"]})
    return True, f"알림 채널 '{ch['name']}'을(를) 지웠습니다."


def log_df(limit: int = 50):
    return db.query_df("SELECT n.id, n.created_at, c.name AS channel, n.event, n.to_addr, n.subject, n.status, n.tries, "
                       "n.last_error, n.sent_at FROM notifications n LEFT JOIN messenger_channels c ON c.id = n.channel_id "
                       "WHERE n.channel_id IS NOT NULL ORDER BY n.id DESC LIMIT ?", (limit,))


# ── 쌓기 (notify.queue 가 부른다) ────────────────────────────
def rows_for(conn, to: list[dict], event: str) -> list[tuple]:
    """이 알림을 받을 채널별 줄: [(channel kind, channel id, to_addr)]."""
    out = []
    for ch in list_channels(active_only=True):
        if ch["kind"] not in KINDS or (ch["events"] and event not in ch["events"]):
            continue
        targets = ch["config"].get("targets") or [KINDS[ch["kind"]][2][0]]
        if "room" in targets and to:
            names = ", ".join(r["name"] for r in to[:3]) + (f" 외 {len(to) - 3}명" if len(to) > 3 else "")
            out.append((ch["kind"], int(ch["id"]), f"room:{names}"[:200]))
        if "user" in targets:
            seen = set()
            for r in to:
                addr = ((r.get("messenger_id") or "").strip() if ch["kind"] == "naverworks" else "") or (r.get("email") or "").strip()
                if addr and addr.lower() not in seen:
                    seen.add(addr.lower())
                    out.append((ch["kind"], int(ch["id"]), addr))
    return out


# ── 보내기 ───────────────────────────────────────────────────
def _post_json(url: str, payload: dict, headers: dict | None = None) -> bytes:
    from core import notify                          # 테스트에서 notify._post 를 바꿔 끼운다
    status, body = notify._post(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                {"Content-Type": "application/json; charset=utf-8", **(headers or {})})
    if status >= 300:
        raise RuntimeError(f"응답 {status}: {body[:200]!r}")
    return body


def _text(msg: dict) -> str:
    lines = [msg["title"]]
    if msg.get("body"):
        lines.append(msg["body"])
    if msg.get("to"):
        lines.append(f"받는 사람: {msg['to']}")
    if msg.get("url"):
        lines.append(msg["url"])
    return "\n".join(lines)[:1900]


def _jandi(cfg, msg, target):
    _post_json(cfg["webhook_url"], {"body": msg["title"] + (f" — [바로 가기]({msg['url']})" if msg.get("url") else ""),
                                    "connectColor": "#2747A3",
                                    "connectInfo": [{"title": msg["title"], "description": _text({**msg, "title": "", "url": ""})}]},
               {"Accept": "application/vnd.tosslab.jandi-v2+json"})


def _slack(cfg, msg, target):
    _post_json(cfg["webhook_url"], {"text": _text(msg)})


def _webhook(cfg, msg, target):
    _post_json(cfg["webhook_url"], {"text": _text(msg), "title": msg["title"], "event": msg.get("event", ""),
                                    "url": msg.get("url", "")})


def _teams(cfg, msg, target):
    card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "body": [{"type": "TextBlock", "text": msg["title"], "weight": "Bolder", "wrap": True},
                     {"type": "TextBlock", "text": _text({**msg, "title": "", "url": ""}), "wrap": True}],
            "actions": [{"type": "Action.OpenUrl", "title": "바로 가기", "url": msg["url"]}] if msg.get("url", "").startswith("http") else []}
    _post_json(cfg["webhook_url"], {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})


_nw_tokens: dict[str, tuple[str, float]] = {}


def _nw_token(cfg) -> str:
    key = cfg["client_id"] + ":" + cfg["service_account"]
    tok = _nw_tokens.get(key)
    if tok and tok[1] > time.time() + 60:
        return tok[0]
    from joserfc import jwt
    from joserfc.jwk import RSAKey
    from core import notify
    now = int(time.time())
    assertion = jwt.encode({"alg": "RS256", "typ": "JWT"},
                           {"iss": cfg["client_id"], "sub": cfg["service_account"], "iat": now, "exp": now + 3600},
                           RSAKey.import_key(cfg["private_key"]))
    form = urllib.parse.urlencode({"assertion": assertion, "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "scope": "bot"}).encode()
    status, body = notify._post(NW_TOKEN_URL, form, {"Content-Type": "application/x-www-form-urlencoded"})
    data = json.loads(body or b"{}")
    if status >= 300 or not data.get("access_token"):
        raise RuntimeError(f"네이버웍스 토큰 발급 실패 ({status})")
    _nw_tokens[key] = (data["access_token"], time.time() + int(data.get("expires_in") or 3600))
    return data["access_token"]


def _naverworks(cfg, msg, target):
    token = _nw_token(cfg)
    path = (f"users/{urllib.parse.quote(target)}" if target else f"channels/{urllib.parse.quote(cfg['channel_id'])}")
    _post_json(f"{NW_API}/bots/{cfg['bot_id']}/{path}/messages", {"content": {"type": "text", "text": _text(msg)}},
               {"Authorization": f"Bearer {token}"})


def _kakaowork(cfg, msg, target):
    if not target:
        raise RuntimeError("카카오워크는 받는 사람 메일이 필요합니다 (사용자 화면에서 메일 입력).")
    body = _post_json(f"{KAKAOWORK_API}/messages.send_by_email", {"email": target, "text": _text(msg)},
                      {"Authorization": f"Bearer {cfg['app_key']}"})
    try:
        res = json.loads(body or b"{}")
    except ValueError:
        res = {}
    if res.get("success") is False:
        raise RuntimeError(f"카카오워크 오류: {res.get('error')}")


SENDERS = {"jandi": _jandi, "slack": _slack, "teams": _teams, "webhook": _webhook,
           "naverworks": _naverworks, "kakaowork": _kakaowork}


def send_row(row) -> None:
    """notify.send_pending 이 채널 줄 하나를 보낸다. 실패하면 예외(다음 주기에 다시)."""
    ch = get(int(row.channel_id))
    if ch is None or not ch["active"]:
        raise RuntimeError("채널이 없거나 꺼져 있습니다.")
    if ch["lost"]:
        raise RuntimeError("암호 키가 바뀌어 비밀값을 읽을 수 없습니다 — 알림 채널 화면에서 다시 입력하세요.")
    to_addr = str(row.to_addr or "")
    room = to_addr.startswith("room:")
    title, _, rest = str(row.body or "").partition("\n")
    link = str(getattr(row, "link", "") or "")
    msg = {"title": str(row.subject), "body": str(row.body or ""), "to": to_addr[5:] if room else "",
           "url": (config.BASE_URL + link) if link and config.BASE_URL else link, "event": str(getattr(row, "event", "") or "")}
    SENDERS[ch["kind"]](ch["config"], msg, "" if room else to_addr)


def send_test(cid: int, actor: dict) -> tuple[bool, str]:
    """시험 메시지 한 통을 지금 보낸다 (개인 메시지 채널은 관리자 본인 메일·메신저 아이디로). 결과는 발송 기록에 남는다."""
    ch = get(cid)
    if ch is None:
        return False, "채널이 없습니다."
    targets = ch["config"].get("targets") or ["room"]
    me = db.query_df("SELECT email, messenger_id FROM users WHERE id = ?", (actor.get("id"),))
    mine = "" if me.empty else ((me.iloc[0]["messenger_id"] if ch["kind"] == "naverworks" else "") or me.iloc[0]["email"] or "")
    if "room" not in targets and not mine:
        return False, "개인 메시지 채널은 시험 받을 본인 메일(또는 네이버웍스 아이디)이 사용자 정보에 있어야 합니다."
    addr = f"room:{actor['name']} (시험)" if "room" in targets else mine
    subject = f"[{config.APP_TITLE}] 알림 채널 시험"
    body = f"'{ch['name']}' 채널 연결 시험입니다 — {actor['name']}"
    with db.transaction() as conn:
        nid = conn.execute("INSERT INTO notifications (ref, to_user_id, to_addr, subject, body, status, created_at, channel, "
                           "channel_id, event, link) VALUES ('test', ?, ?, ?, ?, 'PENDING', ?, ?, ?, 'TEST', '/admin/channels')",
                           (actor.get("id"), addr, subject, body, now_str(), ch["kind"], cid)).lastrowid
        if nid is None:
            nid = conn.execute("SELECT MAX(id) FROM notifications").fetchone()[0]
    row = db.query_df("SELECT * FROM notifications WHERE id = ?", (nid,)).iloc[0]
    try:
        send_row(row)
    except Exception as exc:
        db.execute("UPDATE notifications SET status = 'FAILED', tries = tries + 1, last_error = ? WHERE id = ?", (str(exc)[:500], nid))
        return False, f"시험 보내기 실패: {exc}"
    db.execute("UPDATE notifications SET status = 'SENT', tries = tries + 1, sent_at = ? WHERE id = ?", (now_str(), nid))
    return True, f"시험 메시지를 보냈습니다 ({'그룹방' if 'room' in targets else mine})."

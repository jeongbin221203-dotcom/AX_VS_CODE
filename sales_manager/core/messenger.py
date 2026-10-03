"""사내 메신저 알림 — 잔디 · 네이버웍스 · 카카오워크 · Slack · Microsoft Teams · 일반 웹훅.

관리자 > 🔔 알림 채널 에서 채널을 등록하면, 알림(notify.notify)이 생길 때 워커(notify.deliver)가 메일과 함께 보낸다.

  종류          보내는 곳                                  필요한 값
  jandi         잔디 토픽 (수신 웹훅, 커넥트 카드)         webhook_url
  naverworks    네이버웍스 봇 → 채널방 또는 개인 메시지    client_id · client_secret · service_account · private_key · bot_id
                                                            (+ channel_id: 채널방 / 개인: 사용자 이메일 = 웍스 ID)
  kakaowork     카카오워크 봇 → 개인 메시지(이메일)        app_key
  slack         Slack 수신 웹훅                             webhook_url
  teams         Microsoft Teams 수신 웹훅 (Workflows)      webhook_url
  webhook       기타 사내 메신저 웹훅 (JSON {"text": …})    webhook_url

받는 사람
  room   그룹방 한 곳에 한 번 ("결재요청 → 한팀장 외 1명")       잔디·Slack·Teams·웹훅·네이버웍스(channel_id)
  user   알림 받는 사람마다 개인 메시지 (사용자 이메일 기준)      네이버웍스·카카오워크
비밀값(토큰·키)은 DB 에 암호화해서 둔다 (키: SALES_NOTIFY_KEY → 없으면 SALES_SECRET_KEY → 개발용 data/.notify.key).
같은 알림을 다시 보내지 않도록 (채널, 알림, 받는 곳) 성공 기록을 notify_log 에 남긴다 — 작업이 재시도돼도 중복 발송 없음.
실제 잔디·네이버웍스·카카오워크 서버로 보내는 것은 검증하지 않았다(각 서비스 공개 API 문서 형식대로, 테스트는 가짜 서버).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import sales_db as db

TIMEOUT = 15
KINDS = {   # 종류: (표시 이름, 필드 [(키, 이름, 비밀값?, 필수?)], 받는 사람 방식)
    "jandi": ("잔디 (JANDI)", [("webhook_url", "수신 웹훅 URL", True, True)], ("room",)),
    "naverworks": ("네이버웍스 (NAVER WORKS)", [
        ("client_id", "Client ID", False, True), ("client_secret", "Client Secret", True, True),
        ("service_account", "Service Account", False, True), ("private_key", "Private Key (PEM)", True, True),
        ("bot_id", "Bot ID", False, True), ("channel_id", "채널 ID (채널방으로 보낼 때)", False, False)], ("room", "user")),
    "kakaowork": ("카카오워크 (Kakao Work)", [("app_key", "봇 App Key", True, True)], ("user",)),
    "slack": ("Slack", [("webhook_url", "Incoming Webhook URL", True, True)], ("room",)),
    "teams": ("Microsoft Teams", [("webhook_url", "Workflows 웹훅 URL", True, True)], ("room",)),
    "webhook": ("기타 웹훅 (JSON text)", [("webhook_url", "웹훅 URL", True, True)], ("room",)),
}
EVENT_KINDS = ["결재요청", "결재결과", "결재독촉", "결재지연", "거래정지", "담당이관", "요약", "작업실패", "보안"]
NAVERWORKS_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
NAVERWORKS_API = "https://www.worksapis.com/v1.0"
KAKAOWORK_API = "https://api.kakaowork.com/v1"


# ---------------------------------------------------------------------------
# 비밀값 암호화
# ---------------------------------------------------------------------------
def _fernet():
    from cryptography.fernet import Fernet
    secret = os.environ.get("SALES_NOTIFY_KEY") or os.environ.get("SALES_SECRET_KEY")
    if not secret:                                  # 개발용: 처음 한 번 만든 키를 data/ 에 둔다 (재시작해도 같은 키)
        path = os.path.join(db.BASE_DIR, "data", ".notify.key")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="ascii") as f:
                f.write(base64.urlsafe_b64encode(os.urandom(32)).decode())
        with open(path, encoding="ascii") as f:
            secret = f.read().strip()
    key = base64.urlsafe_b64encode(hashlib.sha256(("notify:" + secret).encode()).digest())
    return Fernet(key)


def _secret_fields(kind: str) -> set[str]:
    return {k for k, _l, secret, _r in KINDS[kind][1] if secret}


def encrypt_config(kind: str, cfg: dict) -> str:
    f = _fernet()
    out = {k: ("enc:" + f.encrypt(str(v).encode()).decode()) if k in _secret_fields(kind) and v else v
           for k, v in cfg.items()}
    return json.dumps(out, ensure_ascii=False)


def decrypt_config(kind: str, raw: str) -> dict:
    cfg = json.loads(raw or "{}")
    f = _fernet()
    for k, v in list(cfg.items()):
        if isinstance(v, str) and v.startswith("enc:"):
            try:
                cfg[k] = f.decrypt(v[4:].encode()).decode()
            except Exception:                       # noqa: BLE001 - 키가 바뀌면 다시 입력해야 한다
                cfg[k] = ""
    return cfg


def masked(kind: str, cfg: dict) -> dict:
    """화면 표시용: 비밀값은 끝 4자리만."""
    return {k: (("•" * 6 + str(v)[-4:]) if v and k in _secret_fields(kind) else v) for k, v in cfg.items()}


# ---------------------------------------------------------------------------
# 채널 관리
# ---------------------------------------------------------------------------
def list_channels(active_only: bool = False) -> list[dict]:
    try:
        rows = db._df("SELECT * FROM notify_channels" + (" WHERE active = 1" if active_only else "") + " ORDER BY id")
    except Exception:                               # noqa: BLE001 - 마이그레이션 전
        return []
    out = []
    for r in rows.to_dict("records"):
        r["config"] = decrypt_config(r["kind"], r["config"])
        r["kinds"] = json.loads(r["kinds"] or "[]")
        r["kind_label"] = KINDS.get(r["kind"], (r["kind"],))[0]
        out.append(r)
    return out


def get_channel(cid: int) -> dict | None:
    return next((c for c in list_channels() if int(c["id"]) == int(cid)), None)


def save_channel(data: dict, actor: str) -> int:
    """채널 등록·수정. 비밀값 칸을 비워 두면 기존 값을 유지한다."""
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError("알 수 없는 메신저 종류입니다.")
    name = str(data.get("name") or "").strip()[:60] or KINDS[kind][0]
    old = get_channel(int(data["id"])) if data.get("id") else None
    if old and old["kind"] != kind:
        raise ValueError("메신저 종류는 바꿀 수 없습니다. 새 채널로 등록하세요.")
    cfg = {}
    for key, label, secret, required in KINDS[kind][1]:
        val = str(data.get(key) or "").strip()
        if not val and secret and old:
            val = old["config"].get(key, "")
        if required and not val:
            raise ValueError(f"{label} 을(를) 입력하세요.")
        if key == "webhook_url" and val and not val.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("웹훅 URL 은 https:// 로 시작해야 합니다.")
        cfg[key] = val
    targets = [t for t in data.get("targets") or [] if t in KINDS[kind][2]] or list(KINDS[kind][2][:1])
    if "room" in targets and kind == "naverworks" and not cfg.get("channel_id"):
        targets = [t for t in targets if t != "room"] or ["user"]
    cfg["targets"] = targets
    kinds = [k for k in data.get("kinds") or [] if k in EVENT_KINDS]
    now = db._now()
    with db.get_conn() as conn:
        if old:
            conn.execute("UPDATE notify_channels SET name=?, config=?, kinds=?, active=?, updated_at=? WHERE id=?",
                         (name, encrypt_config(kind, cfg), json.dumps(kinds, ensure_ascii=False),
                          1 if data.get("active", True) else 0, now, old["id"]))
            cid = int(old["id"])
        else:
            cur = conn.execute("INSERT INTO notify_channels (kind, name, config, kinds, active, created_by, created_at, "
                               "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                               (kind, name, encrypt_config(kind, cfg), json.dumps(kinds, ensure_ascii=False),
                                1 if data.get("active", True) else 0, actor, now, now))
            cid = int(cur.lastrowid)
    db.audit("수정" if old else "등록", "알림채널", cid, {"종류": KINDS[kind][0], "이름": name, "알림": kinds or "전체",
                                                    "받는 사람": targets})
    return cid


def delete_channel(cid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM notify_channels WHERE id=?", (int(cid),))
    db.audit("삭제", "알림채널", int(cid))


def recent_log(limit: int = 50):
    return db._df("SELECT l.sent_at AS 시각, c.name AS 채널, l.target AS 받는곳, l.status AS 결과, l.error AS 오류, "
                  "n.kind AS 알림, n.title AS 제목 FROM notify_log l LEFT JOIN notify_channels c ON c.id = l.channel_id "
                  "LEFT JOIN notifications n ON n.id = l.notification_id ORDER BY l.id DESC LIMIT ?", [int(limit)])


# ---------------------------------------------------------------------------
# HTTP · 메신저별 보내기
# ---------------------------------------------------------------------------
def _post(url: str, payload: Any, headers: dict | None = None, form: bool = False) -> dict:
    if form:
        data = urllib.parse.urlencode(payload).encode()
        hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdr = {"Content-Type": "application/json; charset=utf-8"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            body = res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} {detail}") from exc
    try:
        return json.loads(body) if body.strip() else {}
    except ValueError:
        return {"raw": body[:300]}


def _text(msg: dict) -> str:
    lines = [f"[영업관리] {msg['title']}"]
    if msg.get("body"):
        lines.append(msg["body"])
    if msg.get("to"):
        lines.append(f"받는 사람: {msg['to']}")
    if msg.get("url"):
        lines.append(msg["url"])
    return "\n".join(lines)


def send_jandi(cfg: dict, msg: dict, target: str = "") -> None:
    info = [{"title": msg["title"], "description": (msg.get("body") or "") + (f"\n받는 사람: {msg['to']}" if msg.get("to") else "")}]
    payload = {"body": f"[영업관리] {msg['kind']}" + (f" — [바로 가기]({msg['url']})" if msg.get("url") else ""),
               "connectColor": "#2747A3", "connectInfo": info}
    _post(cfg["webhook_url"], payload, {"Accept": "application/vnd.tosslab.jandi-v2+json"})


def send_slack(cfg: dict, msg: dict, target: str = "") -> None:
    _post(cfg["webhook_url"], {"text": _text(msg)})


def send_webhook(cfg: dict, msg: dict, target: str = "") -> None:
    _post(cfg["webhook_url"], {"text": _text(msg), "title": msg["title"], "kind": msg["kind"], "url": msg.get("url", "")})


def send_teams(cfg: dict, msg: dict, target: str = "") -> None:
    card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "body": [{"type": "TextBlock", "text": f"[영업관리] {msg['title']}", "weight": "Bolder", "wrap": True},
                     {"type": "TextBlock", "text": (msg.get("body") or "") + (f"\n\n받는 사람: {msg['to']}" if msg.get("to") else ""),
                      "wrap": True}],
            "actions": [{"type": "Action.OpenUrl", "title": "바로 가기", "url": msg["url"]}] if msg.get("url") else []}
    _post(cfg["webhook_url"], {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})


_nw_tokens: dict[str, tuple[str, float]] = {}
_nw_lock = threading.Lock()


def _naverworks_token(cfg: dict) -> str:
    """서비스 계정 JWT(RS256) → Access Token (만료 전까지 재사용)."""
    key = cfg["client_id"] + ":" + cfg["service_account"]
    with _nw_lock:
        tok = _nw_tokens.get(key)
        if tok and tok[1] > time.time() + 60:
            return tok[0]
        from authlib.jose import jwt
        now = int(time.time())
        assertion = jwt.encode({"alg": "RS256"}, {"iss": cfg["client_id"], "sub": cfg["service_account"],
                                                  "iat": now, "exp": now + 3600}, cfg["private_key"]).decode()
        res = _post(NAVERWORKS_TOKEN_URL, {"assertion": assertion, "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                           "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                           "scope": "bot"}, form=True)
        if not res.get("access_token"):
            raise RuntimeError(f"네이버웍스 토큰 발급 실패: {res}")
        _nw_tokens[key] = (res["access_token"], time.time() + int(res.get("expires_in") or 3600))
        return res["access_token"]


def send_naverworks(cfg: dict, msg: dict, target: str = "") -> None:
    token = _naverworks_token(cfg)
    if target:                                      # 개인 메시지: 웍스 ID(이메일)
        url = f"{NAVERWORKS_API}/bots/{cfg['bot_id']}/users/{urllib.parse.quote(target)}/messages"
    else:
        url = f"{NAVERWORKS_API}/bots/{cfg['bot_id']}/channels/{cfg['channel_id']}/messages"
    content: dict = {"type": "text", "text": _text(msg)}
    if msg.get("url"):
        content = {"type": "button_template", "contentText": _text({**msg, "url": ""})[:1000],
                   "actions": [{"type": "uri", "label": "바로 가기", "uri": msg["url"]}]}
    _post(url, {"content": content}, {"Authorization": f"Bearer {token}"})


def send_kakaowork(cfg: dict, msg: dict, target: str = "") -> None:
    if not target:
        raise RuntimeError("카카오워크는 받는 사람 이메일이 필요합니다.")
    res = _post(f"{KAKAOWORK_API}/messages.send_by_email", {"email": target, "text": _text(msg)},
                {"Authorization": f"Bearer {cfg['app_key']}"})
    if res.get("success") is False:
        raise RuntimeError(f"카카오워크 오류: {res.get('error')}")


SENDERS: dict[str, Callable[[dict, dict, str], None]] = {
    "jandi": send_jandi, "slack": send_slack, "teams": send_teams, "webhook": send_webhook,
    "naverworks": send_naverworks, "kakaowork": send_kakaowork,
}


# ---------------------------------------------------------------------------
# 발송 (notify.deliver 가 부른다)
# ---------------------------------------------------------------------------
def _already(conn, cid: int, nid: int, target: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM notify_log WHERE channel_id=? AND notification_id=? AND target=? "
                             "AND status='발송'", (cid, nid, target)).fetchone())


def _log(cid: int, nid: int | None, target: str, status: str, error: str = "") -> None:
    with db.get_conn() as conn:
        conn.execute("INSERT INTO notify_log (channel_id, notification_id, target, status, error, sent_at) "
                     "VALUES (?,?,?,?,?,?)", (cid, nid, target, status, error[:500] or None, db._now()))


def deliver(rows: list[dict], base_url: str) -> dict:
    """알림 묶음(같은 notify() 호출에서 나온 행들)을 활성 채널로 보낸다. 실패한 채널이 있으면 끝에 예외 → 작업 재시도.

    rows: [{id, kind, title, body, link, name, email, notify_messenger}]"""
    channels = list_channels(active_only=True)
    if not channels or not rows:
        return {"sent": 0, "failed": 0}
    sent = failed = 0
    errors = []
    first = rows[0]
    for ch in channels:
        if ch["kinds"] and first["kind"] not in ch["kinds"]:
            continue
        cfg, send = ch["config"], SENDERS[ch["kind"]]
        jobs: list[tuple[int, str, dict]] = []
        url = f"{base_url}{first.get('link') or ''}"
        if "room" in cfg.get("targets", ["room"]):
            names = [r["name"] for r in rows]
            to = names[0] + (f" 외 {len(names) - 1}명" if len(names) > 1 else "")
            jobs.append((int(first["id"]), "", {"kind": first["kind"], "title": first["title"], "body": first.get("body"),
                                                "url": url, "to": to}))
        if "user" in cfg.get("targets", []):
            for r in rows:
                if r.get("email") and int(r.get("notify_messenger") or 0):
                    jobs.append((int(r["id"]), r["email"], {"kind": r["kind"], "title": r["title"], "body": r.get("body"),
                                                            "url": f"{base_url}{r.get('link') or ''}", "to": ""}))
        for nid, target, msg in jobs:
            with db.get_conn() as conn:
                if _already(conn, int(ch["id"]), nid, target):
                    continue
            try:
                send(cfg, msg, target)
                _log(int(ch["id"]), nid, target, "발송")
                sent += 1
            except Exception as exc:                # noqa: BLE001 - 다른 채널은 계속 보낸다
                _log(int(ch["id"]), nid, target, "실패", str(exc))
                errors.append(f"{ch['name']}: {exc}")
                failed += 1
    if errors:
        raise RuntimeError("메신저 발송 실패 — " + " / ".join(errors[:3]))
    return {"sent": sent, "failed": failed}


def send_test(cid: int, base_url: str, email: str = "") -> str:
    """테스트 메시지 한 통. 개인 메시지 채널은 관리자 본인 이메일로."""
    ch = get_channel(cid)
    if not ch:
        raise ValueError("채널을 찾을 수 없습니다.")
    cfg = ch["config"]
    msg = {"kind": "테스트", "title": "알림 채널 테스트", "body": f"'{ch['name']}' 채널 연결이 정상입니다.",
           "url": base_url + "/", "to": ""}
    targets = cfg.get("targets", ["room"])
    target = "" if "room" in targets else email
    if not target and "room" not in targets:
        raise ValueError("개인 메시지 채널은 테스트 받을 본인 이메일이 사용자 정보에 있어야 합니다.")
    try:
        SENDERS[ch["kind"]](cfg, msg, target)
    except Exception as exc:                        # noqa: BLE001
        _log(int(ch["id"]), None, target, "실패", str(exc))
        raise RuntimeError(str(exc)) from exc
    _log(int(ch["id"]), None, target, "발송")
    return target or "그룹방"

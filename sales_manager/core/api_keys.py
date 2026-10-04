"""외부 연동 API 클라이언트 — 키 발급·검증 · 권한 범위 · 호출 한도 · 멱등키

  키 형식  sk_<접두 8자>_<비밀 40자>. DB 에는 접두와 SHA-256 해시만 남는다(발급 화면에서 한 번만 보여 준다).
  대리 사용자  API 는 지정한 사용자의 역할·데이터 범위로 동작하고, 감사로그 주체는 "API:<이름>" 이다.
  호출 한도  클라이언트별 분당 rate_limit. 분 단위 카운터를 DB 에 두므로 서버가 여러 대여도 한도를 함께 쓴다.
  멱등키  쓰기 요청의 Idempotency-Key 가 같으면 처음 응답을 그대로 돌려준다(재전송으로 매출이 두 번 생기지 않게).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from . import enterprise as ent
from . import sales_db as db

SCOPES = {
    "customers:read": "거래처 조회",
    "deals:read": "영업기회 조회",
    "sales:read": "매출 조회",
    "sales:write": "매출 등록",
    "products:read": "품목·단가 조회",
    "quotes:read": "견적 조회",
    "erp:write": "ERP 수신 (입금·전표번호·여신·품목)",
}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_client(name: str, user_id: int, scopes: list[str], rate_limit: int = 120,
                  allowed_ips: str = "") -> tuple[int, str]:
    name = (name or "").strip()
    if not name:
        raise ValueError("클라이언트 이름을 입력하세요 (예: 그룹웨어, BI).")
    bad = [s for s in scopes if s not in SCOPES]
    if bad or not scopes:
        raise ValueError(f"권한 범위를 한 개 이상 올바르게 고르세요: {bad or '없음'}")
    acting = ent.get_user(user_id=int(user_id or 0))
    if not acting or not acting.get("active"):
        raise ValueError("대리 사용자는 활성 사용자여야 합니다.")
    for part in [p.strip() for p in (allowed_ips or "").split(",") if p.strip()]:
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError as exc:
            raise ValueError(f"허용 IP 형식이 올바르지 않습니다: {part}") from exc
    if db._one("SELECT id FROM api_clients WHERE name=?", [name]):
        raise ValueError(f"'{name}' 이름의 클라이언트가 이미 있습니다.")
    prefix = secrets.token_hex(4)
    key = f"sk_{prefix}_{secrets.token_urlsafe(30)}"
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO api_clients (name, key_prefix, key_hash, scopes, user_id, rate_limit, allowed_ips, "
            "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, prefix, _hash(key), " ".join(sorted(set(scopes))), int(user_id), max(1, int(rate_limit)),
             allowed_ips.strip() or None, db.current_actor(), db._now()))
        cid = int(cur.lastrowid)
    db.audit("API키발급", "시스템", cid, {"이름": name, "권한": sorted(set(scopes)), "대리사용자": acting["name"],
                                        "분당한도": rate_limit})
    return cid, key


def revoke_client(client_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE api_clients SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (db._now(), client_id))
    db.audit("API키폐기", "시스템", client_id, None)


def list_clients() -> pd.DataFrame:
    return db._df("SELECT c.id, c.name AS 이름, 'sk_' || c.key_prefix || '_…' AS 키, c.scopes AS 권한, "
                  "u.name AS 대리사용자, c.rate_limit AS 분당한도, c.allowed_ips AS 허용IP, c.created_at AS 발급일, "
                  "c.last_used_at AS 최근사용, CASE WHEN c.revoked_at IS NULL THEN '사용' ELSE '폐기' END AS 상태 "
                  "FROM api_clients c JOIN users u ON u.id = c.user_id ORDER BY c.id DESC")


def authenticate(key: str, remote_ip: str | None) -> tuple[Optional[dict], str]:
    """(클라이언트, 오류메시지). 키 비교는 상수 시간으로 한다."""
    parts = (key or "").split("_", 2)
    if len(parts) != 3 or parts[0] != "sk":
        return None, "API 키 형식이 올바르지 않습니다."
    row = db._one("SELECT * FROM api_clients WHERE key_prefix=?", [parts[1]])
    if not row or not hmac.compare_digest(row["key_hash"], _hash(key)):
        return None, "API 키가 올바르지 않습니다."
    if row.get("revoked_at"):
        return None, "폐기된 API 키입니다."
    if row.get("allowed_ips"):
        nets = [ipaddress.ip_network(p.strip(), strict=False) for p in row["allowed_ips"].split(",") if p.strip()]
        try:
            ip = ipaddress.ip_address(remote_ip or "")
        except ValueError:
            ip = None
        if not ip or not any(ip in n for n in nets):
            return None, "허용되지 않은 IP 에서 호출했습니다."
    user = ent.get_user(user_id=int(row["user_id"]))
    if not user or not user.get("active"):
        return None, "API 대리 사용자가 비활성 상태입니다."
    row["scope_set"] = set(row["scopes"].split())
    row["user"] = user
    return row, ""


def touch(client_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE api_clients SET last_used_at=? WHERE id=?", (db._now(), client_id))


def hit(client: dict, now: Optional[datetime] = None) -> tuple[bool, int, int]:
    """분당 호출 수를 1 올리고 (허용 여부, 남은 횟수, 초기화까지 초)."""
    now = now or datetime.now()
    win = now.strftime("%Y%m%d%H%M")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO api_usage (client_id, win, hits) VALUES (?,?,1) "
                     "ON CONFLICT (client_id, win) DO UPDATE SET hits = api_usage.hits + 1",
                     (int(client["id"]), win))
        hits = int(conn.execute("SELECT hits FROM api_usage WHERE client_id=? AND win=?",
                                (int(client["id"]), win)).fetchone()[0])
    limit = int(client["rate_limit"])
    return hits <= limit, max(0, limit - hits), 60 - now.second


# ---------------------------------------------------------------------------
# 멱등키
# ---------------------------------------------------------------------------
def idem_begin(client_id: int, key: str, body: bytes) -> tuple[str, Optional[tuple[int, dict]]]:
    """('new' | 'replay' | 'conflict' | 'busy', 저장된 응답)."""
    digest = hashlib.sha256(body or b"").hexdigest()
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO api_idempotency (client_id, idem_key, request_hash, status, response, "
                           "created_at) VALUES (?,?,?,0,'',?) ON CONFLICT (client_id, idem_key) DO NOTHING",
                           (client_id, key, digest, db._now()))
        if cur.rowcount == 1:
            return "new", None
        row = conn.execute("SELECT * FROM api_idempotency WHERE client_id=? AND idem_key=?",
                           (client_id, key)).fetchone()
    if row["request_hash"] != digest:
        return "conflict", None
    if int(row["status"]) == 0:
        # 처리 중 서버가 죽으면 '처리 중'으로 남아 7일 동안 409 → 5분 넘었으면 이 요청이 이어받는다
        started = datetime.strptime(str(row["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
        if datetime.now() - started > timedelta(minutes=5):
            with db.get_conn() as conn:
                taken = conn.execute("UPDATE api_idempotency SET created_at=? WHERE client_id=? AND idem_key=? "
                                     "AND status=0 AND created_at=?", (db._now(), client_id, key, row["created_at"])).rowcount
            if taken:
                return "new", None
        return "busy", None
    return "replay", (int(row["status"]), json.loads(row["response"]))


def idem_finish(client_id: int, key: str, status: int, payload: dict) -> None:
    with db.get_conn() as conn:
        if status >= 500:
            conn.execute("DELETE FROM api_idempotency WHERE client_id=? AND idem_key=?", (client_id, key))
        else:
            conn.execute("UPDATE api_idempotency SET status=?, response=? WHERE client_id=? AND idem_key=?",
                         (status, json.dumps(payload, ensure_ascii=False, default=str), client_id, key))

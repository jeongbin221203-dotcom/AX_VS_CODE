"""외부 연동 API 키 (MES·WMS·사내 포털이 재고를 조회하고 입출고를 등록).

- 키는 처음 만들 때 한 번만 보여 주고, DB에는 SHA-256 해시만 둔다(앞 8자 prefix 로 어떤 키인지 알아본다).
- 범위(scope): materials:read · stock:read · transactions:write. 창고를 정하면 그 창고만, IP 를 정하면 그 주소에서만.
- 키마다 1분에 MAX_PER_MINUTE 번까지(서버 한 대 기준). 넘으면 429.
- 키로 한 일은 감사로그에 'API:키 이름'으로 남는다. 폐기하면 바로 막힌다.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque

import pandas as pd

from core import audit, db, services
from core.utils import now_str

SCOPES = {"materials:read": "자재 조회", "stock:read": "재고 조회", "transactions:write": "입출고 등록"}
MAX_PER_MINUTE = 120
_hits: dict[int, deque] = defaultdict(deque)
_lock = threading.Lock()


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(name: str, scopes: list[str], allowed_ips: str, warehouse_ids: list[int], actor: dict) -> tuple[services.Result, str]:
    """(결과, 원래 키 — 이때만 보여 준다)."""
    name = name.strip()[:60]
    scopes = [s for s in scopes if s in SCOPES]
    if not name or not scopes:
        return services.Result(False, "이름과 범위를 하나 이상 정하세요."), ""
    ips = [x.strip() for x in allowed_ips.replace("\n", ",").split(",") if x.strip()]
    for ip in ips:
        try:
            ipaddress.ip_network(ip, strict=False)
        except ValueError:
            return services.Result(False, f"IP 주소 형식이 아닙니다: {ip}"), ""
    raw = "mmk_" + secrets.token_urlsafe(32)
    prefix = raw[:12]
    with db.transaction() as conn:
        kid = conn.execute("INSERT INTO api_keys (name, prefix, key_hash, scopes, allowed_ips, warehouse_ids, active, created_by, "
                           "created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                           (name, prefix, _hash(raw), ",".join(scopes), ",".join(ips), ",".join(map(str, warehouse_ids)),
                            actor["name"], now_str())).lastrowid
        audit.record(conn, actor, "API_KEY", "api_key", kid, {"create": name, "scopes": scopes, "ips": ips,
                                                             "warehouses": warehouse_ids})
    return services.Result(True, f"API 키 '{name}'를 만들었습니다. 키는 지금 한 번만 보입니다 — 복사해 두세요.", tx_id=kid), raw


def revoke(key_id: int, actor: dict) -> services.Result:
    with db.transaction() as conn:
        n = conn.execute("UPDATE api_keys SET active = 0, revoked_at = ? WHERE id = ? AND active = 1", (now_str(), key_id)).rowcount
        if n:
            audit.record(conn, actor, "API_KEY", "api_key", key_id, {"revoke": True})
    return services.Result(bool(n), "키를 폐기했습니다. 이 키로 오는 요청은 바로 막힙니다." if n else "이미 폐기된 키입니다.")


def check(raw: str, ip: str, scope: str) -> tuple[dict | None, int, str]:
    """(키 정보, HTTP 상태, 문제). 문제가 없으면 상태 200."""
    if not raw or not raw.startswith("mmk_"):
        return None, 401, "API 키가 필요합니다 (Authorization: Bearer mmk_...)."
    row = db.query_df("SELECT * FROM api_keys WHERE prefix = ?", (raw[:12],))
    if row.empty or not secrets.compare_digest(row.iloc[0]["key_hash"], _hash(raw)):
        return None, 401, "API 키가 맞지 않습니다."
    key = row.iloc[0].to_dict()
    if not key["active"]:
        return None, 401, "폐기된 API 키입니다."
    if scope not in key["scopes"].split(","):
        return None, 403, f"이 키에는 '{SCOPES.get(scope, scope)}' 권한이 없습니다."
    if key["allowed_ips"]:
        try:
            addr = ipaddress.ip_address(ip)
            if not any(addr in ipaddress.ip_network(n, strict=False) for n in key["allowed_ips"].split(",")):
                return None, 403, "허용하지 않은 IP 주소입니다."
        except ValueError:
            return None, 403, "IP 주소를 확인할 수 없습니다."
    with _lock:
        q, now = _hits[int(key["id"])], time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= MAX_PER_MINUTE:
            return None, 429, f"요청이 너무 많습니다 (1분에 {MAX_PER_MINUTE}번까지)."
        q.append(now)
    db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now_str(), int(key["id"])))
    key["wh_ids"] = {int(x) for x in key["warehouse_ids"].split(",") if x} or None
    return key, 200, ""


def actor_of(key: dict, ip: str) -> dict:
    return {"id": None, "name": f"API:{key['name']}", "role": "CLERK", "ip": ip}


def list_df() -> pd.DataFrame:
    return db.query_df("SELECT id, name, prefix, scopes, allowed_ips, warehouse_ids, active, last_used_at, created_by, created_at, "
                       "revoked_at FROM api_keys ORDER BY active DESC, id DESC")

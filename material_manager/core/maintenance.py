"""점검(읽기 전용) 모드: DB 옮기기·백업 복구·대량 정리처럼 데이터를 멈춰야 할 때 조회만 열어 둔다.

- 켜면 모든 화면 위에 '🛠️ 시스템 점검 중' 띠가 나오고, 저장·변경(POST)은 막힌다(로그인·로그아웃·알림 읽음·이 모드 끄기는 허용).
  API 쓰기는 503. 오프라인 대기열은 503을 받으면 보내지 않고 기다린다.
- app_settings 'read_only' 에 둔다 → 서버 여러 대가 함께 따른다(5초 캐시). 켜고 끈 일은 감사로그에.
- 켜기·끄기: 관리자 → 🏢 회사 설정, 또는 `flask --app app read-only on --reason "..."` / `off`.
"""
from __future__ import annotations

import json
import time

from core import audit, db
from core.utils import now_str

KEY = "read_only"
ALLOWED = {"auth.login", "auth.logout", "auth.sso_login", "auth.sso_callback", "notifications.read", "admin.read_only",
           "static", "health", "readyz", "metrics"}
_cache = {"at": 0.0, "value": None}


def state(cache_seconds: float = 5) -> dict | None:
    """켜져 있으면 {reason, by, at}, 아니면 None."""
    if time.time() - _cache["at"] < cache_seconds:
        return _cache["value"]
    try:
        raw = db.scalar("SELECT value FROM app_settings WHERE key = ?", (KEY,))
        value = json.loads(raw) if raw else None
    except Exception:
        value = None
    value = value if value and value.get("on") else None
    _cache.update(at=time.time(), value=value)
    return value


def set_mode(on: bool, reason: str, actor: dict | None) -> str:
    who = (actor or audit.SYSTEM)["name"]
    value = json.dumps({"on": on, "reason": reason.strip()[:200], "by": who, "at": now_str()}, ensure_ascii=False)
    with db.transaction() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (KEY, value))
        audit.record(conn, actor, "READ_ONLY", "settings", KEY, {"on": on, "reason": reason})
    _cache["at"] = 0
    return "점검(읽기 전용) 모드를 켰습니다 — 저장·변경이 막힙니다." if on else "점검 모드를 껐습니다 — 다시 저장할 수 있습니다."

"""사용자별 화면 설정 — 사이드바 메뉴 순서·즐겨찾기.

값은 user_prefs(user_id, key, value)에 JSON으로 둔다. 시연 서버(SALES_DEMO_AUTOLOGIN)는 방문자가 같은 계정을 함께 쓰므로
DB 대신 그 방문자의 세션(쿠키)에 둔다 → 다른 방문자의 메뉴가 바뀌지 않는다.
"""
from __future__ import annotations

import json

from flask import has_request_context, session

import config

from .database import get_conn

MENU_KEY = "menu"


def _demo() -> bool:
    return bool(config.DEMO_AUTOLOGIN) and has_request_context()


def menu(user_id: int) -> dict:
    """{"order": [메뉴키...], "fav": [메뉴키...]} — 정한 적 없으면 빈 목록(기본 순서)."""
    if _demo():
        raw = session.get("menu_prefs")
    else:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND key = ?",
                               (user_id, MENU_KEY)).fetchone()
        raw = row[0] if row else None
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        data = {}
    out = {"order": [], "fav": []}
    for k in out:
        v = data.get(k) if isinstance(data, dict) else None
        out[k] = [str(x) for x in v][:50] if isinstance(v, list) else []
    return out


def save_menu(user_id: int, order: list[str], fav: list[str], allowed: set[str]) -> dict:
    """순서·즐겨찾기 저장. 모르는 키·중복은 버린다. 둘 다 비우면 기본값으로 돌아간다."""
    seen: set[str] = set()
    order = [k for k in order if k in allowed and not (k in seen or seen.add(k))]
    fav = [k for k in dict.fromkeys(fav) if k in allowed]
    value = json.dumps({"order": order, "fav": fav}, ensure_ascii=False)
    if _demo():
        if order or fav:
            session["menu_prefs"] = value
        else:
            session.pop("menu_prefs", None)
        return {"order": order, "fav": fav}
    with get_conn() as conn:
        if order or fav:
            conn.execute("INSERT INTO user_prefs (user_id, key, value) VALUES (?, ?, ?) "
                         "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", (user_id, MENU_KEY, value))
        else:
            conn.execute("DELETE FROM user_prefs WHERE user_id = ? AND key = ?", (user_id, MENU_KEY))
    return {"order": order, "fav": fav}

"""앞단 전달 (MM_FORWARD_URL): 실제 서버가 살아 있으면 방문자를 그쪽으로 보낸다.

포트폴리오 구성: 고정 주소(Render)는 이 기능만 켜 두고, 데이터는 개인 PC 서버(Cloudflare Tunnel)에 저장한다.
PC가 켜져 있으면 → PC로 이동(저장됨). 꺼져 있으면 → 이 서버가 시연 데이터로 그대로 응답(저장 안 됨).
상태 확인은 /health 를 짧게 부르고 결과를 잠깐 기억한다(방문할 때마다 부르지 않게).
"""
from __future__ import annotations

import time
import urllib.request

from flask import redirect, request

import config

SKIP = {"static", "health", "favicon", "service_worker"}
UP_TTL, DOWN_TTL, TIMEOUT = 30, 15, 3
_state = {"url": "", "up": False, "at": 0.0}


def target_up() -> bool:
    url, now = config.FORWARD_URL, time.time()
    if _state["url"] == url and now - _state["at"] < (UP_TTL if _state["up"] else DOWN_TTL):
        return _state["up"]
    up = False
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/health", headers={"User-Agent": "mm-forward"}),
                                    timeout=TIMEOUT) as res:
            up = res.status == 200
    except (OSError, ValueError):
        up = False
    _state.update(url=url, up=up, at=now)
    return up


def redirect_if_up():
    if not config.FORWARD_URL or request.method not in ("GET", "HEAD") or request.endpoint in SKIP:
        return None
    if not target_up():
        return None
    path = request.full_path if request.query_string else request.path
    return redirect(config.FORWARD_URL + path, code=302)

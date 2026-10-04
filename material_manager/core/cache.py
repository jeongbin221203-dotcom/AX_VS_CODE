"""짧은 계산 결과 보관 (자재·거래가 많을 때 대시보드·사이드바가 매번 전체를 다시 세지 않게).

- 프로세스 안에만 둔다. 이 서버에서 저장(POST 등)이 성공하면 모두 비운다(app.after_request → bump)
  → 내가 바꾼 것은 바로 보인다. 다른 서버에서 바꾼 것은 길어야 ttl 초 뒤에 보인다(화면에 '최대 1분 전 기준').
- 업무 판정(재고 부족·마감·권한)에는 쓰지 않는다 — 화면 표시용 집계만.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

ENABLED = {"on": True}                       # 테스트(app.testing)는 끈다 — 테스트마다 DB를 새로 만들기 때문
_store: dict = {}
_lock = threading.Lock()
MAX_KEYS = 500


def wh_key(wh_ids) -> tuple | None:
    return None if wh_ids is None else tuple(sorted(int(w) for w in wh_ids))


def memo(key: tuple, ttl: float, fn: Callable[[], Any]) -> Any:
    if not ENABLED["on"] or ttl <= 0:
        return fn()
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()                                   # 계산은 잠금 밖에서 (느린 계산이 다른 요청을 막지 않게)
    with _lock:
        if len(_store) >= MAX_KEYS:
            _store.clear()
        _store[key] = (now, value)
    return value


def bump() -> None:
    """데이터가 바뀌었다 — 보관한 것을 버린다 (키가 'slow' 로 시작하는 무거운 집계는 ttl 까지 둔다)."""
    with _lock:
        for k in [k for k in _store if not (k and k[0] == "slow")]:
            del _store[k]

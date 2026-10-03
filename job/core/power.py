"""수집하는 동안 Windows 가 잠들지 않게 (절전 설정은 바꾸지 않음).

Windows 의 SetThreadExecutionState 로 "이 스레드가 일하는 중"이라고 알린다. 화면은 꺼져도 되고,
요청을 거두면(또는 앱이 꺼지면) 원래 절전 설정대로 돌아간다. 요청은 스레드마다 따로라서
수집을 실제로 돌리는 스레드(예약 스레드·'지금 실행' 스레드)에서 불러야 한다. Windows 가 아니면 아무것도 안 함.
"""
from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger("job.power")

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_state = threading.local()


def keep_awake(on: bool) -> bool:
    """on=True: 이 스레드가 거둘 때까지 잠들지 않게. 바뀌었으면 True."""
    if sys.platform != "win32":
        return False
    if getattr(_state, "on", False) == on:
        return False
    import ctypes
    flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if on else 0)
    if not ctypes.windll.kernel32.SetThreadExecutionState(flags):
        log.warning("SetThreadExecutionState 실패")
        return False
    _state.on = on
    log.info("PC 잠들기 %s", "막음 (수집 중)" if on else "허용 (원래 절전 설정)")
    return True


def is_awake_held() -> bool:
    return bool(getattr(_state, "on", False))

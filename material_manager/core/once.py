"""같은 화면 제출을 두 번 처리하지 않는다 (두 번 클릭 · 느린 네트워크에서 다시 누름 · 브라우저 재전송).

화면의 모든 POST 폼에는 csrf() 매크로가 한 번 쓰는 표(_once)를 넣는다. 요청이 오면
  1) 표를 DB에 '처리 중'으로 먼저 넣는다. 이미 있으면 같은 제출이 또 온 것 → 처리하지 않는다.
  2) 응답이 이동(3xx, 처리 성공 후 결과 화면으로)이면 '완료'로 남기고 이동한 주소를 적어 둔다.
     같은 표가 다시 오면 그 주소로 보내 결과를 보여 준다.
  3) 그 밖의 응답(입력 오류로 화면을 다시 그림, 4xx·5xx)은 표를 지운다 → 고쳐서 다시 제출할 수 있다.
     다시 그린 화면에는 새 표가 들어간다.
서버가 처리 도중 죽어 '처리 중'으로 남은 표는 다시 처리하지 않는다(중복 등록보다 확인 요청이 안전).
표는 DB에 두므로 서버가 여러 대여도 같다.
"""

import re
import secrets
from datetime import datetime, timedelta

import config
from core import db
from core.utils import now_str

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def new_token() -> str:
    return secrets.token_hex(16)


def valid(token: str) -> bool:
    return bool(token and TOKEN_RE.match(token))


def claim(token: str, user_id: int | None) -> tuple[bool, str, str]:
    """(처음 온 표인가, 이미 있던 표의 상태 RUN|DONE, 완료 때 이동한 주소)."""
    with db.transaction() as conn:
        cur = conn.execute("INSERT INTO form_once (token, user_id, status, location, created_at) "
                           "VALUES (?, ?, 'RUN', '', ?) ON CONFLICT (token) DO NOTHING",
                           (token, user_id, now_str()))
        if cur.rowcount == 1:
            return True, "", ""
        row = conn.execute("SELECT status, location FROM form_once WHERE token = ?", (token,)).fetchone()
    return False, (row["status"] if row else "RUN"), (row["location"] if row else "")


def finish(token: str, done: bool, location: str = "") -> None:
    with db.transaction() as conn:
        if done:
            conn.execute("UPDATE form_once SET status = 'DONE', location = ? WHERE token = ?",
                         (location[:500], token))
        else:
            conn.execute("DELETE FROM form_once WHERE token = ?", (token,))


OFFLINE = "offline-queue"                 # 오프라인 대기열로 반영한 표의 location (오래 보관)


def cleanup() -> int:
    """화면 제출 표는 FORM_ONCE_KEEP_HOURS, 오프라인 대기열 표는 OFFLINE_KEEP_DAYS 뒤에 지운다."""
    now = datetime.now()
    cutoff = (now - timedelta(hours=config.FORM_ONCE_KEEP_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    offline_cutoff = (now - timedelta(days=config.OFFLINE_KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db.transaction() as conn:
        n = conn.execute("DELETE FROM form_once WHERE created_at < ? AND location <> ?", (cutoff, OFFLINE)).rowcount
        return n + conn.execute("DELETE FROM form_once WHERE created_at < ? AND location = ?", (offline_cutoff, OFFLINE)).rowcount

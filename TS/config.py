"""TS 영어 시험 학습 앱 설정 (환경변수로 바꿀 수 있음)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(os.environ.get("TS_DB_PATH", BASE_DIR / "data" / "ts.db"))
CONTENT_DIR = Path(os.environ.get("TS_CONTENT_DIR", BASE_DIR / "content" / "toeic"))
PORT = int(os.environ.get("TS_PORT", "5003"))          # 대한사료 5000, 영업 5001, 자재 5002와 분리
HOST = os.environ.get("TS_HOST", "127.0.0.1")          # 같은 와이파이의 휴대폰에서 접속하려면 TS_HOST=0.0.0.0
DEBUG = os.environ.get("TS_DEBUG", "0") == "1"


def _secret_key() -> str:
    """세션 서명 키. 환경변수가 없으면 data/ 에 한 번 만들어 두고 계속 쓴다."""
    if os.environ.get("TS_SECRET_KEY"):
        return os.environ["TS_SECRET_KEY"]
    key_file = DB_PATH.parent / ".secret_key"
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_hex(32)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, encoding="utf-8")
        return key


SECRET_KEY = _secret_key()
SESSION_COOKIE_NAME = "ts_session"
SESSION_COOKIE_SAMESITE = "Lax"
JSON_AS_ASCII = False

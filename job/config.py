"""채용공고 적합성 확인 앱 설정 (환경변수로 바꿀 수 있음)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(os.environ.get("JOB_DB_PATH", BASE_DIR / "data" / "job.db"))
SAMPLE_PATH = BASE_DIR / "content" / "sample_postings.json"
PORT = int(os.environ.get("JOB_PORT", "5004"))          # 대한사료 5000, 영업 5001, 자재 5002, TS 5003과 분리
HOST = os.environ.get("JOB_HOST", "127.0.0.1")
DEBUG = os.environ.get("JOB_DEBUG", "0") == "1"

# ── 공고 출처 ──────────────────────────────────────────────
# 사람인 Open API: https://oapi.saramin.co.kr  (access-key 발급 필요, 하루 호출 수 제한)
SARAMIN_KEY = os.environ.get("JOB_SARAMIN_KEY", "")
SARAMIN_URL = os.environ.get("JOB_SARAMIN_URL", "https://oapi.saramin.co.kr/job-search")
# 고용24(옛 워크넷) 채용정보 Open API (authKey 발급 필요). 주소가 바뀌면 환경변수로 교체.
WORK24_KEY = os.environ.get("JOB_WORK24_KEY", "")
WORK24_URL = os.environ.get(
    "JOB_WORK24_URL", "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do")
# 원티드: 공개 API가 아니라 웹이 쓰는 JSON. 약관을 확인하고 직접 켤 때만 사용.
WANTED_ENABLED = os.environ.get("JOB_WANTED_ENABLED", "0") == "1"
WANTED_URL = os.environ.get("JOB_WANTED_URL", "https://www.wanted.co.kr/api/v4/jobs")

HTTP_TIMEOUT = float(os.environ.get("JOB_HTTP_TIMEOUT", "15"))
MAX_PAGES = int(os.environ.get("JOB_MAX_PAGES", "3"))    # 한 번 수집할 때 출처별 최대 페이지
USER_AGENT = "job-fit-checker/1.0 (personal use)"

# ── 정기 크롤링 (켜기·검색어·간격은 화면 '공고 수집 → 자동 수집'에서) ──
CRAWL_DELAY = float(os.environ.get("JOB_CRAWL_DELAY", "3"))      # 같은 실행 안에서 요청 사이 최소 간격(초)
# 앱 안에서 예약 실행 스레드를 띄울지. 별도 작업(crawl.py --loop, 작업 스케줄러)으로 돌리면 0
START_SCHEDULER = os.environ.get("JOB_SCHEDULER", "1") == "1"


def _secret_key() -> str:
    """세션 서명 키. 환경변수가 없으면 data/ 에 한 번 만들어 두고 계속 쓴다."""
    if os.environ.get("JOB_SECRET_KEY"):
        return os.environ["JOB_SECRET_KEY"]
    key_file = DB_PATH.parent / ".secret_key"
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_hex(32)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, encoding="utf-8")
        return key


SECRET_KEY = _secret_key()
# Render 사본 모드: 수집은 하지 않고, 내 PC 원본이 올려 주는 DB 를 보여 준다 (core/sync.py)
MIRROR = os.environ.get("JOB_MIRROR", "0") == "1"
SYNC_TOKEN = os.environ.get("JOB_SYNC_TOKEN", "")        # 원본이 올릴 때 쓰는 열쇠 (사본 쪽)
SESSION_COOKIE_SECURE = os.environ.get("JOB_COOKIE_SECURE", "0") == "1"   # https 배포에서 1
SESSION_COOKIE_NAME = "job_session"
SESSION_COOKIE_SAMESITE = "Lax"
MAX_CONTENT_LENGTH = 5 * 1024 * 1024                   # CSV/엑셀 업로드 5MB
JSON_AS_ASCII = False

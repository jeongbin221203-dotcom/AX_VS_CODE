"""애플리케이션 전역 설정.

환경변수로 덮어쓸 수 있는 값
  MM_DATABASE_URL    postgresql://user:pw@host:5432/db 이면 PostgreSQL(여러 서버 운영). 비우면 SQLite
  MM_DB_PATH         SQLite 파일 경로 (기본: data/materials.db). 한 서버·시연용
  MM_STORAGE         local | s3   증빙·업로드 파일 저장소 (여러 서버면 s3 또는 공유 NAS 경로의 local)
  MM_STORAGE_DIR     local 저장소 폴더 (기본: DB 옆)
  MM_S3_BUCKET / MM_S3_PREFIX / MM_S3_ENDPOINT / MM_S3_REGION   s3 저장소 (MinIO 등 S3 호환 가능)
  MM_SECRET_KEY      세션 서명 키. 비워 두면 기동할 때마다 새로 만들어 재시작 시 모두 로그아웃된다.
  MM_HOST / MM_PORT / MM_DEBUG  개발 서버 설정 (디버그는 기본 꺼짐, 127.0.0.1에서만 켤 수 있음)
  MM_COOKIE_SECURE   1이면 세션 쿠키를 HTTPS에서만 보낸다 (운영에서 HTTPS 뒤에 둘 때 반드시 1)
  MM_TRUST_PROXY     1이면 리버스 프록시가 넘긴 X-Forwarded-For/Proto를 믿는다 (프록시 뒤에 둘 때만)
  MM_IDLE_MINUTES    이 시간 동안 아무 요청이 없으면 자동 로그아웃 (기본 30분)
  MM_PW_ITERATIONS   비밀번호 해시 반복 횟수 (테스트에서만 낮춘다)
  MM_SAP_MODE        off | mock | http   SAP 전송 방식 (core/sap.py)
  MM_SAP_ENDPOINT    http 모드에서 전송할 사내 연계서버(EAI · SAP Integration Suite 등) 주소
  MM_SAP_TOKEN       http 모드 인증 토큰 (Authorization: Bearer)
"""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
# ── DB ───────────────────────────────────────────────────────
DATABASE_URL = os.getenv("MM_DATABASE_URL", "")
DB_PATH = Path(os.getenv("MM_DB_PATH", DATA_DIR / "materials.db"))
DB_POOL_SIZE = int(os.getenv("MM_DB_POOL_SIZE", "10"))     # PostgreSQL 서버 1대(프로세스)당 커넥션 수

# ── 파일 저장소 (core/storage.py) ─────────────────────────────
# 키: attachments/<파일>, uploads/<토큰>.json. local이면 STORAGE_DIR 아래 같은 경로에 둔다.
STORAGE = os.getenv("MM_STORAGE", "local")
STORAGE_DIR = Path(os.getenv("MM_STORAGE_DIR", DB_PATH.parent))
S3_BUCKET = os.getenv("MM_S3_BUCKET", "")
S3_PREFIX = os.getenv("MM_S3_PREFIX", "material-manager/")
S3_ENDPOINT = os.getenv("MM_S3_ENDPOINT", "") or None
S3_REGION = os.getenv("MM_S3_REGION", "ap-northeast-2")

APP_TITLE = "자재관리 시스템"
APP_ICON = "📦"

# Flask
SECRET_KEY_FROM_ENV = bool(os.getenv("MM_SECRET_KEY"))
SECRET_KEY = os.getenv("MM_SECRET_KEY") or secrets.token_hex(32)
MAX_CONTENT_LENGTH = 20 * 1024 * 1024      # 업로드 최대 20MB
HOST = os.getenv("MM_HOST", "127.0.0.1")
PORT = int(os.getenv("MM_PORT", "5002"))   # 대한사료(5000) · 영업관리(5001)와 겹치지 않게
# 디버그 모드는 브라우저에서 파이썬 코드를 실행할 수 있는 디버거를 연다 → 기본은 끈다.
DEBUG = os.getenv("MM_DEBUG", "0") == "1"
TRUST_PROXY = os.getenv("MM_TRUST_PROXY", "0") == "1"
SESSION_COOKIE_NAME = "mm_session"
SESSION_COOKIE_HTTPONLY = True               # 스크립트에서 쿠키를 읽지 못하게
SESSION_COOKIE_SAMESITE = "Lax"              # 다른 사이트에서 보낸 POST에는 쿠키를 싣지 않게
SESSION_COOKIE_SECURE = os.getenv("MM_COOKIE_SECURE", "0") == "1"
PERMANENT_SESSION_LIFETIME = 8 * 60 * 60     # 로그인 후 최대 8시간
IDLE_MINUTES = int(os.getenv("MM_IDLE_MINUTES", "30"))

# ── 사용자 · 권한 ─────────────────────────────────────────────
# 코드: (등급, 화면 표기). 등급이 높을수록 아래 등급의 권한을 모두 가진다.
ROLES = {
    "VIEWER": (1, "조회"),          # 조회 · 다운로드
    "CLERK": (2, "담당자"),         # + 입출고 등록 · 증빙 등록/연결
    "MANAGER": (3, "관리자"),       # + 자재 마스터 · 거래 취소 · 월 마감 · 증빙 삭제 · SAP 재전송
    "ADMIN": (4, "시스템관리자"),   # + 사용자 · 감사로그 · 데이터 관리 · 마감 해제
}
PW_ITERATIONS = int(os.getenv("MM_PW_ITERATIONS", "600000"))
PW_MIN_LENGTH = 8
LOGIN_MAX_FAILS = 5                        # 연속 실패 시 잠금
LOGIN_LOCK_MINUTES = 15
LOGIN_IP_MAX_FAILS = 20                    # 한 IP에서 15분 안에 이만큼 실패하면 그 IP의 로그인 시도를 막는다

# ── 거래 ──────────────────────────────────────────────────────
TX_LABEL = {"IN": "입고", "OUT": "출고", "ADJ": "조정"}

# 자재 마스터 컬럼 코드 ↔ 엑셀 헤더 (순서 = 업로드 반영 순서)
MATERIAL_COLS = {
    "code": "자재코드",
    "name": "자재명",
    "spec": "규격",
    "unit": "단위",
    "category": "분류",
    "safety_stock": "안전재고",
    "unit_price": "단가",
    "location": "보관위치",
    "supplier": "공급처",
    "sap_matnr": "SAP자재번호",        # SAP 플랜트·저장위치는 창고(조직 화면)에 둔다
}

# ── SAP 연동 ──────────────────────────────────────────────────
SAP_MODE = os.getenv("MM_SAP_MODE", "off")           # off | mock | http
SAP_ENDPOINT = os.getenv("MM_SAP_ENDPOINT", "")
SAP_TOKEN = os.getenv("MM_SAP_TOKEN", "")
SAP_TIMEOUT = 15
SAP_MAX_ATTEMPTS = 5                                  # 자동 재시도 한도. 넘으면 FAILED → 수동 재전송
# 이동유형은 회사 SAP 설정(커스터마이징)마다 다를 수 있다 → SAP 담당 팀과 확인 후 여기만 바꾼다.
SAP_MOVEMENT_TYPES = {
    "IN_PO": "101",      # 구매오더 입고
    "IN_NO_PO": "501",   # 구매오더 없는 입고
    "OUT": "201",        # 원가센터 출고
    "ADJ_PLUS": "701",   # 재고실사 차이(증가)
    "ADJ_MINUS": "702",  # 재고실사 차이(감소)
    "TRF_SLOC": "311",   # 같은 플랜트 안 저장위치 이전
    "TRF_PLANT": "301",  # 플랜트 간 이전
}
SAP_REVERSAL_TYPES = {"101": "102", "501": "502", "201": "202", "701": "702", "702": "701",
                      "311": "312", "301": "302"}
SAP_STATUS = {
    "PENDING": "전송 대기", "SENDING": "전송 중", "SENT": "전기 완료", "ERROR": "오류(자동 재시도)",
    "FAILED": "실패(조치 필요)", "CANCELLED": "전송 전 취소",
}

# ── SAP 마스터 동기화 ─────────────────────────────────────────
SAP_MASTER_SYNC = os.getenv("MM_SAP_MASTER_SYNC", "0") == "1"     # 배치로 자재·원가센터를 SAP에서 받아온다
SAP_MASTER_READONLY = os.getenv("MM_SAP_MASTER_READONLY", "1") == "1"   # 동기화된 항목은 화면에서 못 고친다

# ── 재고 평가 ─────────────────────────────────────────────────
VALUATION_METHODS = {"MAVG": "이동평균", "FIFO": "선입선출"}
VALUATION_DEFAULT = os.getenv("MM_VALUATION", "MAVG")

# ── 구매 ──────────────────────────────────────────────────────
# 구매요청 금액별 필요한 결재 단계 수 (금액 이하 → 단계). 마지막 단계는 시스템관리자(또는 두 번째 관리자).
PR_APPROVAL_TIERS = [(1_000_000, 1), (10_000_000, 2), (float("inf"), 3)]
PO_OVER_PR_TOLERANCE = 0.10          # 발주 금액이 요청 승인 금액보다 10% 넘게 크면 발주도 결재
GR_OVER_TOLERANCE = 0.0              # 발주 수량 초과 입고 허용 비율

# ── 사내 SSO (OIDC) · 2단계 인증 ─────────────────────────────
SSO_ENABLED = os.getenv("MM_SSO_ENABLED", "0") == "1"
SSO_ISSUER = os.getenv("MM_SSO_ISSUER", "")                 # 예: https://login.microsoftonline.com/<tenant>/v2.0
SSO_CLIENT_ID = os.getenv("MM_SSO_CLIENT_ID", "")
SSO_CLIENT_SECRET = os.getenv("MM_SSO_CLIENT_SECRET", "")
SSO_REDIRECT_URI = os.getenv("MM_SSO_REDIRECT_URI", "")     # 예: https://mm.사내/sso/callback
SSO_GROUPS_CLAIM = os.getenv("MM_SSO_GROUPS_CLAIM", "groups")
# IdP 그룹 → 역할. "그룹=역할,그룹=역할" (여러 그룹이면 가장 높은 역할). 맞는 그룹이 없으면 로그인 거부.
SSO_ROLE_MAP = dict(kv.split("=", 1) for kv in os.getenv("MM_SSO_ROLE_MAP", "").split(",") if "=" in kv)
SSO_ONLY = os.getenv("MM_SSO_ONLY", "0") == "1"             # 1이면 비밀번호 로그인은 비상용 시스템관리자만
SSO_REQUIRE_MFA = os.getenv("MM_SSO_REQUIRE_MFA", "0") == "1"   # 1이면 ID 토큰 amr에 mfa가 있어야 로그인
# 새 사용자(직접 등록·SSO 첫 로그인)의 창고 범위. 기본은 '없음' → 관리자가 범위를 줘야 데이터가 보인다.
NEW_USER_ALL_WAREHOUSES = os.getenv("MM_NEW_USER_ALL_WAREHOUSES", "0") == "1"
MFA_REQUIRED_ROLES = set(filter(None, os.getenv("MM_MFA_REQUIRED_ROLES", "ADMIN").split(",")))
TOTP_ISSUER = "자재관리"

# ── 직무 분리 · 결재 ──────────────────────────────────────────
SOD_ENFORCE = os.getenv("MM_SOD_ENFORCE", "1") == "1"          # 본인 등록 거래 취소 금지 등
ADJ_APPROVAL_AMOUNT = int(os.getenv("MM_ADJ_APPROVAL_AMOUNT", "500000"))  # 실사 조정 금액이 이 이상이면 결재

# ── 목록 · 배치 ───────────────────────────────────────────────
PAGE_SIZE = 100
EXPORT_MAX_ROWS = 100_000                  # 엑셀 한 번에 내려받는 최대 행
JOB_LEASE_SECONDS = 300                    # 배치 작업 잠금 유효 시간 (서버가 죽어도 이 시간 뒤 다른 서버가 이어받음)

# ── 증빙 ──────────────────────────────────────────────────────
DOC_TYPES = {
    "E_TAX_INVOICE": "전자세금계산서",
    "TAX_INVOICE": "세금계산서(종이)",
    "INVOICE": "계산서(면세)",
    "STATEMENT": "거래명세서",
    "ETC": "기타 증빙",
}
DOC_NEED_BIZ_NO = {"E_TAX_INVOICE", "TAX_INVOICE", "INVOICE"}   # 공급자 사업자번호·공급가액 필수
DOC_VAT = {"E_TAX_INVOICE", "TAX_INVOICE"}                        # 부가세 10% 검산 대상
DOC_MAX_BYTES = 10 * 1024 * 1024                                  # 증빙 파일 1건 최대 10MB

# ── 업로드 방어 ──────────────────────────────────────────────
UPLOAD_MAX_ROWS = 20000                    # 자재 일괄 업로드 최대 행 수
XLSX_MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 엑셀(zip) 압축 해제 후 최대 크기 — 압축 폭탄 방어
XLSX_MAX_RATIO = 100                       # 압축률이 이보다 크면 거부
UPLOAD_KEEP_HOURS = 24                     # 반영하지 않은 업로드 미리보기 파일 보관 시간

DEFAULT_UNIT = "EA"
DEFAULT_CATEGORY = "미분류"

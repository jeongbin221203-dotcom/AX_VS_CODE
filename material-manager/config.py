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
  MM_FORWARD_URL     이 주소의 서버가 살아 있으면 방문자를 그쪽으로 보낸다 (꺼져 있으면 이 서버가 응답)
  MM_DEMO            1이면 포트폴리오 시연 모드 — 방문자를 시연용 시스템관리자로 자동 로그인 (운영에서는 끌 것)
  MM_PW_ITERATIONS   비밀번호 해시 반복 횟수 (테스트에서만 낮춘다)
  MM_ERP_MODE        off | mock | http | sap_odata | sap_rfc | rest | file   ERP·SAP 연결 방식 (core/erp.py,
                     예전 이름 MM_SAP_MODE도 읽는다). 방식별 설정(MM_SAP_* · MM_ERP_*)은 .env.example
  MM_BACKUP_DIR      자동 백업 폴더 (기본: DB 옆 backups — 운영은 다른 디스크·NAS 권장)
  MM_BACKUP_HOURS / MM_BACKUP_KEEP   자동 백업 주기(시간, 0이면 끔) · 보관 개수
  MM_PG_DUMP / MM_PG_RESTORE   PostgreSQL 백업에 쓸 pg_dump · pg_restore 경로 (비우면 PATH)
  MM_STORAGE_SPOOL_DIR   s3 저장소에 연결할 수 없을 때 파일을 임시로 두는 이 서버의 폴더
"""

import os
import secrets
import shutil
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    """숫자 환경변수. 잘못 넣으면 기동이 멈추지 않게 기본값을 쓴다."""
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


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
# s3에 연결할 수 없을 때(인터넷·클라우드 장애) 파일을 이 서버에 잠시 두고, 배치(storage_flush)가 나중에 올린다
STORAGE_SPOOL_DIR = Path(os.getenv("MM_STORAGE_SPOOL_DIR", DB_PATH.parent / "spool"))

# ── 자동 백업 (SQLite) ────────────────────────────────────────
# 배치(db_backup)가 주기마다 DB를 온라인 백업 API로 복사하고 무결성을 확인한다. PostgreSQL은 DB 서버의 pg_dump·스냅샷.
BACKUP_DIR = Path(os.getenv("MM_BACKUP_DIR", DB_PATH.parent / "backups"))
BACKUP_HOURS = _int_env("MM_BACKUP_HOURS", 24)
BACKUP_KEEP = max(_int_env("MM_BACKUP_KEEP", 14), 1)
# PostgreSQL 백업: pg_dump · pg_restore 경로 (비우면 PATH에서 찾는다, 없으면 PostgreSQL 백업은 건너뜀)
PG_DUMP = os.getenv("MM_PG_DUMP") or shutil.which("pg_dump") or ""
PG_RESTORE = os.getenv("MM_PG_RESTORE") or shutil.which("pg_restore") or ""

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
# 프록시가 접속자 IP를 따로 넣어 주는 헤더 (예: Render·Cloudflare의 True-Client-IP). TRUST_PROXY일 때만 쓴다.
CLIENT_IP_HEADER = os.getenv("MM_CLIENT_IP_HEADER", "").strip()
SESSION_COOKIE_NAME = "mm_session"
SESSION_COOKIE_HTTPONLY = True               # 스크립트에서 쿠키를 읽지 못하게
SESSION_COOKIE_SAMESITE = "Lax"              # 다른 사이트에서 보낸 POST에는 쿠키를 싣지 않게
SESSION_COOKIE_SECURE = os.getenv("MM_COOKIE_SECURE", "0") == "1"
PERMANENT_SESSION_LIFETIME = 8 * 60 * 60     # 로그인 후 최대 8시간
IDLE_MINUTES = int(os.getenv("MM_IDLE_MINUTES", "30"))
# 포트폴리오 시연 모드: 방문자를 시연용 시스템관리자로 자동 로그인 + 빈 DB에 샘플 데이터 (core/demo.py). 운영에서는 끌 것
DEMO = os.getenv("MM_DEMO", "0") == "1"
# 앞단 전달: 이 주소의 서버(예: 개인 PC + Cloudflare Tunnel)가 살아 있으면 방문자를 그쪽으로 보낸다(core/forward.py).
# 꺼져 있으면 이 서버가 그대로 보여 준다. 고정 주소(Render)는 그대로 두고 실제 데이터는 PC에 저장할 때 쓴다.
FORWARD_URL = os.getenv("MM_FORWARD_URL", "").strip().rstrip("/")
# 감사로그를 서버 로그(표준출력)에도 한 줄 JSON으로 (시연 모드는 기본으로 켬)
AUDIT_STDOUT = os.getenv("MM_AUDIT_STDOUT", "1" if DEMO else "0") == "1"
DEMO_RESET_HOUR = int(os.getenv("MM_DEMO_RESET_HOUR", "4"))    # 시연 데이터 매일 자동 초기화 시각 (한국 시간)

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
# 연결 방식 (core/erp.py): off | mock | http(사내 연계서버) | sap_odata(S/4HANA API) | sap_rfc(BAPI) | rest(기타 ERP) | file
SAP_MODE = os.getenv("MM_ERP_MODE") or os.getenv("MM_SAP_MODE", "off")
SAP_ENDPOINT = os.getenv("MM_SAP_ENDPOINT", "")                 # http: 사내 연계서버 주소
SAP_TOKEN = os.getenv("MM_SAP_TOKEN", "")                       # http: Bearer 토큰
SAP_TIMEOUT = _int_env("MM_ERP_TIMEOUT", 15)
# SAP 직접 연결 (sap_odata · sap_rfc 공통)
SAP_CLIENT = os.getenv("MM_SAP_CLIENT", "")                     # SAP 클라이언트(맨더트), 예: 100
SAP_USER = os.getenv("MM_SAP_USER", "")                         # 통신 사용자(RFC·Basic 인증)
SAP_PASSWORD = os.getenv("MM_SAP_PASSWORD", "")
SAP_LANGUAGE = os.getenv("MM_SAP_LANGUAGE", "KO")
# sap_odata: S/4HANA (온프레미스는 https://s4.사내:44300, 클라우드는 https://my000000-api.s4hana.cloud.sap)
SAP_ODATA_URL = os.getenv("MM_SAP_ODATA_URL", "")
SAP_OAUTH_TOKEN_URL = os.getenv("MM_SAP_OAUTH_TOKEN_URL", "")    # 있으면 OAuth2 클라이언트 자격 증명, 없으면 Basic
SAP_OAUTH_CLIENT_ID = os.getenv("MM_SAP_OAUTH_CLIENT_ID", "")
SAP_OAUTH_CLIENT_SECRET = os.getenv("MM_SAP_OAUTH_CLIENT_SECRET", "")
SAP_ODATA_PRODUCT_PATH = os.getenv("MM_SAP_ODATA_PRODUCT_PATH", "/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product")
SAP_ODATA_COSTCENTER_PATH = os.getenv("MM_SAP_ODATA_COSTCENTER_PATH", "/sap/opu/odata/sap/API_COSTCENTER_SRV/A_CostCenter")
SAP_ODATA_STOCK_PATH = os.getenv("MM_SAP_ODATA_STOCK_PATH", "/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod")
SAP_CONTROLLING_AREA = os.getenv("MM_SAP_CONTROLLING_AREA", "")
# sap_rfc: ECC · S/4HANA (pyrfc + SAP NW RFC SDK). 앱 서버 직접(ASHOST+SYSNR) 또는 로드밸런싱(MSHOST+SYSID+GROUP)
SAP_RFC_ASHOST = os.getenv("MM_SAP_RFC_ASHOST", "")
SAP_RFC_SYSNR = os.getenv("MM_SAP_RFC_SYSNR", "")
SAP_RFC_MSHOST = os.getenv("MM_SAP_RFC_MSHOST", "")
SAP_RFC_SYSID = os.getenv("MM_SAP_RFC_SYSID", "")
SAP_RFC_GROUP = os.getenv("MM_SAP_RFC_GROUP", "")
# rest: 기타 ERP (Oracle · Dynamics · 더존 · 영림원 · 자체 ERP). 필드·주소는 매핑 파일(erp_maps/)
ERP_REST_URL = os.getenv("MM_ERP_REST_URL", "")
ERP_REST_MAP = os.getenv("MM_ERP_REST_MAP", "")                 # 예: erp_maps/generic_example.json
ERP_REST_AUTH = os.getenv("MM_ERP_REST_AUTH", "")               # bearer:토큰 | basic:아이디:비번 | header:이름:값
# file: 공유 폴더 파일 연계
ERP_FILE_DIR = os.getenv("MM_ERP_FILE_DIR", "")
ERP_FILE_FORMAT = os.getenv("MM_ERP_FILE_FORMAT", "json")       # json | csv
# 단위 코드 변환 (이 시스템 → ERP). 예: MM_ERP_UNIT_MAP="EA:ST,BOX:KAR"
ERP_UNIT_MAP = {k.strip(): v.strip() for k, v in
                (p.split(":", 1) for p in os.getenv("MM_ERP_UNIT_MAP", "").split(",") if ":" in p)}
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
# 이동유형 → SAP 거래 코드(GM_CODE): 01 구매오더 입고 · 03 출고 · 04 이전 · 05 기타 입고 · 06 취소
SAP_GM_CODES = {"101": "01", "501": "05", "201": "03", "701": "05", "702": "03", "311": "04", "301": "04",
                "561": "05", "551": "03"}
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

# ── 사내 SSO (OIDC) ───────────────────────────────────────────
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
SSO_OUTAGE_MAX_HOURS = 24                   # SSO 장애 모드(비밀번호 로그인 임시 허용) 최대 시간
# 새 사용자(직접 등록·SSO 첫 로그인)의 창고 범위. 기본은 '없음' → 관리자가 범위를 줘야 데이터가 보인다.
NEW_USER_ALL_WAREHOUSES = os.getenv("MM_NEW_USER_ALL_WAREHOUSES", "0") == "1"

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
FORM_ONCE_KEEP_HOURS = 48                  # 중복 제출 방지 기록 보관 시간

# 거래명세서 스캔 읽기 (core/statement_reader.py). 서버에 Tesseract OCR + 한국어 데이터(kor)가 있을 때만 쓴다.
TESSERACT_CMD = os.getenv("MM_TESSERACT_CMD", "")          # 비우면 PATH의 tesseract
OCR_LANG = os.getenv("MM_OCR_LANG", "kor+eng")

DEFAULT_UNIT = "EA"
DEFAULT_CATEGORY = "미분류"

"""애플리케이션 전역 설정 (환경변수로 덮어쓴다).

  SALES_ENV            development | production. production 이면 위험한 설정으로는 기동하지 않는다
  SALES_DB_PATH        SQLite 파일 경로 (기본: data/sales.db)
  SALES_AUTH_MODE      simple(개발 전용) | password | sso        → core/auth.py
  SALES_SSO_HEADER     sso 모드에서 인증된 사번을 담는 헤더 (기본 X-Remote-User)
  SALES_TRUSTED_PROXIES  그 헤더를 믿을 프록시 IP (쉼표 구분, 기본 127.0.0.1)
  SALES_SECRET_KEY     세션 서명 키 (production 필수)
  SALES_SESSION_MINUTES  미사용 시 자동 로그아웃 (기본 30분)
  SALES_HOST / SALES_PORT / SALES_DEBUG   개발 서버 설정
  SALES_ERP_ADAPTER 등 ERP 설정          → core/erp.py
  SALES_COMPANY_BIZ_NO  우리 회사 사업자번호 (세금계산서 공급자 확인용) → core/documents.py
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = Path(os.environ.get("SALES_BACKUP_DIR", DATA_DIR / "backups"))

APP_TITLE = "영업관리 시스템"
ENV = os.environ.get("SALES_ENV", "development")
PRODUCTION = ENV == "production"

SECRET_KEY = os.environ.get("SALES_SECRET_KEY") or ("" if PRODUCTION else secrets.token_hex(32))
MAX_CONTENT_LENGTH = 20 * 1024 * 1024      # 업로드 최대 20MB

# 세션: 30분 미사용 시 만료(요청마다 갱신), 스크립트 접근 차단, 운영은 HTTPS 전용 쿠키
SESSION_MINUTES = int(os.environ.get("SALES_SESSION_MINUTES", "30"))
PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_MINUTES)
SESSION_ABSOLUTE_HOURS = 12                # 계속 사용해도 12시간이 지나면 다시 로그인
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = PRODUCTION or os.environ.get("SALES_COOKIE_SECURE") == "1"

SSO_HEADER = os.environ.get("SALES_SSO_HEADER", "X-Remote-User")
TRUSTED_PROXIES = {p.strip() for p in os.environ.get("SALES_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
                   if p.strip()}

HOST = os.environ.get("SALES_HOST", "127.0.0.1")
PORT = int(os.environ.get("SALES_PORT", "5001"))   # 대한사료 사이트(5000)와 겹치지 않게
DEBUG = os.environ.get("SALES_DEBUG", "0" if PRODUCTION else "1") == "1"
# 개발은 켤 때 자동으로 스키마를 올리고, 운영은 배포 단계(manage.py db upgrade)에서만 올린다
AUTO_MIGRATE = os.environ.get("SALES_AUTO_MIGRATE", "0" if PRODUCTION else "1") == "1"
# 시연 서버(포트폴리오용 공개 서버): SALES_DEMO=1 일 때만 SALES_DEMO_AUTOLOGIN(사번)으로 로그인 없이 들어온다
DEMO = os.environ.get("SALES_DEMO") == "1"
DEMO_AUTOLOGIN = os.environ.get("SALES_DEMO_AUTOLOGIN", "").strip() if DEMO else ""
DEMO_ROLES = [("9999", "시스템관리자"), ("2001", "임원"), ("2002", "팀장"), ("2003", "영업사원")]   # 역할 바꿔 보기


def production_problems(auth_mode: str) -> list[str]:
    """운영 모드에서 기동을 막아야 하는 설정."""
    problems = []
    if not PRODUCTION:
        return problems
    if not os.environ.get("SALES_SECRET_KEY"):
        problems.append("SALES_SECRET_KEY 를 지정하세요 (재시작해도 세션이 유지되고 위조를 막습니다).")
    if auth_mode == "simple":
        problems.append("SALES_AUTH_MODE=simple(목록에서 계정 선택)은 운영에서 쓸 수 없습니다. password · sso · oidc 중 하나로 바꾸세요.")
    if DEBUG:
        problems.append("SALES_DEBUG=1 은 운영에서 쓸 수 없습니다 (오류 화면에 코드가 노출됩니다).")
    if os.environ.get("SALES_SAP_VERIFY_TLS", "1") != "1":
        problems.append("SALES_SAP_VERIFY_TLS=0(ERP 인증서 검증 끄기)은 운영에서 쓸 수 없습니다. 사내 CA 인증서를 신뢰 저장소에 넣으세요.")
    if os.environ.get("SALES_ETAX_ADAPTER") == "mock":
        problems.append("SALES_ETAX_ADAPTER=mock(가짜 승인번호)은 운영에서 쓸 수 없습니다. file 또는 rest 로 ASP 에 연결하세요.")
    if auth_mode == "oidc":
        from core import oidc
        problems += oidc.problems()
    if auth_mode not in ("password", "sso", "oidc", "simple"):
        problems.append(f"SALES_AUTH_MODE={auth_mode} 는 지원하지 않습니다 (password | sso | oidc).")
    if os.environ.get("SALES_SSO_USER"):
        problems.append("SALES_SSO_USER(개발용 고정 로그인)를 운영에서 지정하면 안 됩니다.")
    return problems

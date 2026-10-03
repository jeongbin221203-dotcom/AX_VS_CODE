"""운영 서버 실행 (waitress — Windows/Linux 공용 WSGI 서버).

    set SALES_ENV=production
    set SALES_SECRET_KEY=...           (고정값)
    set SALES_AUTH_MODE=sso            (또는 password)
    python serve.py

HTTPS 는 앞단 리버스 프록시(IIS·nginx·L4)에서 처리하고, 이 서버는 사내망 내부 포트로만 연다.
SSO 헤더는 직접 연결된 프록시 IP(SALES_TRUSTED_PROXIES)에서 온 요청만 믿으므로
waitress 의 X-Forwarded-For 치환 기능은 켜지 않는다.
"""
import os

from waitress import serve

import config
from app import create_app

if __name__ == "__main__":
    if config.DEMO:                     # 시연 서버: 미리 만든 샘플로 바로 열고, 오늘 기준 샘플은 뒤에서 만들어 바꿔 끼운다
        from core import database, demo_data
        demo_data.prepare()
        database.migrate()              # 같은 프로세스에서 (manage.py db upgrade 를 따로 띄우면 라이브러리를 두 번 읽어 느림)
        demo_data.start_background()
    serve(create_app(), host=config.HOST, port=config.PORT,
          threads=int(os.environ.get("SALES_THREADS", "8")),
          url_scheme="https" if config.PRODUCTION else "http")

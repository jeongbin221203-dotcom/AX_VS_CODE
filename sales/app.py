"""영업관리 시스템 (Sales CRM) - Flask 진입점

개발:  python app.py                 # http://127.0.0.1:5001
운영:  python serve.py               # waitress WSGI 서버 (SALES_ENV=production)
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
from logging.handlers import RotatingFileHandler

from flask import Flask, abort, jsonify, request, session

import config
from core import auth as core_auth
from core import database
from core import observability
from core import sales_db as db
from views import register_blueprints
from views.helpers import register_template_helpers


def _setup_logging(app: Flask) -> None:
    """앱·접근 로그. 운영은 JSON 한 줄 로그(요청 ID 포함). 컨테이너는 SALES_LOG_STDOUT=1 로 표준출력에도 쓴다."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(config.LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024,
                                                           backupCount=10, encoding="utf-8")]
    if os.environ.get("SALES_LOG_STDOUT") == "1":
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(observability.formatter())
        handler.addFilter(observability.RequestIdFilter())
        handler.setLevel(logging.INFO)
        for logger in (app.logger, logging.getLogger("sales"), logging.getLogger("core")):
            logger.addHandler(handler)
    for logger in (app.logger, logging.getLogger("sales"), logging.getLogger("core")):
        logger.setLevel(logging.INFO)
        logger.propagate = False


def create_app(test_config: dict | None = None) -> Flask:
    problems = config.production_problems(core_auth.AUTH_MODE)
    if problems:
        raise RuntimeError("운영 설정 오류 — 서버를 시작하지 않습니다:\n  - " + "\n  - ".join(problems))

    app = Flask(__name__)
    app.config.from_object(config)
    if os.environ.get("SALES_PROXY_FIX") == "1":
        # 앞단 프록시(nginx·L7) 1단계가 넘긴 X-Forwarded-For/Proto/Host 로 실제 클라이언트 IP·https 를 복원한다.
        # SSO 헤더 신뢰 판단은 직접 연결된 프록시 IP(원래 REMOTE_ADDR)로 한다 → views/helpers.sso_identity
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if test_config:
        app.config.update(test_config)

    if app.config.get("AUTO_MIGRATE"):
        db.init_db()                       # 개발: 켤 때 스키마를 자동으로 최신화
    else:
        current, head = database.current_revision(), database.head_revision()
        if current != head:
            raise RuntimeError(f"DB 스키마가 최신이 아닙니다 (현재 {current}, 최신 {head}). "
                               f"배포 단계에서 'python manage.py db upgrade' 를 먼저 실행하세요.")
    observability.init_app(app)            # 요청 ID·지표·읽기 전용 점검 — 다른 요청 훅보다 먼저
    register_blueprints(app)
    register_template_helpers(app)
    if not app.config.get("TESTING"):
        _setup_logging(app)

    @app.before_request
    def csrf_protect() -> None:
        """모든 POST 요청은 세션에 발급한 토큰과 일치해야 한다."""
        if request.method != "POST" or request.blueprint == "api":     # API 는 쿠키를 쓰지 않는다
            return
        token = session.get("_csrf")
        if not token or not secrets.compare_digest(token, request.form.get("_csrf", "")):
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'self'; frame-ancestors 'none'")
        if request.endpoint not in ("static",):
            response.headers.setdefault("Cache-Control", "no-store")   # 개인정보 화면을 브라우저에 남기지 않는다
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.route("/healthz")
    def healthz():
        """로드밸런서·모니터링용 상태 확인 (로그인 불필요, 업무 데이터 미노출)."""
        try:
            db._scalar("SELECT 1")
            return jsonify(status="ok")
        except Exception:   # noqa: BLE001
            app.logger.exception("healthz DB 확인 실패")
            return jsonify(status="db_error"), 503

    return app


if __name__ == "__main__":
    create_app().run(host=config.HOST, port=config.PORT, debug=config.DEBUG)

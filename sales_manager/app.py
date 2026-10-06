"""영업관리 시스템 (Sales CRM) - Flask 진입점

개발:  python app.py                 # http://127.0.0.1:5001
운영:  python serve.py               # waitress WSGI 서버 (SALES_ENV=production)
"""
from __future__ import annotations

import logging
import os
import secrets
import time
import sys
from logging.handlers import RotatingFileHandler

from flask import Flask, abort, flash, g, jsonify, redirect, request, send_from_directory, session

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
    if not app.config.get("TESTING"):
        from core.storage import start_flusher
        start_flusher()                             # S3 장애 때 이 서버가 임시 보관한 파일을 복구되면 올림
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
            abort(400, "요청이 만료되었습니다. 입력한 내용은 이 브라우저에 보관되어 있으니, 화면을 새로고침한 뒤 "
                       "'보관된 입력 불러오기'로 되살려 다시 저장하세요.")
        return _once(request.form.get("_submit_id", ""))

    def _once(submit_id: str):
        """같은 화면에서 보낸 같은 요청(두 번 클릭, 응답이 끊겨 다시 보냄)은 한 번만 처리한다."""
        if not submit_id:
            return None
        if len(submit_id) > 64 or not submit_id.replace("-", "").isalnum():
            abort(400, "잘못된 요청입니다.")
        with database.get_conn() as conn:
            fresh = conn.execute("INSERT OR IGNORE INTO form_submissions (submit_id, user_id, endpoint, created_at) "
                                 "VALUES (?,?,?,?)", (submit_id, session.get("user_id"), request.endpoint,
                                                      db._now())).rowcount
        if fresh:
            g.submit_id = submit_id
            return None
        flash("이미 처리된 요청입니다 — 같은 내용을 두 번 저장하지 않았습니다. 목록에서 결과를 확인하세요.", "warning")
        back = request.referrer or "/"
        return redirect(back if back.startswith(request.host_url) or back.startswith("/") else "/")

    @app.after_request
    def _bump_data_version(response):
        """저장(POST 성공)이 있으면 화면 집계 캐시(views.helpers.cached)를 새로 계산하게 한다."""
        if request.method == "POST" and response.status_code < 400:
            from views.helpers import CACHE_SECONDS, bump_data_version
            bump_data_version()
            # 서버가 여러 대면 다음 화면이 다른 서버로 갈 수 있다 → 저장한 사람은 잠시 캐시 없이 (본인 저장은 바로 보이게)
            session["_fresh_until"] = time.time() + CACHE_SECONDS
        return response

    @app.after_request
    def _release_submit(response):
        """처리에 실패한 요청(4xx·5xx)은 같은 화면에서 고쳐서 다시 보낼 수 있게 기록을 지운다."""
        sid = g.pop("submit_id", None)
        if sid and response.status_code >= 400:
            with database.get_conn() as conn:
                conn.execute("DELETE FROM form_submissions WHERE submit_id=?", (sid,))
        return response

    @app.teardown_request
    def _release_on_error(exc):
        sid = g.pop("submit_id", None)
        if sid and exc is not None:
            with database.get_conn() as conn:
                conn.execute("DELETE FROM form_submissions WHERE submit_id=?", (sid,))

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'self'; frame-ancestors 'none'")
        if request.endpoint not in ("static",):
            response.headers.setdefault("Cache-Control", "no-store")   # 개인정보 화면을 브라우저에 남기지 않는다
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.route("/sw.js")
    def service_worker():
        """서비스 워커는 사이트 전체(/)를 맡아야 해서 루트 주소로 낸다. 바뀌면 바로 반영되게 캐시하지 않는다."""
        res = send_from_directory(os.path.join(app.root_path, "static", "js"), "sw.js", mimetype="text/javascript")
        res.headers["Cache-Control"] = "no-cache"
        res.headers["Service-Worker-Allowed"] = "/"
        return res

    @app.route("/offline")
    def offline():
        """연결이 끊겼을 때 서비스 워커가 보여 주는 화면 (업무 데이터 없음)."""
        return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' "
                "content='width=device-width, initial-scale=1'><title>연결 끊김 · 영업관리</title>"
                "<link rel='stylesheet' href='/static/css/app.css'></head><body><main style='max-width:560px;margin:15vh auto;"
                "padding:16px'><h2>📡 서버에 연결할 수 없습니다</h2><p>인터넷이나 회사망 연결을 확인한 뒤 다시 시도하세요. "
                "입력하던 내용은 이 브라우저에 보관되어 있어, 연결이 돌아오면 같은 화면에서 이어서 저장할 수 있습니다.</p>"
                "<p><a href='/'>다시 시도</a></p></main></body></html>")

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

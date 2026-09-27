"""자재관리 시스템 - Flask 진입점

실행:
    pip install -r requirements.txt
    MM_SECRET_KEY=... python app.py            # http://127.0.0.1:5002
"""
from __future__ import annotations

import os
import secrets

from flask import Flask, abort, jsonify, request, session

import config
from core import auth, db, storage
from views import register_blueprints
from views.helpers import register_template_helpers

# 모든 화면에 붙이는 보안 헤더. 스크립트·스타일은 이 서버의 파일만 허용한다(인라인 스크립트 금지).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",              # 다른 사이트가 이 화면을 iframe에 넣어 클릭을 유도하지 못하게
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    if test_config:
        app.config.update(test_config)

    if app.config.get("TRUST_PROXY"):
        # 리버스 프록시(nginx 등) 뒤에서만 켠다. 켜지 않으면 X-Forwarded-For를 위조해 IP 제한을 피할 수 있다.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_db()
    register_blueprints(app)
    register_template_helpers(app)

    # 최초 설정(첫 시스템관리자 등록)은 서버 콘솔에 찍힌 코드를 아는 사람만 할 수 있다
    # → 설치 직후 먼저 접속한 아무나 관리자를 차지하는 것을 막는다.
    # 서버가 여러 대면 모두 같은 코드를 봐야 하므로 DB(app_settings)에 한 번만 만들어 둔다.
    if auth.count_users() == 0 and not app.config.get("SETUP_CODE"):
        with db.transaction() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('setup_code', ?) ON CONFLICT (key) DO NOTHING",
                         (os.getenv("MM_SETUP_CODE") or secrets.token_hex(8),))
            app.config["SETUP_CODE"] = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'setup_code'").fetchone()[0]
        app.logger.warning("최초 설정 코드: %s  (http://%s:%s/setup 에서 입력)",
                           app.config["SETUP_CODE"], config.HOST, config.PORT)
    if config.PW_ITERATIONS < 100_000 and not app.testing:
        app.logger.warning("MM_PW_ITERATIONS=%s 는 너무 낮습니다. 운영에서는 기본값(600000)을 쓰세요.",
                           config.PW_ITERATIONS)
    if not config.SECRET_KEY_FROM_ENV and not (app.debug or app.testing):
        app.logger.warning("MM_SECRET_KEY가 없어 임시 키를 씁니다. 재시작하면 모두 로그아웃됩니다.")

    @app.get("/health")
    def health():
        """로드밸런서·모니터링용. 로그인 없이 열리므로 버전·경로 같은 내부 정보는 내보내지 않는다."""
        checks = {}
        try:
            db.scalar("SELECT 1")
            checks["db"] = "ok"
        except Exception:
            checks["db"] = "error"
        try:
            storage.get().check()
            checks["storage"] = "ok"
        except Exception:
            checks["storage"] = "error"
        ok = all(v == "ok" for v in checks.values())
        return jsonify(status="ok" if ok else "error", **checks), (200 if ok else 503)

    @app.before_request
    def csrf_protect() -> None:
        """상태를 바꾸는 모든 요청은 세션에 발급한 토큰과 일치해야 한다."""
        if request.method in SAFE_METHODS:
            return
        token = session.get("_csrf")
        if not token or not secrets.compare_digest(token, request.form.get("_csrf", "")):
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    @app.after_request
    def security_headers(response):
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)       # 증빙 파일처럼 더 엄격하게 정한 응답은 그대로 둔다
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.endpoint != "static":
            # 로그인해야 보이는 화면·다운로드가 브라우저·프록시 캐시에 남지 않게
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


if __name__ == "__main__":
    debug = config.DEBUG
    if debug and not _loopback(config.HOST):
        print(f"경고: {config.HOST}로 열 때는 디버그 모드를 켤 수 없습니다(원격 코드 실행 위험). 디버그를 끄고 시작합니다.")
        debug = False
    create_app().run(host=config.HOST, port=config.PORT, debug=debug)

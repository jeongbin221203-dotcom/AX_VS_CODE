"""자재관리 시스템 - Flask 진입점

실행:
    pip install -r requirements.txt
    MM_SECRET_KEY=... python app.py            # 개발 서버 http://127.0.0.1:5002
    waitress-serve --port=5002 --call app:create_app   # 운영
    flask --app app batch --loop               # 배치 (운영 명령은 cli.py)
"""
from __future__ import annotations

import os
import secrets

from urllib.parse import urlsplit

from flask import Flask, abort, flash, g, jsonify, redirect, request, session

import config
from core import auth, db, once, storage
from cli import register_cli
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
    register_cli(app)                             # flask --app app batch / erp / init-db
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
    if not db.is_pg() and str(config.DB_PATH).startswith(("\\\\", "//")):
        # 네트워크 공유 폴더(SMB)의 SQLite는 잠금이 보장되지 않아 동시에 쓰면 DB가 깨질 수 있다
        app.logger.warning("SQLite DB가 네트워크 폴더(%s)에 있습니다. 로컬 디스크에 두거나 PostgreSQL을 쓰세요.",
                           config.DB_PATH)
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
            # degraded = S3에 닿지 않지만 이 서버의 임시 폴더로 업무는 계속됨 → 로드밸런서에서 빼지 않는다
            checks["storage"] = storage.get().check() or "ok"
        except Exception:
            checks["storage"] = "error"
        ok = all(v in ("ok", "degraded") for v in checks.values())
        return jsonify(status="ok" if ok else "error", **checks), (200 if ok else 503)

    @app.get("/favicon.ico")
    def favicon():
        """브라우저가 저절로 찾는 /favicon.ico — 로고(SVG)를 준다."""
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "logo.svg", mimetype="image/svg+xml", max_age=86400)

    @app.get("/sw.js")
    def service_worker():
        """오프라인용 서비스 워커. 사이트 전체 범위가 되도록 /static 이 아니라 / 아래에서 준다."""
        from flask import send_from_directory
        res = send_from_directory(app.static_folder, "js/sw.js", mimetype="text/javascript", max_age=0)
        res.headers["Cache-Control"] = "no-cache"
        return res

    @app.before_request
    def csrf_protect() -> None:
        """상태를 바꾸는 모든 요청은 세션에 발급한 토큰과 일치해야 한다."""
        if request.method in SAFE_METHODS:
            return
        token = session.get("_csrf")
        if not token or not secrets.compare_digest(token, request.form.get("_csrf", "")):
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    @app.before_request
    def once_guard():
        """같은 화면 제출이 두 번 오면(두 번 클릭·네트워크 재전송) 두 번째는 처리하지 않는다 (core/once.py)."""
        token = request.form.get("_once", "") if request.method == "POST" else ""
        if not once.valid(token):
            return None
        user = getattr(g, "user", None)
        first, status, location = once.claim(token, user["id"] if user else None)
        if first:
            g.once = token
            return None
        if request.headers.get("X-MM-Queue") == "1":       # 오프라인 대기열(브라우저 스크립트)은 JSON으로 답한다
            if status == "DONE":
                return jsonify(ok=True, duplicate=True, message="이미 반영된 입력입니다(두 번 반영하지 않음).")
            return jsonify(ok=False, retry=True, message="같은 입력을 처리하는 중이거나 결과를 확인하지 못했습니다."), 409
        if status == "DONE":
            flash("같은 요청이 이미 처리되어 다시 등록하지 않았습니다. 아래 결과를 확인하세요.", "info")
            return redirect(location or _same_site_referrer() or "/")
        flash("같은 요청을 처리하는 중이거나 처리 결과를 확인하지 못했습니다. 다시 등록하기 전에 내역을 확인하세요.",
              "warning")
        return redirect(_same_site_referrer() or "/")

    @app.after_request
    def once_finish(response):
        token = g.pop("once", None)
        if token:
            # 이동(3xx) = 처리 끝 → 결과 주소를 남긴다. 화면 다시 그림(입력 오류)·오류 = 처리 안 됨 → 표를 지운다
            done = 300 <= response.status_code < 400 or bool(g.pop("once_done", False))
            try:
                once.finish(token, done, response.headers.get("Location", "") if done else "")
            except db.DBError:
                app.logger.exception("중복 제출 표 정리 실패")
        return response

    @app.after_request
    def security_headers(response):
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)       # 증빙 파일처럼 더 엄격하게 정한 응답은 그대로 둔다
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.endpoint not in ("static", "service_worker", "favicon"):
            # 로그인해야 보이는 화면·다운로드가 브라우저·프록시 캐시에 남지 않게
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _same_site_referrer() -> str:
    """이 사이트의 이전 화면 경로 (다른 사이트 주소면 빈 값)."""
    ref = request.referrer or ""
    parts = urlsplit(ref)
    if not ref or parts.netloc != request.host:
        return ""
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


if __name__ == "__main__":
    debug = config.DEBUG
    if debug and not _loopback(config.HOST):
        print(f"경고: {config.HOST}로 열 때는 디버그 모드를 켤 수 없습니다(원격 코드 실행 위험). 디버그를 끄고 시작합니다.")
        debug = False
    create_app().run(host=config.HOST, port=config.PORT, debug=debug)

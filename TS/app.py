"""TS 영어 시험 학습 (토익) - Flask 진입점

실행: python app.py   → http://127.0.0.1:5003
"""
from __future__ import annotations

import secrets

from flask import Flask, abort, request, session

import config
from core import db
from core.content import Bank
from views import register_blueprints
from views.helpers import register_template_helpers


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    if test_config:
        app.config.update(test_config)

    db.configure(app.config["DB_PATH"])
    app.extensions["bank"] = Bank(app.config["CONTENT_DIR"])

    register_blueprints(app)
    register_template_helpers(app)

    @app.before_request
    def refresh_content() -> None:
        if request.endpoint != "static":
            app.extensions["bank"].refresh_if_changed()

    @app.before_request
    def csrf_protect() -> None:
        """POST 는 세션에 발급한 토큰과 일치해야 한다 (폼: _csrf, JSON: X-CSRF-Token 헤더)."""
        if request.method != "POST" or app.config.get("TESTING_NO_CSRF"):
            return
        token = session.get("_csrf")
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        if not token or not secrets.compare_digest(token, sent):
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    @app.after_request
    def headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
        return response

    return app


if __name__ == "__main__":
    if config.HOST != "127.0.0.1":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            print(f" * 휴대폰(같은 와이파이)에서: http://{s.getsockname()[0]}:{config.PORT}")
            s.close()
        except OSError:
            pass
    create_app().run(host=config.HOST, port=config.PORT, debug=config.DEBUG)

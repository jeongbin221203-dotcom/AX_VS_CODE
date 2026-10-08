"""TS 영어 시험 학습 (토익·토플·토익스피킹·오픽) - Flask 진입점

실행: python app.py   → http://127.0.0.1:5003
"""
from __future__ import annotations

import os
import secrets
import sys
import time

from flask import Flask, Request, abort, render_template, request, session
from werkzeug.routing import IntegerConverter

import config
from core import db
from core import speaking, toefl
from core.content import Bank
from views import register_blueprints
from views.helpers import register_template_helpers


if sys.platform != "win32":
    # 서버(Render 등)가 UTC 여도 '오늘'·'내일 복습'·연속 학습일이 한국 시간 기준이 되게 한다 (이 PC 는 이미 KST)
    os.environ.setdefault("TZ", "Asia/Seoul")
    time.tzset()


class JsonDictRequest(Request):
    """JSON 본문이 객체가 아니면(배열·숫자 등) 없는 것으로 본다 — 뷰의 `get_json() or {}` 가 500 대신 400 안내로 이어지게."""

    def get_json(self, *args, **kwargs):
        data = super().get_json(*args, **kwargs)
        return data if isinstance(data, dict) else None


class SafeIntConverter(IntegerConverter):
    """주소의 번호는 최대 10자리까지만 — 아주 큰 숫자가 DB 에서 OverflowError(500)가 되지 않게 404 로 처리."""

    def __init__(self, map, **kwargs):
        kwargs.setdefault("max", 2**31 - 1)
        super().__init__(map, **kwargs)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.request_class = JsonDictRequest
    app.url_map.converters["int"] = SafeIntConverter
    app.config.from_object(config)
    if test_config:
        app.config.update(test_config)

    db.configure(app.config["DB_PATH"])
    app.extensions["bank"] = Bank(app.config["CONTENT_DIR"])
    toefl.ensure_schema()
    toefl.ensure_mock_schema()
    app.extensions["toefl_bank"] = toefl.ToeflBank(app.config["TOEFL_CONTENT_DIR"])
    speaking.ensure_schema()
    app.extensions["speaking_bank"] = speaking.SpeakingBank(app.config["SPEAKING_CONTENT_DIR"])

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
        try:
            ok = bool(token) and secrets.compare_digest(token, sent)
        except TypeError:                                  # 한글 등 ASCII 가 아닌 글자
            ok = False
        if not ok:
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    @app.errorhandler(OverflowError)
    def too_big(_e):
        return ("값이 너무 큽니다.", 400)

    @app.errorhandler(404)
    def not_found(_e):
        if "/api/" in request.path:                          # JS 가 부르는 주소는 기본 응답 그대로
            return ("찾을 수 없습니다.", 404)
        return render_template("error.html", code=404, title="페이지를 찾을 수 없습니다",
                               message="주소가 바뀌었거나 지워진 기록일 수 있습니다. 처음 화면에서 다시 찾아 주세요."), 404

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

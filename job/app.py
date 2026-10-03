"""채용공고 적합성 확인 - Flask 진입점

여러 취업정보 사이트의 공고를 모아 지역·연봉·신입/경력을 맞춰 보고, 내 조건과의 적합성을 확인해 지원을 관리한다.
실행: python app.py   → http://127.0.0.1:5004
"""
from __future__ import annotations

import os
import secrets
import time

from flask import Flask, abort, redirect, render_template, request, session, url_for

import config
from core import db
from views import register_blueprints
from views.helpers import register_template_helpers


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    if test_config:
        app.config.update(test_config)

    db.configure(app.config["DB_PATH"])
    from core import postings
    postings.recompute_missing()          # 미리 계산하는 점수·직무 열을 새로 더한 뒤 처음 켤 때
    register_blueprints(app)
    register_template_helpers(app)

    @app.before_request
    def csrf_protect() -> None:
        """POST 는 세션에 발급한 토큰과 일치해야 한다 (폼 필드 _csrf). 동기화 API 는 열쇠(Bearer)로 확인."""
        if request.method != "POST" or app.config.get("TESTING_NO_CSRF") or request.path.startswith("/api/sync/"):
            return
        token = session.get("_csrf")
        sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
        if not token or not secrets.compare_digest(token, sent):
            abort(400, "요청이 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")

    @app.before_request
    def require_login():
        """JOB_PASSWORD 가 있으면 로그인한 사람만 (배포용). 로고·CSS·상태 확인 주소는 예외."""
        if not app.config.get("PASSWORD"):
            return None
        if request.endpoint in ("static", "login", "healthz", "favicon", "sync_changes", "sync_upload") \
                or session.get("auth"):
            return None
        return redirect(url_for("login", next=request.full_path if request.method == "GET" else "/"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if secrets.compare_digest(request.form.get("password", ""), app.config.get("PASSWORD", "")):
                session.clear()
                session.permanent = True
                session["auth"] = True
                nxt = request.args.get("next") or "/"
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            time.sleep(1)                                   # 비밀번호 마구 넣기를 늦춤
            error = "비밀번호가 맞지 않습니다."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ── Render 사본: 원본(내 PC)과 주고받기 ──
    def _sync_auth() -> None:
        token = app.config.get("SYNC_TOKEN") or ""
        sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not app.config.get("MIRROR") or not token or not secrets.compare_digest(token, sent):
            abort(403)

    @app.get("/api/sync/changes")
    def sync_changes():
        _sync_auth()
        from core import sync
        return {"changes": sync.changes_after(request.args.get("after", 0, type=int))}

    @app.post("/api/sync/upload")
    def sync_upload():
        _sync_auth()
        from core import sync
        try:
            return sync.receive(request.get_data(), request.headers.get("X-Last-Change", 0, type=int))
        except (ValueError, OSError) as e:
            abort(400, str(e))

    @app.before_request
    def mirror_read_only():
        """사본에서는 수집·등록을 하지 않는다 (내 PC에서만). 저장·지원 기록·내 조건은 사본에서도 바꿀 수 있음."""
        if not app.config.get("MIRROR") or request.method != "POST":
            return None
        if request.endpoint and (request.endpoint.startswith("collect.") or request.endpoint == "jobs.new"):
            abort(403, "Render 사본에서는 수집·공고 등록을 하지 않습니다. 내 PC 앱에서 해 주세요.")
        return None

    @app.context_processor
    def mirror_info():
        if not app.config.get("MIRROR"):
            return {"mirror": None}
        from core import db as _db
        return {"mirror": {"synced_at": _db.get_setting("mirror_synced_at")}}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/favicon.ico")
    def favicon():
        """브라우저가 기본으로 찾는 주소 — SVG 로고를 준다."""
        return app.send_static_file("favicon.svg")

    @app.after_request
    def headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'")
        return response

    # 정기 크롤링 예약 스레드 (테스트·디버그 재시작 감시 프로세스에서는 띄우지 않음)
    reloader_parent = app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    if app.config.get("START_SCHEDULER") and not app.config.get("TESTING") and not reloader_parent \
            and not app.config.get("MIRROR"):
        from core import scheduler
        scheduler.start()

    return app


if __name__ == "__main__":
    create_app().run(host=config.HOST, port=config.PORT, debug=config.DEBUG)

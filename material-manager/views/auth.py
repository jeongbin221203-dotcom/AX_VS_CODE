"""로그인(비밀번호 / 사내 SSO) · 로그아웃 · 최초 설정 · 비밀번호 변경."""

import secrets
import time

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, session,
                   url_for)

import config
from core import audit, auth, db, sso
from views.helpers import actor, f_str, safe_next

bp = Blueprint("auth", __name__)


def _sign_in(user: dict) -> None:
    session.clear()                      # 로그인 전 세션을 버린다(세션 고정 방지)
    session["user_id"] = user["id"]
    session["stamp"] = auth.session_stamp(user)
    session["seen"] = int(time.time())
    session.permanent = True


def _login_page():
    outage = sso.enabled() and sso.outage_active()
    return render_template("login.html", title="로그인", next=request.values.get("next", ""),
                           username=request.form.get("username", ""), sso=sso.enabled(),
                           sso_only=config.SSO_ONLY and not outage, sso_outage=sso.outage_until() if outage else "")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.count_users() == 0:
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        result = auth.authenticate(f_str("username"), request.form.get("password", ""),
                                   request.remote_addr or "")
        if result.ok and config.SSO_ONLY and result.user["role"] != "ADMIN" and not sso.outage_active():
            audit.log({"id": result.user["id"], "name": result.user["name"], "ip": request.remote_addr or ""},
                      "LOGIN_FAIL", "user", result.user["id"], {"reason": "SSO 전용 — 비밀번호 로그인 금지"})
            flash("사내 계정(SSO)으로 로그인하세요. 비밀번호 로그인은 비상용 관리자만 쓸 수 있습니다.", "error")
            return _login_page()
        if result.ok:
            nxt = safe_next(request.form.get("next"), url_for("dashboard.index"))
            _sign_in(result.user)
            flash(result.message, "success")
            return redirect(nxt)
        flash(result.message, "error")
    return _login_page()



@bp.post("/sso/login")
def sso_login():
    if not sso.enabled():
        abort(404)
    try:
        url, stash = sso.start()                    # IdP 발견 문서를 받아야 한다 (인터넷·IdP 장애면 실패)
    except (OSError, ValueError, KeyError):
        current_app.logger.warning("SSO 시작 실패 — IdP에 연결할 수 없음", exc_info=True)
        flash("사내 로그인 서버(SSO)에 연결할 수 없습니다. 잠시 뒤 다시 시도하세요. 장애가 길어지면 시스템관리자가 "
              "'SSO 장애 모드'를 켜서 비밀번호 계정으로 로그인할 수 있게 합니다.", "error")
        return redirect(url_for("auth.login", next=request.form.get("next", "")))
    session.clear()
    session["sso"] = stash
    session["sso_next"] = safe_next(request.form.get("next"), url_for("dashboard.index"))
    return redirect(url)


@bp.get("/sso/callback")
def sso_callback():
    if not sso.enabled():
        abort(404)
    stash, nxt = session.pop("sso", None), session.pop("sso_next", None)
    user, error = sso.finish(request.args.to_dict(), stash or {}, request.remote_addr or "")
    if user is None:
        flash(error, "error")
        return redirect(url_for("auth.login"))
    _sign_in(user)
    flash(f"{user['name']}님 환영합니다.", "success")
    return redirect(nxt or url_for("dashboard.index"))


@bp.post("/logout")
def logout():
    if g.user:
        audit.log(actor(), "LOGOUT", "user", g.user["id"])
        auth.end_sessions(g.user["id"])             # 쿠키가 복사돼 있어도 더는 쓸 수 없다
    session.clear()
    flash("로그아웃했습니다.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """사용자가 한 명도 없을 때만 열린다: 첫 시스템관리자 등록."""
    if auth.count_users() > 0:
        abort(404)
    if request.method == "POST":
        code = current_app.config.get("SETUP_CODE") or ""
        if not code or not secrets.compare_digest(code, f_str("setup_code")):
            flash("설정 코드가 올바르지 않습니다. 서버 콘솔에 찍힌 코드를 입력하세요.", "error")
        elif request.form.get("password") != request.form.get("password2"):
            flash("비밀번호 확인이 일치하지 않습니다.", "error")
        else:
            result = auth.create_user(f_str("username"), f_str("name"), "ADMIN",
                                      request.form.get("password", ""),
                                      {**audit.SYSTEM, "ip": request.remote_addr or ""}, must_change_pw=False)
            if result.ok:
                db.execute("DELETE FROM app_settings WHERE key = 'setup_code'")   # 한 번 쓴 코드는 버린다
                _sign_in(result.user)
                flash("시스템관리자를 등록했습니다. 사용자 메뉴에서 다른 사람을 추가하세요.", "success")
                return redirect(url_for("dashboard.index"))
            flash(result.message, "error")
    return render_template("setup.html", title="최초 설정", form=request.form)


@bp.route("/password", methods=["GET", "POST"])
def password():
    if g.user.get("auth_source") == "sso":
        flash("사내 계정(SSO) 사용자는 사내 계정 비밀번호를 쓰세요.", "info")
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        if request.form.get("new") != request.form.get("new2"):
            flash("새 비밀번호 확인이 일치하지 않습니다.", "error")
        else:
            result = auth.change_password(g.user["id"], request.form.get("current", ""),
                                          request.form.get("new", ""), actor())
            flash(result.message, "success" if result.ok else "error")
            if result.ok:
                session["stamp"] = auth.session_stamp(result.user)   # 이 세션은 유지, 다른 세션은 무효
                return redirect(url_for("dashboard.index"))
    return render_template("password.html", title="비밀번호 변경", active=None)

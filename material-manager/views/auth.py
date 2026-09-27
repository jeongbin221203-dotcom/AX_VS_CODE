"""로그인(비밀번호 + 2단계 인증 / 사내 SSO) · 로그아웃 · 최초 설정 · 비밀번호 변경 · 2단계 인증 등록."""

import secrets
import time

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, session,
                   url_for)

import config
from core import audit, auth, db, mfa, sso
from views.helpers import actor, f_str, safe_next

bp = Blueprint("auth", __name__)

MFA_PENDING_SECONDS = 300          # 비밀번호 확인 후 2단계 코드를 넣어야 하는 시간


def _sign_in(user: dict) -> None:
    session.clear()                      # 로그인 전 세션을 버린다(세션 고정 방지)
    session["user_id"] = user["id"]
    session["stamp"] = auth.session_stamp(user)
    session["seen"] = int(time.time())
    session.permanent = True


def _login_page():
    return render_template("login.html", title="로그인", next=request.values.get("next", ""),
                           username=request.form.get("username", ""), sso=sso.enabled(),
                           sso_only=config.SSO_ONLY)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.count_users() == 0:
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        result = auth.authenticate(f_str("username"), request.form.get("password", ""),
                                   request.remote_addr or "")
        if result.ok and config.SSO_ONLY and result.user["role"] != "ADMIN":
            audit.log({"id": result.user["id"], "name": result.user["name"], "ip": request.remote_addr or ""},
                      "LOGIN_FAIL", "user", result.user["id"], {"reason": "SSO 전용 — 비밀번호 로그인 금지"})
            flash("사내 계정(SSO)으로 로그인하세요. 비밀번호 로그인은 비상용 관리자만 쓸 수 있습니다.", "error")
            return _login_page()
        if result.ok:
            nxt = safe_next(request.form.get("next"), url_for("dashboard.index"))
            if result.user["totp_enabled"]:
                session.clear()
                session["mfa_uid"], session["mfa_at"], session["mfa_next"] = result.user["id"], int(time.time()), nxt
                return redirect(url_for("auth.login_mfa"))
            _sign_in(result.user)
            flash(result.message, "success")
            return redirect(nxt)
        flash(result.message, "error")
    return _login_page()


@bp.route("/login/mfa", methods=["GET", "POST"])
def login_mfa():
    uid, at = session.get("mfa_uid"), int(session.get("mfa_at", 0))
    if not uid or time.time() - at > MFA_PENDING_SECONDS:
        session.clear()
        flash("다시 로그인하세요.", "info")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        ok, msg = mfa.verify(uid, f_str("code"), request.remote_addr or "")
        if ok:
            nxt = session.get("mfa_next") or url_for("dashboard.index")
            _sign_in(auth.get_user(uid))
            if msg:
                flash(msg, "warning")
            return redirect(nxt)
        flash(msg, "error")
        if "잠겼" in msg:
            session.clear()
            return redirect(url_for("auth.login"))
    return render_template("login_mfa.html", title="2단계 인증")


@bp.get("/sso/login")
def sso_login():
    if not sso.enabled():
        abort(404)
    url, stash = sso.start()
    session.clear()
    session["sso"] = stash
    session["sso_next"] = safe_next(request.args.get("next"), url_for("dashboard.index"))
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


@bp.route("/mfa", methods=["GET", "POST"])
def mfa_setup():
    """2단계 인증 등록·해제 (본인)."""
    user = g.user
    if user.get("auth_source") == "sso":
        flash("사내 계정(SSO) 사용자는 사내 로그인의 2단계 인증을 씁니다.", "info")
        return redirect(url_for("dashboard.index"))
    codes = None
    if request.method == "POST" and request.form.get("action") == "enable":
        secret = session.get("mfa_new_secret", "")
        ok, msg, codes = mfa.enable(user["id"], secret, f_str("code"), actor()) if secret else (False, "다시 시도하세요.", [])
        flash(msg, "success" if ok else "error")
        if ok:
            session.pop("mfa_new_secret", None)
            g.user = auth.get_user(user["id"])
            return render_template("mfa.html", title="2단계 인증", active=None, enabled=True, codes=codes,
                                   required=mfa.required(g.user))
    elif request.method == "POST" and request.form.get("action") == "disable":
        if mfa.required(user):
            flash(f"{auth.role_label(user['role'])}은 2단계 인증을 끌 수 없습니다.", "error")
        elif mfa.verify(user["id"], f_str("code"), request.remote_addr or "")[0]:
            mfa.disable(user["id"], actor())
            flash("2단계 인증을 껐습니다.", "success")
            return redirect(url_for("auth.mfa_setup"))
        else:
            flash("현재 인증 코드가 맞아야 끌 수 있습니다.", "error")
    if user["totp_enabled"]:
        return render_template("mfa.html", title="2단계 인증", active=None, enabled=True, codes=None,
                               required=mfa.required(user))
    secret = session.get("mfa_new_secret") or mfa.new_secret()
    session["mfa_new_secret"] = secret
    return render_template("mfa.html", title="2단계 인증", active=None, enabled=False, secret=secret,
                           uri=mfa.provisioning_uri(secret, user["username"]), required=mfa.required(user))

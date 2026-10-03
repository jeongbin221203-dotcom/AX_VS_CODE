"""로그인 · 로그아웃 · 최초 설정 · 비밀번호 변경."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

import config
from core import auth as core_auth
from core import enterprise as ent
from core import sales_db as db

from .helpers import demo_login, demo_user, f_str, sso_identity

bp = Blueprint("auth", __name__)


def _next_url(nxt: str | None = None) -> str:
    """로그인 후 돌아갈 주소. 같은 사이트의 경로만 허용한다(외부 주소로의 리다이렉트 차단)."""
    nxt = nxt if nxt is not None else (request.args.get("next") or "")
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return url_for("reports.dashboard")


def _login(user: dict, method: str, nxt: str | None = None, keep: dict | None = None):
    session.clear()                               # 세션 고정 공격 차단: 로그인 때 새 세션
    session.permanent = True                      # PERMANENT_SESSION_LIFETIME(미사용 만료) 적용
    session["user_id"] = user["id"]
    session["login_at"] = datetime.now().isoformat(timespec="seconds")
    session["auth_mode"] = core_auth.AUTH_MODE    # 인증 방식이 바뀌면 옛 세션을 끊기 위해 기록
    session["login_method"] = method
    session.update(keep or {})
    ent.apply_context(user)
    db.audit("로그인", "사용자", user["id"], {"역할": user["role"], "방식": method, "IP": request.remote_addr})
    if core_auth.password_expired(user):
        flash("비밀번호를 변경해야 계속 사용할 수 있습니다.", "warning")
        return redirect(url_for("auth.password"))
    return redirect(_next_url(nxt))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(_next_url())
    if ent.list_users(active_only=True).empty:
        return redirect(url_for("auth.setup"))
    mode = core_auth.AUTH_MODE

    if mode == "oidc":
        return render_template("login.html", mode=mode, users=[], sso_error=None,
                               next=request.args.get("next") or "", breakglass=core_auth.breakglass_enabled())

    if mode == "sso":
        identity = sso_identity()
        user = core_auth.authenticate_sso(identity) if identity else None
        if not user and not identity and not config.PRODUCTION:
            user = core_auth.authenticate_dev_sso()          # 개발·시연용 고정 사용자
        if user:
            return _login(user, "SSO")
        if identity:
            db.audit("로그인실패", "사용자", None, {"SSO": identity, "사유": "등록되지 않은 사용자"})
        return render_template("login.html", mode=mode, users=[], breakglass=core_auth.breakglass_enabled(),
                               sso_error="SSO 로 확인된 사용자가 이 시스템에 등록되어 있지 않습니다."
                               if identity else "SSO 인증 정보가 없습니다. 사내 포털을 통해 접속하세요.")

    if request.method == "POST":
        if mode == "password":
            user, message = core_auth.authenticate_password(f_str("emp_no"), request.form.get("password", ""))
        else:
            uid = f_str("user_id")
            user = core_auth.authenticate_simple(int(uid)) if uid.isdigit() else None
            message = "로그인에 실패했습니다."
        if user:
            return _login(user, mode)
        flash(message, "error")

    options = []
    if mode == "simple":            # 개발·시연 전용 — 운영(production)에서는 기동 단계에서 막힌다
        users = ent.list_users(active_only=True)
        options = [(int(r.id), f"{r.이름} · {r.역할} · {r.소속 or '미배정'} ({r.사번})")
                   for r in users.itertuples()]
    return render_template("login.html", mode=mode, users=options, sso_error=None)


@bp.route("/login/breakglass", methods=["POST"])
def breakglass():
    """사내 인증 장애 시 비상 계정 로그인 (SALES_BREAKGLASS_USERS)."""
    if not core_auth.breakglass_enabled():
        abort(404)
    user, message = core_auth.authenticate_breakglass(f_str("emp_no"), request.form.get("password", ""))
    if not user:
        flash(message, "error")
        return redirect(url_for("auth.login"))
    db.audit("비상로그인", "사용자", user["id"], {"IP": request.remote_addr, "방식": core_auth.AUTH_MODE})
    from core import notify
    notify.notify_role("ADMIN", "보안", "비상 계정 로그인", f"{user['name']}({user['emp_no']}) · IP {request.remote_addr}",
                       "/admin/audit")
    return _login(user, "비상로그인")


# ── OIDC ─────────────────────────────────────────────────────────────────
def _oidc_error(message: str, status: int = 401):
    return render_template("login.html", mode="oidc", users=[], sso_error=message, next=""), status


@bp.route("/login/oidc")
def oidc_start():
    if core_auth.AUTH_MODE != "oidc":
        abort(404)
    from core import oidc
    session["oidc_next"] = _next_url()
    redirect_uri = oidc.settings()["redirect_url"] or url_for("auth.oidc_callback", _external=True)
    try:
        return oidc.client().authorize_redirect(redirect_uri)
    except Exception:   # noqa: BLE001 - IdP 메타데이터를 못 읽는 경우 등
        current_app.logger.exception("OIDC 시작 실패")
        return _oidc_error("사내 인증 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.", 503)


@bp.route("/login/oidc/callback")
def oidc_callback():
    if core_auth.AUTH_MODE != "oidc":
        abort(404)
    from authlib.integrations.base_client.errors import OAuthError

    from core import oidc
    if request.args.get("error"):
        db.audit("로그인실패", "사용자", None, {"OIDC": request.args.get("error"), "IP": request.remote_addr})
        return _oidc_error(f"사내 인증이 취소되었거나 실패했습니다 ({request.args.get('error')}).")
    try:
        token = oidc.client().authorize_access_token()
    except (OAuthError, ValueError, KeyError) as exc:          # state·nonce·서명·만료 불일치 등
        db.audit("로그인실패", "사용자", None, {"OIDC": type(exc).__name__, "IP": request.remote_addr})
        return _oidc_error("인증 응답을 확인하지 못했습니다. 처음부터 다시 로그인하세요.")
    identity = oidc.identity(token)
    user = core_auth.authenticate_sso(identity) if identity else None
    if not user:
        db.audit("로그인실패", "사용자", None, {"OIDC": identity, "사유": "등록되지 않은 사용자"})
        return _oidc_error("사내 계정으로 확인되었지만 이 시스템에 등록된 활성 사용자가 아닙니다. 관리자에게 문의하세요.", 403)
    nxt = session.get("oidc_next")
    return _login(user, "OIDC", nxt, keep={"oidc_id_token": token.get("id_token")})


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """사용자가 한 명도 없을 때의 초기 설정 화면."""
    if not ent.list_users(active_only=True).empty:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        db.set_context("system", None)
        raw_pw = request.form.get("password", "")
        try:
            # password 모드에서는 관리자 비밀번호 없이 계정을 만들면 아무도 로그인할 수 없다
            if core_auth.AUTH_MODE == "password":
                problems = core_auth.password_problems(raw_pw)
                if problems:
                    raise ValueError("관리자 비밀번호 규칙: " + ", ".join(problems))
            if request.form.get("action") == "seed":
                created = ent.seed_org_demo()
                admin_id = ent.get_user(emp_no="9999")["id"]
                flash(f"조직 {created['orgs']}개, 사용자 {created['users']}명 생성 완료", "success")
            else:
                admin_id = ent.upsert_user({"emp_no": f_str("emp_no"), "name": f_str("name"), "role": "ADMIN"})
                flash("관리자 계정을 만들었습니다. 로그인하세요.", "success")
            if core_auth.AUTH_MODE == "password":
                core_auth.set_password(admin_id, raw_pw, must_change=False)
            return redirect(url_for("auth.login"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("setup.html")


@bp.route("/demo/as/<emp_no>")
def demo_as(emp_no: str):
    """시연 서버: 다른 역할로 바꿔 보기 (SALES_DEMO_AUTOLOGIN 이 켜진 서버에서만)."""
    if not config.DEMO_AUTOLOGIN or emp_no not in {e for e, _ in config.DEMO_ROLES}:
        abort(404)
    user = ent.get_user(emp_no=emp_no)
    if not user or not user.get("active"):
        abort(404)
    demo_login(user)
    return redirect(url_for("reports.dashboard"))


@bp.route("/demo/reset", methods=["POST"])
def demo_reset():
    """시연 서버: 샘플로 되돌리기 — 방문자들이 바꾼 내용을 지우고 처음 샘플로 (모든 방문자에게 적용)."""
    if not config.DEMO_AUTOLOGIN:
        abort(404)
    from core import demo_data
    day = demo_data.reset()
    user = demo_user()
    if user:
        demo_login(user)                                # 되돌린 DB 의 시연 관리자로 다시 들어온다
    flash(f"처음 샘플로 되돌렸습니다 (기준일 {day or '-'}).", "success")
    return redirect(url_for("reports.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    uid = session.get("user_id")
    if uid:
        db.audit("로그아웃", "사용자", uid)
    id_token = session.get("oidc_id_token")
    session.clear()
    if core_auth.AUTH_MODE == "oidc":
        from core import oidc
        end = None
        if oidc.settings()["logout"]:
            try:
                end = oidc.client().load_server_metadata().get("end_session_endpoint")
            except Exception:   # noqa: BLE001
                end = None
        if end:
            from urllib.parse import urlencode
            return redirect(end + "?" + urlencode({"id_token_hint": id_token or "",
                                                    "client_id": oidc.settings()["client_id"],
                                                    "post_logout_redirect_uri": url_for("auth.login", _external=True)}))
    return redirect(url_for("auth.login"))


@bp.route("/notifications")
def notifications():
    from core import notify
    rows = notify.list_for(int(g.user["id"]))
    return render_template("notifications.html", title="알림", active=None, rows=rows.to_dict("records"))


@bp.route("/notifications/read", methods=["POST"])
def notifications_read():
    from core import notify
    nid = f_str("id")
    notify.mark_read(int(g.user["id"]), int(nid) if nid.isdigit() else None)
    target = f_str("next")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("auth.notifications"))


@bp.route("/account/password", methods=["GET", "POST"])
def password():
    """본인 비밀번호 변경 (password 모드). 임시 비밀번호·만료 시 이 화면으로 강제 이동한다."""
    if core_auth.AUTH_MODE != "password":
        return render_template("account_password.html", mode=core_auth.AUTH_MODE, title="비밀번호 변경",
                               active=None, policy=None)
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if new != request.form.get("confirm_password", ""):
            flash("새 비밀번호 확인이 일치하지 않습니다.", "error")
        else:
            try:
                core_auth.set_password(int(g.user["id"]), new, must_change=False,
                                       current=request.form.get("current_password", ""))
                flash("비밀번호를 변경했습니다.", "success")
                return redirect(url_for("reports.dashboard"))
            except ValueError as exc:
                flash(str(exc), "error")
    policy = (f"{core_auth.PASSWORD_MIN_LENGTH}자 이상, 영문 대문자·소문자·숫자·특수문자 중 3종류 이상, "
              f"사번 포함 금지, {core_auth.PASSWORD_MAX_AGE_DAYS}일마다 변경")
    return render_template("account_password.html", mode="password", title="비밀번호 변경",
                           active=None, policy=policy)

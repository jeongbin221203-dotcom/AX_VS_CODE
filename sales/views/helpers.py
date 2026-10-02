"""화면 공통 도구: 메뉴·로그인 게이트·표/차트 변환·폼 파싱·다운로드 응답."""
from __future__ import annotations

import io
import math
import secrets
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Iterable
from urllib.parse import urlencode

import pandas as pd
from flask import (Flask, abort, current_app, flash, g, redirect, render_template, request,
                   send_file, session, url_for)

import config
from core import auth as core_auth
from core import company
from core import dataio
from core import enterprise as ent
from core import notify
from core import sales_db as db

# ----------------------------------------------------------------------------
# 메뉴 (키, 표시명, 엔드포인트, 최소 권한)
# ----------------------------------------------------------------------------
MENUS = [
    ("dashboard", "📊 대시보드", "reports.dashboard", "REP"),
    ("forecast", "🔮 매출예측", "reports.forecast", "REP"),
    ("analytics", "📈 파이프라인 분석", "reports.analytics", "REP"),
    ("customers", "🏢 거래처", "crm.customers", "REP"),
    ("deals", "💼 영업기회", "crm.deals", "REP"),
    ("activities", "📞 영업활동", "crm.activities", "REP"),
    ("sales", "💰 매출·채권", "finance.sales", "REP"),
    ("targets", "🎯 목표", "finance.targets", "REP"),
    ("quotes", "📝 견적", "catalog.quotes", "REP"),
    ("orders", "📑 수주", "catalog.orders", "REP"),
    ("products", "📦 품목·단가", "catalog.products", "REP"),
    ("approvals", "✅ 결재함", "finance.approvals", "REP"),
    ("dataio", "📥 데이터 등록·추출", "io.index", "REP"),
    ("org", "👥 조직·사용자", "admin.org", "ADMIN"),
    ("erp", "🔗 ERP 연동", "admin.erp", "ADMIN"),
    ("jobs", "⏱️ 배치 작업", "admin.jobs", "ADMIN"),
    ("api", "🔑 API 연동", "admin.api_clients", "ADMIN"),
    ("audit", "🗂️ 감사로그", "admin.audit", "ADMIN"),
    ("settings", "🏢 회사 설정", "admin.settings", "ADMIN"),
    ("privacy", "🛡️ 개인정보 요청", "admin.privacy", "ADMIN"),
    ("admin", "⚙️ 데이터 관리", "admin.data", "ADMIN"),
]

# 로그인 없이 열 수 있는 엔드포인트
PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "auth.oidc_start", "auth.oidc_callback", "auth.breakglass",
                    "static",
                    "healthz", "readyz", "metrics"}
# 비밀번호 변경이 필요한 사용자도 열 수 있는 엔드포인트
PASSWORD_ENDPOINTS = {"auth.password", "auth.logout", "static", "healthz"}


def menus_for(user: dict) -> list[tuple]:
    return [m for m in MENUS if ent.has_role(user, m[3])]


def menu_label(key: str) -> str:
    return next((m[1] for m in MENUS if m[0] == key), key)


# ----------------------------------------------------------------------------
# 요청 컨텍스트: 로그인 사용자 · 데이터 접근범위 · 기준월/담당자 필터
# ----------------------------------------------------------------------------
def sso_identity() -> str | None:
    """신뢰하는 프록시에서 온 요청일 때만 SSO 헤더 값을 돌려준다(헤더 위조 차단)."""
    peer = (request.environ.get("werkzeug.proxy_fix.orig") or {}).get("REMOTE_ADDR") or request.remote_addr
    if peer not in current_app.config["TRUSTED_PROXIES"]:
        return None
    return (request.headers.get(current_app.config["SSO_HEADER"]) or "").strip() or None


def _logout(message: str, kind: str = "warning"):
    session.clear()
    flash(message, kind)
    return redirect(url_for("auth.login"))


def load_context():
    """매 요청마다 사용자와 접근범위를 다시 읽는다 → 권한 변경·비활성화가 즉시 반영된다."""
    g.user = None
    db.set_ip(request.remote_addr)
    company.refresh()                                 # 회사 설정 (서버마다 15초 간격으로 다시 읽음)
    if request.endpoint == "static" or request.blueprint == "api":     # API 는 Bearer 키로 따로 인증
        return None

    uid = session.get("user_id")
    if uid:
        user = ent.get_user(user_id=uid)
        if not user or not user.get("active"):
            return _logout("계정이 비활성화되었습니다. 관리자에게 문의하세요.")
        if session.get("auth_mode") != core_auth.AUTH_MODE:
            return _logout("인증 방식이 바뀌어 다시 로그인해야 합니다.", "info")
        login_at = session.get("login_at")
        if not login_at or datetime.fromisoformat(login_at) < datetime.now() - timedelta(
                hours=config.SESSION_ABSOLUTE_HOURS):
            return _logout("로그인 후 오랜 시간이 지나 다시 로그인해야 합니다.", "info")
        if core_auth.AUTH_MODE == "sso":
            identity = sso_identity()
            key = "email" if core_auth.SSO_MATCH == "email" else "emp_no"
            if identity and identity != str(user.get(key) or ""):
                return _logout("SSO 사용자가 바뀌어 다시 로그인합니다.", "info")
        g.user = user

    if not g.user:
        db.set_context("anonymous", [])          # 로그인 전에는 어떤 영업 데이터도 보이지 않는다
        if request.endpoint not in PUBLIC_ENDPOINTS:
            nxt = request.full_path if request.method == "GET" and request.path != "/" else None
            return redirect(url_for("auth.login", next=nxt))
        return None

    ent.apply_context(g.user)
    if core_auth.password_expired(g.user) and request.endpoint not in PASSWORD_ENDPOINTS:
        flash("비밀번호를 변경해야 계속 사용할 수 있습니다.", "warning")
        return redirect(url_for("auth.password"))

    g.scope = db.current_scope()
    g.owner_choices = db.owner_choices()                    # 조회 필터용 (퇴사자 포함)
    g.assignable = db.owner_choices(assignable=True)        # 담당자 지정용 (활성 사용자)

    months = db.month_options()
    if request.args.get("ym") in months:
        session["ym"] = request.args["ym"]
    if "owner" in request.args:
        session["owner"] = request.args["owner"]
    g.months = months
    g.ym = session.get("ym") if session.get("ym") in months else date.today().strftime("%Y-%m")
    owner = str(session.get("owner", ""))
    ids = {o["id"] for o in g.owner_choices}
    # 영업사원은 본인 데이터만 보이므로 필터가 무의미하다. 범위 밖 id 도 무시한다.
    g.owner_filter = int(owner) if owner.isdigit() and int(owner) in ids and len(ids) > 1 else None
    from core import credit
    g.pending_cnt = len(ent.pending_for(g.user)) + (len(credit.pending_for(g.user))
                                                     if ent.has_role(g.user, "MANAGER") else 0)
    g.unread = notify.unread_count(int(g.user["id"]))
    return None


def role_required(minimum: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not ent.has_role(g.user, minimum):
                abort(403, f"이 메뉴는 {db.ROLE_LABEL.get(minimum, minimum)} 이상만 사용할 수 있습니다.")
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ----------------------------------------------------------------------------
# 표시 유틸
# ----------------------------------------------------------------------------
def won(value) -> str:
    """정수 금액 → '12,345,678원'"""
    try:
        return f"{int(value):,}원"
    except (TypeError, ValueError):
        return "-"


def mil(value) -> str:
    """정수 금액 → '123.4백만'"""
    try:
        return f"{int(value) / 1_000_000:,.1f}백만"
    except (TypeError, ValueError):
        return "-"


def _cell(value: Any, money: bool) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if money:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:,.1f}"
    return str(value)


class Table:
    """DataFrame → 템플릿용 표.

    link=(엔드포인트, 원본 컬럼, 인자명)  행 클릭 시 이동할 주소
    select=(폼 필드명, 원본 컬럼)        행마다 체크박스 (다건 처리용)
    page_size=N                          N행씩 나눠 보여 준다 (?page=)
    """

    def __init__(self, df: pd.DataFrame, money: Iterable[str] = (), drop: Iterable[str] = (),
                 limit: int | None = None, link: tuple | None = None,
                 select: tuple | None = None, highlight: dict | None = None,
                 page_size: int | None = None):
        self.total = len(df)
        self.empty = df.empty
        self.page, self.pages = 1, 1
        if page_size and self.total > page_size:
            self.pages = math.ceil(self.total / page_size)
            self.page = min(max(a_int("page", 1) or 1, 1), self.pages)
            df = df.iloc[(self.page - 1) * page_size: self.page * page_size]
        src = df.head(limit) if limit else df
        view = src.drop(columns=[c for c in drop if c in src.columns])
        money = set(money)
        self.columns = list(view.columns)
        self.numeric = {c for c in self.columns
                        if pd.api.types.is_numeric_dtype(view[c]) or c in money}
        self.select_name = select[0] if select else None
        self.rows = []
        records = src.to_dict("records")
        for rec, vals in zip(records, view.itertuples(index=False)):
            row = {"cells": [_cell(v, c in money) for c, v in zip(self.columns, vals)],
                   "href": None, "value": None, "tone": None}
            if link:
                endpoint, key, arg = link
                row["href"] = url_for(endpoint, **{arg: rec[key]})
            if select:
                row["value"] = rec[select[1]]
            if highlight:
                col, mapping = next(iter(highlight.items()))
                row["tone"] = mapping.get(rec.get(col))
            self.rows.append(row)


CHART_HEIGHT = 280     # 모든 차트 같은 높이 (색·막대폭·축 단위는 static/js/app.js 에서 통일)


def chart(df: pd.DataFrame, x: str, ys: list[str] | str, kind: str = "bar",
          money: bool = True) -> dict:
    """Chart.js 에 그대로 넘길 수 있는 dict.

    같은 지표는 어느 화면에서든 같은 색으로 그려지도록 계열 이름(매출·목표 등)으로 색을 정한다.
    여러 계열은 '실적 → 비교 기준' 순서(예: 매출, 목표)로 넘긴다.
    """
    ys = [ys] if isinstance(ys, str) else ys
    height = CHART_HEIGHT
    if df.empty:
        return {"empty": True, "height": height}

    def num(v):
        return 0 if pd.isna(v) else float(v)

    return {"type": kind, "height": height, "money": money,
            "labels": [str(v) for v in df[x].tolist()],
            "datasets": [{"label": y, "data": [num(v) for v in df[y].tolist()]} for y in ys]}


# ----------------------------------------------------------------------------
# 폼 파싱
# ----------------------------------------------------------------------------
def f_str(name: str, default: str = "") -> str:
    return (request.form.get(name) or default).strip()


def f_int(name: str, default: int = 0) -> int:
    raw = f_str(name).replace(",", "")
    try:
        return int(float(raw)) if raw else default
    except ValueError as exc:
        raise ValueError(f"숫자를 입력하세요 ({raw})") from exc


def f_float(name: str, default: float = 0.0) -> float:
    raw = f_str(name).replace(",", "").replace("%", "")
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"숫자를 입력하세요 ({raw})") from exc


def f_bool(name: str) -> int:
    return 1 if request.form.get(name) in ("1", "on", "true") else 0


def f_ids(name: str) -> list[int]:
    return [int(v) for v in request.form.getlist(name) if str(v).isdigit()]


def f_owner(name: str = "owner_id") -> int:
    """담당자 선택값(사용자 id). 비어 있으면 로그인 사용자."""
    raw = f_str(name)
    return int(raw) if raw.isdigit() else int(g.user["id"])


def a_str(name: str, default: str = "") -> str:
    """쿼리스트링 값 ('전체' 는 빈 값으로)."""
    value = (request.args.get(name) or default).strip()
    return "" if value == "전체" else value


def a_int(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name, "")
    return int(raw) if raw.isdigit() else default


# ----------------------------------------------------------------------------
# 응답
# ----------------------------------------------------------------------------
def file_response(data: bytes, filename: str, mimetype: str, rows: int | None = None,
                  pii: bool = False):
    """파일 내려받기. 누가 어떤 조건으로 무엇을 받았는지 감사로그에 남긴다(개인정보 다운로드 기록)."""
    db.audit("다운로드", "파일", None, {"파일": filename, "행수": rows, "개인정보포함": pii,
                                        "요청": request.full_path})
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True,
                     download_name=filename)


def csv_response(df: pd.DataFrame, filename: str, pii: bool = False):
    return file_response(dataio.to_csv(df), filename, "text/csv", rows=len(df), pii=pii)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_response(data: bytes, filename: str, rows: int | None = None, pii: bool = False):
    return file_response(data, filename, XLSX, rows=rows, pii=pii)


def page_path() -> str:
    """현재 메뉴 화면의 주소. 저장 실패로 POST 주소에서 다시 그릴 때도 목록 화면을 가리킨다."""
    return g.get("page_path") or request.path


def _url_with(**changes) -> str:
    args = {k: v for k, v in request.args.items() if k != "export"}
    args.update({k: v for k, v in changes.items()})
    return f"{page_path()}?{urlencode(args)}"


def export_url(name: str) -> str:
    """현재 화면의 조회 조건을 유지한 채 CSV 로 받는 주소."""
    return _url_with(export=name)


def tab_url(key: str) -> str:
    """현재 조회 조건을 유지한 채 탭만 바꾸는 주소."""
    return _url_with(tab=key, page=1)


def page_url(n: int) -> str:
    return _url_with(page=n)


def render_page(template: str, active: str, **ctx):
    endpoint = next((m[2] for m in MENUS if m[0] == active), None)
    if endpoint:
        g.page_path = url_for(endpoint)
    return render_template(template, active=active, title=menu_label(active), **ctx)


# ----------------------------------------------------------------------------
# 템플릿 등록
# ----------------------------------------------------------------------------
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def register_template_helpers(app: Flask) -> None:
    app.jinja_env.filters.update(won=won, mil=mil)

    @app.context_processor
    def _company():
        values = company.all_values()
        return {"APP_TITLE": values["app_title"], "COMPANY": values,
                "DISC_M": values["discount_manager_max"], "DISC_E": values["discount_exec_max"]}

    app.jinja_env.globals.update(
        csrf_token=csrf_token, menus_for=menus_for, export_url=export_url, tab_url=tab_url,
        page_url=page_url, page_path=page_path,
        STAGES=db.STAGES, STAGE_PROB=db.STAGE_PROB, GRADES=db.GRADES, INDUSTRIES=db.INDUSTRIES,
        ACT_TYPES=db.ACT_TYPES, SALE_STATUS=db.SALE_STATUS, LEAD_SOURCES=db.LEAD_SOURCES,
        FORECAST_CATS=db.FORECAST_CATS, FORECAST_DESC=db.FORECAST_DESC,
        LOST_REASONS=db.LOST_REASONS, MEDDIC_FIELDS=db.MEDDIC_FIELDS,
        ROLE_LABEL=db.ROLE_LABEL, AUTH_MODE=core_auth.AUTH_MODE,
        ENV=config.ENV, today=lambda: date.today(), now=lambda: datetime.now(),
    )

    def error_page(code: int, title: str, message: str):
        return render_template("error.html", code=code, message=message, active=None,
                               title=title), code

    @app.errorhandler(403)
    def forbidden(err):
        return error_page(403, "접근 권한 없음", err.description)

    @app.errorhandler(PermissionError)
    def permission_error(err):
        return error_page(403, "접근 권한 없음", str(err))

    @app.errorhandler(db.ConflictError)
    def conflict(err):
        return error_page(409, "동시 수정 충돌", str(err))

    @app.errorhandler(409)
    def conflict_http(err):
        return error_page(409, "처리할 수 없음", err.description)

    @app.errorhandler(400)
    def bad_request(err):
        return error_page(400, "잘못된 요청", err.description)

    @app.errorhandler(404)
    def not_found(err):
        return error_page(404, "없는 화면", "요청한 화면이 없습니다.")

    @app.errorhandler(413)
    def too_large(err):
        return error_page(413, "파일이 너무 큼", "업로드 파일이 허용 크기(20MB)를 넘습니다.")

    @app.errorhandler(500)
    def server_error(err):
        # 예외 내용은 로그(data/logs/app.log)에만 남기고 화면에는 드러내지 않는다
        return error_page(500, "처리 중 오류", "처리 중 오류가 발생했습니다. 같은 문제가 반복되면 관리자에게 알려 주세요.")

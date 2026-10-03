"""화면 공통 도구: 메뉴·로그인 게이트·권한·표/차트 변환·폼 파싱·다운로드 응답."""
from __future__ import annotations

import io
import math
import secrets
import time
from datetime import date
from functools import wraps
from typing import Any, Iterable, Mapping

from urllib.parse import urlsplit

import pandas as pd
from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for

import config
from core import approvals, audit, auth, org, periods, repository as repo, sap

# 메뉴 (키, 표시명, 엔드포인트, 최소 역할) — 기본 순서는 현장에서 자주 쓰는 순. 대시보드는 항상 맨 위.
# 사용자는 사이드바 '메뉴 편집'으로 순서·즐겨찾기를 바꿀 수 있다(core/prefs.py).
MENUS = [
    ("dashboard", "📊 대시보드", "dashboard.index", "VIEWER"),
    ("transactions", "🔄 입출고 등록", "transactions.index", "CLERK"),
    ("batch", "📷 여러 줄·스캔 입출고", "transactions.batch", "CLERK"),
    ("stock", "📦 재고 현황", "stock.index", "VIEWER"),
    ("history", "🧾 거래 이력", "history.index", "VIEWER"),
    ("purchase", "🛒 구매 (요청·발주)", "purchase.index", "CLERK"),
    ("approvals", "✅ 결재함", "approvals.index", "CLERK"),
    ("materials", "🗂️ 자재 마스터", "materials.index", "VIEWER"),
    ("statements", "🧾 거래명세서 입출고", "statements.index", "CLERK"),
    ("production", "🏭 생산 투입 (BOM)", "production.index", "CLERK"),
    ("partners", "🤝 거래처", "partners.index", "VIEWER"),
    ("documents", "📎 증빙 (세금계산서)", "documents.index", "VIEWER"),
    ("ledger", "📒 수불부", "reports.ledger", "VIEWER"),
    ("valuation", "💴 재고 평가", "reports.valuation_view", "MANAGER"),
    ("reconcile", "⚖️ 재고 대사", "reports.reconcile_view", "MANAGER"),
    ("periods", "🔒 월 마감", "periods.index", "MANAGER"),
    ("sap", "🔗 ERP·SAP 연동", "sap.index", "MANAGER"),
    ("data", "🛠️ 데이터 관리", "data_admin.index", "ADMIN"),
    ("org", "🏭 플랜트·창고", "admin.org_page", "ADMIN"),
    ("users", "👥 사용자", "admin.users", "ADMIN"),
    ("jobs", "⏱️ 배치", "admin.jobs_page", "ADMIN"),
    ("forms", "📑 엑셀 양식", "admin.forms_list", "ADMIN"),
    ("audit", "🗂️ 감사로그", "admin.audit_log", "ADMIN"),
]
PINNED_MENU = "dashboard"
# 로그인 없이 열 수 있는 화면
PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "static", "health"}
# 비밀번호를 바꿔야 하는 사용자가 열 수 있는 화면
PASSWORD_ENDPOINTS = {"auth.password", "auth.logout", "static"}
PUBLIC_ENDPOINTS |= {"auth.sso_login", "auth.sso_callback"}

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def load_context():
    """매 요청마다 사용자를 다시 읽는다 → 역할 변경·계정 중지가 즉시 반영된다."""
    g.user = None
    if request.endpoint in ("static", "health", "service_worker", "favicon"):
        return None
    uid = session.get("user_id")
    if uid:
        user = auth.get_user(uid)
        now = int(time.time())
        idle = now - int(session.get("seen", now))
        if not user or not user["active"]:
            _end_session("계정이 중지되었습니다. 관리자에게 문의하세요.", "warning")
        elif not secrets.compare_digest(session.get("stamp", ""), auth.session_stamp(user)):
            # 비밀번호가 바뀐 뒤의 옛 세션(탈취된 쿠키 포함)
            _end_session("비밀번호가 변경되어 다시 로그인해야 합니다.", "warning")
        elif idle > config.IDLE_MINUTES * 60:
            _end_session(f"{config.IDLE_MINUTES}분 동안 사용하지 않아 로그아웃했습니다.", "info")
        else:
            g.user = user
            if now - int(session.get("seen", 0)) > 60:      # 쿠키를 매 요청 새로 쓰지 않게 1분 단위로 갱신
                session["seen"] = now
    if config.DEMO:
        from core import demo
        demo.maybe_daily_reset()
        if g.user is not None and auth.get_user(g.user["id"]) is None:    # 초기화로 계정 번호가 바뀜
            g.user = None
    if g.user is None and config.DEMO and request.endpoint not in PUBLIC_ENDPOINTS | {"auth.logout"}:
        g.user = _demo_sign_in()
    if g.user is not None and config.DEMO and request.method == "POST":
        from core import demo
        if demo.locked(request.blueprint, request.endpoint):
            # 누구나 시스템관리자로 들어오므로 관리자 설정은 저장을 막는다 (화면은 볼 수 있고 업무 데이터는 저장됨)
            flash("시연 서버에서는 관리자 설정을 바꿀 수 없습니다 — 화면만 둘러볼 수 있습니다.", "warning")
            back = request.referrer or ""
            return redirect(back if back.startswith(request.host_url) else url_for("dashboard.index"))
    if g.user is None:
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if auth.count_users() == 0:
            return redirect(url_for("auth.setup"))
        nxt = request.full_path if request.method == "GET" and request.path != "/" else None
        return redirect(url_for("auth.login", next=nxt))
    if g.user["must_change_pw"] and request.endpoint not in PASSWORD_ENDPOINTS:
        flash("임시 비밀번호입니다. 새 비밀번호로 바꿔 주세요.", "warning")
        return redirect(url_for("auth.password"))

    g.wh_ids = org.allowed_warehouses(g.user)      # None = 모든 창고
    g.closed_through = periods.closed_through()
    g.sap_on = sap.enabled()
    g.shortage_cnt = g.sap_failed = g.pending_approvals = 0
    if request.method == "GET":                   # 사이드바 알림은 화면을 그릴 때만 계산
        stock = repo.stock_df(wh_ids=g.wh_ids)
        g.shortage_cnt = int(stock["shortage"].sum()) if not stock.empty else 0
        g.sap_failed = sap.summary(g.wh_ids).get("FAILED", 0) if g.sap_on and can("MANAGER") else 0
        g.pending_approvals = approvals.pending_count(g.wh_ids) if can("MANAGER") else 0
    return None


def _end_session(message: str, category: str) -> None:
    """세션을 끝낸다. 시연 모드는 곧바로 자동 로그인하므로 안내 없이, 남은 알림은 그대로 둔다."""
    if config.DEMO:
        flashes = session.get("_flashes")
        session.clear()
        if flashes:
            session["_flashes"] = flashes
        return
    session.clear()
    flash(message, category)


def _demo_sign_in(user: dict | None = None) -> dict:
    """시연 모드: 로그인하지 않은 방문자를 시연용 시스템관리자로(또는 '다른 역할로 보기'에서 고른 계정으로) 로그인시킨다."""
    from core import demo
    user = user or demo.ensure_user()
    token, flashes = session.get("_csrf"), session.get("_flashes")
    session.clear()
    if token:                                      # 이미 열어 둔 화면의 폼이 그대로 제출되게
        session["_csrf"] = token
    if flashes:                                    # 초기화 직후 안내 문구가 사라지지 않게
        session["_flashes"] = flashes
    session["user_id"] = user["id"]
    session["stamp"] = auth.session_stamp(user)
    session["seen"] = int(time.time())
    session.permanent = True
    return user


def scope_all() -> bool:
    """모든 창고 권한이 있는가 (월 마감 등 회사 전체 작업)."""
    return g.wh_ids is None


def page_arg() -> int:
    raw = request.args.get("page", "1")
    return min(max(int(raw), 1), 1_000_000) if raw.isascii() and raw.isdigit() and len(raw) < 10 else 1


def pager(total: int, page: int, size: int = config.PAGE_SIZE) -> dict:
    pages = max((total + size - 1) // size, 1)
    return {"total": total, "page": min(page, pages), "pages": pages, "size": size,
            "first": (page - 1) * size + 1 if total else 0, "last": min(page * size, total)}


def log_export(what: str, rows: int, **detail) -> None:
    """엑셀 내보내기를 감사로그에 남긴다 (대량 반출 추적)."""
    audit.log(actor(), "EXPORT", what, "", {"rows": rows, **detail})


def safe_next(target: str | None, fallback: str, prefix: str = "/") -> str:
    """사이트 안의 경로만 허용한다(외부로 튕기는 열린 리다이렉트 방지).

    '//evil.com', 역슬래시, 제어문자('/<탭>/evil.com'은 브라우저가 '//evil.com'으로 읽음),
    'https://...' 모두 거부한다.
    """
    if not target or any(ord(ch) < 32 or ch == "\\" for ch in target):
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith(prefix) or target.startswith("//"):
        return fallback
    return target


def can(minimum: str) -> bool:
    return auth.has_role(g.get("user"), minimum)


def role_required(minimum: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not can(minimum):
                abort(403, f"이 기능은 {auth.role_label(minimum)} 이상만 사용할 수 있습니다.")
            return view(*args, **kwargs)
        return wrapped
    return decorator


def actor() -> dict:
    """감사로그에 남길 행위자."""
    u = g.user
    return {"id": u["id"], "name": u["name"], "role": u["role"], "ip": request.remote_addr or ""}


def menus_for_user() -> list[tuple]:
    return [m for m in MENUS if can(m[3])]


def menu_layout() -> dict:
    """사이드바 메뉴: 대시보드(맨 위 고정) · 즐겨찾기 · 나머지 — 사용자가 정한 순서대로."""
    from core import prefs
    items = menus_for_user()
    pinned = [m for m in items if m[0] == PINNED_MENU]
    rest = [m for m in items if m[0] != PINNED_MENU]
    p = prefs.menu(g.user["id"])
    rank = {k: i for i, k in enumerate(p["order"])}
    rest.sort(key=lambda m: rank.get(m[0], len(rank) + [x[0] for x in MENUS].index(m[0])))
    fav = set(p["fav"])
    return {"pinned": pinned, "fav": [m for m in rest if m[0] in fav], "others": [m for m in rest if m[0] not in fav],
            "customized": bool(p["order"] or p["fav"])}


def render_page(template: str, active: str, **ctx):
    title = next((m[1] for m in MENUS if m[0] == active), "")
    return render_template(template, active=active, title=title, **ctx)


# ----------------------------------------------------------------------------
# 표 · 차트
# ----------------------------------------------------------------------------
def _cell(value: Any, fmt: str | None) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if fmt:
        try:
            return fmt.format(value)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return f"{int(value):,}" if value.is_integer() else f"{value:,.2f}"
    return str(value)


class Table:
    """DataFrame → 템플릿용 표.

    fmt     {컬럼: 형식 문자열}  예) {"단가": "₩{:,.0f}"}
    tones   행마다 강조 이름(예: "danger") 또는 None. DataFrame과 같은 길이.
    links   행마다 첫 칸에 걸 주소 또는 None. DataFrame과 같은 길이.
    """

    def __init__(self, df: pd.DataFrame, fmt: Mapping[str, str] | None = None,
                 tones: Iterable[str | None] | None = None,
                 links: Iterable[str | None] | None = None):
        fmt = dict(fmt or {})
        self.empty = df.empty
        self.columns = list(df.columns)
        self.numeric = {c for c in self.columns
                        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])}
        tones = list(tones) if tones is not None else [None] * len(df)
        links = list(links) if links is not None else [None] * len(df)
        self.rows = [
            {"cells": [_cell(v, fmt.get(c)) for c, v in zip(self.columns, vals)],
             "tone": tone, "href": href}
            for vals, tone, href in zip(df.itertuples(index=False), tones, links)
        ]


CHART_HEIGHT = 280


def chart(labels: Iterable, series: Mapping[str, Iterable], kind: str = "bar",
          money: bool = False) -> dict:
    """Chart.js 에 넘길 dict. 계열 색은 계열 이름(입고·출고·재고금액)으로 static/js/app.js 에서 정한다."""
    labels = [str(v) for v in labels]
    if not labels:
        return {"empty": True, "height": CHART_HEIGHT}
    return {"type": kind, "height": CHART_HEIGHT, "money": money, "labels": labels,
            "datasets": [{"label": name, "data": [0 if pd.isna(v) else float(v) for v in values]}
                         for name, values in series.items()]}


# ----------------------------------------------------------------------------
# 폼 · 쿼리 파싱
# ----------------------------------------------------------------------------
def f_str(name: str, default: str = "") -> str:
    return (request.form.get(name) or default).strip()


MAX_ID = 2 ** 63 - 1                      # DB 정수 한도 (이보다 큰 번호는 DB에 보내기 전에 거부)
MAX_NUMBER = 1e15                         # 수량·금액 한도 (nan·무한대·터무니없이 큰 값 거부)


def f_float(name: str, default: float = 0.0) -> float:
    raw = f_str(name).replace(",", "")
    try:
        value = float(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"숫자를 입력하세요 ({raw[:30]})") from exc
    if not math.isfinite(value) or abs(value) > MAX_NUMBER:
        raise ValueError(f"숫자를 다시 확인하세요 ({raw[:30]})")
    return value


def as_id(raw: str | None) -> int | None:
    """'123' → 123. 숫자가 아니거나 DB 정수 한도를 넘으면 None."""
    raw = (raw or "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and int(raw) <= MAX_ID else None


def f_id(name: str) -> int:
    """폼에서 고른 항목 번호(자재·창고 등). 숫자가 아니거나 너무 크면 ValueError."""
    raw = f_str(name)
    if not (raw.isascii() and raw.isdigit()) or int(raw) > MAX_ID:
        raise ValueError("선택한 항목이 올바르지 않습니다. 화면을 새로 고친 뒤 다시 선택하세요.")
    return int(raw)


def a_int(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name, "")
    return int(raw) if raw.isascii() and raw.isdigit() and int(raw) <= MAX_ID else default


def a_date(name: str, default: date) -> date:
    try:
        return date.fromisoformat(request.args.get(name, ""))
    except ValueError:
        return default


# ----------------------------------------------------------------------------
# 응답
# ----------------------------------------------------------------------------
def file_response(data: bytes, filename: str, mimetype: str):
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True,
                     download_name=filename)


def xlsx_response(data: bytes, filename: str):
    return file_response(data, filename, XLSX)


def form_response(form_key: str, df: pd.DataFrame, filename: str, period: str = ""):
    """회사 엑셀 양식(시스템관리자 설정)으로 채워 내려준다. 양식이 없으면 기본 양식."""
    from core import excel_forms
    data = excel_forms.export(form_key, df, {"user": g.user["name"] if g.get("user") else "", "period": period})
    return file_response(data, filename, XLSX)


def url_with(**changes) -> str:
    """현재 조회 조건을 유지한 채 일부 인자만 바꾼 주소 (값이 None이면 제거)."""
    args = request.args.to_dict(flat=False)
    for key, value in changes.items():
        if value is None:
            args.pop(key, None)
        else:
            args[key] = [value]
    return url_for(request.endpoint, **request.view_args, **args)


# ----------------------------------------------------------------------------
# 템플릿 등록
# ----------------------------------------------------------------------------
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def register_template_helpers(app: Flask) -> None:
    from core import once, version
    app.jinja_env.globals.update(
        csrf_token=csrf_token, once_token=once.new_token, ver=version.of_row, url_with=url_with, menus_for_user=menus_for_user, menu_layout=menu_layout, can=can, scope_all=scope_all,
        role_label=auth.role_label, ROLES=config.ROLES, SAP_STATUS=config.SAP_STATUS,
        LOGIN_MAX_FAILS=config.LOGIN_MAX_FAILS, LOGIN_LOCK_MINUTES=config.LOGIN_LOCK_MINUTES,
        APP_TITLE=config.APP_TITLE, APP_ICON=config.APP_ICON, TX_LABEL=config.TX_LABEL,
        DOC_TYPES=config.DOC_TYPES, DOC_NEED_BIZ_NO=config.DOC_NEED_BIZ_NO,
        DB_NAME=config.DB_PATH.name, today=date.today,
    )
    from core import demo, partners
    app.jinja_env.globals["DEMO_ROLE_VIEWS"] = demo.ROLE_VIEWS
    app.jinja_env.globals["partner_names"] = partners.names
    app.jinja_env.globals["PARTNER_KINDS"] = partners.KINDS
    # 화면 파일(css·js) 판 — 배포로 파일이 바뀌면 주소가 바뀌어 브라우저가 예전 파일을 쓰지 않는다
    from pathlib import Path
    static = Path(app.static_folder)
    app.jinja_env.globals["ASSET_V"] = str(int(max((f.stat().st_mtime for f in (static / "css").glob("*.css")),
                                                   default=0) + sum(f.stat().st_size for f in (static / "js").glob("*.js"))))

    def error_page(code: int, title: str, message: str):
        return render_template("error.html", code=code, title=title, message=message,
                               active=None), code

    app.register_error_handler(400, lambda e: error_page(400, "잘못된 요청", e.description))
    app.register_error_handler(403, lambda e: error_page(403, "권한 없음", e.description))
    # 예상하지 못한 오류: 내부 내용(경로·SQL 등)은 서버 로그에만 남기고 화면에는 일반 문구만
    app.register_error_handler(500, lambda e: error_page(500, "오류", "처리 중 오류가 발생했습니다. 관리자에게 문의하세요."))
    app.register_error_handler(404, lambda e: error_page(404, "없는 화면", "요청한 화면이 없습니다."))
    app.register_error_handler(413, lambda e: error_page(
        413, "파일이 너무 큽니다", f"업로드는 {config.MAX_CONTENT_LENGTH // 1024 // 1024}MB까지 가능합니다."))

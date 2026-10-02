"""조직·사용자 · ERP 연동 · 감사로그 · 데이터 관리 (관리자 전용)."""
from __future__ import annotations

import io
import os
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, flash, g, redirect, request, session, url_for

import config
from core import api_keys
from core import auth as core_auth
from core import company
from pandas import DataFrame as pd_frame
from core import database
from core import dataio
from core import enterprise as ent
from core import erp
from core import hr
from core import jobs
from core import sales_db as db
from core.storage import get_storage

from .helpers import (Table, a_int, a_str, csv_response, f_bool, f_int, f_str, render_page,
                      role_required, xlsx_response)

bp = Blueprint("admin", __name__, url_prefix="/admin")

ORG_TYPES = ["본부", "팀", "파트"]
AUDIT_ENTITIES = ["거래처", "영업기회", "영업활동", "매출", "목표", "승인", "사용자", "조직", "예측",
                  "파일", "증빙", "ERP", "시스템"]
AUDIT_LIMITS = [100, 300, 1000]


@bp.before_request
@role_required("ADMIN")
def _admin_only():
    return None


# ============================================================================
# 조직 · 사용자
# ============================================================================
@bp.route("/org")
def org():
    orgs = ent.list_orgs()
    users = ent.list_users(active_only=False)
    active = int(users["사용여부"].sum()) if not users.empty else 0
    edit_id = a_int("uid")
    target = ent.get_user(user_id=edit_id) if edit_id else None
    all_users = [(int(r.id), f"{r.이름} ({r.사번}){'' if r.사용여부 else ' · 퇴사'}")
                 for r in users.itertuples()] if not users.empty else []
    return render_page(
        "admin/org.html", "org", tab=request.args.get("tab", "orgs"),
        org_cnt=len(orgs), user_cnt=active, inactive_cnt=len(users) - active,
        orgs=Table(orgs, drop=["id", "parent_id"]),
        org_options=[(int(r.id), r.조직명) for r in orgs.itertuples()],
        org_types=ORG_TYPES,
        users=Table(users, drop=["id", "org_id", "역할코드"], link=("admin.org", "id", "uid")),
        target=target or {"active": 1, "role": "REP"}, roles=list(db.ROLES),
        all_users=all_users, active_users=[(o["id"], o["label"]) for o in g.assignable],
        password_mode=core_auth.AUTH_MODE == "password" or bool(
            target and core_auth.breakglass_enabled() and target.get("emp_no") in core_auth.breakglass_users()),
        locked=bool(target and (target.get("locked_until") or target.get("failed_logins"))),
        mfa_on=bool(target and target.get("totp_enabled_at")),
        **_hr_context(),
    )


# ── 인사(HR) 연동 ─────────────────────────────────────────────────────────
def _hr_key(token: str) -> str:
    return f"uploads/hr_{token}.json"


def _hr_context() -> dict:
    token = a_str("hr_token")
    preview, error = None, None
    if token and token == session.get("hr_token"):
        try:
            preview = hr.plan(hr.parse_feed(get_storage().get(_hr_key(token))))
        except (ValueError, KeyError, FileNotFoundError) as exc:
            error = str(exc)
    last = db._one("SELECT ts, actor, detail FROM audit_log WHERE action='인사연동' ORDER BY id DESC LIMIT 1")
    return {"hr_source": hr.source(), "hr_preview": preview, "hr_error": error, "hr_token": token if preview else "",
            "hr_last": last}


@bp.route("/hr/preview", methods=["POST"])
def hr_preview():
    upload = request.files.get("file")
    try:
        raw = upload.read() if upload and upload.filename else None
        feed = hr.parse_feed(raw) if raw else hr.load_feed()
    except (ValueError, OSError) as exc:
        flash(f"인사 데이터를 읽지 못했습니다: {exc}", "error")
        return redirect(url_for("admin.org", tab="hr"))
    import json
    import secrets as _secrets
    token = _secrets.token_hex(8)
    get_storage().put(_hr_key(token), json.dumps(feed, ensure_ascii=False).encode("utf-8"), "application/json")
    session["hr_token"] = token
    return redirect(url_for("admin.org", tab="hr", hr_token=token))


@bp.route("/hr/apply", methods=["POST"])
def hr_apply():
    token = f_str("hr_token")
    if not token or token != session.get("hr_token"):
        flash("미리보기를 다시 만든 뒤 반영하세요.", "error")
        return redirect(url_for("admin.org", tab="hr"))
    try:
        feed = hr.parse_feed(get_storage().get(_hr_key(token)))
        result = hr.apply_plan(feed, hr.plan(feed))
        get_storage().delete(_hr_key(token))
        session.pop("hr_token", None)
        flash("인사 정보를 반영했습니다: " + ", ".join(f"{k} {v}" for k, v in result.items() if v), "success")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.org", tab="hr", hr_token=token))
    return redirect(url_for("admin.org", tab="hr"))


@bp.route("/org/save", methods=["POST"])
def org_save():
    org_type = f_str("org_type")
    if org_type not in ORG_TYPES:
        abort(400, "조직 구분 값이 올바르지 않습니다.")
    try:
        ent.upsert_org(f_str("name"), f_int("parent_id") or None, org_type)
        flash(f"조직 '{f_str('name')}' 을 등록했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.org", tab="orgs"))


@bp.route("/org/delete", methods=["POST"])
def org_delete():
    org_id = f_int("org_id")
    if not org_id:
        abort(400, "삭제할 조직을 선택하세요.")
    ent.delete_org(org_id)
    flash("조직을 삭제했습니다(소속 사용자는 미배정으로 변경).", "warning")
    return redirect(url_for("admin.org", tab="orgs"))


@bp.route("/users/save", methods=["POST"])
def user_save():
    uid = f_int("id") or None
    if uid and uid == g.user["id"] and (f_str("role") != "ADMIN" or not f_bool("active")):
        flash("본인의 관리자 권한을 해제하거나 본인 계정을 비활성화할 수 없습니다.", "error")
        return redirect(url_for("admin.org", tab="users", uid=uid))
    try:
        new_id = ent.upsert_user({"id": uid, "emp_no": f_str("emp_no"), "name": f_str("name"),
                                  "role": f_str("role"), "org_id": f_int("org_id") or None,
                                  "email": f_str("email"), "active": f_bool("active")})
        uid = new_id
        temp = request.form.get("temp_password", "")
        if temp:
            core_auth.set_password(new_id, temp, must_change=core_auth.AUTH_MODE == "password")
        flash(f"사용자 '{f_str('name')}' 정보를 저장했습니다."
              + (" 임시 비밀번호를 설정했습니다(첫 로그인 때 변경)." if temp else ""), "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.org", tab="users", **({"uid": uid} if uid else {})))


@bp.route("/users/<int:uid>/mfa-reset", methods=["POST"])
def user_mfa_reset(uid: int):
    from core import mfa
    reason = f_str("reason")
    if not reason:
        flash("초기화 사유를 입력하세요 (예: 휴대폰 분실, 본인 확인 완료).", "error")
    elif uid == int(g.user["id"]):
        flash("본인의 2단계 인증은 다른 관리자가 초기화해야 합니다.", "error")
    else:
        mfa.disable(uid, g.user, reason)
        flash("2단계 인증을 초기화했습니다. 다음 로그인 때 다시 등록합니다.", "warning")
    return redirect(url_for("admin.org", tab="users", uid=uid))


@bp.route("/users/<int:uid>/unlock", methods=["POST"])
def user_unlock(uid: int):
    core_auth.unlock(uid)
    flash("로그인 잠금을 해제했습니다.", "success")
    return redirect(url_for("admin.org", tab="users", uid=uid))


@bp.route("/users/transfer", methods=["POST"])
def user_transfer():
    """담당자 이관: 퇴사·팀 이동 시 거래처와 진행 중 기회를 다른 사람에게 넘긴다."""
    try:
        result = ent.transfer_owner(f_int("from_id"), f_int("to_id"),
                                    include_closed=bool(f_bool("include_closed")))
        flash(f"이관 완료: 거래처 {result['거래처']}건, 영업기회 {result['영업기회']}건", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.org", tab="transfer"))


# ============================================================================
# ERP 연동
# ============================================================================
def _erp_render(**extra):
    status = a_str("status")
    queue = erp.list_outbox(status)
    ctx = dict(
        tab=request.args.get("tab", "outbox"), cfg=erp.settings(), summary=erp.outbox_summary(),
        status=status,
        queue=Table(queue, money=["금액"], select=("ids", "id") if status == "실패" else None,
                    highlight={"상태": {"실패": "danger"}}, page_size=100),
        missing_codes=int(db._scalar("SELECT COUNT(*) FROM customers WHERE COALESCE(erp_code,'')='' "
                                     "AND status <> '종료'")),
        recon=None,
    )
    ctx.update(extra)
    return render_page("admin/erp.html", "erp", **ctx)


@bp.route("/erp", endpoint="erp")
def erp_page():
    return _erp_render()


@bp.route("/erp/send", methods=["POST"])
def erp_send():
    try:
        result = erp.process_outbox()
        if result.get("message"):
            flash(result["message"], "warning")
        else:
            flash(f"ERP 전송: 성공 {result['sent']}건 · 실패 {result['failed']}건",
                  "warning" if result["failed"] else "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.erp"))


@bp.route("/erp/test", methods=["POST"])
def erp_test():
    ok, message = erp.test_connection()
    db.audit("ERP연결테스트", "ERP", None, {"성공": ok, "내용": message[:200]})
    flash(("ERP 연결 정상 — " if ok else "ERP 연결 실패 — ") + message, "success" if ok else "error")
    return redirect(url_for("admin.erp", tab="settings"))


@bp.route("/erp/retry", methods=["POST"])
def erp_retry():
    ids = [int(v) for v in request.form.getlist("ids") if v.isdigit()]
    for oid in ids:
        erp.retry(oid)
    flash(f"{len(ids)}건을 다시 전송 대상으로 돌렸습니다.", "success")
    return redirect(url_for("admin.erp", status="실패"))


@bp.route("/erp/template.csv")
def erp_payment_template():
    return csv_response(erp.payment_template(), "ERP입금대사_양식.csv")


@bp.route("/erp/reconcile", methods=["POST"])
def erp_reconcile():
    """ERP 입금 파일 대사: 먼저 결과를 보여 주고(검증), '반영' 을 누르면 차액만 입금 등록한다."""
    apply = request.form.get("apply") == "1"
    storage = get_storage()
    if apply:
        info = session.get("erp_recon")
        key = f"uploads/{info['file']}" if info else None
        if not key or not storage.exists(key):
            flash("대사할 파일이 없습니다. 다시 올려 주세요.", "error")
            return redirect(url_for("admin.erp", tab="payments"))
        name, data = info["name"], storage.get(key)
    else:
        upload = request.files.get("file")
        suffix = Path(upload.filename or "").suffix.lower() if upload else ""
        if not upload or suffix not in (".csv", ".xlsx", ".xls"):
            flash("ERP 입금 파일(CSV/Excel)을 선택하세요.", "error")
            return redirect(url_for("admin.erp", tab="payments"))
        name, data = upload.filename, upload.read()
        stored = f"erp_{datetime.now():%Y%m%d%H%M%S}_{g.user['id']}{suffix}"
        key = storage.put(f"uploads/{stored}", data)
        session["erp_recon"] = {"file": stored, "name": name}
    try:
        frame = dataio.read_upload(io.BytesIO(data), name)
        result = erp.reconcile_payments(frame, apply=apply)
    except Exception as exc:   # noqa: BLE001 - 사용자 파일 문제는 화면에 그대로 알린다
        flash(f"대사하지 못했습니다: {exc}", "error")
        return redirect(url_for("admin.erp", tab="payments"))
    if apply:
        storage.delete(key)
        session.pop("erp_recon", None)
        flash("ERP 입금을 반영했습니다.", "success")
    pending = int((result["결과"] == "반영예정").sum()) if not result.empty else 0
    return _erp_render(tab="payments", recon_name=name, recon_applied=apply, recon_pending=pending,
                       recon=Table(result, money=["CRM입금", "ERP입금"],
                                   highlight={"결과": {"확인필요": "danger", "미일치": "danger"}}))


# ============================================================================
# 배치 작업 (작업 큐 · 스케줄러)
# ============================================================================
@bp.route("/jobs")
def jobs_page():
    status = a_str("status")
    return render_page(
        "admin/jobs.html", "jobs", status=status, summary=jobs.summary(),
        schedules=Table(jobs.schedule_table()),
        workers=Table(jobs.workers(), highlight={"상태": {"응답 없음": "danger"}}),
        tbl=Table(jobs.list_jobs(status), select=("ids", "id") if status in ("실패", "취소") else None,
                  highlight={"상태": {"실패": "danger"}}, page_size=100),
        statuses=["대기", "실행중", "완료", "실패", "취소"])


bp.add_url_rule("/jobs", endpoint="jobs", view_func=jobs_page)


@bp.route("/jobs/action", methods=["POST"])
def jobs_action():
    action = request.form.get("action")
    if action == "tick":
        queued = jobs.tick()
        flash(f"예약 작업 점검: {', '.join(queued) if queued else '새로 올린 작업 없음'}", "success")
    elif action == "run":
        n = jobs.run_pending(limit=50)
        flash(f"대기 작업 {n}건을 이 서버에서 실행했습니다.", "success")
    elif action == "retry":
        ids = [int(v) for v in request.form.getlist("ids") if v.isdigit()]
        for jid in ids:
            jobs.retry(jid)
        flash(f"{len(ids)}건을 다시 대기로 돌렸습니다.", "success")
    elif action == "enqueue":
        kind = f_str("kind")
        if kind not in jobs.HANDLERS:
            abort(400, "알 수 없는 작업입니다.")
        jid = jobs.enqueue(kind, {"manual": g.user["name"]})
        db.audit("작업수동등록", "작업", jid, {"작업": kind})
        flash(f"'{kind}' 작업을 대기열에 올렸습니다. 워커가 곧 실행합니다.", "success")
    else:
        abort(400, "알 수 없는 작업입니다.")
    return redirect(url_for("admin.jobs"))


# ============================================================================
# 감사로그
# ============================================================================
# ============================================================================
# 회사 설정 (회사마다 다른 정책·코드)
# ============================================================================
@bp.route("/settings")
def settings():
    values = company.all_values()
    history = db._df("SELECT ts AS 일시, actor AS 변경자, detail AS 내용 FROM audit_log "
                     "WHERE action='설정변경' ORDER BY id DESC LIMIT 20")
    usage = {key: {row["v"]: int(row["n"]) for row in db._df(
        f"SELECT {col} AS v, COUNT(*) AS n FROM {table} WHERE {col} IS NOT NULL GROUP BY {col}").to_dict("records")}
        for key, (_a, table, col, _l) in company.CODE_LISTS.items()}
    pii_preview = company.purge_pii(dry_run=True)
    from core import entities as ent_mod
    from core import offline
    deps = pd_frame(offline.dependencies())
    return render_page("admin/settings.html", "settings", v=values, labels=company.LABELS,
                       deps=Table(deps, highlight={"위치": {"외부 인터넷": "danger"}}),
                       entities=ent_mod.list_entities(active_only=False), currencies=ent_mod.CURRENCIES[1:],
                       rates=Table(ent_mod.latest_rates()), edit_entity=ent_mod.get(a_int("eid")) or {"active": 1},
                       external=int((deps["위치"] == "외부 인터넷").sum()) if not deps.empty else 0,
                       code_lists=company.CODE_LISTS, usage=usage, open_stages=db.OPEN_STAGES,
                       history=Table(history), pii_preview=pii_preview)


@bp.route("/settings/save", methods=["POST"])
def settings_save():
    section = f_str("section")
    form = request.form
    if section == "company":
        changes = {k: form[k] for k in ("company_name", "company_biz_no", "company_ceo",
                                         "company_address", "app_title") if k in form}
    elif section == "policy":
        changes = {k: form[k] for k in ("discount_manager_max", "discount_exec_max", "approval_sla_hours",
                                         "quote_valid_days", "default_payment_terms", "pii_retention_years",
                                         "audit_retention_years", "backup_keep_daily", "backup_keep_monthly",
                                         "backup_keep_yearly", "fiscal_start_month") if k in form}
        if "mfa_present" in form:
            changes["mfa_required_roles"] = form.getlist("mfa_required_roles")
        probs = {s: form[f"prob_{s}"] for s in db.OPEN_STAGES if f"prob_{s}" in form}
        if probs:
            changes["stage_prob"] = probs
    elif section == "codes":
        changes = {k: form[k] for k in company.CODE_LISTS if k in form}
    else:
        abort(400)
    try:
        diff = company.save(changes, g.user["name"])
        flash(f"저장했습니다 ({len(diff)}개 항목 변경). 다른 서버에도 15초 안에 반영됩니다." if diff
              else "바뀐 값이 없습니다.", "success" if diff else "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.settings", _anchor=section))


@bp.route("/settings/entity", methods=["POST"])
def settings_entity():
    from core import entities as ent_mod
    try:
        ent_mod.upsert({k: request.form.get(k) for k in ("id", "code", "name", "biz_no", "ceo", "address",
                                                         "erp_company_code", "sap_sales_org")}
                       | {"active": 1 if request.form.get("active") else 0})
        flash("법인을 저장했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.settings", _anchor="entities"))


@bp.route("/settings/fx", methods=["POST"])
def settings_fx():
    from core import entities as ent_mod
    try:
        ent_mod.set_rate(f_str("currency"), f_str("rate_date"), f_str("rate"), f"수기 ({g.user['name']})")
        flash("환율을 저장했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.settings", _anchor="entities"))


@bp.route("/settings/purge", methods=["POST"])
def settings_purge():
    if not f_bool("confirm"):
        flash("파기 확인에 체크하세요.", "error")
        return redirect(url_for("admin.settings", _anchor="policy"))
    result = company.purge_pii()
    flash(f"고객 연락처 {result['purged']}건을 파기했습니다." if result["purged"] else
          result.get("message") or "파기할 대상이 없습니다.", "warning" if result["purged"] else "info")
    return redirect(url_for("admin.settings", _anchor="policy"))


# ============================================================================
# 개인정보 열람·삭제 요청
# ============================================================================
@bp.route("/privacy", methods=["GET", "POST"])
def privacy():
    from core import privacy as pv
    term = (request.form.get("term") or request.args.get("term") or "").strip()
    requester = f_str("requester")
    found = None
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "export":
                data, n = pv.export(term, requester, g.user["name"])
                return xlsx_response(data, f"개인정보열람_{date.today():%Y%m%d}.xlsx", rows=n, pii=True)
            if action == "erase":
                if not f_bool("confirm"):
                    raise ValueError("파기 확인에 체크하세요. 파기하면 되돌릴 수 없습니다.")
                r = pv.erase(term, requester, g.user["name"], request.form.getlist("cid"), request.form.getlist("aid"))
                flash(f"요청 #{r['request_id']}: 거래처 {r['customers']}건 · 활동 {r['activities']}건의 개인정보를 파기했습니다.",
                      "warning")
                return redirect(url_for("admin.privacy"))
        except ValueError as exc:
            flash(str(exc), "error")
    if term:
        try:
            found = pv.search(term)
        except ValueError as exc:
            flash(str(exc), "error")
    return render_page("admin/privacy.html", "privacy", term=term, requester=requester, found=found,
                       history=Table(pv.history()))


# ============================================================================
# 외부 연동 API 키
# ============================================================================
def _api_page(new_key: str | None = None, status: int = 200):
    users = ent.list_users(active_only=True)
    return render_page(
        "admin/api.html", "api", clients=Table(api_keys.list_clients(), drop=["id"]),
        revocable=[(int(r.id), f"{r.이름} ({r.키})") for r in api_keys.list_clients().itertuples()
                   if r.상태 == "사용"],
        scopes=api_keys.SCOPES, new_key=new_key,
        user_opts=[(int(u.id), f"{u.이름} ({u.역할})") for u in users.itertuples()] if not users.empty else [],
    ), status


@bp.route("/api")
def api_clients():
    return _api_page()


@bp.route("/api/create", methods=["POST"])
def api_create():
    try:
        _cid, key = api_keys.create_client(f_str("name"), f_int("user_id"), request.form.getlist("scopes"),
                                           f_int("rate_limit", 120), f_str("allowed_ips"))
    except ValueError as exc:
        flash(str(exc), "error")
        return _api_page(status=400)
    # 키는 이 응답에서 한 번만 보여 준다 (세션·로그에 남기지 않도록 리다이렉트하지 않는다)
    return _api_page(new_key=key)


@bp.route("/api/<int:cid>/revoke", methods=["POST"])
def api_revoke(cid: int):
    api_keys.revoke_client(cid)
    flash("API 키를 폐기했습니다. 이 키로 들어오는 호출은 바로 거부됩니다.", "warning")
    return redirect(url_for("admin.api_clients"))


@bp.route("/audit")
def audit():
    users = ent.list_users(active_only=False)
    actor, entity = a_str("actor"), a_str("entity")
    limit = a_int("limit", 300)
    limit = limit if limit in AUDIT_LIMITS else 300
    df = db.list_audit(limit, actor, entity if entity in AUDIT_ENTITIES else "")
    if request.args.get("export") == "audit":
        return csv_response(df, "감사로그.csv")
    return render_page(
        "admin/audit.html", "audit", actor=actor, entity=entity, limit=limit,
        actors=sorted(set(users["이름"].tolist())) if not users.empty else [],
        entities=AUDIT_ENTITIES, limits=AUDIT_LIMITS, tbl=Table(df, page_size=100),
        chain=db.verify_audit_chain() if request.args.get("verify") else None,
        archives=Table(_archives(), drop=["sha256"]), archive_check=_archive_check())


def _archives():
    from core import retention
    return retention.list_archives()


def _archive_check():
    aid = a_int("verify_archive")
    if not aid:
        return None
    from core import retention
    try:
        return {"id": aid, **retention.verify_archive(aid)}
    except (ValueError, FileNotFoundError) as exc:
        return {"id": aid, "ok": False, "reason": str(exc)}


@bp.route("/audit/archive", methods=["POST"])
def audit_archive_now():
    from core import retention
    try:
        result = retention.archive_audit(actor=g.user["name"])
        flash(f"감사로그 {result['archived']:,}건을 파일로 이관했습니다." if result["archived"]
              else result.get("message", "이관할 기록이 없습니다."), "success" if result["archived"] else "info")
    except (ValueError, RuntimeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.audit"))


# ============================================================================
# 데이터 관리
# ============================================================================
@bp.route("/data")
def data():
    folder = config.BACKUP_DIR
    backups = sorted([*folder.glob("sales_*.db"), *folder.glob("sales_*.dump")],
                     key=lambda f: f.name, reverse=True)[:10] if folder.exists() else []
    return render_page(
        "admin/data.html", "admin",
        cust_cnt=len(db.list_customers()), deal_cnt=len(db.list_deals()),
        sale_cnt=len(db.list_sales()), db_path=database.describe(), unlinked=ent.unlinked_counts(),
        backups=[(b.name, f"{b.stat().st_size / 1024 / 1024:,.1f}MB") for b in backups],
        production=config.PRODUCTION, backup_dir=folder)


@bp.route("/data/backup.xlsx")
def backup():
    """엑셀 백업. 고객 개인정보는 가린다(원본 보관은 DB 백업으로)."""
    sheets = {"거래처": dataio.mask_pii(db.list_customers()), "영업기회": db.list_deals(),
              "영업활동": db.list_activities(days=3650), "매출": db.list_sales(), "목표": db.list_targets()}
    return xlsx_response(dataio.to_excel(sheets), f"영업관리_백업_{date.today():%Y%m%d}.xlsx",
                         rows=sum(len(f) for f in sheets.values()))


@bp.route("/data/action", methods=["POST"])
def data_action():
    action = request.form.get("action")
    if action in ("seed_demo", "reset") and config.PRODUCTION:
        abort(403, "운영 환경에서는 샘플 데이터 생성과 전체 초기화를 쓸 수 없습니다.")
    try:
        if action == "seed_org":
            created = ent.seed_org_demo()
            flash(f"조직 {created['orgs']}개, 사용자 {created['users']}명을 생성했습니다.", "success")
        elif action == "snapshot":
            cnt = ent.take_snapshot(date.today().strftime("%Y-%m"))
            flash(f"스냅샷 {cnt}건 저장" if cnt else "스냅샷 대상 데이터가 없습니다.",
                  "success" if cnt else "warning")
        elif action == "backup_db":
            flash(f"DB 백업 완료: {os.path.basename(db.backup_database(config.BACKUP_DIR))}", "success")
        elif action == "seed_demo":
            created = db.seed_demo_data()
            flash("샘플 데이터 생성 완료: " + ", ".join(f"{k} {v}건" for k, v in created.items()), "success")
        elif action == "reset":
            if not f_bool("confirm"):
                flash("동의 체크 후 실행하세요.", "error")
            else:
                db.backup_database(config.BACKUP_DIR)      # 되돌릴 수 있도록 지우기 전에 백업
                db.reset_db()                               # 사용자 계정도 지워지므로 최초 설정 화면으로
                session.clear()
                flash("모든 업무 데이터를 삭제했습니다(직전 DB 백업과 감사로그는 남아 있습니다). "
                      "초기 조직과 계정을 다시 만드세요.", "warning")
                return redirect(url_for("auth.setup"))
        else:
            abort(400, "알 수 없는 작업입니다.")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.data"))

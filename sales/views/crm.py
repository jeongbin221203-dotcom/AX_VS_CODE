"""거래처 · 영업기회 · 영업활동."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import dataio
from core import enterprise as ent
from core import sales_db as db

from .helpers import (Table, a_int, a_str, chart, csv_response, f_bool, f_float, f_ids,
                      f_int, f_owner, f_str, render_page)

bp = Blueprint("crm", __name__)

CUST_STATUS = ["활성", "휴면", "종료"]


def visible_customer(cid: int | None) -> int:
    """접근범위 안의 거래처인지 확인한다 (폼 값 조작으로 다른 팀 데이터를 건드리지 못하게)."""
    if not cid or int(cid) not in db.customer_options():
        abort(403, "조회 권한이 없는 거래처입니다.")
    return int(cid)


def visible_deal(did: int | None) -> int:
    if not did or int(did) not in db.deal_options():
        abort(403, "조회 권한이 없는 영업기회입니다.")
    return int(did)


def deal_choices() -> list[dict]:
    """영업기회 선택 목록 (거래처별로 걸러 쓰도록 customer_id 포함)."""
    df = db.list_deals()
    return [{"id": int(r.id), "customer_id": int(r.customer_id), "label": f"{r.거래처} - {r.기회명}"}
            for r in df.itertuples()]


def _form_id(form: dict | None) -> int | None:
    """저장 실패로 다시 그릴 때 수정 중이던 레코드 id."""
    raw = str((form or {}).get("id") or "")
    return int(raw) if raw.isdigit() else None


# ============================================================================
# 거래처
# ============================================================================
CUSTOMER_FIELDS = ["name", "biz_no", "industry", "grade", "manager", "phone",
                   "email", "address", "status", "memo", "erp_code"]
PAGE_SIZE = 100


def _customers_page(form: dict | None = None, status: int = 200):
    keyword, grade, cstatus = a_str("q"), a_str("grade"), a_str("status")
    df = db.list_customers(keyword=keyword, owner_id=g.owner_filter, grade=grade, status=cstatus)
    # 목록·CSV 에서는 고객 측 연락처·이메일·담당자명을 가린다 (원문은 수정 화면에서만)
    df = dataio.mask_pii(df)
    if request.args.get("export") == "customers":
        return csv_response(df.drop(columns=["id", "owner_id"], errors="ignore"), "거래처목록.csv")

    options = db.customer_options()
    edit_id = a_int("id") or _form_id(form)
    edit_id = edit_id if edit_id in options else None
    tab = request.args.get("tab") or ("edit" if edit_id else "new")
    row = db.get_customer(edit_id) if edit_id else None

    # 신규 탭은 항상 빈 양식 (수정 중이던 거래처 값이 새 등록으로 새지 않도록)
    base = dict(row) if row and tab == "edit" else {
        "grade": "B", "status": "활성", "industry": db.INDUSTRIES[0],
        "payment_terms": 30, "credit_limit": 0, "owner_id": g.user["id"]}
    if form:
        base.update(form)

    ctx = dict(
        q=keyword, grade=grade, cstatus=cstatus, tbl=Table(
            df, money=["누적매출", "여신한도"], drop=["id", "owner_id"],
            link=("crm.customers", "id", "id"), page_size=PAGE_SIZE),
        options=options, edit_id=edit_id, tab=tab, f=base, statuses=CUST_STATUS,
    )
    if edit_id:
        ctx["cust_deals"] = Table(db.list_deals(customer_id=edit_id, only_open=True),
                                  money=["예상금액", "가중금액"],
                                  drop=["id", "customer_id", "종료일", "owner_id"])
        ctx["cust_acts"] = Table(db.list_activities(days=365, customer_id=edit_id).head(10),
                                 drop=["id", "customer_id", "owner_id"])
    return render_page("crm/customers.html", "customers", **ctx), status


@bp.route("/customers")
def customers():
    return _customers_page()


@bp.route("/customers/save", methods=["POST"])
def customer_save():
    cid = f_int("id") or None
    if cid:
        visible_customer(cid)
    data = {f: f_str(f) for f in CUSTOMER_FIELDS}
    data["status"] = data["status"] or "활성"
    try:
        data["credit_limit"] = f_int("credit_limit")
        data["payment_terms"] = f_int("payment_terms", 30)
        data["owner_id"] = f_owner()
        new_id = db.upsert_customer({"id": cid, "row_version": f_str("row_version") or None, **data})
    except ValueError as exc:
        flash(str(exc), "error")
        return _customers_page(form=dict(request.form), status=400)
    flash("저장했습니다." if cid else f"'{data['name']}' 거래처를 등록했습니다.", "success")
    return redirect(url_for("crm.customers", id=new_id))


@bp.route("/customers/<int:cid>/delete", methods=["POST"])
def customer_delete(cid: int):
    visible_customer(cid)
    if not f_bool("confirm"):
        flash("삭제하려면 '삭제 확인'을 체크하세요.", "error")
        return redirect(url_for("crm.customers", id=cid))
    try:
        db.delete_customer(cid)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("crm.customers", id=cid))
    flash("삭제했습니다.", "warning")
    return redirect(url_for("crm.customers"))


# ============================================================================
# 영업기회
# ============================================================================
def _deal_form_data(user: dict) -> dict:
    """폼 → upsert_deal 입력. 제안금액은 정가 × (1 - 할인율) 로 계산한다."""
    stage = f_str("stage") or "리드"
    if stage not in db.STAGES:
        raise ValueError(f"단계 값이 올바르지 않습니다: {stage}")
    list_amount = f_int("list_amount")
    discount = f_float("discount_rate")
    if not 0 <= discount <= 100:
        raise ValueError("할인율은 0~100 사이여야 합니다.")
    cid = f_int("customer_id") or None
    if cid:
        visible_customer(cid)
    return {
        "customer_id": cid, "title": f_str("title"), "owner_id": f_owner(),
        "stage": stage, "list_amount": list_amount, "discount_rate": discount,
        "amount": int(list_amount * (100 - discount) / 100),
        "probability": db.STAGE_PROB[stage], "expected_close": f_str("expected_close") or None,
        "source": f_str("source"), "competitor": f_str("competitor"), "memo": f_str("memo"),
        "forecast_category": f_str("forecast_category") or "Pipeline",
        "lost_reason": f_str("lost_reason") or None,
        **{m: f_bool(m) for m in db.MEDDIC_FIELDS},
    }


def _deals_page(form: dict | None = None, status: int = 200):
    keyword, stage, fcat = a_str("q"), a_str("stage"), a_str("fcat")
    only_open = request.args.get("open", "1") == "1"
    df = db.list_deals(keyword=keyword, owner_id=g.owner_filter, stage=stage, only_open=only_open)
    if fcat and not df.empty:
        df = df[df["예측구분"] == fcat]
    if request.args.get("export") == "deals":
        return csv_response(df.drop(columns=["customer_id", "owner_id"], errors="ignore"), "영업기회.csv")

    options = db.deal_options()
    edit_id = a_int("id") or _form_id(form)
    edit_id = edit_id if edit_id in options else None
    tab = request.args.get("tab") or ("edit" if edit_id else "new")
    row = db.get_deal(edit_id) if edit_id else None

    base = dict(row) if row and tab == "edit" else {
        "stage": "리드", "list_amount": 10_000_000, "discount_rate": 0.0,
        "expected_close": (date.today() + timedelta(days=30)).isoformat(),
        "owner_id": g.user["id"], "forecast_category": "Pipeline", "source": db.LEAD_SOURCES[0]}
    if form:
        base.update(form)
        for m in db.MEDDIC_FIELDS:                 # 체크 해제된 항목은 폼에 없다
            base[m] = 1 if form.get(m) else 0

    open_df = db.list_deals(owner_id=g.owner_filter, only_open=True)
    need = open_df
    if not need.empty:
        need = need[(need["할인율"] > 0) & (need["승인상태"].isin(["미요청", "반려"]))]
    history = None
    if edit_id:
        history = Table(db._df(
            "SELECT changed_at AS 변경일시, COALESCE(from_stage,'(신규)') AS 이전단계, "
            "to_stage AS 변경단계, days_in_stage AS 이전단계체류일, actor AS 변경자 "
            "FROM deal_stage_history WHERE deal_id=? ORDER BY id", [edit_id]))

    ctx = dict(
        q=keyword, stage=stage, fcat=fcat, only_open=only_open,
        m_count=len(df),
        m_total=int(df["예상금액"].sum()) if not df.empty else 0,
        m_weighted=int(df["가중금액"].sum()) if not df.empty else 0,
        m_score=int(df["검증점수"].mean()) if not df.empty else 0,
        m_stuck=int((df["단계체류일"] > 30).sum()) if not df.empty else 0,
        tbl=Table(df, money=["예상금액", "가중금액"], drop=["customer_id", "owner_id"],
                  link=("crm.deals", "id", "id"), page_size=PAGE_SIZE),
        options=options, edit_id=edit_id, tab=tab, row=row, f=base,
        customers=db.customer_options(), history=history,
        qual_score=db.qual_score(base),
        move_tbl=Table(open_df[["id", "거래처", "기회명", "단계", "예상금액", "검증점수",
                                  "예상마감일", "담당자"]] if not open_df.empty else open_df,
                         money=["예상금액"], drop=["id"], select=("ids", "id")),
        need=[(int(r.id), f"{r.거래처} · {r.기회명} · 할인 {r.할인율}% · {int(r.예상금액):,}원")
              for r in need.itertuples()] if not need.empty else [],
        is_admin=ent.has_role(g.user, "ADMIN"),
    )
    return render_page("crm/deals.html", "deals", **ctx), status


@bp.route("/deals")
def deals():
    return _deals_page()


@bp.route("/deals/save", methods=["POST"])
def deal_save():
    did = f_int("id") or None
    prev = None
    if did:
        visible_deal(did)
        prev = db.get_deal(did) or {}
    try:
        data = _deal_form_data(g.user)
        if prev is not None:
            data.update({"id": did, "closed_at": prev.get("closed_at"),
                         "row_version": f_str("row_version") or None})
        force = bool(f_bool("force")) and ent.has_role(g.user, "ADMIN")
        if force and not f_str("force_reason"):
            raise ValueError("단계 검증을 우회하려면 사유를 입력하세요 (감사로그에 남습니다).")
        new_id = db.upsert_deal(data, force=force, force_reason=f_str("force_reason"))
    except ValueError as exc:
        flash(str(exc), "error")
        return _deals_page(form=dict(request.form), status=400)
    flash("저장했습니다." if did else f"'{data['title']}' 기회를 등록했습니다.", "success")
    return redirect(url_for("crm.deals", id=new_id, tab="edit"))


@bp.route("/deals/<int:did>/delete", methods=["POST"])
def deal_delete(did: int):
    visible_deal(did)
    if not f_bool("confirm"):
        flash("삭제하려면 '삭제 확인'을 체크하세요.", "error")
        return redirect(url_for("crm.deals", id=did, tab="edit"))
    try:
        db.delete_deal(did)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("crm.deals", id=did, tab="edit"))
    flash("삭제했습니다.", "warning")
    return redirect(url_for("crm.deals"))


@bp.route("/deals/move", methods=["POST"])
def deal_move():
    ids = f_ids("ids")
    new_stage = f_str("stage")
    lost_reason = f_str("lost_reason") or None
    if new_stage not in db.STAGES:
        abort(400, "단계 값이 올바르지 않습니다.")
    if not ids:
        flash("대상 기회를 선택하세요.", "error")
        return redirect(url_for("crm.deals", tab="move"))
    visible = db.deal_options()
    ok, errors = 0, []
    for deal_id in ids:
        if deal_id not in visible:
            continue
        try:
            db.change_stage(deal_id, new_stage, lost_reason=lost_reason)
            ok += 1
        except ValueError as exc:
            errors.append(f"{visible[deal_id]}: {exc}")
    if ok:
        flash(f"{ok}건을 '{new_stage}' 단계로 변경했습니다.", "success")
    if errors:
        flash("다음 건은 조건 미충족으로 변경되지 않았습니다.\n" + "\n".join(errors[:5]), "error")
    return redirect(url_for("crm.deals", tab="move"))


@bp.route("/deals/request-approval", methods=["POST"])
def deal_request_approval():
    did = visible_deal(f_int("deal_id") or None)
    reason = f_str("reason")
    if not reason:
        flash("요청 사유를 입력하세요.", "error")
        return redirect(url_for("crm.deals", tab="approval"))
    try:
        ent.request_approval(did, g.user, reason)
        flash("결재를 요청했습니다. 결재자가 승인하면 수주 단계로 진행할 수 있습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.deals", tab="approval"))


# ============================================================================
# 영업활동
# ============================================================================
ACT_PERIODS = [7, 30, 90, 180, 365]


def _activities_page(form: dict | None = None, status: int = 200):
    days = a_int("days", 90)
    days = days if days in ACT_PERIODS else 90
    act_type = a_str("type")
    cid_filter = a_int("customer_id")
    df = db.list_activities(days=days, owner_id=g.owner_filter, act_type=act_type,
                            customer_id=cid_filter)
    if request.args.get("export") == "activities":
        return csv_response(df.drop(columns=["customer_id", "owner_id"], errors="ignore"), "영업활동.csv")

    by_type = df.groupby("유형").size().rename("건수").reset_index() if not df.empty else df
    base = {"act_date": date.today().isoformat(), "act_type": db.ACT_TYPES[0],
            "owner_id": g.user["id"],
            "next_date": (date.today() + timedelta(days=7)).isoformat()}
    if form:
        base.update(form)
    return render_page(
        "crm/activities.html", "activities",
        days=days, periods=ACT_PERIODS, act_type=act_type, cid_filter=cid_filter,
        m_count=len(df), m_customers=df["거래처"].nunique() if not df.empty else 0,
        m_upcoming=len(db.upcoming_actions(7, g.owner_filter)),
        type_chart=chart(by_type, "유형", "건수", money=False),
        tbl=Table(df, drop=["customer_id", "owner_id"], page_size=PAGE_SIZE),
        del_options=[(int(r.id), f"{r.활동일} · {r.거래처} · {r.유형} · {str(r.활동내용)[:20]}")
                     for r in df.itertuples()] if not df.empty else [],
        customers=db.customer_options(), deals=deal_choices(), f=base,
    ), status


@bp.route("/activities")
def activities():
    return _activities_page()


@bp.route("/activities/add", methods=["POST"])
def activity_add():
    try:
        cid = f_int("customer_id") or None
        if cid:
            visible_customer(cid)
        deal_id = f_int("deal_id") or None
        if deal_id:
            visible_deal(deal_id)
            if (db.get_deal(deal_id) or {}).get("customer_id") != cid:
                raise ValueError("선택한 영업기회가 해당 거래처의 기회가 아닙니다.")
        next_action = f_str("next_action")
        db.add_activity({"customer_id": cid, "deal_id": deal_id,
                         "act_date": f_str("act_date") or None, "act_type": f_str("act_type"),
                         "owner_id": f_owner(), "summary": f_str("summary"),
                         "next_action": next_action,
                         "next_date": (f_str("next_date") or None) if next_action else None})
    except ValueError as exc:
        flash(str(exc), "error")
        return _activities_page(form=dict(request.form), status=400)
    flash("활동을 등록했습니다.", "success")
    return redirect(url_for("crm.activities"))


@bp.route("/activities/delete", methods=["POST"])
def activity_delete():
    aid = f_int("activity_id")
    visible_ids = set(db.list_activities(days=3650)["id"].tolist())
    if aid not in visible_ids:
        abort(403, "조회 권한이 없는 활동입니다.")
    db.delete_activity(aid)          # 삭제 전 내용이 감사로그에 남는다
    flash("삭제했습니다.", "warning")
    return redirect(url_for("crm.activities"))


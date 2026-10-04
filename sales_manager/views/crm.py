"""거래처 · 영업기회 · 영업활동."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

from core import attachments as att
from core import company
from core import contacts as ct
from core import customer_names as cn
from core import credit
from core import dataio
from core import enterprise as ent
from core import sales_db as db

from .helpers import (BIG_SELECT, Table, a_int, a_str, cached, chart, csv_response, f_bool, f_float, f_ids,
                      f_int, f_owner, f_str, render_page, role_required)

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


class DealChoices(list):
    """영업기회 선택 목록. remote=True 면 전체가 아니라 고른 거래처 것만 담겨 있고, 화면이 거래처를 바꿀 때 서버에서 받아 온다."""
    remote = False


def deal_choices(customer_id: int | None = None) -> DealChoices:
    """영업기회 선택 목록 (거래처별로 걸러 쓰도록 customer_id 포함). BIG_SELECT 건을 넘으면 그 거래처 것만."""
    scope_sql, scope_params = db._scope_clause("d")
    sql = ("SELECT d.id, d.customer_id, c.name || ' - ' || d.title FROM deals d "
           "JOIN customers c ON c.id = d.customer_id WHERE 1=1" + scope_sql)
    with db.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM deals d WHERE 1=1{scope_sql}", scope_params).fetchone()[0]
        out = DealChoices()
        if total > BIG_SELECT:
            out.remote = True
            if not customer_id:
                return out
            rows = conn.execute(sql + " AND d.customer_id = ? ORDER BY d.id DESC", [*scope_params, int(customer_id)]).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY d.id DESC", scope_params).fetchall()
    out.extend({"id": int(r[0]), "customer_id": int(r[1]), "label": r[2]} for r in rows)
    return out


@bp.route("/options/<kind>")
def options(kind: str):
    """긴 선택 목록 검색 (거래처·영업기회) — 화면의 '입력해서 찾기'가 부른다. 본인 조회 범위 안에서만."""
    q = a_str("q").strip()
    limit = 30
    if kind == "customers":
        scope_sql, scope_params = db._scope_clause()
        sql, params = f"SELECT id, name, biz_no FROM customers WHERE merged_into IS NULL{scope_sql}", list(scope_params)
        if a_str("open") == "1":
            sql += " AND status <> '종료'"
        for word in q.split()[:5]:
            sql += " AND (name LIKE ? OR biz_no_norm LIKE ?)"
            params += [f"%{word}%", f"%{''.join(ch for ch in word if ch.isdigit()) or word}%"]
        with db.get_conn() as conn:
            rows = conn.execute(sql + " ORDER BY name LIMIT ?", [*params, limit]).fetchall()
        return jsonify([[int(r[0]), r[1] + (f" ({r[2]})" if r[2] else "")] for r in rows])
    if kind == "deals":
        scope_sql, scope_params = db._scope_clause("d")
        sql = ("SELECT d.id, c.name || ' - ' || d.title, d.customer_id FROM deals d JOIN customers c ON c.id = d.customer_id "
               "WHERE 1=1" + scope_sql)
        params = list(scope_params)
        if a_int("customer_id"):
            sql += " AND d.customer_id = ?"
            params.append(a_int("customer_id"))
            limit = 300
        for word in q.split()[:5]:
            sql += " AND (d.title LIKE ? OR c.name LIKE ?)"
            params += [f"%{word}%", f"%{word}%"]
        with db.get_conn() as conn:
            rows = conn.execute(sql + " ORDER BY d.id DESC LIMIT ?", [*params, limit]).fetchall()
        return jsonify([[int(r[0]), r[1], int(r[2])] for r in rows])
    abort(404)


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
        "payment_terms": company.get("default_payment_terms"), "credit_limit": 0, "owner_id": g.user["id"],
        **({"name": a_str("name")} if tab == "new" and a_str("name") else {})}     # 이름 정리에서 '새 거래처로 등록'
    if form:
        base.update(form)

    ctx = dict(
        q=keyword, grade=grade, cstatus=cstatus, tbl=Table(
            df, money=["누적매출", "여신한도"], drop=["id", "owner_id"],
            link=("crm.customers", "id", "id", {"tab": "edit"}), page_size=PAGE_SIZE),
        options=options, edit_id=edit_id, tab=tab, f=base, statuses=CUST_STATUS,
    )
    if edit_id:
        ctx["cust_deals"] = Table(db.list_deals(customer_id=edit_id, only_open=True),
                                  money=["예상금액", "가중금액"],
                                  drop=["id", "customer_id", "종료일", "owner_id"])
        ctx["contacts"] = ct.list_for(edit_id)
        ctx["aliases"] = cn.aliases_of(edit_id)
        ctx["contact_roles"] = ct.ROLES
        ctx["edit_contact"] = next((c for c in ctx["contacts"] if c["id"] == a_int("contact")), {})
        ctx["cust_acts"] = Table(db.list_activities(days=365, customer_id=edit_id).head(10),
                                 drop=["id", "customer_id", "owner_id"])
    if tab == "merge" and ent.has_role(g.user, "SUPPORT"):
        groups = db.duplicate_groups()
        for grp in groups:
            grp["options"] = [(int(c["id"]), f"#{c['id']} {c['name']}") for c in grp["거래처"]]
        ctx["dup_groups"] = groups
        ctx["all_customers"] = [(int(k), v) for k, v in db.customer_options(include_closed=False).items()]
    ctx["is_admin"] = ent.has_role(g.user, "ADMIN")
    ctx["can_merge"] = ent.has_role(g.user, "SUPPORT")
    ctx["unknown_cnt"] = int(db._scalar("SELECT COUNT(*) FROM unknown_names WHERE resolved_at IS NULL") or 0)         if ctx["can_merge"] else 0
    ctx["unknown"] = cn.list_unknown() if tab == "names" and ctx["can_merge"] else []
    return render_page("crm/customers.html", "customers", **ctx), status


@bp.route("/customers/<int:cid>/contacts", methods=["POST"])
def contact_save(cid: int):
    visible_customer(cid)
    try:
        if f_str("action") == "deactivate":
            ct.deactivate(cid, f_int("contact_id"))
            flash("담당자를 사용 안 함으로 바꿨습니다(기록은 남습니다).", "warning")
        else:
            ct.save(cid, {k: f_str(k) for k in ct.FIELDS} | {"is_primary": f_bool("is_primary")},
                    f_int("contact_id") or None)
            flash("담당자를 저장했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.customers", tab="edit", id=cid))


@bp.route("/customers/<int:cid>/unblock", methods=["POST"])
def customer_unblock_request(cid: int):
    visible_customer(cid)
    try:
        rid = credit.request_unblock(cid, f_str("reason"), g.user)
        flash(f"거래정지 해제 결재를 요청했습니다(요청 #{rid}).", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.customers", tab="edit", id=cid))


@bp.route("/customers/aliases/add", methods=["POST"])
@role_required("SUPPORT")
def alias_add():
    cid = f_int("customer_id")
    try:
        added = cn.add_alias(cid, f_str("alias"), g.user["name"])
        flash("다른 이름을 추가했습니다." if added else "정식 이름과 같은 표기라 따로 넣지 않았습니다.", "success" if added else "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.customers", tab="edit", id=cid, _anchor="edit"))


@bp.route("/customers/aliases/<int:aid>/delete", methods=["POST"])
@role_required("SUPPORT")
def alias_delete(aid: int):
    row = db._one("SELECT customer_id FROM customer_aliases WHERE id=?", [aid])
    cn.remove_alias(aid)
    flash("다른 이름에서 뺐습니다.", "success")
    return redirect(url_for("crm.customers", tab="edit", id=(row or {}).get("customer_id"), _anchor="edit"))


@bp.route("/customers/names/link", methods=["POST"])
@role_required("SUPPORT")
def unknown_link():
    cid = f_int("other_id") or f_int("customer_id")
    if not cid:
        flash("연결할 거래처를 고르세요.", "error")
        return redirect(url_for("crm.customers", tab="names"))
    try:
        cn.link_unknown(f_int("unknown_id"), cid, g.user["name"])
        flash("기존 거래처의 다른 이름으로 연결했습니다 — 다음 업로드부터 자동으로 맞춰집니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.customers", tab="names"))


@bp.route("/customers/names/dismiss", methods=["POST"])
@role_required("SUPPORT")
def unknown_dismiss():
    cn.dismiss_unknown(f_int("unknown_id"))
    flash("정리 목록에서 뺐습니다.", "info")
    return redirect(url_for("crm.customers", tab="names"))


@bp.route("/customers/merge", methods=["POST"])
def customer_merge():
    if not ent.has_role(g.user, "SUPPORT"):
        abort(403, "거래처 병합은 영업지원·시스템관리자만 할 수 있습니다.")
    try:
        result = db.merge_customers(f_int("source_id"), f_int("target_id"), f_str("reason"))
        moved = ", ".join(f"{k} {v}건" for k, v in result["이동"].items() if v) or "옮길 데이터 없음"
        flash(f"병합했습니다 — {moved}.", "success")
        for w in result["경고"]:
            flash(w, "warning")
        return redirect(url_for("crm.customers", id=f_int("target_id")))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("crm.customers", tab="merge"))


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
        data["payment_terms"] = f_int("payment_terms", company.get("default_payment_terms"))
        data["owner_id"] = f_owner()
        new_id = db.upsert_customer({"id": cid, "row_version": f_str("row_version") or None, **data},
                                    confirm_similar=f_bool("confirm_similar"))
    except db.SimilarCustomer as exc:
        flash(str(exc), "warning")
        return _customers_page(form={**request.form.to_dict(), "similar": exc.names}, status=409)
    except ValueError as exc:
        flash(str(exc), "error")
        return _customers_page(form=dict(request.form), status=400)
    if cid and f_str("trade_blocked_present") and ent.has_role(g.user, "MANAGER"):
        cur = db.get_customer(new_id)
        want = 1 if f_bool("trade_blocked") else 0
        if not cur.get("erp_synced_at") and int(cur.get("trade_blocked") or 0) != want:
            with db.get_conn() as conn:
                conn.execute("UPDATE customers SET trade_blocked=? WHERE id=?", (want, new_id))
            db.audit("거래정지" if want else "거래정지해제", "거래처", new_id, {"거래처명": cur["name"]})
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
    stage = f_str("stage") or db.OPEN_STAGES[0]
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
    df = cached("deals", (g.user["id"], str(g.scope), keyword, g.owner_filter, stage, only_open),
                lambda: db.list_deals(keyword=keyword, owner_id=g.owner_filter, stage=stage, only_open=only_open))
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
        "stage": db.OPEN_STAGES[0], "list_amount": "", "discount_rate": 0.0,
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

    view = "board" if a_str("view") == "board" else "list"
    ctx = dict(
        q=keyword, stage=stage, fcat=fcat, only_open=only_open, view=view,
        board=cached("deal_board", (g.user["id"], str(g.scope), g.owner_filter, keyword, stage, fcat),
                     lambda: _deal_board(keyword, stage, fcat)) if view == "board" else None,
        m_count=len(df),
        m_total=int(df["예상금액"].sum()) if not df.empty else 0,
        m_weighted=int(df["가중금액"].sum()) if not df.empty else 0,
        m_score=int(df["검증점수"].mean()) if not df.empty else 0,
        m_stuck=int((df["단계체류일"] > 30).sum()) if not df.empty else 0,
        tbl=Table(df, money=["예상금액", "가중금액"], drop=["customer_id", "owner_id"],
                  link=("crm.deals", "id", "id", {"tab": "edit"}), page_size=PAGE_SIZE),
        options=options, edit_id=edit_id, tab=tab, row=row, f=base,
        customers=db.customer_options(), history=history,
        deal_files=att.list_for("deal", [edit_id]) if edit_id else [], attach_kinds=att.KINDS,
        qual_score=db.qual_score(base),
        move_tbl=Table(open_df[["id", "거래처", "기회명", "단계", "예상금액", "검증점수",
                                  "예상마감일", "담당자"]] if not open_df.empty else open_df,
                         money=["예상금액"], drop=["id"], select=("ids", "id"), page_size=PAGE_SIZE),
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
    back = {k: request.form.get(f"_back_{k}") for k in ("view", "q", "stage", "fcat", "open")
            if request.form.get(f"_back_{k}")}                       # 보드·검색 조건을 유지한 채 돌아간다
    return redirect(url_for("crm.deals", id=new_id, tab="edit", **back))


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


BOARD_CLOSED_DAYS = 30          # 보드의 수주·실주 열에는 최근 30일에 끝난 기회만
BOARD_LIMIT = 60                # 열마다 카드는 마감이 가까운 순으로 이만큼만 (건수·합계는 전체, 나머지는 목록에서)


def _txt(v) -> str:
    return "" if v is None or (isinstance(v, float) and v != v) else str(v)


def _num(v) -> int:
    return 0 if v is None or (isinstance(v, float) and v != v) else int(v)


def _deal_board(keyword: str, stage: str, fcat: str) -> list[dict]:
    """영업기회 보드: 단계별 열. 진행 중 기회 전부 + 최근 끝난(수주·실주) 기회."""
    df = db.list_deals(keyword=keyword, owner_id=g.owner_filter, stage=stage, only_open=False)
    if fcat and not df.empty:
        df = df[df["예측구분"] == fcat]
    since = (date.today() - timedelta(days=BOARD_CLOSED_DAYS)).isoformat()
    cols = []
    for s in db.STAGES:
        part = df[df["단계"] == s] if not df.empty else df
        closed = s in (db.STAGE_WON, db.STAGE_LOST)
        if closed and not part.empty:
            part = part[part["종료일"].fillna("").astype(str) >= since]
        if not part.empty:
            part = part.sort_values(["예상마감일", "예상금액"], ascending=[True, False], na_position="last")
        total, total_sum = len(part), int(part["예상금액"].fillna(0).sum()) if not part.empty else 0
        part = part.head(BOARD_LIMIT) if not part.empty else part
        cards = [{"id": int(r["id"]), "customer": _txt(r["거래처"]), "title": _txt(r["기회명"]), "amount": _num(r["예상금액"]),
                  "owner": _txt(r["담당자"]), "close": _txt(r["예상마감일"])[:10], "days": _num(r["단계체류일"]),
                  "score": _num(r["검증점수"]), "lost": _txt(r.get("실주사유"))}
                 for r in part.to_dict("records")] if not part.empty else []
        cols.append({"stage": s, "prob": db.STAGE_PROB[s], "closed": closed, "cards": cards,
                     "count": total, "sum": total_sum, "more": total - len(cards)})
    return cols


@bp.route("/deals/stage", methods=["POST"])
def deal_stage():
    """보드에서 카드를 옮길 때 (한 건). 규칙은 '단계 변경' 탭과 같다(db.change_stage). 결과는 JSON."""
    deal_id = f_ids("id")[:1]
    new_stage = f_str("stage")
    if not deal_id or new_stage not in db.STAGES:
        return jsonify(ok=False, error="단계 값이 올바르지 않습니다."), 400
    deal_id = deal_id[0]
    if deal_id not in db.deal_options():
        return jsonify(ok=False, error="볼 수 없는 영업기회입니다."), 404
    try:
        db.change_stage(deal_id, new_stage, lost_reason=f_str("lost_reason") or None)
    except (ValueError, PermissionError) as exc:
        return jsonify(ok=False, error=str(exc)), 409
    deal = db.get_deal(deal_id)
    return jsonify(ok=True, id=deal_id, stage=deal["stage"], probability=deal["probability"])


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
        act_files=_activity_files(df), attach_kinds=att.KINDS,
        del_options=[(int(r.id), f"{r.활동일} · {r.거래처} · {r.유형} · {str(r.활동내용)[:20]}")
                     for r in df.head(BIG_SELECT).itertuples()] if not df.empty else [],
        customers=db.customer_options(), deals=deal_choices(base.get("customer_id")), f=base,
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
        new_act = db.add_activity({"customer_id": cid, "deal_id": deal_id,
                         "act_date": f_str("act_date") or None, "act_type": f_str("act_type"),
                         "owner_id": f_owner(), "summary": f_str("summary"),
                         "next_action": next_action,
                         "next_date": (f_str("next_date") or None) if next_action else None})
    except ValueError as exc:
        flash(str(exc), "error")
        return _activities_page(form=dict(request.form), status=400)
    saved, failed = 0, []
    for upload in request.files.getlist("files"):
        if upload and upload.filename:
            try:
                att.add("activity", int(new_act), upload.read(), upload.filename, f_str("attach_kind"), "", g.user)
                saved += 1
            except ValueError as exc:
                failed.append(f"{upload.filename}: {exc}")
    flash("활동을 등록했습니다." + (f" 첨부 {saved}개." if saved else ""), "success")
    for msg in failed:
        flash("첨부하지 못한 파일 — " + msg, "warning")
    return redirect(url_for("crm.activities"))


def _activity_files(df) -> dict:
    if df.empty:
        return {}
    rows = att.list_for("activity", [int(i) for i in df["id"].head(PAGE_SIZE * 4)])
    names = {int(r.id): f"{r.활동일} · {r.거래처} · {r.유형}" for r in df.itertuples()}
    out: dict = {}
    for r in rows:
        out.setdefault(names.get(int(r["entity_id"]), f"활동 #{r['entity_id']}"), []).append(r)
    return out


# ── 첨부 ─────────────────────────────────────────────────────────────────
@bp.route("/attachments/<entity>/<int:eid>", methods=["POST"])
def attachment_upload(entity: str, eid: int):
    if entity not in att.ENTITIES:
        abort(404)
    back = url_for("crm.deals", tab="edit", id=eid) if entity == "deal" else url_for("crm.activities", tab="files")
    files = [u for u in request.files.getlist("files") if u and u.filename]
    if not files:
        flash("올릴 파일을 고르세요.", "error")
        return redirect(back)
    for upload in files:
        try:
            att.add(entity, eid, upload.read(), upload.filename, f_str("kind"), f_str("memo"), g.user)
            flash(f"'{upload.filename}' 을(를) 첨부했습니다.", "success")
        except ValueError as exc:
            flash(f"{upload.filename}: {exc}", "error")
    return redirect(back)


@bp.route("/attachments/<int:aid>/file")
def attachment_file(aid: int):
    try:
        row = att.get(aid, with_data=True)
    except ValueError:
        abort(404)
    if not row["intact"]:
        db.audit("위변조의심", "첨부", aid, {"파일": row["file_name"]})
        abort(409, "첨부 파일이 올린 당시와 다릅니다(위변조 의심). 관리자에게 알리세요.")
    inline = row["mime"] in ("application/pdf", "image/jpeg", "image/png") and not request.args.get("download")
    if not inline:
        db.audit("다운로드", "첨부", aid, {"파일": row["file_name"]})
    from flask import send_file
    import io as _io
    res = send_file(_io.BytesIO(row["data"]), mimetype=row["mime"], as_attachment=not inline,
                    download_name=row["file_name"])
    res.headers["X-Frame-Options"] = "SAMEORIGIN"
    return res


@bp.route("/attachments/<int:aid>/void", methods=["POST"])
def attachment_void(aid: int):
    try:
        row = att.void(aid, f_str("reason"), g.user)
        flash("첨부를 무효 처리했습니다(기록은 남습니다).", "warning")
        back = url_for("crm.deals", tab="edit", id=row["entity_id"]) if row["entity"] == "deal" \
            else url_for("crm.activities", tab="files")
    except ValueError as exc:
        flash(str(exc), "error")
        back = request.referrer or url_for("crm.deals")
    return redirect(back)


@bp.route("/activities/delete", methods=["POST"])
def activity_delete():
    aid = f_int("activity_id")
    visible_ids = set(db.list_activities(days=3650)["id"].tolist())
    if aid not in visible_ids:
        abort(403, "조회 권한이 없는 활동입니다.")
    db.delete_activity(aid)          # 삭제 전 내용이 감사로그에 남는다
    flash("삭제했습니다.", "warning")
    return redirect(url_for("crm.activities"))


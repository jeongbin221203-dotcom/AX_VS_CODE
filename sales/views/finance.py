"""매출·채권·여신·증빙 · 목표 · 결재함."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, send_file, url_for

from core import catalog
from core import entities as ent_mod
from core import etax
from core import documents as docs
from core import enterprise as ent
from core import sales_db as db

from .crm import deal_choices, visible_customer, visible_deal
from .helpers import (Table, a_int, a_str, chart, csv_response, f_int, f_owner, f_str,
                      render_page, won)

bp = Blueprint("finance", __name__)

PAGE_SIZE = 100


# ============================================================================
# 매출 · 채권 · 여신
# ============================================================================
def _sale_filters() -> tuple[str, str, str]:
    months = g.months
    idx = months.index(g.ym) if g.ym in months else len(months) - 1
    ym_from = request.args.get("from") or months[max(idx - 5, 0)]
    ym_to = request.args.get("to") or g.ym
    return ym_from, ym_to, a_str("status")


def _sales_page(form: dict | None = None, status: int = 200):
    tab = request.args.get("tab", "records")
    ym_from, ym_to, sale_status = _sale_filters()
    df = db.list_sales(ym_from=ym_from, ym_to=ym_to, owner_id=g.owner_filter, status=sale_status)
    aging = ent.ar_aging()
    bucket = a_str("bucket")
    aging_view = aging if not bucket or aging.empty else aging[aging["연체구간"] == bucket]
    credit = ent.credit_exposure()

    export = request.args.get("export")
    if export == "sales":
        return csv_response(df.drop(columns=["customer_id", "owner_id"], errors="ignore"), "매출목록.csv")
    if export == "ar":
        return csv_response(aging_view.drop(columns=["id"], errors="ignore"), "미수채권.csv")
    if export == "credit":
        return csv_response(credit, "여신현황.csv")

    active = df[df["수금상태"] != db.SALE_CANCELLED] if not df.empty else df
    monthly = (active.assign(월=active["매출일"].str[:7]).groupby("월")["공급가액"].sum().reset_index()
               if not active.empty else active)
    edit_id = a_int("sid")
    edit_row, documents, customer_biz_no = None, [], None
    if edit_id:
        visible = db.list_sales()
        if not visible.empty and edit_id in set(visible["id"]):
            edit_row = visible[visible["id"] == edit_id].iloc[0].to_dict()
            edit_row["raw"] = db.get_sale(edit_id)
            documents = docs.list_documents(edit_id)
            customer_biz_no = (db.get_customer(int(edit_row["raw"]["customer_id"])) or {}).get("biz_no")

    summary = ent.ar_summary()
    ar_total = int(summary["미수금"].sum()) if not summary.empty else 0
    overdue = summary[summary["연체구간"] != "정상"]
    ar_overdue = int(overdue["미수금"].sum()) if not summary.empty else 0

    base = {"sale_date": date.today().isoformat(), "qty": 1, "unit_price": 1_000_000,
            "owner_id": g.user["id"], "status": db.SALE_STATUS[0]}
    if form:
        base.update(form)

    over = credit[credit["한도초과"] == "초과"] if not credit.empty else credit
    return render_page(
        "finance/sales.html", "sales", tab=tab,
        ym_from=ym_from, ym_to=ym_to, sale_status=sale_status,
        m_total=int(active["공급가액"].sum()) if not active.empty else 0,
        m_unpaid=int((active["합계"] - active["입금액"]).clip(lower=0).sum()) if not active.empty else 0,
        m_count=len(active),
        m_missing_docs=docs.missing_documents(ym_from, ym_to),
        monthly_chart=chart(monthly, "월", "공급가액"),
        tbl=Table(df, money=["단가", "공급가액", "부가세", "합계", "입금액"], drop=["customer_id", "owner_id"],
                  link=("finance.sales", "id", "sid"), page_size=PAGE_SIZE,
                  highlight={"수금상태": {db.SALE_CANCELLED: "muted"}}),
        edit_row=edit_row, documents=documents, doc_types=docs.DOC_TYPES, customer_biz_no=customer_biz_no,
        f=base, customers=db.customer_options(include_closed=False), deals=deal_choices(),
        products=catalog.product_options(), tax_types=db.TAX_TYPES,
        entity_opts=ent_mod.options(), currencies=ent_mod.CURRENCIES,
        etax_rows=etax.list_for_sale(edit_id) if edit_id else [], etax_on=etax.enabled(),
        # 채권
        ar_total=ar_total, ar_overdue=ar_overdue,
        ar_overdue_cnt=int(overdue["건수"].sum()) if not summary.empty else 0,
        ar_chart=chart(summary, "연체구간", "미수금")
        if not summary.empty and summary["미수금"].sum() > 0 else None,
        ar_summary=Table(summary, money=["미수금"]),
        bucket=bucket, buckets=db.AR_BUCKETS,
        aging=Table(aging_view, money=["매출액", "입금액", "미수금"], drop=["id"], page_size=PAGE_SIZE),
        pay_options=[(int(r.id), int(r.미수금),
                      f"{r.거래처} · {r.품목} · 미수 {int(r.미수금):,}원 "
                      f"(기일 {r.결제기일}, {int(r.경과일)}일 경과)")
                     for r in aging.itertuples()] if not aging.empty else [],
        # 여신
        credit_empty=credit.empty,
        credit_limit=int(credit["여신한도"].sum()) if not credit.empty else 0,
        credit_unpaid=int(credit["미수잔액"].sum()) if not credit.empty else 0,
        over=Table(over, money=["여신한도", "미수잔액"]), over_cnt=len(over),
        credit_chart=chart(credit.head(15), "거래처", "소진율", money=False),
        credit=Table(credit, money=["여신한도", "미수잔액"],
                     highlight={"한도초과": {"초과": "danger"}}, page_size=PAGE_SIZE),
    ), status


@bp.route("/sales")
def sales():
    return _sales_page()


@bp.route("/sales/add", methods=["POST"])
def sale_add():
    try:
        cid = f_int("customer_id") or None
        if cid:
            visible_customer(cid)
        deal_id = f_int("deal_id") or None
        if deal_id:
            visible_deal(deal_id)
        qty, unit_price = f_int("qty", 1), f_int("unit_price")
        if qty < 1 or unit_price < 0:
            raise ValueError("수량은 1 이상, 단가는 0 이상이어야 합니다.")
        if (f_str("currency") or "KRW") != "KRW" and not f_str("foreign_unit_price"):
            raise ValueError("외화 매출은 외화 단가를 입력하세요.")
        db.upsert_sale({"customer_id": cid, "deal_id": deal_id,
                        "sale_date": f_str("sale_date") or None, "item": f_str("item"),
                        "item_code": f_str("item_code"), "product_id": f_int("product_id") or None,
                        "tax_type": f_str("tax_type") or "과세",
                        "entity_id": f_str("entity_id") or None, "currency": f_str("currency") or "KRW",
                        "fx_rate": f_str("fx_rate") or None, "foreign_unit_price": f_str("foreign_unit_price") or None,
                        "qty": qty, "unit_price": unit_price, "amount": qty * unit_price,
                        "owner_id": f_owner(), "status": f_str("status") or db.SALE_STATUS[0],
                        "memo": f_str("memo")})
    except ValueError as exc:
        flash(str(exc), "error")
        return _sales_page(form=dict(request.form), status=400)
    supply = qty * unit_price
    total = supply + db.vat_for(supply, f_str("tax_type") or "과세")
    flash(f"매출 {won(supply)}(부가세 포함 {won(total)})을 등록했습니다. ERP 전송 대기열에 올라갔습니다.", "success")
    return redirect(url_for("finance.sales"))


@bp.route("/sales/<int:sid>/update", methods=["POST"])
def sale_update(sid: int):
    row = db.get_sale(sid)
    db.check_record_scope(row, "매출")
    try:
        db.upsert_sale({"id": sid, "customer_id": int(row["customer_id"]), "deal_id": row["deal_id"],
                        "sale_date": row["sale_date"], "item": row["item"],
                        "item_code": f_str("item_code") or row.get("item_code"),
                        "qty": int(row["qty"]), "unit_price": int(row["unit_price"]),
                        "amount": f_int("amount"), "owner_id": row["owner_id"], "owner": row["owner"],
                        "row_version": f_str("row_version") or None,
                        "product_id": row.get("product_id"), "quote_id": row.get("quote_id"),
                        "tax_type": f_str("tax_type") or row.get("tax_type") or "과세",
                        "status": f_str("status"), "memo": row["memo"], "due_date": row["due_date"]})
        flash("저장했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.sales", sid=sid))


@bp.route("/sales/<int:sid>/etax", methods=["POST"])
def sale_etax(sid: int):
    try:
        eid = etax.request_issue(sid, f_str("issue_date") or None, g.user)
        flash(f"전자세금계산서 발행을 요청했습니다(요청 #{eid}). 승인번호가 오면 증빙에 자동으로 붙습니다.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.sales", sid=sid))


@bp.route("/sales/<int:sid>/cancel", methods=["POST"])
def sale_cancel(sid: int):
    try:
        db.cancel_sale(sid, f_str("reason"))
        flash("매출을 취소했습니다. 기록은 '취소' 상태로 남습니다.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.sales", sid=sid))


@bp.route("/sales/payment", methods=["POST"])
def sale_payment():
    try:
        amount = f_int("amount")
        if amount <= 0:
            raise ValueError("입금액은 0보다 커야 합니다.")
        ent.record_payment(f_int("sale_id"), amount)
        flash(f"{won(amount)} 입금 처리했습니다.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.sales", tab="ar"))


# ── 증빙 (세금계산서 · 전자세금계산서) ─────────────────────────────────────
@bp.route("/sales/<int:sid>/documents", methods=["POST"])
def document_upload(sid: int):
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("증빙 파일(이미지·PDF·전자세금계산서 XML)을 선택하세요.", "error")
        return redirect(url_for("finance.sales", sid=sid))
    try:
        _doc_id, warnings = docs.add_document(sid, request.form.to_dict(), upload.read(),
                                              upload.filename, g.user)
        flash("증빙을 등록했습니다.", "success")
        if warnings:
            flash("확인이 필요한 항목:\n" + "\n".join(warnings), "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.sales", sid=sid))


@bp.route("/documents/<int:doc_id>/file")
def document_file(doc_id: int):
    """증빙 원본. 이미지·PDF 는 화면 안에서 보고, XML 은 내려받는다(브라우저 실행 차단)."""
    doc = docs.get_document(doc_id, with_data=True)
    if doc["data"] is None:
        abort(404)
    if not doc["intact"]:
        db.audit("위변조의심", "증빙", doc_id, {"파일": doc["file_name"], "sha256": doc["sha256"]})
        abort(409, "증빙 원본이 등록 당시와 다릅니다(위변조 의심). 관리자에게 알리세요.")
    inline = doc["mime"] in ("image/jpeg", "image/png", "application/pdf") and not request.args.get("download")
    if not inline:
        db.audit("다운로드", "증빙", doc_id, {"파일": doc["file_name"], "매출": doc["sale_id"]})
    response = send_file(io.BytesIO(doc["data"]), mimetype=doc["mime"], as_attachment=not inline,
                         download_name=doc["file_name"])
    response.headers["X-Frame-Options"] = "SAMEORIGIN"        # 매출 화면 안 미리보기만 허용
    return response


@bp.route("/documents/<int:doc_id>/void", methods=["POST"])
def document_void(doc_id: int):
    doc = docs.get_document(doc_id)
    try:
        docs.void_document(doc_id, f_str("reason"), g.user)
        flash("증빙을 무효 처리했습니다(기록은 남습니다).", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.sales", sid=doc["sale_id"]))


# ============================================================================
# 목표
# ============================================================================
@bp.route("/targets")
def targets():
    ym = g.ym
    perf = db.owner_performance(ym)
    current = db.list_targets(ym)
    target_map = {int(r.owner_id): int(r.목표금액) for r in current.itertuples() if pd.notna(r.owner_id)}
    trend = db.monthly_trend(12)
    total_t = int(perf["목표"].sum()) if not perf.empty else 0
    total_s = int(perf["매출"].sum()) if not perf.empty else 0
    from core import fiscal
    fy = a_int("fy") or fiscal.current_fy()
    fy_summary = fiscal.summary(fy)
    money_cols = [c for c in fy_summary.columns if c not in ("담당자", "달성률(%)")]
    return render_page(
        "finance/targets.html", "targets",
        fy=fy, fy_label=fiscal.label(fy), fy_options=[(y, fiscal.label(y)) for y in range(fy - 3, fy + 2)],
        fy_table=Table(fy_summary, money=money_cols), fy_months=fiscal.months(fy),
        rows=[(o["id"], o["label"], target_map.get(o["id"], 0)) for o in g.assignable],
        perf_chart=chart(perf, "담당자", ["매출", "목표"]),
        perf=Table(perf, money=["매출", "목표", "파이프라인"], drop=["owner_id"]),
        total_t=total_t, total_s=total_s,
        rate=(total_s / total_t * 100) if total_t else 0.0,
        trend_chart=chart(trend, "월", ["매출", "목표"]),
        trend=Table(trend, money=["매출", "목표"]),
    )


@bp.route("/targets/distribute", methods=["POST"])
def targets_distribute():
    from core import fiscal
    fy, period = f_int("fy"), f_str("period")
    quarter = int(period[1]) if period in ("Q1", "Q2", "Q3", "Q4") else None
    try:
        parts = fiscal.distribute(fy, quarter, f_int("owner_id"), f_int("amount"))
        flash(f"{fiscal.label(fy)} {period if quarter else '연간'} 목표를 {len(parts)}개월에 나눠 저장했습니다.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.targets", fy=fy))


@bp.route("/targets/save", methods=["POST"])
def targets_save():
    ids = request.form.getlist("owner_id")
    amounts = request.form.getlist("amount")
    saved, skipped = 0, []
    for raw_id, raw in zip(ids, amounts):
        if not raw_id.isdigit():
            continue
        try:
            amount = int(float(raw.replace(",", "") or 0))
            db.upsert_target(g.ym, int(raw_id), amount)       # 범위 밖·비활성 담당자는 여기서 거부된다
            saved += 1
        except ValueError as exc:
            skipped.append(str(exc))
    flash(f"{saved}명의 {g.ym} 목표를 저장했습니다.", "success")
    if skipped:
        flash("저장하지 않은 행:\n" + "\n".join(skipped), "warning")
    return redirect(url_for("finance.targets"))


# ============================================================================
# 결재함
# ============================================================================
@bp.route("/approvals")
def approvals():
    """결재함. 결재 권한은 결재선 단계별로 판단하므로 대결 지정을 받은 담당자도 여기서 결재한다."""
    user = g.user
    mine = ent.pending_for(user)
    requested = ent.list_approvals(requested_by_id=user["id"])
    hist_status = a_str("status")
    hist = ent.list_approvals(hist_status) if ent.has_role(user, "MANAGER") else requested
    hidden = ["id", "deal_id", "requested_by_id"]
    if request.args.get("export") == "history":
        return csv_response(hist.drop(columns=hidden, errors="ignore"), "결재이력.csv")

    pick = a_int("aid")
    ids = [int(i) for i in mine["id"].tolist()] if not mine.empty else []
    pick = pick if pick in ids else (ids[0] if ids else None)
    picked = mine[mine["id"] == pick].iloc[0].to_dict() if pick else None
    is_admin = ent.has_role(user, "ADMIN")
    delegations = ent.list_delegations(None if is_admin else int(user["id"]))
    users = ent.list_users(active_only=True)
    return render_page(
        "finance/approvals.html", "approvals", tab=request.args.get("tab", "mine"),
        mine_cnt=len(mine), requested_cnt=len(requested),
        mine_total=int(mine["제안가"].sum()) if not mine.empty else 0,
        mine=Table(mine, money=["정가", "제안가"], drop=hidden,
                   link=("finance.approvals", "id", "aid")),
        options=[(int(r.id), f"{r.거래처} · {r.기회명} · 할인 {r.할인율}% "
                             f"(정가 {int(r.정가):,} → {int(r.제안가):,}원)")
                 for r in mine.itertuples()] if not mine.empty else [],
        pick=pick, picked=picked,
        requested=Table(requested, money=["정가", "제안가"], drop=hidden),
        hist=Table(hist, money=["정가", "제안가"], drop=hidden, page_size=PAGE_SIZE),
        hist_status=hist_status, statuses=db.APPROVAL_STATUS[1:],
        delegations=Table(delegations, drop=["id", "from_user_id"]),
        revocable=[(int(r.id), f"{r.원결재자} → {r.대결자} ({r.시작}~{r.종료}, {r.상태})")
                   for r in delegations.itertuples() if r.상태 in ("진행중", "예정")
                   and (is_admin or int(r.from_user_id) == int(user["id"]))] if not delegations.empty else [],
        is_admin=is_admin, sla_hours=ent.sla_hours(),
        user_opts=[(int(u.id), f"{u.이름} ({db.ROLE_LABEL.get(u.역할코드, u.역할코드)})")
                   for u in users.itertuples()] if not users.empty else [],
        today_str=date.today().isoformat(),
    )


@bp.route("/approvals/<int:aid>/decide", methods=["POST"])
def approval_decide(aid: int):
    mine = ent.pending_for(g.user)
    if mine.empty or aid not in set(mine["id"]):
        abort(403, "결재 권한이 없거나 이미 처리된 건입니다.")
    approve = request.form.get("decision") == "approve"
    comment = f_str("comment")
    if not approve and not comment:
        flash("반려 시에는 사유를 입력해야 합니다.", "error")
        return redirect(url_for("finance.approvals", aid=aid))
    try:
        ent.decide_approval(aid, g.user, approve, comment)
        flash("승인 처리했습니다." if approve else "반려 처리했습니다.",
              "success" if approve else "warning")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.approvals"))


@bp.route("/approvals/delegations", methods=["POST"])
def delegation_add():
    from_id = f_int("from_user_id") if ent.has_role(g.user, "ADMIN") and f_int("from_user_id") else int(g.user["id"])
    try:
        ent.create_delegation(from_id, f_int("to_user_id"), f_str("start_date"), f_str("end_date"),
                              f_str("reason"), g.user)
        flash("대결자를 지정했습니다. 기간 동안 대결자가 결재할 수 있습니다.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.approvals", tab="delegation"))


@bp.route("/approvals/delegations/<int:did>/revoke", methods=["POST"])
def delegation_revoke(did: int):
    try:
        ent.revoke_delegation(did, g.user)
        flash("대결 지정을 취소했습니다.", "warning")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("finance.approvals", tab="delegation"))

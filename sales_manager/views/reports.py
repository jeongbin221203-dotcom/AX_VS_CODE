"""대시보드 · 매출예측 · 파이프라인 분석."""
from __future__ import annotations

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, url_for

from core import enterprise as ent
from core import erp, insights
from core import sales_db as db

from .helpers import Table, a_int, cached, chart, csv_response, f_ids, f_str, render_page

bp = Blueprint("reports", __name__)


# ----------------------------------------------------------------------------
# 대시보드
# ----------------------------------------------------------------------------

DASH_PAGE = 30      # 대시보드 '즉시 확인' 표는 30행씩 (전체 건수는 탭 이름에)

@bp.route("/")
def dashboard():
    ym, owner = g.ym, g.owner_filter

    def compute() -> dict:
        top = db.top_customers(ym, 10)
        aging = ent.ar_aging()
        return {
            "trend": db.monthly_trend(12, owner), "funnel": db.stage_funnel(owner), "perf": db.owner_performance(ym),
            "top_all": top.empty, "top": db.top_customers("", 10) if top.empty else top,
            "soon": db.deals_closing_soon(14, owner), "stale": db.stale_customers(30, owner),
            "upcoming": db.upcoming_actions(7, owner),
            # 자재관리 대시보드와 맞춘 항목: 최근 30일 일별 흐름 · 분류(품목군)별 구성 · 기한 임박
            "daily": insights.daily_sales(30, owner), "by_cat": insights.category_sales(ym, owner),
            "overdue": aging[aging["연체구간"] != "정상"].drop(columns=["id"]) if not aging.empty else aging,
            "expiring": insights.quotes_expiring(7, owner),
            "k": db.kpi_summary(ym, owner), "fc": ent.forecast_summary(ym, owner),
            "empty": not db._scalar("SELECT COUNT(*) FROM customers") and not db._scalar("SELECT COUNT(*) FROM sales"),
        }
    # 같은 사용자·범위·기준월이면 잠깐 기억 (저장이 있으면 바로 새로) — 결재 대기·ERP 실패는 매번
    d = cached("dashboard", (g.user["id"], g.user.get("role"), str(g.scope), ym, owner), compute)
    trend, funnel, perf, top, top_all = d["trend"], d["funnel"], d["perf"], d["top"], d["top_all"]
    soon, stale, upcoming, daily, by_cat = d["soon"], d["stale"], d["upcoming"], d["daily"], d["by_cat"]
    overdue, expiring, empty = d["overdue"], d["expiring"], d["empty"]
    pending = ent.pending_for(g.user)
    manager = ent.has_role(g.user, "MANAGER")
    erp_failed = erp.list_outbox("실패", 50) if manager else pd.DataFrame()

    return render_page(
        "reports/dashboard.html", "dashboard",
        k=d["k"], fc=d["fc"],
        trend_chart=chart(trend, "월", ["매출", "목표"]),
        trend=Table(trend, money=["매출", "목표"]),
        funnel_chart=chart(funnel, "단계", ["금액", "가중금액"]),
        funnel=Table(funnel, money=["금액", "가중금액"]),
        perf_chart=chart(perf, "담당자", ["매출", "목표"]),
        perf=Table(perf, money=["매출", "목표", "파이프라인"], drop=["owner_id"]),
        top_chart=chart(top, "거래처", "매출"), top=Table(top, money=["매출"]), top_all=top_all,
        soon=Table(soon, money=["예상금액"], page_size=DASH_PAGE), stale=Table(stale, page_size=DASH_PAGE),
        upcoming=Table(upcoming, page_size=DASH_PAGE),
        daily_chart=chart(daily, "일자", "매출"), daily=Table(daily, money=["매출"]),
        cat_chart=chart(by_cat, "품목군", "매출"), cat=Table(by_cat, money=["매출"]),
        overdue=Table(overdue, money=["청구액(VAT포함)", "입금액", "미수금"],
                      highlight={"연체구간": {"90일 초과": "danger", "61~90일": "warn"}}, page_size=DASH_PAGE),
        overdue_amount=int(overdue["미수금"].sum()) if not overdue.empty else 0,
        expiring=Table(expiring, money=["합계"], page_size=DASH_PAGE),
        pending=Table(pending, money=[c for c in ("금액", "예상금액") if c in pending.columns],
                      drop=[c for c in pending.columns if c == "id" or c.endswith("_id")], page_size=DASH_PAGE),
        erp_failed=Table(erp_failed, money=["금액"], drop=["id"], page_size=DASH_PAGE), manager=manager, empty=empty,
    )


# ----------------------------------------------------------------------------
# 매출예측
# ----------------------------------------------------------------------------
@bp.route("/forecast")
def forecast():
    ym, owner = g.ym, g.owner_filter
    fc = ent.forecast_summary(ym, owner)
    by_owner = ent.forecast_by_owner(ym)
    if request.args.get("export") == "by_owner":
        return csv_response(by_owner, f"예측_{ym}.csv")

    comp = pd.DataFrame({"구분": ["확정 매출", "Commit", "Best Case", "Pipeline"],
                         "금액": [fc["closed"], fc["commit"], fc["best_case"], fc["pipeline"]]})

    month_deals = db.list_deals(owner_id=owner, only_open=True)
    if not month_deals.empty:
        month_deals = month_deals[month_deals["예상마감일"].fillna("").str[:7] == ym]

    trend = ent.snapshot_trend(ym)
    trend_cols = [c for c in db.FORECAST_CATS if c in trend.columns]   # 범례 순서 고정
    acc = ent.forecast_accuracy(6)

    return render_page(
        "reports/forecast.html", "forecast", fc=fc,
        comp_chart=chart(comp, "구분", "금액"),
        by_owner=Table(by_owner, money=["목표", "확정매출", "Commit", "Best Case", "Pipeline",
                                        "Commit포함전망", "목표갭"]),
        month_deals=Table(month_deals[["id", "예측구분", "거래처", "기회명", "예상금액", "단계",
                                       "검증점수", "예상마감일", "담당자"]]
                          if not month_deals.empty else month_deals,
                          money=["예상금액"], drop=["id"], select=("ids", "id")),
        trend_chart=chart(trend, "기준일", trend_cols, kind="line"),
        trend=Table(trend, money=trend_cols),
        acc_chart=chart(acc, "월", "예측정확도", money=False),
        acc=Table(acc, money=["월초 Commit", "실제 매출"]),
        acc_avg=float(acc["예측정확도"].mean()) if not acc.empty else None,
        can_snapshot=ent.has_role(g.user, "MANAGER"),
        tab=request.args.get("tab", "category"),
    )


@bp.route("/forecast/category", methods=["POST"])
def forecast_category():
    ids = f_ids("ids")
    new_cat = f_str("category")
    if new_cat not in db.FORECAST_CATS:
        abort(400, "예측구분 값이 올바르지 않습니다.")
    if not ids:
        flash("대상 기회를 선택하세요.", "error")
        return redirect(url_for("reports.forecast", tab="category"))
    # 접근범위 밖의 기회 id 를 끼워 넣어도 반영되지 않도록 조회 가능한 id 만 남긴다
    visible = set(db.deal_options().keys())
    ids = [i for i in ids if i in visible]
    with db.get_conn() as conn:
        for deal_id in ids:
            conn.execute("UPDATE deals SET forecast_category=?, updated_at=? WHERE id=?",
                         (new_cat, db._now(), deal_id))
    db.audit("예측구분변경", "영업기회", None, {"건수": len(ids), "구분": new_cat})
    flash(f"{len(ids)}건을 '{new_cat}' 으로 변경했습니다.", "success")
    return redirect(url_for("reports.forecast", tab="category"))


@bp.route("/forecast/snapshot", methods=["POST"])
def forecast_snapshot():
    if not ent.has_role(g.user, "MANAGER"):
        abort(403, "스냅샷은 팀장 이상만 생성할 수 있습니다.")
    cnt = ent.take_snapshot(g.ym)
    flash(f"스냅샷 {cnt}건을 저장했습니다." if cnt else "스냅샷 대상 데이터가 없습니다.",
          "success" if cnt else "warning")
    return redirect(url_for("reports.forecast", tab="snapshot"))


# ----------------------------------------------------------------------------
# 파이프라인 분석
# ----------------------------------------------------------------------------
PERIODS = [90, 180, 365, 730]


@bp.route("/analytics")
def analytics():
    period = a_int("days", 365)
    period = period if period in PERIODS else 365
    conv = ent.stage_conversion(period)
    worst = conv[conv["전환율"].notna()].sort_values("전환율").head(1)
    wl = ent.win_loss_analysis(period)
    return render_page(
        "reports/analytics.html", "analytics", period=period, periods=PERIODS,
        v=ent.sales_velocity(period),
        conv_chart=chart(conv, "단계", "진입건수", money=False),
        dwell_chart=chart(conv, "단계", "평균체류일", money=False),
        conv=Table(conv),
        worst=worst.iloc[0].to_dict() if not worst.empty else None,
        reasons_chart=chart(wl["reasons"], "실주사유", "건수", money=False),
        reasons=Table(wl["reasons"], money=["금액"]),
        comp_chart=chart(wl["competitor"], "경쟁사", "승률", money=False),
        competitor=Table(wl["competitor"]),
        source=Table(wl["source"], money=["수주금액"]),
    )

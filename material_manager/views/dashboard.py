"""대시보드 화면.

기준월(?ym=)의 입출고 금액·전월 대비, 최근 30일·12개월 추이, 분류·창고별 재고금액, 출고 상위 자재,
그리고 '즉시 확인할 항목'(안전재고 미달 · 유효기한 · 소진 임박 · 장기 미사용 · 결재 대기 · 입고 예정 발주 · ERP 전송 실패).
"""

from datetime import date

import pandas as pd
from flask import Blueprint, g, request

import config
from core import insights, repository as repo, sap, services, valuation
from views.helpers import Table, can, chart, render_page
from views.stock import STOCK_FMT

bp = Blueprint("dashboard", __name__)

MONEY = "₩{:,.0f}"
COVER_DAYS = 14          # 소진 임박: 이 날 수 안에 재고가 바닥날 자재
DEAD_DAYS = 90           # 장기 미사용: 이 날 수 동안 출고가 없는 자재
INCOMING_DAYS = 14       # 입고 예정 발주: 필요일이 이 날 수 안이거나 지난 것


def _pct(now: float, before: float) -> float | None:
    return (now - before) / before * 100 if before else None


CACHE_SECONDS = 60                     # 집계 보관 (이 서버에서 저장하면 바로 비움, core/cache.py)
VALUED_SECONDS = 300                   # 재고평가 합계 (이동평균·선입선출 재계산이 무거워 5분)
TABLE_ROWS = 200                       # '즉시 확인' 표는 앞 200줄 (전체는 재고 현황·엑셀)


def _memo(name: str, fn, *args):
    from core import cache
    out = cache.memo(("dashboard", name, cache.wh_key(g.wh_ids), *args), CACHE_SECONDS, fn)
    return out.copy() if hasattr(out, "copy") else out


@bp.get("/")
def index():
    if g.wh_ids is not None and not g.wh_ids:          # 데이터 범위(플랜트·창고)를 아직 받지 못한 사용자
        return render_page("dashboard.html", "dashboard", empty=True, no_scope=True)
    stock = _memo("stock", lambda: repo.stock_df(wh_ids=g.wh_ids))
    if stock.empty:
        return render_page("dashboard.html", "dashboard", empty=True)

    months = insights.month_list(24)
    ym = request.args.get("ym", "")
    if ym not in months:
        ym = months[-1]
    tab = request.args.get("tab", "shortage")

    # ── 핵심 지표 ──
    amounts = _memo("amounts", lambda: insights.monthly_amounts(12, g.wh_ids, end_ym=ym), ym)
    cur, prev, same_period = _memo("mtd", lambda: insights.month_to_date(ym, g.wh_ids), ym)
    shortage = stock[stock["shortage"]].sort_values("shortage_qty", ascending=False)
    cover = _memo("cover", lambda: insights.stock_cover(stock, g.wh_ids, horizon=COVER_DAYS))
    dead = _memo("dead", lambda: insights.dead_stock(stock, g.wh_ids, days=DEAD_DAYS))

    # 재고평가(이동평균·선입선출) 기말 금액. 평가는 플랜트 단위라 창고 범위가 정해진 사용자에게는 보이지 않는다.
    valued = None
    if g.wh_ids is None:
        try:
            def _valued():
                vdf, _warn = valuation.report("2000-01-01", date.today().isoformat())
                return float(vdf["close_value"].sum()) if not vdf.empty else 0.0
            from core import cache                           # 거래 전체를 다시 평가 → 5분 보관(저장해도 유지)
            valued = cache.memo(("slow", "dashboard_valued", cache.wh_key(g.wh_ids)), VALUED_SECONDS, _valued)
        except Exception:                                           # 평가 실패가 대시보드를 막지 않게
            from flask import current_app
            current_app.logger.exception("대시보드 재고평가 계산 실패")

    # ── 추이 · 구성 ──
    daily = _memo("daily", lambda: insights.daily_amounts(30, g.wh_ids))
    has_daily = bool(daily[["입고금액", "출고금액", "조정금액"]].abs().to_numpy().sum())
    series = {k: daily[k] for k in ("입고금액", "출고금액", "조정금액") if k != "조정금액" or daily[k].abs().sum()}
    trend_chart = chart(daily["일자"].str[5:], series, money=True) if has_daily else None
    by_cat = stock.groupby("category")["stock_value"].sum().sort_values(ascending=False)
    whs = _memo("whs", lambda: insights.warehouse_values(g.wh_ids))
    top = _memo("top", lambda: insights.top_issues(ym, g.wh_ids), ym)
    top_all = top.empty
    if top_all:
        top = _memo("top_all", lambda: insights.top_issues("", g.wh_ids))

    # ── 즉시 확인 ──
    lots = _memo("lots", lambda: repo.stock_by_lot(wh_ids=g.wh_ids))
    lots = lots[lots["days_left"].notna()]
    expired = lots[lots["days_left"] < 0]
    expiring = lots[(lots["days_left"] >= 0) & (lots["days_left"] <= 30)]
    from core import workflow
    pending = pd.DataFrame(workflow.queue(g.user)) if can("CLERK") else pd.DataFrame()
    incoming = _memo("incoming", lambda: insights.incoming_po(g.wh_ids, INCOMING_DAYS))
    erp_fail, erp_fail_cnt = (sap.outbox_page(["FAILED", "ERROR"], g.wh_ids, page=1, size=50)
                              if g.sap_on and can("MANAGER") else (pd.DataFrame(), 0))

    checks = [("shortage", f"🚨 안전재고 미달 {len(shortage)}"),
              ("expiry", f"⏳ 유효기한 {len(expired) + len(expiring)}"),
              ("cover", f"📉 소진 임박 {len(cover)}"),
              ("dead", f"💤 장기 미사용 {len(dead)}"),
              ("incoming", f"🚚 입고 예정·지연 {len(incoming)}")]
    if can("CLERK"):
        checks.insert(4, ("approvals", f"✅ 내 결재 대기 {len(pending)}"))
    if g.sap_on and can("MANAGER"):
        checks.append(("erp", f"🔗 ERP 전송 실패 {erp_fail_cnt}"))
    if tab not in {k for k, _ in checks}:
        tab = "shortage"

    tables = {
        "shortage": Table(services.stock_display(shortage.head(TABLE_ROWS)), STOCK_FMT),
        "expiry": Table(pd.concat([expired, expiring])[["code", "name", "wh_code", "lot_no", "expiry_date", "days_left", "stock"]]
                        .rename(columns={"code": "자재코드", "name": "자재명", "wh_code": "창고", "lot_no": "로트",
                                         "expiry_date": "유효기한", "days_left": "남은 일수", "stock": "현재고"}),
                        {"현재고": "{:,.2f}", "남은 일수": "{:,.0f}"},
                        tones=["danger"] * len(expired) + ["warn"] * len(expiring)),
        "cover": Table(cover.head(TABLE_ROWS), {"현재고": "{:,.2f}", "하루 평균 출고": "{:,.2f}", "남은 일수": "{:,.1f}"},
                       tones=["danger" if d <= 3 else "warn" for d in cover["남은 일수"].head(TABLE_ROWS)]),
        "dead": Table(dead.head(TABLE_ROWS), {"현재고": "{:,.2f}", "재고금액": MONEY}),
        "incoming": Table(incoming, {"잔량": "{:,.2f}", "품목": "{}"},
                          tones=["danger" if s == "지연" else None for s in incoming["상태"]]),
    }
    if can("CLERK"):
        kinds = workflow.KIND
        tables["approvals"] = Table(
            pending.assign(kind=pending["kind"].map(kinds), via=pending["via"].fillna(""))[
                ["kind", "no", "step", "title", "amount", "requested_by", "requested_at", "via"]]
            .rename(columns={"kind": "종류", "no": "번호", "step": "단계", "title": "내용", "amount": "금액",
                             "requested_by": "요청자", "requested_at": "요청일시", "via": "대결"})
            if not pending.empty else pd.DataFrame(), {"금액": MONEY},
            links=list(pending["link"]) if not pending.empty else None,
            tones=["danger" if x else None for x in pending["late"]] if not pending.empty else None)
    if "erp" in {k for k, _ in checks}:
        tables["erp"] = Table(
            erp_fail[["tx_id", "tx_date", "code", "name", "wh_code", "status", "attempts", "last_error"]]
            .assign(status=lambda d: d["status"].map(config.SAP_STATUS))
            .rename(columns={"tx_id": "거래", "tx_date": "일자", "code": "자재코드", "name": "자재명", "wh_code": "창고",
                             "status": "상태", "attempts": "시도", "last_error": "오류"})
            if not erp_fail.empty else pd.DataFrame(), {"거래": "{}"})

    return render_page(
        "dashboard.html", "dashboard", empty=False, ym=ym, months=months[::-1], is_current=ym == months[-1],
        material_cnt=len(stock), stock_value=stock["stock_value"].sum(), shortage_cnt=len(shortage),
        valued=valued, val_label=config.VALUATION_METHODS.get(config.VALUATION_DEFAULT, ""),
        cur=cur, same_period=same_period,
        in_mom=_pct(cur["입고금액"], prev["입고금액"]), out_mom=_pct(cur["출고금액"], prev["출고금액"]),
        cover_cnt=len(cover), dead_value=dead["재고금액"].sum() if not dead.empty else 0, table_rows=TABLE_ROWS,
        tab_total={"shortage": len(shortage), "cover": len(cover), "dead": len(dead)}.get(tab, 0),
        dead_cnt=len(dead), cover_days=COVER_DAYS, dead_days=DEAD_DAYS, incoming_days=INCOMING_DAYS,
        trend_chart=trend_chart,
        trend=Table(daily[daily[["입고금액", "출고금액", "조정금액"]].abs().sum(axis=1) > 0],
                    {"입고금액": MONEY, "출고금액": MONEY, "조정금액": MONEY}),
        cat_chart=chart(by_cat.index, {"재고금액": by_cat.values}, money=True),
        cat=Table(by_cat.reset_index().rename(columns={"category": "분류", "stock_value": "재고금액"}), {"재고금액": MONEY}),
        month_chart=chart(amounts["월"], {"입고금액": amounts["입고금액"], "출고금액": amounts["출고금액"]}, money=True),
        month=Table(amounts, {"입고금액": MONEY, "출고금액": MONEY, "조정금액": MONEY}),
        wh_chart=chart(whs["창고"], {"재고금액": whs["재고금액"]}, money=True),
        wh=Table(whs, {"재고금액": MONEY, "자재 종수": "{:,}"}),
        top_chart=chart(top["자재명"], {"출고금액": top["출고금액"]}, money=True),
        top=Table(top, {"출고수량": "{:,.2f}", "출고금액": MONEY}), top_all=top_all,
        checks=checks, tab=tab, check_table=tables[tab],
    )

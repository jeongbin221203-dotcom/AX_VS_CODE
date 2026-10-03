"""재고 현황 화면."""

from datetime import date

import pandas as pd
from flask import Blueprint, g, request

from core import org, repository as repo, services
from views.helpers import Table, form_response, log_export, page_arg, pager, render_page

bp = Blueprint("stock", __name__, url_prefix="/stock")

STOCK_FMT = {"현재고": "{:,.2f}", "안전재고": "{:,.2f}", "부족수량": "{:,.2f}",
             "단가": "₩{:,.0f}", "재고금액": "₩{:,.0f}"}


WH_FMT = {"현재고": "{:,.2f}", "단가": "₩{:,.0f}", "재고금액": "₩{:,.0f}"}


@bp.get("/")
def index():
    """자재별(선택한 창고 합계) 또는 창고별 재고. 사용자 권한 범위 안의 창고만 보인다."""
    wh_opts = org.warehouse_options(g.wh_ids, active_only=False)
    wh_sel = [w for w in repo.ids_in(request.args.getlist("wh")) if w in wh_opts]
    scope = set(wh_sel) if wh_sel else g.wh_ids
    view_mode = request.args.get("view", "")
    if view_mode == "lot":
        return _lot_page(wh_opts, wh_sel, scope)
    by_wh = view_mode == "wh"
    keyword = request.args.get("q", "").strip()
    selected_cats = request.args.getlist("cat")
    only_shortage = request.args.get("shortage") == "1"

    df = repo.stock_by_wh(wh_ids=scope) if by_wh else repo.stock_df(wh_ids=scope)
    if df.empty and not by_wh:
        return render_page("stock.html", "stock", empty=True, by_lot=False)
    filtered = df
    if keyword:
        kw = keyword.lower()
        cols = ["code", "name"] + ([] if by_wh else ["spec", "location"]) + (["wh_code", "wh_name"] if by_wh else [])
        mask = pd.Series(False, index=filtered.index)
        for c in cols:
            mask |= filtered[c].fillna("").astype(str).str.lower().str.contains(kw, regex=False)
        filtered = filtered[mask]
    if selected_cats:
        filtered = filtered[filtered["category"].isin(selected_cats)]
    if only_shortage and not by_wh:
        filtered = filtered[filtered["shortage"]]

    view = services.stock_by_wh_display(filtered) if by_wh else services.stock_display(filtered)
    if request.args.get("export") == "xlsx":
        log_export("stock", len(view), by_warehouse=by_wh, warehouses=wh_sel or None)
        return form_response("stock_wh" if by_wh else "stock", view, f"재고현황_{date.today():%Y%m%d}.xlsx",
                             period=f"{date.today():%Y-%m-%d} 현재")

    p = pager(len(view), page_arg())
    rows = slice(p["first"] - 1 if p["total"] else 0, p["last"])
    page_view, page_df = view.iloc[rows], filtered.iloc[rows]
    tones = None if by_wh else ["danger" if s else None for s in page_df["shortage"]]
    return render_page(
        "stock.html", "stock", empty=False, by_wh=by_wh, by_lot=False, wh_opts=wh_opts, wh_sel=wh_sel,
        keyword=keyword, categories=sorted(df["category"].dropna().unique()),
        selected_cats=selected_cats, only_shortage=only_shortage,
        count=len(filtered), stock_value=filtered["stock_value"].sum(),
        shortage_cnt=0 if by_wh else int(filtered["shortage"].sum()), pager=p,
        grid=Table(page_view, WH_FMT if by_wh else STOCK_FMT, tones=tones),
    )


LOT_FMT = {"현재고": "{:,.2f}", "남은 일수": "{:,.0f}"}
EXPIRY_WARN_DAYS = 30


def _lot_page(wh_opts, wh_sel, scope):
    """로트별 재고와 유효기한 (기한 지남·임박 강조)."""
    df = repo.stock_by_lot(wh_ids=scope)
    keyword = request.args.get("q", "").strip().lower()
    if keyword:
        mask = pd.Series(False, index=df.index)
        for c in ("code", "name", "lot_no", "wh_code"):
            mask |= df[c].fillna("").astype(str).str.lower().str.contains(keyword, regex=False)
        df = df[mask]
    if request.args.get("expiring") == "1":
        df = df[df["days_left"].notna() & (df["days_left"] <= EXPIRY_WARN_DAYS)]
    view = df[["code", "name", "wh_code", "lot_no", "expiry_date", "days_left", "unit", "stock"]].copy()
    view.columns = ["자재코드", "자재명", "창고", "로트", "유효기한", "남은 일수", "단위", "현재고"]
    if request.args.get("export") == "xlsx":
        log_export("stock_lots", len(view), warehouses=wh_sel or None)
        return form_response("stock_lots", view, f"로트별재고_{date.today():%Y%m%d}.xlsx",
                             period=f"{date.today():%Y-%m-%d} 현재")
    tones = ["danger" if d is not None and d == d and d < 0 else ("warn" if d is not None and d == d and d <= EXPIRY_WARN_DAYS else None)
             for d in df["days_left"]]
    p = pager(len(view), page_arg())
    rows = slice(p["first"] - 1 if p["total"] else 0, p["last"])
    return render_page("stock.html", "stock", empty=False, by_wh=False, by_lot=True, wh_opts=wh_opts, wh_sel=wh_sel,
                       keyword=request.args.get("q", ""), categories=[], selected_cats=[], only_shortage=False,
                       expiring=request.args.get("expiring") == "1", count=len(view),
                       stock_value=0, shortage_cnt=0, pager=p,
                       grid=Table(view.iloc[rows], LOT_FMT, tones=tones[rows]))

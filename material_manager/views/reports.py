"""보고서: 수불부(월별 기초·입고·출고·조정·이동·기말) · 재고 대사(앱 ↔ SAP)."""

from datetime import date

import pandas as pd
from flask import Blueprint, current_app, flash, g, request

import config
from core import audit, excel_forms, org, reconcile, repository as repo
from core.utils import month_end, prev_month, xlsx_problem
from views.helpers import Table, actor, form_response, log_export, page_arg, pager, render_page, role_required

bp = Blueprint("reports", __name__, url_prefix="/reports")

LEDGER_COLS = {"code": "자재코드", "name": "자재명", "unit": "단위", "opening": "기초", "in_qty": "입고",
               "out_qty": "출고", "adj_qty": "조정", "trf_in": "이동입고", "trf_out": "이동출고",
               "closing": "기말", "unit_price": "단가", "closing_value": "기말금액"}
QTY = "{:,.2f}"
LEDGER_FMT = {**{v: QTY for v in ("기초", "입고", "출고", "조정", "이동입고", "이동출고", "기말")},
              "단가": "₩{:,.0f}", "기말금액": "₩{:,.0f}"}
REC_FMT = {v: QTY for v in ("앱 재고", "미전기", "SAP 예상", "SAP 재고", "차이")}


def _wh_scope():
    wh_opts = org.warehouse_options(g.wh_ids, active_only=False)
    wh_sel = [w for w in repo.ids_in(request.values.getlist("wh")) if w in wh_opts]
    return wh_opts, wh_sel, (set(wh_sel) if wh_sel else g.wh_ids)


@bp.get("/ledger")
def ledger():
    ym = request.args.get("ym") or prev_month(date.today().strftime("%Y-%m"))
    try:
        start, end = date.fromisoformat(f"{ym}-01").isoformat(), month_end(ym)
    except ValueError:
        ym = date.today().strftime("%Y-%m")
        start, end = f"{ym}-01", month_end(ym)
    wh_opts, wh_sel, scope = _wh_scope()
    df = repo.ledger_df(start, end, scope)
    view = df[list(LEDGER_COLS)].rename(columns=LEDGER_COLS)
    if request.args.get("export") == "xlsx":
        log_export("ledger", len(view), ym=ym, warehouses=wh_sel or None)
        return form_response("ledger", view, f"수불부_{ym}.xlsx", period=f"{start} ~ {end}")
    totals = {k: float(df[k].sum()) for k in ("opening", "in_qty", "out_qty", "adj_qty", "trf_in", "trf_out",
                                              "closing", "closing_value")}
    moved = {"in_n": int((df["in_qty"] != 0).sum()), "out_n": int((df["out_qty"] != 0).sum()),
             "adj_n": int(((df["adj_qty"] != 0) | (df["trf_in"] != 0) | (df["trf_out"] != 0)).sum())}
    pg = pager(len(view), page_arg())                 # 화면은 100건씩 (합계·엑셀은 전부)
    view = view.iloc[pg["first"] - 1:pg["last"]] if len(view) else view
    return render_page("ledger.html", "ledger", ym=ym, start=start, end=end, wh_opts=wh_opts, wh_sel=wh_sel,
                       grid=Table(view, LEDGER_FMT), totals=totals, count=len(df), pg=pg, moved=moved,
                       closed=g.closed_through and ym <= g.closed_through)


@bp.route("/reconcile", methods=["GET", "POST"])
@role_required("MANAGER")
def reconcile_view():
    wh_opts, wh_sel, scope = _wh_scope()
    result, source = None, ""
    if request.method == "POST":
        sap_stock, problem = _sap_stock_from_request()
        if problem:
            flash(problem, "error")
        else:
            source = "업로드 파일" if request.files.get("file") else f"SAP 조회({config.SAP_MODE})"
            result = reconcile.compare(sap_stock, scope)
            counts = result["verdict"].value_counts().to_dict()
            audit.log(actor(), "RECONCILE", "stock", "", {"source": source, "rows": len(result), **counts})
            if request.form.get("export") == "1":
                log_export("reconcile", len(result))
                return form_response("reconcile", reconcile.display(result), f"재고대사_{date.today():%Y%m%d}.xlsx",
                                     period=f"{date.today():%Y-%m-%d} 기준 · {source}")
    summary = result["verdict"].value_counts().to_dict() if result is not None else {}
    tones = ([("danger" if v.startswith(("차이", "SAP에만")) else None) for v in result["verdict"]]
             if result is not None else None)
    return render_page("reconcile.html", "reconcile", wh_opts=wh_opts, wh_sel=wh_sel, source=source,
                       summary=summary, sap_mode=config.SAP_MODE,
                       grid=Table(reconcile.display(result), REC_FMT, tones=tones) if result is not None else None)


def _sap_stock_from_request() -> tuple[pd.DataFrame, str]:
    file = request.files.get("file")
    if file and file.filename:
        name = file.filename.lower()
        data = file.read()
        if name.endswith(".xlsx"):
            problem = xlsx_problem(data, config.XLSX_MAX_UNCOMPRESSED, config.XLSX_MAX_RATIO)
            if problem:
                return pd.DataFrame(), problem
        raw, problem = excel_forms.read_import("sap_stock", data, name, config.UPLOAD_MAX_ROWS * 5)
        if problem:
            return pd.DataFrame(), problem
        return reconcile.normalize_sap_stock(raw)
    try:
        return reconcile.fetch_sap_stock(g.wh_ids)
    except Exception:
        current_app.logger.exception("SAP 재고 조회 실패")
        return pd.DataFrame(), "SAP 재고를 가져오지 못했습니다. 연계서버 상태를 확인하거나 엑셀로 올려 주세요."


VAL_COLS = {"code": "자재코드", "name": "자재명", "plant": "플랜트", "unit": "단위", "open_qty": "기초수량",
            "open_value": "기초금액", "receipts": "입고금액", "issues": "출고원가", "adjustments": "조정금액",
            "transfers": "이동금액", "close_qty": "기말수량", "unit_cost": "평가단가", "close_value": "기말금액"}
VAL_FMT = {**{v: "₩{:,.0f}" for v in ("기초금액", "입고금액", "출고원가", "조정금액", "이동금액", "기말금액", "평가단가")},
           "기초수량": QTY, "기말수량": QTY}


@bp.get("/valuation")
@role_required("MANAGER")
def valuation_view():
    """재고 평가 (이동평균 · 선입선출). 자재 × 플랜트, 기간 입고금액·출고원가·기말 재고자산."""
    from core import valuation
    ym = request.args.get("ym") or prev_month(date.today().strftime("%Y-%m"))
    try:
        start, end = date.fromisoformat(f"{ym}-01").isoformat(), month_end(ym)
    except ValueError:
        ym = date.today().strftime("%Y-%m")
        start, end = f"{ym}-01", month_end(ym)
    method = request.args.get("method") or config.VALUATION_DEFAULT
    plant_ids = None
    if g.wh_ids is not None:                          # 평가는 플랜트 단위 → 그 플랜트의 창고를 모두 볼 수 있어야 한다
        whs = org.warehouses_df()
        plant_ids = {int(p) for p in whs["plant_id"].unique()
                     if set(whs.loc[whs["plant_id"] == p, "id"].astype(int)) <= g.wh_ids}
    df, warnings = valuation.report(start, end, method, plant_ids)
    if plant_ids is not None and not plant_ids:
        warnings = ["재고 평가는 플랜트 단위입니다 — 한 플랜트의 모든 창고 권한이 있어야 금액이 보입니다 "
                    "(관리자에게 데이터 범위를 요청하세요)."] + list(warnings)
    view = df[list(VAL_COLS)].rename(columns=VAL_COLS) if not df.empty else pd.DataFrame(columns=list(VAL_COLS.values()))
    if request.args.get("export") == "xlsx":
        log_export("valuation", len(view), ym=ym, method=method)
        return form_response("valuation", view, f"재고평가_{method}_{ym}.xlsx",
                             period=f"{start} ~ {end} · {config.VALUATION_METHODS.get(method, method)}")
    totals = {k: float(df[k].sum()) if not df.empty else 0.0
              for k in ("open_value", "receipts", "issues", "adjustments", "close_value")}
    pg = pager(len(view), page_arg())                 # 자재가 많으면 화면은 100건씩 (합계·엑셀은 전부)
    if not df.empty:
        view = view.iloc[pg["first"] - 1:pg["last"]]
    return render_page("valuation.html", "valuation", ym=ym, start=start, end=end, method=method,
                       methods=config.VALUATION_METHODS, grid=Table(view, VAL_FMT), totals=totals,
                       warnings=warnings[:20], warn_count=len(warnings), pg=pg)

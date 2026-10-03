"""거래 이력 화면 (페이지 나누기 · 창고 범위 · 취소)."""

from datetime import date, timedelta

import pandas as pd
from flask import Blueprint, flash, g, redirect, request, url_for

import config
from core import org, repository as repo, services
from views.helpers import (Table, a_date, actor, as_id, f_str, form_response, log_export, page_arg, pager, render_page,
                           role_required, safe_next)

bp = Blueprint("history", __name__, url_prefix="/history")

RENAME = {"id": "ID", "tx_date": "일자", "tx_type": "구분", "wh_code": "창고", "code": "자재코드", "name": "자재명",
          "unit": "단위", "qty": "수량", "unit_price": "단가", "amount": "금액",
          "ref_no": "문서번호", "partner": "거래처/부서", "created_by": "담당자", "approved_by": "승인자",
          "note": "비고", "created_at": "등록일시", "doc_cnt": "증빙", "state": "상태",
          "po_no": "구매오더", "cost_center": "원가센터", "movement_type": "이동유형",
          "sap_status": "SAP", "sap_doc_no": "자재문서", "transfer_no": "이동번호", "lot_no": "로트"}
COLUMNS = ["id", "tx_date", "tx_type", "state", "wh_code", "code", "name", "lot_no", "unit", "qty", "unit_price", "amount",
           "ref_no", "partner", "po_no", "cost_center", "movement_type", "sap_status", "sap_doc_no",
           "created_by", "approved_by", "transfer_no", "note", "doc_cnt", "created_at"]
FMT = {"ID": "{}", "증빙": "{}", "수량": "{:,.2f}", "단가": "₩{:,.0f}", "금액": "₩{:,.0f}"}


def _decorate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["tx_type"] = [("이동" + config.TX_LABEL[t]) if tn else config.TX_LABEL[t]
                     for t, tn in zip(df["tx_type"], df["transfer_no"].fillna(""))]
    df["state"] = [f"취소거래(#{int(o)})" if pd.notna(o) else (f"취소됨(#{int(r)})" if pd.notna(r) else "")
                   for o, r in zip(df["reversal_of"], df["reversed_by"])]
    df["sap_status"] = df["sap_status"].map(config.SAP_STATUS).fillna("")
    df["sap_doc_no"] = df["sap_doc_no"].fillna("")
    return df


@bp.get("/")
def index():
    keyword = request.args.get("q", "").strip()[:60]
    # 묶음·생산 번호 링크(q만 있음)는 기간을 넓게
    start = a_date("start", date(2000, 1, 1) if keyword and "start" not in request.args else date.today() - timedelta(days=30))
    end = a_date("end", date.today())
    # 조회 버튼을 누르기 전(첫 진입)에는 전체 구분을 선택한 상태로 본다
    types = ([t for t in request.args.getlist("type") if t in config.TX_LABEL]
             if "start" in request.args else list(config.TX_LABEL))
    opts = services.material_options(active_only=False)
    mats = [m for m in repo.ids_in(request.args.getlist("material")) if m in opts]
    wh_opts = org.warehouse_options(g.wh_ids, active_only=False)
    wh_sel = [w for w in repo.ids_in(request.args.getlist("wh")) if w in wh_opts]
    scope = set(wh_sel) if wh_sel else g.wh_ids

    ctx = dict(start=start, end=end, types=types, opts=opts, mats=mats, wh_opts=wh_opts, wh_sel=wh_sel, df=None,
               keyword=keyword)
    if start > end:
        flash("시작일이 종료일보다 늦습니다.", "error")
        return render_page("history.html", "history", **ctx)
    if not types:
        flash("구분을 하나 이상 선택하세요.", "info")
        return render_page("history.html", "history", **ctx)

    if request.args.get("export") == "xlsx":
        full = _decorate(repo.history_df(start.isoformat(), end.isoformat(), types, mats, scope,
                                         limit=config.EXPORT_MAX_ROWS, keyword=keyword))
        log_export("history", len(full), start=start.isoformat(), end=end.isoformat())
        return form_response("history", full[COLUMNS].rename(columns=RENAME),
                             f"거래이력_{start:%Y%m%d}_{end:%Y%m%d}.xlsx", period=f"{start} ~ {end}")

    df, total, sums = repo.history_page(start.isoformat(), end.isoformat(), types, mats, scope,
                                        page=page_arg(), size=config.PAGE_SIZE, keyword=keyword)
    df = _decorate(df)
    view = df[COLUMNS].rename(columns=RENAME)
    reversible = df[df["reversal_of"].isna() & df["reversed_by"].isna()]
    return render_page(
        "history.html", "history", **{**ctx, "df": df},
        total=total, pager=pager(total, page_arg()), in_qty=sums["in_qty"], out_qty=sums["out_qty"],
        reversible=reversible.to_dict("records"),
        grid=Table(view, FMT, links=[url_for("documents.index", tx=int(i), start="2000-01-01")
                                     if c else None for i, c in zip(df["id"], df["doc_cnt"])],
                   tones=["muted" if s else None for s in df["state"]]),
    )


@bp.post("/reverse")
@role_required("MANAGER")
def reverse():
    """잘못 등록한 거래 취소. 원거래는 남기고 반대 거래를 오늘 일자로 기록한다."""
    back = safe_next(f_str("next"), url_for("history.index"), prefix="/history")
    tx_id = f_str("tx_id")
    if as_id(tx_id) is None:
        flash("취소할 거래를 선택하세요.", "error")
        return redirect(back)
    result = services.reverse_transaction(as_id(tx_id), f_str("reason"), actor=actor(), wh_ids=g.wh_ids)
    flash(result.message, "success" if result.ok else "error")
    return redirect(back)

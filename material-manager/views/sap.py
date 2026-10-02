"""ERP·SAP 연동 현황 · 연결 확인 · 지금 전송 · 재전송 · 마스터 동기화 (관리자)."""

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, url_for

import config
from core import audit, erp, master_sync, sap
from views.helpers import Table, actor, page_arg, pager, render_page, role_required

bp = Blueprint("sap", __name__, url_prefix="/sap")

COLS = {"id": "대기ID", "tx_id": "거래ID", "tx_date": "전기일", "tx_type": "구분", "movement_type": "이동유형", "wh_code": "창고",
        "code": "자재코드", "sap_matnr": "SAP자재", "qty": "수량", "unit": "단위", "status": "상태",
        "attempts": "시도", "sap_doc_no": "자재문서", "last_error": "오류", "next_try_at": "다음 시도",
        "updated_at": "갱신"}


@bp.get("/")
@role_required("MANAGER")
def index():
    statuses = [s for s in request.args.getlist("status") if s in config.SAP_STATUS]
    df, total = sap.outbox_page(statuses or None, g.wh_ids, page=page_arg(), size=config.PAGE_SIZE)
    view = df.copy()
    view["tx_type"] = [("취소 " if pd.notna(r) else "") + config.TX_LABEL.get(t, t)
                       for t, r in zip(df["tx_type"], df["reversal_of"])]
    view["status"] = view["status"].map(config.SAP_STATUS)
    view = view[list(COLS)].rename(columns=COLS)
    tones = [{"FAILED": "danger", "ERROR": "danger", "CANCELLED": "muted"}.get(s) for s in df["status"]]
    retryable = df[df["status"].isin(["ERROR", "FAILED"])][["id", "tx_id", "last_error"]].to_dict("records")
    return render_page("sap.html", "sap", mode=config.SAP_MODE, settings=erp.settings_view(), modes=erp.MODES,
                       summary=sap.summary(g.wh_ids), statuses=statuses, retryable=retryable, pager=pager(total, page_arg()),
                       movement=config.SAP_MOVEMENT_TYPES, reversal=config.SAP_REVERSAL_TYPES,
                       master=master_sync.status(), master_on=master_sync.enabled(),
                       grid=Table(view, {"대기ID": "{}", "거래ID": "{}", "수량": "{:,.2f}", "시도": "{}"},
                                  tones=tones))


@bp.post("/run")
@role_required("MANAGER")
def run():
    if g.wh_ids is not None:
        abort(403, "SAP 전송은 회사 전체 대기열을 보내므로 모든 창고 권한이 있는 관리자만 할 수 있습니다.")
    if not sap.enabled():
        flash("ERP 연동이 꺼져 있습니다 (MM_ERP_MODE=off).", "warning")
        return redirect(url_for("sap.index"))
    counts = sap.process_outbox()
    audit.log(actor(), "SAP_RUN", "sap_outbox", "", counts)
    if counts.get("config_error"):
        flash(f"연결 설정 오류로 보내지 못했습니다: {counts['config_error']}", "error")
        return redirect(url_for("sap.index"))
    flash(f"전송 결과 — 완료 {counts['sent']} · 재시도 예정 {counts['error']} · 실패 {counts['failed']} · "
          f"원거래 대기 {counts['waiting']}", "success" if not counts["failed"] else "warning")
    return redirect(url_for("sap.index"))


@bp.post("/test")
@role_required("MANAGER")
def test_connection():
    """설정만 확인하고 거래는 보내지 않는다."""
    if g.wh_ids is not None:
        abort(403, "연결 확인은 모든 창고 권한이 있는 관리자만 할 수 있습니다.")
    if not sap.enabled():
        flash("ERP 연동이 꺼져 있습니다 (MM_ERP_MODE=off).", "warning")
        return redirect(url_for("sap.index"))
    result = erp.ping()
    audit.log(actor(), "ERP_TEST", "erp", config.SAP_MODE, {"ok": result.ok, "message": result.message[:200]})
    flash(("연결 확인: " if result.ok else "연결 실패: ") + result.message, "success" if result.ok else "error")
    return redirect(url_for("sap.index"))


@bp.post("/<int:outbox_id>/retry")
@role_required("MANAGER")
def retry(outbox_id: int):
    msg = sap.retry(outbox_id, actor(), wh_ids=g.wh_ids)
    flash(msg or "재전송할 수 없는 상태입니다.", "success" if msg else "error")
    return redirect(url_for("sap.index"))


@bp.post("/master-sync")
@role_required("MANAGER")
def master_sync_now():
    if g.wh_ids is not None:
        abort(403, "자재 마스터는 회사 전체 데이터라 모든 창고 권한이 있는 관리자만 동기화할 수 있습니다.")
    if not master_sync.enabled():
        flash("ERP 마스터 동기화가 꺼져 있습니다 (MM_SAP_MASTER_SYNC=1).", "warning")
        return redirect(url_for("sap.index"))
    try:
        counts = master_sync.sync(actor())
    except erp.ErpError as exc:
        flash(f"ERP 마스터를 가져오지 못했습니다: {exc}", "error")
        return redirect(url_for("sap.index"))
    except Exception:
        from flask import current_app
        current_app.logger.exception("ERP 마스터 동기화 실패")
        flash("ERP 마스터를 가져오지 못했습니다. 연결 상태를 확인하세요.", "error")
        return redirect(url_for("sap.index"))
    flash(f"마스터 동기화 — 신규 {counts['created']} · 갱신 {counts['updated']} · 사용중지 {counts['deactivated']} · "
          f"원가센터 {counts['cost_centers']}", "success")
    return redirect(url_for("sap.index"))

"""증빙 화면 (세금계산서 · 전자세금계산서 등 이미지 등록 · 조회 · 거래 연결)."""

from datetime import date, timedelta

import io

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, send_file, url_for

import config
from core import audit, db, documents, hometax, repository as repo
from core.utils import xlsx_problem
from views.helpers import log_export, form_response, Table, a_date, a_int, actor, as_id, can, f_str, page_arg, pager, render_page, role_required

bp = Blueprint("documents", __name__, url_prefix="/documents")

TABS = [("list", "📋 목록"), ("new", "➕ 증빙 등록"), ("hometax", "🧾 홈택스 매입 대사")]


def _tabs():
    return TABS if can("MANAGER") else (TABS[:2] if can("CLERK") else TABS[:1])
LIST_COLS = {"id": "증빙ID", "issue_date": "작성일자", "doc_type": "종류", "supplier_name": "공급자",
             "supplier_biz_no": "사업자번호", "supply_amount": "공급가액", "tax_amount": "세액",
             "total_amount": "합계", "approval_no": "승인번호", "tx_label": "연결 거래", "file_name": "파일명"}
FMT = {"증빙ID": "{}", "공급가액": "₩{:,.0f}", "세액": "₩{:,.0f}", "합계": "₩{:,.0f}"}


def meta_from_form() -> dict:
    """폼 → documents.prepare() 입력. 입출고 등록 화면도 같은 칸 이름을 쓴다."""
    return {
        "doc_type": f_str("doc_type"), "issue_date": f_str("doc_issue_date"),
        "approval_no": f_str("approval_no"), "supplier_biz_no": f_str("supplier_biz_no"),
        "supplier_name": f_str("supplier_name"), "supply_amount": f_str("supply_amount"),
        "tax_amount": f_str("tax_amount"), "note": f_str("doc_note"), "tx_id": f_str("tx_id"),
    }


def uploaded_file() -> tuple[bytes, str]:
    file = request.files.get("doc_file")
    if not file or not file.filename:
        return b"", ""
    return file.read(config.DOC_MAX_BYTES + 1), file.filename


@bp.get("/")
def index():
    tab = request.args.get("tab", "list")
    if tab == "hometax" and can("MANAGER"):
        return render_page("documents_hometax.html", "documents", tabs=_tabs(), tab="hometax", result=None,
                           results=hometax.RESULT)
    if tab == "new" and can("CLERK"):
        keys = ("approval_no", "supplier_biz_no", "supplier_name", "supply_amount", "tax_amount", "doc_issue_date", "doc_type")
        form = {"tx_id": request.args.get("tx", ""), **{k: request.args.get(k, "")[:60] for k in keys if request.args.get(k)}}
        return _new_page(form=form)

    start = a_date("start", date.today() - timedelta(days=90))
    end = a_date("end", date.today())
    types = [t for t in request.args.getlist("type") if t in config.DOC_TYPES]
    keyword = request.args.get("q", "").strip()
    tx_id = a_int("tx")
    unlinked = request.args.get("unlinked") == "1"

    exporting = request.args.get("export") == "xlsx"
    df, total, sums = repo.documents_page(start.isoformat(), end.isoformat(), types, keyword, tx_id, unlinked,
                                          wh_ids=g.wh_ids, user_id=g.user["id"], page=1 if exporting else page_arg(),
                                          size=config.EXPORT_MAX_ROWS if exporting else config.PAGE_SIZE)
    view = df.copy()
    view["doc_type"] = view["doc_type"].map(config.DOC_TYPES)
    view["supplier_biz_no"] = view["supplier_biz_no"].map(documents.format_biz_no)
    view["tx_label"] = [f"#{int(t)} [{c}] {n}" if pd.notna(t) else "미연결"
                        for t, c, n in zip(df["tx_id"], df["code"], df["name"])]
    view = view[list(LIST_COLS)].rename(columns=LIST_COLS)
    if exporting:
        log_export("documents", len(view), start=start.isoformat(), end=end.isoformat())
        return form_response("documents", view, f"증빙_{start:%Y%m%d}_{end:%Y%m%d}.xlsx", period=f"{start} ~ {end}")
    return render_page(
        "documents.html", "documents", tabs=_tabs(), tab="list",
        start=start, end=end, types=types, keyword=keyword, tx_id=tx_id, unlinked=unlinked,
        count=total, supply_sum=sums["supply"], tax_sum=sums["tax"], unlinked_cnt=sums["unlinked"],
        pager=pager(total, page_arg()),
        grid=Table(view, FMT, tones=["muted" if pd.isna(t) else None for t in df["tx_id"]],
                   links=[url_for("documents.detail", doc_id=int(i)) for i in df["id"]]),
    )


def _new_page(form):
    return render_page("documents.html", "documents", tabs=_tabs(), tab="new", form=form,
                       tx_opts=repo.recent_tx_options(wh_ids=g.wh_ids), accept=documents.ACCEPT)


def _tx_in_scope(raw: str) -> bool:
    if not raw or g.wh_ids is None:
        return True
    with db.get_conn() as conn:
        tx = repo.get_transaction(conn, as_id(raw)) if as_id(raw) else None
    return tx is not None and tx["warehouse_id"] in g.wh_ids


def _visible_doc(doc_id: int) -> dict:
    """권한 밖 증빙은 '없는 것'으로 응답한다(존재 여부도 알려 주지 않는다)."""
    doc = repo.get_document(doc_id)
    if doc is None or not repo.doc_visible(doc, g.wh_ids, g.user["id"]):
        abort(404)
    return doc


@bp.post("/")
@role_required("CLERK")
def create():
    if not _tx_in_scope(f_str("tx_id")):
        flash("권한이 없는 창고의 거래에는 연결할 수 없습니다.", "error")
        return _new_page(form=request.form)
    data, filename = uploaded_file()
    prepared = documents.prepare(data, filename, meta_from_form())
    for msg in prepared.errors:
        flash(msg, "error")
    if not prepared.ok:
        return _new_page(form=request.form)
    for msg in prepared.warnings:
        flash(msg, "warning")
    saved = documents.save(prepared, actor=actor())
    flash(saved.message, "success" if saved.ok else "error")
    if not saved.ok:
        return _new_page(form=request.form)
    return redirect(url_for("documents.detail", doc_id=saved.doc_id))


@bp.get("/<int:doc_id>")
def detail(doc_id: int):
    doc = _visible_doc(doc_id)
    doc["tx_id"] = int(doc["tx_id"]) if pd.notna(doc["tx_id"]) else None   # 미연결은 NaN으로 읽힌다
    tx_opts = repo.recent_tx_options(wh_ids=g.wh_ids)
    if doc["tx_id"] is not None and doc["tx_id"] not in dict(tx_opts):
        # 오래된 거래라 최근 목록에 없어도 선택 상태를 유지해야 '연결 저장' 때 풀리지 않는다
        tx_opts.insert(0, (doc["tx_id"], f"#{doc['tx_id']} · {doc['tx_date']} [{doc['code']}] {doc['name']}"))
    return render_page("document_detail.html", "documents", doc=doc,
                       doc_type_label=config.DOC_TYPES.get(doc["doc_type"], doc["doc_type"]),
                       biz_no=documents.format_biz_no(doc["supplier_biz_no"]),
                       tx_opts=tx_opts,
                       file_missing=not documents.exists(doc))


@bp.get("/<int:doc_id>/file")
def file(doc_id: int):
    doc = _visible_doc(doc_id)
    download = request.args.get("download") == "1"
    data = documents.read(doc, actor(), download=download)     # 열람·다운로드는 감사로그에 남는다
    if data is None:
        abort(404)
    res = send_file(io.BytesIO(data), mimetype=doc["mime"], as_attachment=download,
                    download_name=doc["file_name"], max_age=0)
    res.headers["X-Content-Type-Options"] = "nosniff"     # 저장한 형식 그대로만 해석하게 한다
    res.headers["Cache-Control"] = "private, no-store"      # 세금계산서가 공용 캐시에 남지 않게
    if doc["mime"] != "application/pdf":
        # 이미지는 단독으로 열어도 스크립트가 돌 수 없게 격리 (PDF는 브라우저 뷰어가 필요해 제외)
        res.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return res


@bp.post("/<int:doc_id>/link")
@role_required("CLERK")
def link(doc_id: int):
    doc = _visible_doc(doc_id)
    raw = f_str("tx_id")
    if not raw and not can("MANAGER") and not (pd.notna(doc.get("created_by_id"))
                                              and int(doc["created_by_id"]) == g.user["id"]):
        flash("연결 해제는 관리자나 증빙을 올린 사람만 할 수 있습니다.", "error")
        return redirect(url_for("documents.detail", doc_id=doc_id))
    if raw and as_id(raw) is None:
        flash("거래 ID는 숫자로 입력하세요.", "error")
        return redirect(url_for("documents.detail", doc_id=doc_id))
    saved = documents.link(doc_id, as_id(raw), actor(), wh_ids=g.wh_ids)
    flash(saved.message, "success" if saved.ok else "error")
    return redirect(url_for("documents.detail", doc_id=doc_id))


@bp.post("/<int:doc_id>/delete")
@role_required("MANAGER")
def delete(doc_id: int):
    _visible_doc(doc_id)
    if request.form.get("confirm") != "1":
        flash("삭제하면 되돌릴 수 없음을 확인해 주세요.", "error")
        return redirect(url_for("documents.detail", doc_id=doc_id))
    saved = documents.delete(doc_id, actor())
    flash(saved.message, "success" if saved.ok else "error")
    return redirect(url_for("documents.index"))


@bp.post("/hometax")
@role_required("MANAGER")
def hometax_check():
    """홈택스 매입 전자세금계산서 목록 ↔ 증빙·입고 대사. 파일은 저장하지 않는다."""
    file = request.files.get("file")
    if not file or not file.filename:
        flash("홈택스에서 받은 매입 전자세금계산서 목록 파일을 고르세요.", "error")
        return redirect(url_for("documents.index", tab="hometax"))
    data = file.read()                                  # 요청 크기는 MAX_CONTENT_LENGTH(20MB)로 이미 제한
    if file.filename.lower().endswith(".xlsx"):
        problem = xlsx_problem(data, config.XLSX_MAX_UNCOMPRESSED, config.XLSX_MAX_RATIO)
        if problem:
            flash(problem, "error")
            return redirect(url_for("documents.index", tab="hometax"))
    inv, problem = hometax.read(data, file.filename)
    if problem or inv.empty:
        flash(problem or "목록에 계산서가 없습니다.", "error")
        return redirect(url_for("documents.index", tab="hometax"))
    result = hometax.reconcile(inv, g.wh_ids, g.user["id"])
    audit.log(actor(), "HOMETAX_CHECK", "document", "", {"invoices": len(inv), **result["counts"],
                                                         "period": f"{result['start']}~{result['end']}"})
    if request.form.get("export") == "1":
        view = hometax.export_df(result)
        log_export("hometax", len(view))
        return form_response("hometax", view, f"홈택스_매입대사_{result['start']}_{result['end']}.xlsx",
                             period=f"{result['start']} ~ {result['end']}")
    return render_page("documents_hometax.html", "documents", tabs=_tabs(), tab="hometax", result=result,
                       results=hometax.RESULT)

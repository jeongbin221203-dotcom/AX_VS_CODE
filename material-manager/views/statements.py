"""거래명세서 입출고 화면: 명세서 머리(입고/출고·창고·일자·거래처·번호) + 품목 줄(엑셀 또는 직접 입력)
→ 미리보기(자재 찾기·금액 확인, 자재 고르기) → 한꺼번에 등록. 규칙은 core/statements.py."""

import base64
import json
import re
import secrets
from datetime import date

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, session, url_for

import config
from core import documents, excel_forms, org, repository as repo, services, statements, storage
from core.utils import xlsx_problem
from views.helpers import Table, actor, f_str, form_response, render_page, role_required

bp = Blueprint("statements", __name__, url_prefix="/statements")

TABS = [("new", "명세서 등록"), ("list", "등록한 명세서")]
MANUAL_ROWS = 12
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
SESSION_KEY = "statement_token"


def _header_from_form() -> dict:
    return {"kind": f_str("kind") or "IN", "warehouse_id": f_str("warehouse_id"), "tx_date": f_str("tx_date"),
            "partner": f_str("partner"), "partner_biz_no": f_str("partner_biz_no"),
            "statement_no": f_str("statement_no"), "cost_center": f_str("cost_center"), "note": f_str("note")}


def _new_page(header: dict | None = None, rows: list[dict] | None = None, status: int = 200):
    header = header or {"kind": request.args.get("kind", "IN"), "tx_date": date.today().isoformat()}
    rows = rows or [{} for _ in range(MANUAL_ROWS)]
    return render_page("statements.html", "statements", tabs=TABS, tab="new", h=header, rows=rows,
                       wh_opts=org.warehouse_options(g.wh_ids), accept=documents.ACCEPT,
                       max_lines=statements.MAX_LINES), status


@bp.get("/")
@role_required("CLERK")
def index():
    if request.args.get("tab") == "list":
        df = statements.list_df(g.wh_ids)
        view = df.assign(kind=df["kind"].map(config.TX_LABEL), doc_id=df["doc_id"].map(lambda v: "있음" if pd.notna(v) else ""))
        view = view.rename(columns={"id": "번호", "tx_date": "일자", "kind": "구분", "partner": "거래처", "statement_no": "명세서번호",
                                    "wh_code": "창고", "line_count": "품목 수", "supply_amount": "공급가액", "tax_amount": "세액",
                                    "doc_id": "파일", "created_by": "등록자", "created_at": "등록일시"})
        return render_page("statements.html", "statements", tabs=TABS, tab="list",
                           grid=Table(view, {"번호": "{}", "공급가액": "₩{:,.0f}", "세액": "₩{:,.0f}"},
                                      links=[url_for("statements.detail", sid=i) for i in df["id"]]))
    return _new_page()


@bp.get("/template.xlsx")
@role_required("CLERK")
def template():
    df = pd.DataFrame([["PKG-001", "수출용 목재 팔레트", "1100x1100", 20, 18000, 360000, 36000, "", "", "", "", ""]],
                      columns=list(statements.LINE_COLS.values()))
    return form_response("statement_template", df, "거래명세서_품목_양식.xlsx")


def _lines_from_request() -> tuple[list[statements.Line], list[str]]:
    """엑셀 파일이 있으면 그 파일, 없으면 화면에 입력한 줄."""
    file = request.files.get("lines_file")
    if file and file.filename:
        name = file.filename.lower()
        if not name.endswith((".xlsx", ".csv")):
            return [], ["품목 파일은 엑셀(.xlsx) 또는 CSV만 올릴 수 있습니다."]
        data = file.read()
        if name.endswith(".xlsx"):
            problem = xlsx_problem(data, config.XLSX_MAX_UNCOMPRESSED, config.XLSX_MAX_RATIO)
            if problem:
                return [], [problem]
        raw, problem = excel_forms.read_import("statement_lines", data, name, statements.MAX_LINES * 2)
        if problem:
            return [], [problem]
        return statements.lines_from_frame(raw)
    rows = []
    for i in range(1, MANUAL_ROWS + 1):
        rows.append({c: request.form.get(f"{c}_{i}", "") for c in statements.LINE_COLS})
    df = pd.DataFrame(rows).rename(columns=statements.LINE_COLS)
    return statements.lines_from_frame(df)


def _evidence_meta(h: dict, lines: list[statements.Line]) -> dict:
    return {"doc_type": "STATEMENT", "issue_date": h["tx_date"], "supplier_name": h["partner"],
            "supplier_biz_no": h.get("partner_biz_no", ""), "approval_no": "",
            "supply_amount": str(int(sum(float(ln.supply or 0) for ln in lines))),
            "tax_amount": str(int(sum(float(ln.tax or 0) for ln in lines if ln.tax == ln.tax and ln.tax is not None))),
            "note": f"거래명세서 {h.get('statement_no') or ''}".strip()}


def _header_problem(h: dict) -> str:
    if h["kind"] not in ("IN", "OUT"):
        return "입고 또는 출고를 고르세요."
    if not h["warehouse_id"].isdigit() or int(h["warehouse_id"]) not in org.warehouse_options(g.wh_ids):
        return "창고를 고르세요 (권한이 있는 창고만)."
    try:
        date.fromisoformat(h["tx_date"])
    except ValueError:
        return "일자를 확인하세요."
    if not statements.norm_partner(h["partner"]):
        return "공급처(입고) 또는 납품처(출고)를 입력하세요."
    return ""


@bp.post("/preview")
@role_required("CLERK")
def preview():
    h = _header_from_form()
    problem = _header_problem(h)
    lines, errors = _lines_from_request()
    if problem or errors or not lines:
        flash(problem or (errors[0] if errors else "품목 줄을 한 줄 이상 입력하거나 엑셀 파일을 올리세요."), "error")
        return _new_page(h, [{c: request.form.get(f"{c}_{i}", "") for c in statements.LINE_COLS}
                             for i in range(1, MANUAL_ROWS + 1)], 200)
    statements.check(h["kind"], lines)
    dup = statements.duplicate_of(h["kind"], h["partner"], h["statement_no"])

    doc = request.files.get("doc_file")
    doc_bytes, doc_name = (doc.read(config.DOC_MAX_BYTES + 1), doc.filename) if doc and doc.filename else (b"", "")
    doc_errors = []
    if doc_bytes:
        prepared = documents.prepare(doc_bytes, doc_name, _evidence_meta(h, lines))
        doc_errors = prepared.errors

    token = secrets.token_hex(16)
    session[SESSION_KEY] = token                         # 미리보기를 본 사람만 등록할 수 있게
    storage.get().put(f"uploads/{token}.json", json.dumps({
        "header": h, "lines": [ln.to_dict() for ln in lines], "doc_name": doc_name,
        "doc": base64.b64encode(doc_bytes).decode() if doc_bytes and not doc_errors else "",
    }, ensure_ascii=False).encode("utf-8"))
    return _preview_page(token, h, lines, dup, doc_name, doc_errors)


def _preview_page(token, h, lines, dup, doc_name, doc_errors):
    with_err = sum(1 for ln in lines if ln.errors)
    supply = sum(float(ln.supply or 0) for ln in lines if ln.supply == ln.supply)
    similar = None if dup or h.get("statement_no") else statements.similar_of(h["kind"], h["partner"], h["tx_date"],
                                                                               supply, len(lines))
    mats = repo.list_materials(active_only=True)
    codes = {int(i): c for i, c in zip(mats["id"], mats["code"])}
    # 자재 목록은 한 번만 그린다(datalist) — 자재가 수만 개여도 줄마다 목록을 만들지 않는다
    return render_page("statements.html", "statements", tabs=TABS, tab="preview", token=token, h=h, lines=lines,
                       dup=dup, similar=similar, doc_name=doc_name, doc_errors=doc_errors, with_err=with_err,
                       wh=org.get_warehouse(int(h["warehouse_id"])), codes=codes,
                       mats=mats[["code", "name", "spec"]].to_dict("records"), supply=supply,
                       tax=sum(float(ln.tax or 0) for ln in lines if ln.tax is not None and ln.tax == ln.tax))


def _load(token: str) -> dict:
    if not TOKEN_RE.match(token) or not secrets.compare_digest(session.get(SESSION_KEY, ""), token):
        abort(400, "미리보기가 만료되었습니다. 명세서를 다시 올려 주세요.")
    raw = storage.get().get(f"uploads/{token}.json")
    if raw is None:
        abort(400, "이미 등록했거나 만료된 미리보기입니다.")
    return json.loads(raw.decode("utf-8"))


@bp.post("/apply")
@role_required("CLERK")
def apply():
    token = f_str("token")
    data = _load(token)
    h = data["header"]
    if _header_problem(h):                               # 그 사이 창고 권한이 바뀌었을 수 있다
        abort(403, _header_problem(h))
    lines = [statements.Line(**{k: v for k, v in d.items() if k in statements.Line.__dataclass_fields__})
             for d in data["lines"]]
    typed = {ln.no: f_str(f"material_{ln.no}").upper() for ln in lines}
    ids = statements.material_ids_by_code(list(typed.values()))
    overrides = {no: ids[code] for no, code in typed.items() if code in ids}
    statements.check(h["kind"], lines, overrides)
    for ln in lines:                                     # 없는 코드를 적은 줄
        if typed[ln.no] and typed[ln.no] not in ids:
            ln.material_id = None
            ln.errors = [f"자재코드 {typed[ln.no]}는 사용 중인 자재가 아닙니다."] + ln.errors
    if any(ln.errors for ln in lines):                   # 자재를 골라 다시 확인
        flash("확인이 필요한 줄이 남아 있습니다. 자재를 고르거나 명세서를 고쳐 다시 올리세요.", "error")
        return _preview_page(token, h, lines, statements.duplicate_of(h["kind"], h["partner"], h["statement_no"]),
                             data.get("doc_name", ""), [])
    evidence = None
    if data.get("doc"):
        evidence = documents.prepare(base64.b64decode(data["doc"]), data["doc_name"], _evidence_meta(h, lines))
        if not evidence.ok:
            evidence = None
    result = statements.register(h, lines, actor=actor(), wh_ids=g.wh_ids, evidence=evidence)
    if not result.ok:
        flash(result.message, "error")
        return _preview_page(token, h, lines, statements.duplicate_of(h["kind"], h["partner"], h["statement_no"]),
                             data.get("doc_name", ""), [])
    session.pop(SESSION_KEY, None)
    storage.get().delete(f"uploads/{token}.json")
    flash(result.message, "success")
    return redirect(url_for("statements.detail", sid=result.statement_id))


@bp.get("/<int:sid>")
@role_required("CLERK")
def detail(sid: int):
    st = statements.get(sid)
    if st is None or not statements.warehouse_ok(st, g.wh_ids):
        abort(404)
    df = statements.lines_df(sid)
    view = df.assign(state=["취소됨" if pd.notna(r) else "" for r in df["reversed_by"]]).drop(
        columns=["reversal_of", "reversed_by"]).rename(columns={
            "id": "거래", "code": "자재코드", "name": "자재명", "unit": "단위", "lot_no": "로트", "qty": "수량",
            "unit_price": "단가", "amount": "금액", "po_no": "발주번호", "po_item": "발주품목", "state": "상태"})
    return render_page("statements.html", "statements", tabs=TABS, tab="detail", st=st,
                       grid=Table(view, {"거래": "{}", "수량": "{:,.2f}", "단가": "₩{:,.0f}", "금액": "₩{:,.0f}"},
                                  tones=["muted" if pd.notna(r) else None for r in df["reversed_by"]]))

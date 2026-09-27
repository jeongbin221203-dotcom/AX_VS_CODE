"""데이터 일괄 등록(업로드) · 조건별 추출(다운로드)."""
from __future__ import annotations

import io
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Blueprint, abort, flash, g, redirect, request, session, url_for

from core import dataio
from core import excel_forms as xf
from core import enterprise as ent
from core import sales_db as db
from core.storage import get_storage

from .helpers import (Table, a_int, a_str, csv_response, f_bool, f_str, render_page,
                      xlsx_response)

bp = Blueprint("io", __name__, url_prefix="/data")

ALLOWED_EXT = {".csv", ".xlsx", ".xls"}


def _upload_key(token: str, ext: str) -> str:
    """검증~실행 사이에 파일을 두는 공용 저장소 키 (다른 서버가 실행을 받아도 같은 파일을 읽는다)."""
    return f"uploads/{token}{ext}"


def _pending_upload() -> tuple[dict, str] | tuple[None, None]:
    """검증 단계에서 저장해 둔 업로드 파일 (본인 세션의 것만)."""
    info = session.get("upload")
    if not info:
        return None, None
    key = _upload_key(info["token"], info["ext"])
    return (info, key) if get_storage().exists(key) else (None, None)


def _read_pending(info: dict, key: str) -> tuple[pd.DataFrame, int]:
    """(표준 컬럼 데이터, 엑셀 행 번호 보정값). 회사 양식을 골랐으면 그 양식으로 변환한다."""
    data = get_storage().get(key)
    form = xf.get_form(info["form_id"]) if info.get("form_id") else None
    if form:
        return xf.to_standard(form, data, info["name"])
    return dataio.read_upload(io.BytesIO(data), info["name"]), 2


def _discard_upload() -> None:
    info, key = _pending_upload()
    if key:
        get_storage().delete(key)
    session.pop("upload", None)


@bp.route("/")
def index():
    entity = request.args.get("entity") or next(iter(dataio.IMPORT_SPECS))
    if entity not in dataio.IMPORT_SPECS:
        abort(404)
    return render_page("io/index.html", "dataio", tab=request.args.get("tab", "import"),
                       entity=entity, spec=dataio.IMPORT_SPECS[entity],
                       entities=list(dataio.IMPORT_SPECS), check=None, **_import_ctx(entity), **_export_ctx())


def _import_ctx(entity: str, form_id: int | None = None) -> dict:
    forms = xf.list_forms("import", entity)
    return {"import_forms": forms, "form_id": form_id if form_id is not None else a_int("form_id")}


@bp.route("/template/form/<int:fid>.xlsx")
def form_template(fid: int):
    form = xf.get_form(fid)
    if not form or not form["active"]:
        abort(404)
    return xlsx_response(xf.blank_template(form), f"{form['name']}.xlsx")


@bp.route("/template/<entity>.<fmt>")
def template(entity: str, fmt: str):
    if entity not in dataio.IMPORT_SPECS or fmt not in ("csv", "xlsx"):
        abort(404)
    df = dataio.template_df(entity)
    if fmt == "csv":
        return csv_response(df, f"{entity}_업로드양식.csv")
    return xlsx_response(dataio.to_excel({entity: df}), f"{entity}_업로드양식.xlsx")


# ----------------------------------------------------------------------------
# 일괄 등록: 1) 검증(dry-run) → 2) 확인 후 실행
# ----------------------------------------------------------------------------
@bp.route("/import/check", methods=["POST"])
def import_check():
    entity = f_str("entity")
    if entity not in dataio.IMPORT_SPECS:
        abort(400, "지원하지 않는 항목입니다.")
    upload = request.files.get("file")
    ext = Path(upload.filename or "").suffix.lower() if upload else ""
    if not upload or ext not in ALLOWED_EXT:
        flash("CSV 또는 Excel 파일을 선택하세요.", "error")
        return redirect(url_for("io.index", entity=entity))

    form_id = int(f_str("form_id")) if f_str("form_id").isdigit() else None
    form = xf.get_form(form_id) if form_id else None
    if form and (form["direction"] != "import" or form["entity"] != entity or not form["active"]):
        abort(400, "선택한 양식이 이 항목용이 아닙니다.")
    _discard_upload()
    token = secrets.token_hex(16)
    data = upload.read()
    get_storage().put(_upload_key(token, ext), data)
    session["upload"] = {"token": token, "ext": ext, "entity": entity, "name": upload.filename, "form_id": form_id}

    try:
        raw, offset = _read_pending(session["upload"], _upload_key(token, ext))
    except Exception as exc:  # noqa: BLE001 - 사용자 파일 문제는 화면에 그대로 알린다
        _discard_upload()
        flash(f"파일을 읽지 못했습니다: {exc}", "error")
        return redirect(url_for("io.index", entity=entity, form_id=form_id))

    try:
        check = dataio.import_rows(entity, raw, g.user, dry_run=True, row_offset=offset)
    except ValueError as exc:
        _discard_upload()
        flash(str(exc), "error")
        return redirect(url_for("io.index", entity=entity, form_id=form_id))

    return render_page("io/index.html", "dataio", tab="import", entity=entity,
                       spec=dataio.IMPORT_SPECS[entity], entities=list(dataio.IMPORT_SPECS),
                       check=check, raw_total=len(raw), filename=upload.filename,
                       used_form=form, **_import_ctx(entity, form_id),
                       raw_preview=Table(raw.head(10)),
                       errors=Table(dataio.errors_to_df(check["errors"])), **_export_ctx())


@bp.route("/import/errors.csv")
def import_errors():
    info, key = _pending_upload()
    if not info:
        abort(404)
    raw, offset = _read_pending(info, key)
    check = dataio.import_rows(info["entity"], raw, g.user, dry_run=True, row_offset=offset)
    return csv_response(dataio.errors_to_df(check["errors"]), f"{info['entity']}_오류목록.csv")


@bp.route("/import/run", methods=["POST"])
def import_run():
    info, key = _pending_upload()
    if not info:
        flash("검증한 업로드 파일이 없습니다. 파일을 다시 올려 주세요.", "error")
        return redirect(url_for("io.index"))
    entity = info["entity"]
    if not f_bool("confirm"):
        flash("등록 확인에 체크하세요.", "error")
        return redirect(url_for("io.index", entity=entity))
    on_dup = f_str("on_duplicate") if f_str("on_duplicate") in ("건너뛰기", "덮어쓰기") else "건너뛰기"
    raw, offset = _read_pending(info, key)
    result = dataio.import_rows(entity, raw, g.user, dry_run=False, on_duplicate=on_dup, row_offset=offset)
    _discard_upload()

    message = f"{entity} {result['ok']}건 등록 완료"
    if result["skipped"]:
        message += f" · 중복 {result['skipped']}건 건너뜀"
    if result["errors"]:
        message += f" · 오류 {len(result['errors'])}건"
    flash(message, "success" if result["ok"] else "warning")
    return redirect(url_for("io.index", entity=entity))


# ----------------------------------------------------------------------------
# 추출
# ----------------------------------------------------------------------------
def _export_params() -> dict:
    available = [s for s in dataio.EXPORT_SOURCES if s != "감사로그" or ent.has_role(g.user, "ADMIN")]
    # 직접 체크한 항목이 있으면 그것을, 없으면 프리셋 묶음을 쓴다
    preset = request.args.get("preset", "")
    sources = (request.args.getlist("sources") or dataio.EXPORT_PRESETS.get(preset)
               or ["매출"])
    sources = [s for s in sources if s in available]
    default_from = date.today().replace(day=1) - timedelta(days=180)
    date_from = request.args.get("from") or default_from.isoformat()
    date_to = request.args.get("to") or date.today().isoformat()
    for value in (date_from, date_to):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            abort(400, "날짜는 YYYY-MM-DD 형식이어야 합니다.")
    owners = {o["id"]: o["label"] for o in g.owner_choices}
    owner_id = a_int("owner")
    owner_id = owner_id if owner_id in owners else None
    stage = a_str("stage")
    stage = stage if stage in db.STAGES else ""
    ym = request.args.get("ym") if request.args.get("ym") in g.months else date.today().strftime("%Y-%m")
    # 개인정보(고객 연락처·이메일·담당자명)는 사유를 적어야만 원문으로 내보낸다
    pii = request.args.get("pii") == "1"
    pii_reason = (request.args.get("pii_reason") or "").strip()
    return {"available": available, "preset": preset, "sources": sources,
            "date_from": date_from, "date_to": date_to, "owner": owner_id,
            "owner_label": owners.get(owner_id, "전체"), "stage": stage, "ym": ym,
            "pii": pii, "pii_reason": pii_reason}


def _export_ctx() -> dict:
    return {"ex": _export_params(), "presets": list(dataio.EXPORT_PRESETS),
            "stages": db.STAGES, "sheets": None, "export_forms": xf.list_forms("export"),
            "is_admin": ent.has_role(g.user, "ADMIN")}


def _collect(p: dict) -> dict[str, pd.DataFrame]:
    return dataio.collect(p["sources"], date_from=p["date_from"], date_to=p["date_to"],
                          owner_id=p["owner"], stage=p["stage"], ym=p["ym"],
                          include_pii=p["pii"] and bool(p["pii_reason"]))


@bp.route("/export")
def export():
    p = _export_params()
    downloading = request.args.get("download") in ("xlsx", "form") or request.args.get("csv")
    if p["pii"] and not p["pii_reason"] and downloading:
        flash("개인정보를 포함해 내려받으려면 사유를 입력하세요.", "error")
        return redirect(url_for("io.export", **{k: v for k, v in request.args.items()
                                                if k not in ("download", "csv")}))
    sheets = _collect(p) if p["sources"] else {}
    with_pii = p["pii"] and bool(p["pii_reason"])

    form_id = a_int("form_id")
    if request.args.get("download") == "form" and form_id:
        form = xf.get_form(form_id)
        if not form or form["direction"] != "export" or not form["active"] or form["entity"] not in p["available"]:
            abort(404)
        frame = _collect({**p, "sources": [form["entity"]]}).get(form["entity"], pd.DataFrame())
        data = xf.render_export(form, frame, xf.export_meta(g.user, f"{p['date_from']} ~ {p['date_to']}"))
        return xlsx_response(data, f"{form['name']}_{date.today():%Y%m%d}.xlsx", rows=len(frame), pii=with_pii)

    if request.args.get("download") == "xlsx":
        scope = db.current_scope()
        meta = {"추출일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "추출자": f"{g.user['name']} ({db.ROLE_LABEL.get(g.user['role'], g.user['role'])})",
                "조회범위": "전사" if scope is None else f"{len(scope)}명",
                "기간": f"{p['date_from']} ~ {p['date_to']}", "담당자": p["owner_label"],
                "기준월": p["ym"], "영업기회 단계": p["stage"] or "전체",
                "개인정보": f"포함 (사유: {p['pii_reason']})" if with_pii else "마스킹"}
        return xlsx_response(dataio.to_excel(sheets, meta), f"영업데이터_{date.today():%Y%m%d}.xlsx",
                             rows=sum(len(f) for f in sheets.values()), pii=with_pii)
    csv_name = request.args.get("csv")
    if csv_name:
        if csv_name not in sheets:
            abort(404)
        return csv_response(sheets[csv_name], f"{csv_name}.csv", pii=with_pii)

    summary = pd.DataFrame([{"항목": n, "행수": len(f), "열수": len(f.columns)}
                            for n, f in sheets.items()])
    ctx = _export_ctx()
    ctx.update(sheets={n: Table(f, limit=200) for n, f in sheets.items()},
               summary=Table(summary))
    entity = next(iter(dataio.IMPORT_SPECS))
    return render_page("io/index.html", "dataio", tab="export", entity=entity,
                       spec=dataio.IMPORT_SPECS[entity], entities=list(dataio.IMPORT_SPECS),
                       check=None, **_import_ctx(entity), **ctx)


# ----------------------------------------------------------------------------
# 회사 엑셀 양식 관리 (관리자)
# ----------------------------------------------------------------------------
def _sample_key(token: str) -> str:
    return f"uploads/form_sample_{token}"


def _forms_page(editor: dict | None = None, status: int = 200):
    entity = next(iter(dataio.IMPORT_SPECS))
    return render_page("io/index.html", "dataio", tab="forms", entity=entity,
                       spec=dataio.IMPORT_SPECS[entity], entities=list(dataio.IMPORT_SPECS), check=None,
                       forms=Table(xf.table(), link=("io.forms", "id", "fid")), editor=editor,
                       directions=xf.DIRECTIONS, export_sources=dataio.EXPORT_SOURCES,
                       **_import_ctx(entity), **_export_ctx()), status


def _editor_for(form: dict, headers: list[str] | None = None, preview=None, sample_token: str = "") -> dict:
    fields = xf.system_fields(form["direction"], form["entity"])
    current = {m["column"]: m["field"] for m in form.get("column_map") or []}
    headers = headers if headers is not None else list(current)
    suggested = {m["column"]: m["field"] for m in xf.suggest_mapping(headers, fields)}
    rows = [{"column": h, "field": current.get(h, suggested.get(h, ""))} for h in headers]
    required = dataio.IMPORT_SPECS[form["entity"]]["required"] if form["direction"] == "import" else []
    return {"form": form, "rows": rows, "fields": fields, "required": required,
            "preview": Table(preview) if preview is not None else None, "sample_token": sample_token,
            "value_map_text": xf.value_map_text(form.get("value_map") or {})}


@bp.route("/forms")
def forms():
    if not ent.has_role(g.user, "ADMIN"):
        abort(403, "회사 엑셀 양식은 시스템관리자가 등록합니다.")
    fid = a_int("fid")
    form = xf.get_form(fid) if fid else None
    return _forms_page(_editor_for(form) if form else None)


@bp.route("/forms/inspect", methods=["POST"])
def forms_inspect():
    """1단계: 회사 샘플 파일을 올리면 시트·머리글 행을 찾아 열 매핑을 제안한다."""
    if not ent.has_role(g.user, "ADMIN"):
        abort(403)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("회사에서 쓰는 엑셀 파일(샘플)을 올리세요.", "error")
        return redirect(url_for("io.forms"))
    direction, entity = f_str("direction"), f_str("entity_" + f_str("direction"))
    data = upload.read()
    try:
        if len(data) > xf.MAX_TEMPLATE_BYTES:
            raise ValueError("샘플 파일은 5MB 이하여야 합니다.")
        header_row = int(f_str("header_row")) if f_str("header_row").isdigit() else None
        info = xf.inspect_file(data, upload.filename, f_str("sheet_name") or None, header_row)
        if not info["headers"]:
            raise ValueError(f"{info['header_row']}행에서 머리글을 찾지 못했습니다. 머리글 행 번호를 지정하세요.")
    except (ValueError, KeyError) as exc:
        flash(f"파일을 읽지 못했습니다: {exc}", "error")
        return redirect(url_for("io.forms"))
    except Exception as exc:  # noqa: BLE001
        flash(f"파일을 읽지 못했습니다: {exc}", "error")
        return redirect(url_for("io.forms"))
    token = secrets.token_hex(12)
    get_storage().put(_sample_key(token), data)
    session["form_sample"] = {"token": token, "name": upload.filename}
    form = {"id": None, "name": f_str("name") or Path(upload.filename).stem, "direction": direction,
            "entity": entity, "sheet_name": info["sheet"], "header_row": info["header_row"],
            "data_start_row": info["header_row"] + 1, "column_map": [], "fill_down": [],
            "stop_words": xf.DEFAULT_STOP_WORDS, "template_name": None, "sheets": info["sheets"]}
    try:
        editor = _editor_for(form, info["headers"], info["preview"], token)
    except KeyError:
        abort(400, "항목을 고르세요.")
    editor["sample_name"] = upload.filename
    return _forms_page(editor)


@bp.route("/forms/save", methods=["POST"])
def forms_save():
    if not ent.has_role(g.user, "ADMIN"):
        abort(403)
    columns, fields = request.form.getlist("map_column"), request.form.getlist("map_field")
    data = {"id": int(f_str("id")) if f_str("id").isdigit() else None, "name": f_str("name"),
            "direction": f_str("direction"), "entity": f_str("entity"), "sheet_name": f_str("sheet_name"),
            "header_row": f_str("header_row"), "data_start_row": f_str("data_start_row") or None,
            "column_map": [{"column": c, "field": f} for c, f in zip(columns, fields)],
            "value_map": f_str("value_map"), "fill_down": request.form.getlist("fill_down"),
            "stop_words": f_str("stop_words"), "memo": f_str("memo")}
    template, template_name = None, None
    upload = request.files.get("template")
    sample = session.get("form_sample")
    if upload and upload.filename:
        template, template_name = upload.read(), upload.filename
    elif f_bool("use_sample") and sample and sample["token"] == f_str("sample_token"):
        template, template_name = get_storage().get(_sample_key(sample["token"])), sample["name"]
    try:
        fid = xf.save_form(data, template, template_name)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("io.forms", fid=data["id"]) if data["id"] else url_for("io.forms"))
    if sample:
        get_storage().delete(_sample_key(sample["token"]))
        session.pop("form_sample", None)
    flash("엑셀 양식을 저장했습니다.", "success")
    return redirect(url_for("io.forms", fid=fid))


@bp.route("/forms/<int:fid>/delete", methods=["POST"])
def forms_delete(fid: int):
    if not ent.has_role(g.user, "ADMIN"):
        abort(403)
    xf.deactivate(fid)
    flash("양식 사용을 중지했습니다.", "warning")
    return redirect(url_for("io.forms"))

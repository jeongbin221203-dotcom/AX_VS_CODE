"""엑셀 일괄 등록 화면 공통 (거래처·BOM): 양식 내려받기 → 올리기 → 미리보기(문제 줄 표시) → 반영.
미리보기 내용은 저장소의 uploads/<표>.json 에 두고(배치가 24시간 뒤 정리), 미리보기를 본 사람만 반영할 수 있다."""
from __future__ import annotations

import json
import re
import secrets

import pandas as pd
from flask import flash, redirect, request, session

import config
from core import bulk, excel_forms, storage
from core.utils import xlsx_problem
from views.helpers import actor, f_str, form_response, render_page

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
KEY = "bulk_tokens"


def template(form_key: str, sample: list[list], filename: str):
    cols = excel_forms.EXPORT_FORMS[form_key][1]
    return form_response(form_key, pd.DataFrame(sample, columns=cols), filename)


def page(kind: str, active: str, title: str, preview: bulk.Preview | None = None, token: str = "", extra: dict | None = None):
    return render_page("bulk.html", active, kind=kind, heading=title, preview=preview, token=token, **(extra or {}))


def upload(kind: str, import_key: str, preview_fn, back: str, active: str, title: str, extra: dict | None = None):
    file = request.files.get("file")
    if not file or not file.filename:
        flash("엑셀(.xlsx) 또는 CSV 파일을 고르세요.", "error")
        return redirect(back)
    name = file.filename.lower()
    if not name.endswith((".xlsx", ".csv")):
        flash("엑셀(.xlsx) 또는 CSV만 올릴 수 있습니다.", "error")
        return redirect(back)
    data = file.read()
    if name.endswith(".xlsx"):
        problem = xlsx_problem(data, config.XLSX_MAX_UNCOMPRESSED, config.XLSX_MAX_RATIO)
        if problem:
            flash(problem, "error")
            return redirect(back)
    raw, problem = excel_forms.read_import(import_key, data, name, bulk.MAX_ROWS)
    if problem:
        flash(problem, "error")
        return redirect(back)
    pv = preview_fn(raw)
    token = ""
    if pv.ok:
        token = secrets.token_hex(16)
        session[KEY] = (session.get(KEY) or [])[-4:] + [token]
        storage.get().put(f"uploads/{token}.json", json.dumps({"kind": kind, "rows": pv.rows}, ensure_ascii=False,
                                                                 default=str).encode("utf-8"))
    elif not pv.rows:
        pv.errors.append("올린 파일에 줄이 없습니다 (머리글 줄 위치는 엑셀 양식 설정에서 바꿀 수 있습니다).")
    return page(kind, active, title, pv, token, extra)


def apply(kind: str, apply_fn, back: str):
    token = f_str("token")
    if not TOKEN_RE.match(token) or token not in (session.get(KEY) or []):
        flash("미리보기가 만료되었습니다. 파일을 다시 올려 주세요.", "error")
        return redirect(back)
    store = storage.get()
    try:
        saved = json.loads(store.get(f"uploads/{token}.json").decode("utf-8"))
    except Exception:
        flash("미리보기가 만료되었습니다. 파일을 다시 올려 주세요.", "error")
        return redirect(back)
    if saved.get("kind") != kind:
        flash("다른 종류의 미리보기입니다.", "error")
        return redirect(back)
    r = apply_fn(saved["rows"], actor())
    flash(r.message, "success" if r.ok else "error")
    if r.ok:
        store.delete(f"uploads/{token}.json")
        session[KEY] = [t for t in session.get(KEY) or [] if t != token]
    return redirect(back)

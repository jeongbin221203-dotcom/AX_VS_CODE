"""엑셀 일괄 등록 화면 공통 (거래처·BOM): 양식 내려받기 → 올리기 → 미리보기(문제 줄 표시) → 반영.
미리보기 내용은 저장소의 uploads/<표>.json 에 두고(배치가 24시간 뒤 정리), 미리보기를 본 사람만 반영할 수 있다."""
from __future__ import annotations

import json
import re
import secrets

import pandas as pd
from flask import flash, redirect, request, session, url_for

import config
from core import bulk, excel_forms, storage, tasks
from core.utils import xlsx_problem
from views.helpers import actor, can, f_str, form_response, render_page

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
BULK_LABEL = {"partners": "거래처", "bom": "BOM", "units": "단위 환산", "mrp_demand": "MRP 수요"}
KEY = "bulk_tokens"


def template(form_key: str, sample: list[list], filename: str):
    cols = excel_forms.EXPORT_FORMS[form_key][1]
    return form_response(form_key, pd.DataFrame(sample, columns=cols), filename)


def page(kind: str, active: str, title: str, preview: bulk.Preview | None = None, token: str = "", extra: dict | None = None):
    ctx = {"learn": excel_forms.learn_state(excel_forms.BULK_IMPORT_KEY.get(kind)), "learn_summary": None, **(extra or {})}
    if token and ctx.get("learn_summary") is None:                  # 미리보기에 보관해 둔 양식이 있으면 요약을 보여 준다
        saved = excel_forms.staged(token)
        ctx["learn_summary"] = saved["summary"] if saved else None
    return render_template_page(kind, active, title, preview, token, ctx)


def render_template_page(kind, active, title, preview, token, ctx):
    return render_page("bulk.html", active, kind=kind, heading=title, preview=preview, token=token, **ctx)


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
    learn_summary = None
    if pv.ok:
        token = secrets.token_hex(16)
        session[KEY] = (session.get(KEY) or [])[-4:] + [token]
        storage.get().put(f"uploads/{token}.json", json.dumps({"kind": kind, "rows": pv.rows}, ensure_ascii=False,
                                                                 default=str).encode("utf-8"))
        state = excel_forms.learn_state(import_key)
        if request.form.get("learn_form") == "1" and can("ADMIN") and state and not state["locked"]:
            learned = excel_forms.stage_learn(token, import_key, data, file.filename)      # 이 엑셀의 양식을 기억 (반영 때 저장)
            if learned["ok"]:
                learn_summary = learned["summary"]
            else:
                flash(f"양식은 기억하지 않았습니다: {learned['problem']}", "warning")
    elif not pv.rows:
        pv.errors.append("올린 파일에 줄이 없습니다 (머리글 줄 위치는 엑셀 양식 설정에서 바꿀 수 있습니다).")
    return page(kind, active, title, pv, token, {**(extra or {}), "learn_summary": learn_summary})


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
    rows, who = saved["rows"], actor()
    if len(rows) > config.BG_ROWS:                  # 줄이 많으면 요청 안에서 기다리지 않고 백그라운드로 (반영은 전부 또는 아무것도)
        store.delete(f"uploads/{token}.json")
        session[KEY] = [t for t in session.get(KEY) or [] if t != token]
        label = BULK_LABEL.get(kind, kind)
        def run(progress):
            r = apply_fn(rows, who)
            if r.ok:
                excel_forms.adopt_staged(token, who)                # 양식을 기억하기로 했다면 반영이 끝난 뒤 저장
            else:
                excel_forms.discard_staged(token)
            return r

        tid = tasks.start(f"bulk_{kind}", f"{label} 일괄 반영 ({len(rows):,}줄)", run, who)
        flash(f"{label} {len(rows):,}줄 반영을 시작했습니다. 끝나면 이 화면에 결과가 나옵니다.", "info")
        return redirect(url_for("tasks.detail", task_id=tid))
    r = apply_fn(rows, who)
    flash(r.message, "success" if r.ok else "error")
    if r.ok:
        store.delete(f"uploads/{token}.json")
        session[KEY] = [t for t in session.get(KEY) or [] if t != token]
        learned = excel_forms.adopt_staged(token, who)               # 양식을 기억하기로 했다면 저장
        if learned:
            flash(learned, "success")
    return redirect(back)

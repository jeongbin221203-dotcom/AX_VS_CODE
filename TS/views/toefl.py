"""토플: 홈(영역별 밴드) · 과제별 연습 · 기록 API."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from core import db, toefl
from core.toefl import BAND_OLD, CEFR, LEVEL_BAND, RUBRIC, SECTIONS, TASKS

bp = Blueprint("toefl", __name__, url_prefix="/toefl")


def tbank() -> toefl.ToeflBank:
    b = current_app.extensions["toefl_bank"]
    b.refresh_if_changed()
    return b


@bp.route("/")
def home():
    b = tbank()
    bands = toefl.section_bands()
    st = toefl.task_stats()
    settings = db.get_settings()
    try:
        target = float(settings.get("toefl_target") or 4.5)
    except ValueError:
        target = 4.5
    sections = []
    for key, sec in SECTIONS.items():
        tasks = [{"key": t, **v, "counts": {lv: b.count(t, lv) for lv in range(1, 6)}, "total": b.count(t),
                  "stat": st.get(t)} for t, v in TASKS.items() if v["section"] == key]
        sections.append({"key": key, **sec, "band": bands[key], "tasks": tasks})
    overall = toefl.overall_band(bands)
    rec_level = next((lv for lv, band in LEVEL_BAND.items() if band >= (overall or target)), 3)
    return render_template("toefl/home.html", sections=sections, bands=bands, overall=overall, target=target,
                           CEFR=CEFR, BAND_OLD=BAND_OLD, LEVEL_BAND=LEVEL_BAND, recent=toefl.recent(),
                           TASKS=TASKS, rec_level=rec_level, cefr=toefl.cefr)


@bp.route("/target", methods=["POST"])
def set_target():
    try:
        t = float(request.form.get("target", "4.5"))
        if not 1 <= t <= 6 or t * 2 != int(t * 2):
            raise ValueError
    except ValueError:
        flash("목표 밴드는 1~6 사이 0.5 단위로 입력하세요.", "error")
        return redirect(url_for("toefl.home"))
    db.save_settings({"toefl_target": str(t)})
    return redirect(url_for("toefl.home"))


@bp.route("/practice/<task>")
def practice(task: str):
    if task not in TASKS:
        abort(404)
    b = tbank()
    try:
        level = int(request.args.get("level", "")) or None
    except ValueError:
        level = None
    level = level if level in LEVEL_BAND else None
    info = TASKS[task]
    default_n = {"r_words": 3, "r_daily": 4, "r_academic": 2, "l_response": 10, "l_conversation": 3, "l_talk": 2,
                 "s_repeat": 1, "s_interview": 1, "w_sentence": 10, "w_email": 1, "w_discussion": 1}[task]
    try:
        n = max(1, min(int(request.args.get("n", default_n)), 30))
    except ValueError:
        n = default_n
    items = b.pick(task, level, n)
    st = db.get_settings()
    payload = {"task": task, "kind": info["kind"], "items": items,
               "tts": {"rate": float(st["tts_rate"]), "accent": st["tts_accent"]},
               "rubric": RUBRIC, "band": LEVEL_BAND}
    return render_template("toefl/practice.html", task=task, info=info, sec=SECTIONS[info["section"]],
                           level=level, n=n, payload=payload, LEVEL_BAND=LEVEL_BAND, CEFR=CEFR,
                           counts={lv: b.count(task, lv) for lv in range(1, 6)})


@bp.route("/api/attempt", methods=["POST"])
def api_attempt():
    data = request.get_json(silent=True) or {}
    task, item_id = str(data.get("task", "")), str(data.get("item_id", ""))
    item = tbank().by_id.get((task, item_id))
    if not item:
        return jsonify(error="문제가 없습니다."), 400
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return jsonify(error="결과 형식 오류"), 400
    try:
        n = toefl.record(task, item_id, item["level"], results)
    except (TypeError, ValueError):
        return jsonify(error="결과 형식 오류"), 400
    bands = toefl.section_bands()
    return jsonify(saved=n, band=bands[TASKS[task]["section"]])

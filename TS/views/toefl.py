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
    review = request.args.get("review") == "1"
    if review:                                   # 오답노트: 마지막에 틀린 문제만, 오래전에 틀린 것부터
        wrong = toefl.wrong_items(task)
        items = [b.by_id[(task, i)] for i in sorted(wrong, key=lambda i: wrong[i]["at"]) if (task, i) in b.by_id][:n]
    else:
        items = b.pick(task, level, n)
    st = db.get_settings()
    payload = {"task": task, "kind": info["kind"], "items": items,
               "tts": {"rate": float(st["tts_rate"]), "accent": st["tts_accent"]},
               "rubric": RUBRIC, "band": LEVEL_BAND}
    return render_template("toefl/practice.html", task=task, info=info, sec=SECTIONS[info["section"]],
                           level=level, n=n, payload=payload, LEVEL_BAND=LEVEL_BAND, CEFR=CEFR, review=review,
                           counts={lv: b.count(task, lv) for lv in range(1, 6)})


@bp.route("/review")
def review():
    """오답노트: 과제별로 마지막에 틀린 문제와 '틀린 문제만 다시 풀기'."""
    b = tbank()
    sections = []
    for key, sec in SECTIONS.items():
        tasks = []
        for t, v in TASKS.items():
            if v["section"] != key:
                continue
            wrong = toefl.wrong_items(t)
            rows = sorted(({"id": i, **w, "label": toefl.item_label(t, b.by_id[(t, i)]), "level": b.by_id[(t, i)]["level"]}
                           for i, w in wrong.items() if (t, i) in b.by_id), key=lambda r: r["at"], reverse=True)
            tasks.append({"key": t, **v, "rows": rows})
        sections.append({"key": key, **sec, "tasks": tasks, "n": sum(len(x["rows"]) for x in tasks)})
    return render_template("toefl/review.html", sections=sections, LEVEL_BAND=LEVEL_BAND,
                           cut_auto=int(toefl.WRONG_CUT_AUTO * 100), cut_self=int(toefl.WRONG_CUT_SELF * 5))


@bp.route("/history")
def history():
    """기록: 날짜별·과제별 푼 문항과 평균, 모의고사."""
    rows = toefl.history()
    days: dict[str, list] = {}
    for r in rows:
        days.setdefault(r["d"], []).append(r)
    return render_template("toefl/history.html", days=days, TASKS=TASKS, SECTIONS=SECTIONS, mocks=toefl.list_mocks(50),
                           stats=toefl.task_stats(), bands=toefl.section_bands(), CEFR=CEFR)


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


# ---- 실전 모의고사 --------------------------------------------------------------------

def _target() -> float:
    try:
        return float(db.get_settings().get("toefl_target") or 4.5)
    except ValueError:
        return 4.5


@bp.route("/mock", methods=["GET", "POST"])
def mock():
    if request.method == "POST":
        mid = toefl.create_mock(tbank(), _target())
        return redirect(url_for("toefl.mock_run", mid=mid))
    return render_template("toefl/mock.html", past=toefl.list_mocks(), target=_target(), CEFR=CEFR,
                           cut=int(toefl.ADAPT_CUT * 100))


@bp.route("/mock/<int:mid>")
def mock_run(mid: int):
    m = toefl.get_mock(mid)
    if not m:
        abort(404)
    if m["finished_at"]:
        return redirect(url_for("toefl.mock_result", mid=mid))
    st = db.get_settings()
    payload = {"id": mid, "plan": m["plan"],
               "tasks": {k: {"name": v["name"], "kind": v["kind"], "section": v["section"]} for k, v in TASKS.items()},
               "tts": {"rate": float(st["tts_rate"]), "accent": st["tts_accent"]}, "rubric": RUBRIC, "band": LEVEL_BAND}
    return render_template("toefl/mock_run.html", m=m, payload=payload)


@bp.route("/api/mock/<int:mid>/finish", methods=["POST"])
def api_mock_finish(mid: int):
    try:
        toefl.finish_mock(tbank(), mid, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(redirect=url_for("toefl.mock_result", mid=mid))


@bp.route("/mock/<int:mid>/result")
def mock_result(mid: int):
    m = toefl.get_mock(mid)
    if not m or not m["finished_at"]:
        abort(404)
    return render_template("toefl/mock_result.html", m=m, r=m["result"], SECTIONS=SECTIONS, TASKS=TASKS, CEFR=CEFR,
                           BAND_OLD=BAND_OLD, LEVEL_BAND=LEVEL_BAND, cefr=toefl.cefr, target=_target())

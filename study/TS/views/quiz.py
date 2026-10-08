"""문제 풀이: 파트 연습 · 진단 · 모의고사 · 오답 복습 · 받아쓰기 · 결과."""
from __future__ import annotations

import random
import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from core import db, planner, scoring, stats, study
from core.content import LC_PARTS, PART_INFO
from core.guide import PART_TIPS, TARGET_SEC

from .helpers import bank

bp = Blueprint("quiz", __name__)


def _int_arg(name, default=None):
    try:
        return int(request.args.get(name, ""))
    except ValueError:
        return default


# ---- 파트 연습 ---------------------------------------------------------------------

@bp.route("/practice")
def practice():
    b = bank()
    score, _ = planner.current_score(db.get_settings())
    g = scoring.grade_for(score)
    lvl_acc = stats.level_accuracy()
    parts = []
    for p, info in PART_INFO.items():
        parts.append({
            "part": p, **info,
            "counts": {lv: b.count_questions(p, lv) for lv in range(1, 6)},
            "types": [(t, b.count_questions(p, None, t)) for t in b.types(p)],
            "acc": {lv: lvl_acc.get((p, lv)) for lv in range(1, 6)},
            "rec_level": planner.recommended_level(p, g.level, lvl_acc) if g else None,
        })
    return render_template("practice.html", parts=parts, grade=g, tips=PART_TIPS)


@bp.route("/practice/start")
def practice_start():
    part = _int_arg("part")
    level = _int_arg("level")
    if level is not None and level not in scoring.GRADE_BY_LEVEL:
        level = None
    n = _int_arg("n", 10)
    try:
        sid = study.start_practice(bank(), part, level, request.args.get("type") or None, n)
    except study.StudyError as e:
        flash(str(e), "error")
        return redirect(url_for("quiz.practice"))
    return redirect(url_for("quiz.quiz", sid=sid))


# ---- 진단 · 모의고사 ------------------------------------------------------------------

@bp.route("/diagnostic", methods=["GET", "POST"])
def diagnostic():
    if request.method == "POST":
        try:
            sid = study.start_diagnostic(bank())
        except study.StudyError as e:
            flash(str(e), "error")
            return redirect(url_for("quiz.diagnostic"))
        return redirect(url_for("quiz.quiz", sid=sid))
    past = [s for s in stats.recent_sessions(50) if s["mode"] == "diagnostic"]
    return render_template("diagnostic.html", past=past, form=scoring.DIAGNOSTIC_FORM)


@bp.route("/mock", methods=["GET", "POST"])
def mock():
    if request.method == "POST":
        try:
            sid = study.start_mock(bank(), request.form.get("form", "mini"))
        except study.StudyError as e:
            flash(str(e), "error")
            return redirect(url_for("quiz.mock"))
        return redirect(url_for("quiz.quiz", sid=sid))
    past = [s for s in stats.recent_sessions(100) if s["mode"] == "mock"]
    return render_template("mock.html", forms=scoring.MOCK_FORMS, past=past, fresh=study.fresh_mock_capacity(bank()),
                           ongoing=stats.unfinished_sessions(),
                           selected=request.args.get("form", "full"))


# ---- 오답노트 ----------------------------------------------------------------------

@bp.route("/review")
def review():
    b = bank()
    status = request.args.get("status", "open")
    part = _int_arg("part")
    qtype = request.args.get("type") or None
    notes = study.wrong_notes(status if status in ("open", "cleared") else "open", part, qtype)
    rows = []
    for n in notes:
        item = b.item(f"{n['part']}:{n['item_id']}")
        if not item:
            continue
        rows.append({**n, "preview": _preview(item, n["qidx"])})
    all_open = study.wrong_notes("open")
    by_part = {p: sum(1 for n in all_open if n["part"] == p) for p in PART_INFO}
    types = sorted({n["qtype"] for n in all_open})
    return render_template("review.html", rows=rows, status=status, part=part, qtype=qtype,
                           by_part=by_part, types=types, n_open=len(all_open))


def _preview(item: dict, qidx: int) -> str:
    p = item["part"]
    if p == 1:
        return item["scene"]
    if p in (2, 5):
        return item["question"]
    if p in (3, 4, 7):
        q = item["questions"][qidx]["q"]
        return q or item.get("topic", "")
    if p == 6:
        return f"{item['title'].splitlines()[0]} — 빈칸 ({qidx + 1})"
    return ""


@bp.route("/review/start")
def review_start():
    try:
        sid = study.start_review(bank(), _int_arg("part"), _int_arg("n", 10))
    except study.StudyError:
        flash("복습할 오답이 없습니다.", "ok")
        return redirect(url_for("quiz.review"))
    return redirect(url_for("quiz.quiz", sid=sid))


@bp.route("/review/note", methods=["POST"])
def review_note():
    qkey = request.form.get("qkey", "")
    if "memo" in request.form:
        study.set_note_memo(qkey, request.form["memo"])
    if request.form.get("status"):
        try:
            study.set_note_status(qkey, request.form["status"])
        except study.StudyError:
            abort(400, "상태 값이 올바르지 않습니다.")
    return redirect(request.referrer or url_for("quiz.review"))


# ---- 풀이 화면 · API --------------------------------------------------------------------

@bp.route("/quiz/<int:sid>")
def quiz(sid: int):
    s = study.get_session(sid)
    if not s:
        abort(404)
    if s["finished_at"]:
        return redirect(url_for("quiz.result", sid=sid))
    b = bank()
    items = [b.public_item(r) for r in s["items"] if b.item(r)]
    graded = {}
    if s["mode"] in ("practice", "review"):          # 이어 풀기: 이미 채점한 문제는 결과를 함께 보낸다
        by_item: dict[str, list] = {}
        for a in study.session_attempts(sid):
            by_item.setdefault(f"{a['part']}:{a['item_id']}", []).append(
                {"qidx": a["qidx"], "chosen": a["chosen"], "correct": bool(a["correct"])})
        for ref, res in by_item.items():
            graded[ref] = {**b.reveal(ref), "results": res}
    st = db.get_settings()
    payload = {
        "sid": sid, "mode": s["mode"], "items": items, "graded": graded,
        "real": bool(s["mode"] == "mock" and scoring.MOCK_FORMS.get(s["variant"] or "", {}).get("real")),
        "time_limit": s["time_limit"], "created_at": s["created_at"],
        "target_sec": TARGET_SEC, "lc_parts": list(LC_PARTS),
        "tts": {"rate": float(st["tts_rate"]), "accent": st["tts_accent"]},
    }
    return render_template("quiz.html", s=s, payload=payload)


@bp.route("/api/quiz/<int:sid>/grade", methods=["POST"])
def api_grade(sid: int):
    data = request.get_json(silent=True) or {}
    s = study.get_session(sid)
    if not s or s["mode"] not in ("practice", "review"):
        return jsonify(error="연습·복습 세션에서만 문제별 채점을 합니다."), 400
    try:
        return jsonify(study.grade_item(bank(), sid, str(data.get("ref", "")), data.get("answers", [])))
    except study.StudyError as e:
        return jsonify(error=str(e)), 400


@bp.route("/api/quiz/<int:sid>/submit", methods=["POST"])
def api_submit(sid: int):
    data = request.get_json(silent=True) or {}
    try:
        study.submit_session(bank(), sid, data)
    except study.StudyError as e:
        return jsonify(error=str(e)), 400
    return jsonify(redirect=url_for("quiz.result", sid=sid))


@bp.route("/session/<int:sid>")
def result(sid: int):
    s = study.get_session(sid)
    if not s:
        abort(404)
    b = bank()
    attempts = study.session_attempts(sid)
    by_part: dict[int, dict] = {}
    by_type: dict[tuple, dict] = {}
    for a in attempts:
        d = by_part.setdefault(a["part"], {"n": 0, "c": 0})
        d["n"] += 1
        d["c"] += a["correct"]
        t = by_type.setdefault((a["part"], a["qtype"]), {"n": 0, "c": 0})
        t["n"] += 1
        t["c"] += a["correct"]
    weak_types = sorted(((k, v) for k, v in by_type.items() if v["c"] < v["n"]),
                        key=lambda kv: (kv[1]["c"] / kv[1]["n"], -kv[1]["n"]))[:6]
    full_items = []
    for ref in s["items"]:
        it = b.item(ref)
        if it:
            full_items.append({**it, "ref": ref})
    answered = {}
    for a in attempts:
        answered.setdefault(f"{a['part']}:{a['item_id']}", []).append(
            {"qidx": a["qidx"], "chosen": a["chosen"], "correct": bool(a["correct"])})
    graded = {ref: {**b.reveal(ref), "results": res} for ref, res in answered.items()}
    st = db.get_settings()
    payload = {"sid": sid, "mode": "result", "items": [i for i in full_items if i["ref"] in graded],
               "graded": graded, "lc_parts": list(LC_PARTS), "target_sec": TARGET_SEC,
               "tts": {"rate": float(st["tts_rate"]), "accent": st["tts_accent"]}}
    grade = scoring.grade_for(s["total_est"]) if s["total_est"] is not None else None
    return render_template("result.html", s=s, by_part=by_part, weak_types=weak_types, payload=payload, grade=grade)


# ---- 받아쓰기 ----------------------------------------------------------------------

@bp.route("/dictation")
def dictation():
    b = bank()
    level = _int_arg("level")
    part = _int_arg("part", 2)
    rng = random.Random()
    lines = []
    for it in b.items.get(part, []):
        if level and it["level"] != level:
            continue
        if part == 1:
            lines += [{"text": t, "voice": None, "src": it["id"]} for t in it["statements"]]
        elif part == 2:
            lines.append({"text": it["question"], "voice": None, "src": it["id"]})
            lines += [{"text": c, "voice": None, "src": it["id"]} for c in it["choices"]]
        elif part == 3:
            lines += [{"text": l["t"], "voice": it["speakers"][l["s"]], "src": it["id"]} for l in it["script"]]
        elif part == 4:
            sents = [x.strip() for x in _split_sentences(it["script"]) if len(x.split()) >= 4]
            lines += [{"text": t, "voice": it["voice"], "src": it["id"]} for t in sents]
    lines = [l for l in lines if 3 <= len(l["text"].split()) <= 30]
    rng.shuffle(lines)
    st = db.get_settings()
    return render_template("dictation.html", lines=lines[:20], part=part, level=level,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


def _split_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)

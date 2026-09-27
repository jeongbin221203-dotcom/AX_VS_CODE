"""등급별 단어장 · 간격 반복 카드 · 뜻 고르기 퀴즈."""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from core import audio, db, srs

from .helpers import bank

bp = Blueprint("vocab", __name__)


def _level_arg():
    try:
        lv = int(request.args.get("level", ""))
        return lv if 1 <= lv <= 5 else None
    except ValueError:
        return None


@bp.route("/vocab")
def overview():
    b = bank()
    st = db.get_settings()
    q = srs.queue(b, None, int(st["daily_new_words"] or 0))
    cs = srs.cards()
    return render_template("vocab.html", progress=srs.level_progress(b), due=len(q["due"]), new=len(q["new"]),
                           new_today=srs.new_learned_today(), daily_new=st["daily_new_words"],
                           starred=sum(1 for c in cs.values() if c["starred"]))


@bp.route("/vocab/study")
def study():
    b = bank()
    level = _level_arg()
    starred = request.args.get("starred") == "1"
    st = db.get_settings()
    q = srs.queue(b, level, int(st["daily_new_words"] or 0), starred_only=starred)
    cs = srs.cards()
    cards = [{**w, "is_new": False, "starred": bool(cs.get(w["id"], {}).get("starred"))} for w in q["due"]] + \
            [{**w, "is_new": True, "starred": bool(cs.get(w["id"], {}).get("starred"))} for w in q["new"]]
    return render_template("vocab_study.html", cards=cards, level=level, starred=starred,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


@bp.route("/vocab/list")
def word_list():
    b = bank()
    level = _level_arg()
    flt = request.args.get("filter", "all")
    term = request.args.get("q", "").strip().lower()
    cs = srs.cards()
    today = date.today().isoformat()
    rows = []
    for w in b.vocab:
        if level and w["level"] != level:
            continue
        if term and term not in w["word"].lower() and term not in w["meaning"]:
            continue
        c = cs.get(w["id"])
        seen = bool(c and c["reps"] + c["lapses"] > 0)
        state = "new" if not seen else ("mastered" if c["interval"] >= srs.MASTERED_DAYS else "learning")
        if flt == "starred" and not (c and c["starred"]):
            continue
        if flt in ("new", "learning", "mastered") and state != flt:
            continue
        if flt == "weak" and not (c and c["lapses"] >= 2):
            continue
        rows.append({**w, "state": state, "card": c, "due_today": bool(seen and c["due"] <= today)})
    st = db.get_settings()
    return render_template("vocab_list.html", rows=rows, level=level, flt=flt, q=term,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


@bp.route("/vocab/quiz")
def quiz():
    b = bank()
    level = _level_arg()
    pool = [w for w in b.vocab if not level or w["level"] == level]
    rng = random.Random()
    picks = rng.sample(pool, min(15, len(pool)))
    questions = []
    for w in picks:
        same_pos = [x for x in pool if x["id"] != w["id"] and x["pos"] == w["pos"]]
        others = same_pos if len(same_pos) >= 3 else [x for x in pool if x["id"] != w["id"]]
        distract = rng.sample(others, min(3, len(others)))
        opts = [w] + distract
        rng.shuffle(opts)
        questions.append({"id": w["id"], "word": w["word"], "pos": w["pos"], "example": w["example"],
                          "meaning": w["meaning"], "options": [o["meaning"] for o in opts],
                          "answer": [o["id"] for o in opts].index(w["id"]), "tip": w["tip"]})
    st = db.get_settings()
    return render_template("vocab_quiz.html", questions=questions, level=level,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


LISTEN_SETS = {"all": "전체", "new": "아직 안 본 단어", "learning": "학습 중", "weak": "자주 잊는 단어(2번 이상)",
               "starred": "★ 별표"}
AUDIO_CHUNK = 50


def _word_state(c) -> str:
    if not c or c["reps"] + c["lapses"] == 0:
        return "new"
    return "mastered" if c["interval"] >= srs.MASTERED_DAYS else "learning"


def _listen_words(level: int | None, which: str) -> list[dict]:
    cs = srs.cards()
    out = []
    for w in bank().vocab:
        if level and w["level"] != level:
            continue
        c = cs.get(w["id"])
        st = _word_state(c)
        if which == "new" and st != "new":
            continue
        if which == "learning" and st != "learning":
            continue
        if which == "weak" and not (c and c["lapses"] >= 2):
            continue
        if which == "starred" and not (c and c["starred"]):
            continue
        out.append({"id": w["id"], "level": w["level"], "word": w["word"], "pos": w["pos"], "meaning": w["meaning"],
                    "example": w["example"], "example_ko": w["example_ko"], "state": st,
                    "starred": bool(c and c["starred"])})
    return out


@bp.route("/vocab/listen")
def listen():
    level = _level_arg()
    which = request.args.get("set", "all")
    which = which if which in LISTEN_SETS else "all"
    words = _listen_words(level, which)
    st = db.get_settings()
    chunks = [(i // AUDIO_CHUNK + 1, i + 1, min(i + AUDIO_CHUNK, len(words))) for i in range(0, len(words), AUDIO_CHUNK)]
    return render_template("vocab_listen.html", words=words, level=level, which=which, sets=LISTEN_SETS,
                           chunks=chunks, audio_ok=audio.available(),
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


@bp.route("/vocab/audio")
def audio_file():
    """단어 묶음(50개)을 음성 파일로 내려준다 — 휴대폰에서 화면을 끈 채 듣기용."""
    level = _level_arg()
    which = request.args.get("set", "all")
    which = which if which in LISTEN_SETS else "all"
    try:
        chunk = max(1, int(request.args.get("chunk", "1")))
        repeats = int(request.args.get("repeats", "3"))
    except ValueError:
        chunk, repeats = 1, 3
    example = request.args.get("example") == "1"
    words = _listen_words(level, which)[(chunk - 1) * AUDIO_CHUNK: chunk * AUDIO_CHUNK]
    try:
        path = audio.build(words, Path(current_app.config["DB_PATH"]).parent / "audio", repeats=repeats, example=example)
    except audio.AudioError as e:
        flash(str(e), "error")
        return redirect(url_for("vocab.listen", level=level or "", set=which))
    grade = f"L{level}" if level else "all"
    name = f"TOEIC_vocab_{grade}_{which}_{(chunk - 1) * AUDIO_CHUNK + 1:04d}-{(chunk - 1) * AUDIO_CHUNK + len(words):04d}.wav"
    return send_file(path, mimetype="audio/wav", as_attachment=True, download_name=name)


@bp.route("/api/vocab/review", methods=["POST"])
def api_review():
    data = request.get_json(silent=True) or {}
    wid = str(data.get("word_id", ""))
    if wid not in bank().vocab_by_id:
        return jsonify(error="없는 단어"), 400
    try:
        grade = int(data.get("grade"))
        return jsonify(srs.review(wid, grade))
    except (TypeError, ValueError):
        return jsonify(error="평가 값 오류"), 400


@bp.route("/api/vocab/star", methods=["POST"])
def api_star():
    data = request.get_json(silent=True) or {}
    wid = str(data.get("word_id", ""))
    if wid not in bank().vocab_by_id:
        return jsonify(error="없는 단어"), 400
    return jsonify(starred=srs.toggle_star(wid))

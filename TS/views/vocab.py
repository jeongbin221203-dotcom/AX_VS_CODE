"""등급별 단어장 · 간격 반복 카드 · 뜻 고르기 퀴즈."""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from core import audio, db, srs
from core.content import TIERS

from .helpers import bank

bp = Blueprint("vocab", __name__)


def _tier_arg():
    t = request.args.get("tier", "")
    return t if t in TIERS else None


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
    tier = _tier_arg()
    starred = request.args.get("starred") == "1"
    st = db.get_settings()
    q = srs.queue(b, level, int(st["daily_new_words"] or 0), starred_only=starred, tier=tier)
    cs = srs.cards()
    cards = [{**w, "is_new": False, "starred": bool(cs.get(w["id"], {}).get("starred"))} for w in q["due"]] + \
            [{**w, "is_new": True, "starred": bool(cs.get(w["id"], {}).get("starred"))} for w in q["new"]]
    return render_template("vocab_study.html", cards=cards, level=level, starred=starred, tier=tier, TIERS=TIERS,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


@bp.route("/vocab/list")
def word_list():
    b = bank()
    level = _level_arg()
    tier = _tier_arg()
    flt = request.args.get("filter", "all")
    term = request.args.get("q", "").strip().lower()
    cs = srs.cards()
    today = date.today().isoformat()
    rows = []
    for w in b.vocab:
        if level and w["level"] != level:
            continue
        if tier and w["tier"] != tier:
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
    return render_template("vocab_list.html", rows=rows, level=level, flt=flt, q=term, tier=tier, TIERS=TIERS,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


@bp.route("/vocab/quiz")
def quiz():
    b = bank()
    level = _level_arg()
    tier = _tier_arg()
    pool = [w for w in b.vocab if (not level or w["level"] == level) and (not tier or w["tier"] == tier)]
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
    return render_template("vocab_quiz.html", questions=questions, level=level, tier=tier, TIERS=TIERS,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


LISTEN_SETS = {"all": "전체", "new": "아직 안 본 단어", "learning": "학습 중", "weak": "자주 잊는 단어(2번 이상)",
               "starred": "★ 별표"}
AUDIO_MINUTES = (10, 30, 60)


def _audio_opts():
    try:
        minutes = int(request.args.get("minutes", "60"))
        repeats = int(request.args.get("repeats", "3"))
    except ValueError:
        minutes, repeats = 60, 3
    return {"minutes": minutes if minutes in AUDIO_MINUTES else 60, "repeats": max(1, min(repeats, 5)),
            "example": request.args.get("example") == "1", "fill": request.args.get("fill", "1") == "1"}


def _word_state(c) -> str:
    if not c or c["reps"] + c["lapses"] == 0:
        return "new"
    return "mastered" if c["interval"] >= srs.MASTERED_DAYS else "learning"


def _listen_words(level: int | None, which: str, tier: str | None = None) -> list[dict]:
    cs = srs.cards()
    out = []
    for w in sorted(bank().vocab, key=lambda w: (w["level"], w["tier"] != "core")):
        if level and w["level"] != level:
            continue
        if tier and w["tier"] != tier:
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
                    "example": w["example"], "example_ko": w["example_ko"], "state": st, "tier": w["tier"],
                    "starred": bool(c and c["starred"])})
    return out


@bp.route("/vocab/listen")
def listen():
    level = _level_arg()
    which = request.args.get("set", "all")
    which = which if which in LISTEN_SETS else "all"
    tier = _tier_arg()
    words = _listen_words(level, which, tier)
    st = db.get_settings()
    ao = _audio_opts()
    files = audio.plan(words, ao["minutes"], repeats=ao["repeats"], example=ao["example"], fill=ao["fill"],
                       seed=f"{level}-{which}-{tier}")
    spw = audio.seconds_per_word(ao["repeats"], ao["example"])
    chunks, start = [], 1
    for n, f in enumerate(files, 1):
        new = min(len(f), max(0, len(words) - start + 1))          # 채우기로 다시 넣은 단어는 제외한 새 단어 수
        chunks.append({"n": n, "first": start, "last": start + new - 1, "count": len(f), "new": new,
                       "minutes": round(len(f) * spw / 60), "head": f[0]["word"], "tail": f[-1]["word"]})
        start += new
    return render_template("vocab_listen.html", words=words, level=level, which=which, sets=LISTEN_SETS,
                           tier=tier, TIERS=TIERS,
                           chunks=chunks, audio_ok=audio.available(), ao=ao, minutes_opts=AUDIO_MINUTES,
                           mp3=audio.lameenc is not None,
                           tts={"rate": float(st["tts_rate"]), "accent": st["tts_accent"]})


def _audio_request(args) -> tuple[list[dict], dict, str]:
    """요청 인자 → (이 파일에 들어갈 단어, 옵션, 파일 이름)."""
    try:
        level = int(args.get("level") or 0) or None
        chunk = max(1, int(args.get("chunk") or 1))
        minutes = int(args.get("minutes") or 60)
        repeats = int(args.get("repeats") or 3)
    except (TypeError, ValueError):
        level, chunk, minutes, repeats = None, 1, 60, 3
    level = level if level in (1, 2, 3, 4, 5) else None
    which = args.get("set") if args.get("set") in LISTEN_SETS else "all"
    tier = args.get("tier") if args.get("tier") in TIERS else None
    ao = {"minutes": minutes if minutes in AUDIO_MINUTES else 60, "repeats": max(1, min(repeats, 5)),
          "example": str(args.get("example")) in ("1", "true", "True"),
          "fill": str(args.get("fill", "1")) in ("1", "true", "True")}
    files = audio.plan(_listen_words(level, which, tier), ao["minutes"], repeats=ao["repeats"],
                       example=ao["example"], fill=ao["fill"], seed=f"{level}-{which}-{tier}")
    words = files[chunk - 1] if chunk <= len(files) else []
    grade = (bank_grade_name(level) if level else "전체") + (f"_{TIERS[tier]}" if tier else "")
    name = f"토익단어_{grade}_{ao['minutes']}분_{chunk:02d}.mp3"
    return words, ao, name


def bank_grade_name(level: int) -> str:
    return {1: "Orange", 2: "Brown", 3: "Green", 4: "Blue", 5: "Gold"}[level]


@bp.route("/api/vocab/audio/prepare", methods=["POST"])
def audio_prepare():
    """1시간짜리 음성 파일 만들기 시작 (처음엔 1~2분, 만든 뒤에는 바로)."""
    words, ao, name = _audio_request(request.get_json(silent=True) or {})
    if not words:
        return jsonify(error="단어가 없습니다."), 400
    if not audio.available():
        return jsonify(error="음성 파일 기능에 필요한 패키지(edge-tts, lameenc)가 설치되지 않았습니다."), 400
    job = audio.start_job(words, Path(current_app.config["DB_PATH"]).parent / "audio",
                          repeats=ao["repeats"], example=ao["example"], name=name)
    return jsonify(_job_view(job))


@bp.route("/api/vocab/audio/status/<key>")
def audio_status(key: str):
    job = audio.get_job(key)
    if not job:
        return jsonify(error="작업이 없습니다. 다시 만들어 주세요."), 404
    return jsonify(_job_view(job))


def _job_view(job: dict) -> dict:
    out = {k: job.get(k) for k in ("key", "state", "done", "total", "error", "name", "minutes", "mb")}
    if job["state"] == "done":
        out["url"] = url_for("vocab.audio_file", key=job["key"])
        out["download"] = url_for("vocab.audio_file", key=job["key"], dl=1)
    return out


@bp.route("/vocab/audio/<key>.mp3")
def audio_file(key: str):
    """만든 음성 파일: 바로 재생(스트리밍) 또는 ?dl=1 로 내려받기."""
    job = audio.get_job(key)
    if not job or job["state"] != "done" or not Path(job["path"]).exists():
        return jsonify(error="파일이 없습니다. 듣기 화면에서 다시 만들어 주세요."), 404
    return send_file(job["path"], mimetype="audio/mpeg", as_attachment=request.args.get("dl") == "1",
                     download_name=job["name"], conditional=True)


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

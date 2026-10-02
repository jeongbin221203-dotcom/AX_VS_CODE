"""대시보드 · 등급별 가이드 · 통계 · 기록 · 설정."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for

from core import db, planner, scoring, stats
from core.exams import EXAMS
from core.content import PART_INFO
from core.guide import GUIDE, PART_TIPS, TARGET_SEC
from core.srs import level_progress

from .helpers import bank

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    """첫 화면: 설정에서 고른 시험의 홈 (기본 토익)."""
    key = db.get_settings().get("home_exam") or "toeic"
    if key != "toeic" and key in EXAMS:
        return redirect(url_for(EXAMS[key]["endpoint"]))
    return dashboard()


@bp.route("/home-exam", methods=["POST"])
def set_home_exam():
    """상단 탭의 '첫 화면으로' 버튼."""
    key = request.form.get("exam", "")
    if key in EXAMS:
        db.save_settings({"home_exam": key})
        flash(f"첫 화면을 {EXAMS[key]['name']}(으)로 바꿨습니다. TS 로고를 누르면 이 화면이 열립니다.", "ok")
        return redirect(url_for(EXAMS[key]["endpoint"]))
    abort(400)


@bp.route("/toeic")
def dashboard():
    settings = db.get_settings()
    plan = planner.build(bank(), settings)
    return render_template("dashboard.html", plan=plan, streak=stats.streak(),
                           acc=stats.part_accuracy(last_n=60), recent=stats.recent_sessions(5),
                           history=stats.score_history(10))


@bp.route("/favicon.ico")
def favicon():
    """브라우저가 /favicon.ico 를 직접 찾을 때 (북마크·옛 브라우저)."""
    return send_from_directory(current_app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon")


@bp.route("/guide")
@bp.route("/guide/<int:level>")
def guide(level: int | None = None):
    if level is None:
        score, _ = planner.current_score(db.get_settings())
        g = scoring.grade_for(score)
        level = g.level if g else 1
    if level not in GUIDE:
        abort(404)
    b = bank()
    counts = {p: b.count_questions(p, level) for p in PART_INFO}
    return render_template("guide.html", level=level, grade=scoring.GRADE_BY_LEVEL[level], g=GUIDE[level],
                           tips=PART_TIPS, counts=counts, vocab_n={t: sum(1 for w in b.vocab if w["level"] == level and w["tier"] == t)
                                    for t in ("core", "stretch")})


@bp.route("/exam/<key>")
def exam(key: str):
    """시험 카테고리. 토익 외 시험은 준비 중 안내."""
    e = EXAMS.get(key)
    if not e:
        abort(404)
    if e["ready"]:
        return redirect(url_for(e["endpoint"]))
    return render_template("exam_soon.html", e=e, key=key)


@bp.route("/stats")
def stats_page():
    b = bank()
    lvl = stats.level_accuracy()
    return render_template("stats.html", acc_all=stats.part_accuracy(), acc_7=stats.part_accuracy(days=7),
                           lvl=lvl, types=stats.type_accuracy(), history=stats.score_history(),
                           daily=stats.daily_counts(30), times=stats.time_by_part(), target_sec=TARGET_SEC,
                           vocab=level_progress(b), streak=stats.streak())


@bp.route("/history")
def history():
    return render_template("history.html", sessions=stats.recent_sessions(200))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        f = request.form
        errors = []
        values = {}

        def int_field(name, lo, hi, label, allow_blank=False):
            raw = f.get(name, "").strip()
            if allow_blank and raw == "":
                values[name] = ""
                return
            try:
                v = int(raw)
                if not lo <= v <= hi:
                    raise ValueError
                values[name] = str(v)
            except ValueError:
                errors.append(f"{label}은(는) {lo}~{hi} 사이 숫자로 입력하세요.")

        int_field("target_score", 10, 990, "목표 점수")
        int_field("current_score", 10, 990, "현재 점수", allow_blank=True)
        int_field("daily_new_words", 0, 200, "하루 새 단어 수")
        int_field("daily_questions", 5, 300, "하루 문항 수")
        exam = f.get("exam_date", "").strip()
        if exam:
            try:
                datetime.strptime(exam, "%Y-%m-%d")
            except ValueError:
                errors.append("시험일 형식이 올바르지 않습니다.")
        values["exam_date"] = exam
        try:
            rate = float(f.get("tts_rate", "1.0"))
            values["tts_rate"] = str(min(max(rate, 0.6), 1.5))
        except ValueError:
            errors.append("음성 속도 값 오류")
        home_exam = f.get("home_exam", "toeic")
        values["home_exam"] = home_exam if home_exam in EXAMS else "toeic"
        accent = f.get("tts_accent", "mix")
        values["tts_accent"] = accent if accent in ("mix", "us", "uk", "au") else "mix"
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("settings.html", s={**db.get_settings(), **f.to_dict()})
        old = db.get_settings()
        if values.get("current_score") and values["current_score"] != old.get("current_score"):
            values["current_score_at"] = db.now()
        db.save_settings(values)
        flash("저장했습니다.", "ok")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", s=db.get_settings())

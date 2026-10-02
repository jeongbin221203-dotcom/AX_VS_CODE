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
    """메인: 네 시험(토익·토플·토익스피킹·오픽)으로 들어가는 화면. 시험마다 지금 위치·목표·문제 수·마지막 학습."""
    from core import speaking as S, toefl as T
    from flask import current_app
    st = db.get_settings()

    def last(sql, *args):
        with db.connect() as con:
            r = con.execute(sql, args).fetchone()
        return (r[0] or "")[:10] if r else ""

    # 토익
    score, src = planner.current_score(st)
    grade = scoring.grade_for(score) if score else None
    tb = bank()
    toeic_n = sum(tb.count_questions(p) for p in PART_INFO)
    # 토플
    fb = current_app.extensions["toefl_bank"]
    bands = T.section_bands()
    overall = T.overall_band(bands)
    toefl_n = sum(len(v) for v in fb.items.values())
    # 말하기
    sb = current_app.extensions["speaking_bank"]
    tsp_est = S.tsp_estimate(S.tsp_task_stats())
    tsp_lv = S.tsp_level(tsp_est)
    opic = S.opic_stats()
    exams = [
        {"key": "toeic", "href": url_for("main.dashboard"), "mark": "LC·RC",
         "now": f"{score}점" if score else "–", "now_sub": f"{grade.name} · {src}" if grade else "진단 테스트로 시작",
         "target": f"{st.get('target_score')}점", "n": f"{toeic_n:,}문항 · 단어 {len(tb.vocab):,}",
         "last": last("SELECT MAX(created_at) FROM sessions"),
         "links": [("오늘 할 일", url_for("main.dashboard")), ("파트 연습", url_for("quiz.practice")),
                   ("모의고사", url_for("quiz.mock")), ("단어", url_for("vocab.overview"))]},
        {"key": "toefl", "href": url_for("toefl.home"), "mark": "R·L·S·W",
         "now": f"밴드 {overall}" if overall else "–",
         "now_sub": T.cefr(overall) if overall else ("영역별 " + " · ".join(f"{k} {v}" for k, v in bands.items() if v) if any(bands.values()) else "네 영역을 풀면 계산"),
         "target": f"밴드 {st.get('toefl_target')}", "n": f"{toefl_n:,}문제 · 어휘 {len(fb.vocab):,}",
         "last": last("SELECT MAX(created_at) FROM toefl_attempts"),
         "links": [("토플 홈", url_for("toefl.home")), ("실전 모의고사", url_for("toefl.mock")),
                   ("학술 어휘", url_for("tvocab.overview"))]},
        {"key": "toeic-speaking", "href": url_for("speaking.tsp_home"), "mark": "11문항",
         "now": f"{tsp_est}점" if tsp_est is not None else "–", "now_sub": tsp_lv[1] if tsp_lv else "다섯 유형을 연습하면 계산",
         "target": f"{st.get('tsp_target')}점", "n": f"{sum(len(v) for v in sb.tsp.values()):,}문제",
         "last": last("SELECT MAX(created_at) FROM speaking_attempts WHERE exam = 'tsp'"),
         "links": [("유형별 연습", url_for("speaking.tsp_home") + "#tasks"), ("실전 모의고사", url_for("speaking.tsp_mock")),
                   ("답변 틀", url_for("speaking.tsp_guide"))]},
        {"key": "opic", "href": url_for("speaking.opic_home"), "mark": "NL~AL",
         "now": opic["grade"] or "–", "now_sub": S.OPIC_GRADE_NAME.get(opic["grade"], "답변 5개를 채점하면 계산"),
         "target": st.get("opic_target") or "IH", "n": f"{len(sb.opic_q) + 3 * len(sb.opic_rp):,}문항 · 롤플레이 {len(sb.opic_rp)}세트",
         "last": last("SELECT MAX(created_at) FROM speaking_attempts WHERE exam = 'opic'"),
         "links": [("설문·난이도", url_for("speaking.opic_survey")), ("주제별 연습", url_for("speaking.opic_home") + "#topics"),
                   ("실전 모의고사", url_for("speaking.opic_mock"))]},
    ]
    for e in exams:
        e.update(EXAMS[e["key"]])
    return render_template("home.html", exams=exams, streak=stats.streak())


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

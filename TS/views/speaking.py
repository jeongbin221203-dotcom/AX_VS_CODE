"""말하기 시험: 토익스피킹(/speaking/toeic) · 오픽(/speaking/opic) — 홈 · 연습 · 실전 모의고사 · 답변 틀 가이드."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from core import db, speaking as S
from core.exams import EXAMS

bp = Blueprint("speaking", __name__, url_prefix="/speaking")

EXAM_KEY = {"tsp": "toeic-speaking", "opic": "opic"}


def sbank() -> S.SpeakingBank:
    b = current_app.extensions["speaking_bank"]
    b.refresh_if_changed()
    return b


def _tts() -> dict:
    st = db.get_settings()
    return {"rate": float(st["tts_rate"]), "accent": st["tts_accent"]}


def _rubrics() -> dict:
    return {**{t: r for t, r in S.TSP_RUBRIC.items()}, "opic": S.OPIC_RUBRIC}


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(request.args.get(name, default)), hi))
    except ValueError:
        return default


def _tsp_target() -> int:
    try:
        return int(db.get_settings().get("tsp_target") or 140)
    except ValueError:
        return 140


def _opic_settings() -> dict:
    st = db.get_settings()
    survey = [t for t in (st.get("opic_survey") or "").split(",") if t in S.OPIC_TOPICS and S.OPIC_TOPICS[t][2]]
    try:
        level = int(st.get("opic_level") or 4)
    except ValueError:
        level = 4
    target = st.get("opic_target") or "IH"
    return {"survey": survey, "level": level if level in S.OPIC_LEVELS else 4,
            "target": target if target in S.OPIC_TARGETS else "IH"}


# ---- 토익스피킹 -----------------------------------------------------------------------

@bp.route("/toeic")
def tsp_home():
    b = sbank()
    stats = S.tsp_task_stats()
    est = S.tsp_estimate(stats)
    target = _tsp_target()
    tasks = [{"key": t, **v, "count": len(b.tsp.get(t, [])), "stat": stats.get(t)} for t, v in S.TSP_TASKS.items()]
    return render_template("speaking/tsp_home.html", tasks=tasks, est=est, level=S.tsp_level(est), target=target,
                           target_level=S.tsp_level(target), LEVELS=S.TSP_LEVELS, TARGETS=S.TSP_TARGETS,
                           mocks=S.list_mocks("tsp", 5), recent=S.recent("tsp", 12), TASKS=S.TSP_TASKS,
                           trend=S.trend("tsp"), e=EXAMS["toeic-speaking"])


@bp.route("/toeic/target", methods=["POST"])
def tsp_target():
    try:
        t = int(request.form.get("target", ""))
        if t not in S.TSP_TARGETS:
            raise ValueError
    except ValueError:
        flash("목표 점수를 목록에서 고르세요.", "error")
        return redirect(url_for("speaking.tsp_home"))
    db.save_settings({"tsp_target": str(t)})
    return redirect(url_for("speaking.tsp_home"))


@bp.route("/toeic/practice/<task>")
def tsp_practice(task: str):
    if task not in S.TSP_TASKS:
        abort(404)
    n = _int_arg("n", 2 if S.TSP_TASKS[task]["n"] == 2 else 1, 1, 10)
    units = S.tsp_practice(sbank(), task, n)
    info = S.TSP_TASKS[task]
    payload = {"exam": "tsp", "mode": "practice", "units": units, "tts": _tts(), "rubrics": _rubrics(),
               "home": url_for("speaking.tsp_home")}
    return render_template("speaking/run.html", payload=payload, title=f"{info['q']} {info['name']}",
                           sub=f"{info['en']} · {info['desc']}", exam="tsp", n=n, task=task, home=url_for("speaking.tsp_home"))


@bp.route("/toeic/mock", methods=["GET", "POST"])
def tsp_mock():
    if request.method == "POST":
        plan = S.tsp_mock_plan(sbank())
        if sum(len(u["steps"]) for u in plan) < 11:
            flash("문제가 아직 모자라 모의고사를 만들 수 없습니다.", "error")
            return redirect(url_for("speaking.tsp_mock"))
        mid = S.create_mock("tsp", plan, {"target": _tsp_target()})
        return redirect(url_for("speaking.mock_run", mid=mid))
    return render_template("speaking/mock_start.html", exam="tsp", past=S.list_mocks("tsp"), TASKS=S.TSP_TASKS,
                           e=EXAMS["toeic-speaking"], home=url_for("speaking.tsp_home"))


@bp.route("/toeic/guide")
def tsp_guide():
    return render_template("speaking/tsp_guide.html", e=EXAMS["toeic-speaking"], TASKS=S.TSP_TASKS, RUBRIC=S.TSP_RUBRIC,
                           LEVELS=S.TSP_LEVELS, target=_tsp_target())


# ---- 오픽 -----------------------------------------------------------------------------

def _topic_rows(b: S.SpeakingBank, survey: list[str]) -> list[dict]:
    counts = b.topic_counts()
    done: dict[str, list[float]] = {}
    for r in S.topic_progress():
        it = b.by_id.get((r["task"], r["item_id"]))
        if it:
            done.setdefault(it["topic"], []).append(r["points"])
    rows = []
    for key, (name, group, is_survey) in S.OPIC_TOPICS.items():
        if key == "intro":
            continue
        c = counts.get(key, {})
        pts = done.get(key, [])
        rows.append({"key": key, "name": name, "group": group, "survey": is_survey, "picked": key in survey,
                     "counts": c, "total": sum(c.values()), "n": len(pts), "avg": sum(pts) / len(pts) if pts else None})
    return rows


@bp.route("/opic")
def opic_home():
    b = sbank()
    st = _opic_settings()
    rows = _topic_rows(b, st["survey"])
    return render_template("speaking/opic_home.html", st=st, rows=rows, stats=S.opic_stats(), mocks=S.list_mocks("opic", 5),
                           GRADES=S.OPIC_GRADES, GRADE_NAME=S.OPIC_GRADE_NAME, LEVELS=S.OPIC_LEVELS, KINDS=S.OPIC_KINDS,
                           TOPICS=S.OPIC_TOPICS, recent=S.recent("opic", 12), by_id=b.by_id, rp_n=len(b.opic_rp),
                           intro_n=sum(1 for q in b.opic_q if q["kind"] == "intro"), trend=S.trend("opic"),
                           e=EXAMS["opic"])


@bp.route("/opic/survey", methods=["GET", "POST"])
def opic_survey():
    if request.method == "POST":
        picked = [t for t in request.form.getlist("topic") if t in S.OPIC_TOPICS and S.OPIC_TOPICS[t][2]]
        errors = []
        for group, need in S.SURVEY_GROUPS.items():
            have = sum(1 for t in picked if S.OPIC_TOPICS[t][1] == group)
            if have < need:
                errors.append(f"{group} 주제를 {need}개 이상 고르세요.")
        try:
            level = int(request.form.get("level", ""))
            if level not in S.OPIC_LEVELS:
                raise ValueError
        except ValueError:
            errors.append("난이도를 고르세요.")
            level = 4
        target = request.form.get("target", "IH")
        if target not in S.OPIC_TARGETS:
            errors.append("목표 등급을 고르세요.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("speaking/opic_survey.html", st={"survey": picked, "level": level, "target": target},
                                   TOPICS=S.OPIC_TOPICS, GROUPS=S.SURVEY_GROUPS, LEVELS=S.OPIC_LEVELS, TARGETS=S.OPIC_TARGETS,
                                   GRADE_NAME=S.OPIC_GRADE_NAME)
        if "home" not in picked:
            picked.insert(0, "home")                     # 거주 주제는 누구나 받는다
        db.save_settings({"opic_survey": ",".join(picked), "opic_level": str(level), "opic_target": target})
        flash("설문을 저장했습니다. 모의고사와 추천 연습에 이 주제가 나옵니다.", "ok")
        return redirect(url_for("speaking.opic_home"))
    return render_template("speaking/opic_survey.html", st=_opic_settings(), TOPICS=S.OPIC_TOPICS, GROUPS=S.SURVEY_GROUPS,
                           LEVELS=S.OPIC_LEVELS, TARGETS=S.OPIC_TARGETS, GRADE_NAME=S.OPIC_GRADE_NAME)


@bp.route("/opic/practice")
def opic_practice():
    topic = request.args.get("topic") or None
    kind = request.args.get("kind") or None
    if topic and topic not in S.OPIC_TOPICS:
        abort(404)
    if kind and kind not in ("intro", "describe", "routine", "past", "compare", "issue", "roleplay"):
        abort(404)
    if topic == "intro":
        topic, kind = None, "intro"
    show_text = request.args.get("text", "1") == "1"
    n = _int_arg("n", 1 if kind == "roleplay" else (3 if not topic else 5), 1, 10)
    units = S.opic_practice(sbank(), topic, kind, n, show_text)
    name = S.OPIC_TOPICS[topic][0] if topic else "전체 주제"
    kname = "롤플레이" if kind == "roleplay" else S.OPIC_KINDS.get(kind, "콤보 (묘사 → 습관 → 경험)" if topic else "섞어서")
    payload = {"exam": "opic", "mode": "practice", "units": units, "tts": _tts(), "rubrics": _rubrics(),
               "home": url_for("speaking.opic_home"), "show_text": show_text}
    return render_template("speaking/run.html", payload=payload, title=f"{name} · {kname}",
                           sub="질문을 듣고 바로 답합니다. 준비 시간은 없고, 다시 듣기는 한 번만 됩니다 (실제 시험처럼).",
                           exam="opic", n=n, topic=topic, kind=kind, show_text=show_text, home=url_for("speaking.opic_home"))


@bp.route("/opic/mock", methods=["GET", "POST"])
def opic_mock():
    st = _opic_settings()
    if request.method == "POST":
        try:
            level = int(request.form.get("level", st["level"]))
        except ValueError:
            level = st["level"]
        level = level if level in S.OPIC_LEVELS else st["level"]
        plan = S.opic_mock_plan(sbank(), st["survey"], level)
        if sum(len(u["steps"]) for u in plan) < 10:
            flash("문제가 아직 모자라 모의고사를 만들 수 없습니다.", "error")
            return redirect(url_for("speaking.opic_mock"))
        mid = S.create_mock("opic", plan, {"level": level, "survey": st["survey"], "target": st["target"]})
        return redirect(url_for("speaking.mock_run", mid=mid))
    return render_template("speaking/mock_start.html", exam="opic", past=S.list_mocks("opic"), st=st, LEVELS=S.OPIC_LEVELS,
                           TOPICS=S.OPIC_TOPICS, e=EXAMS["opic"], home=url_for("speaking.opic_home"), minutes=S.OPIC_MINUTES)


@bp.route("/opic/guide")
def opic_guide():
    return render_template("speaking/opic_guide.html", e=EXAMS["opic"], RUBRIC=S.OPIC_RUBRIC, KINDS=S.OPIC_KINDS,
                           GRADE_NAME=S.OPIC_GRADE_NAME, st=_opic_settings())


# ---- 모의고사 공통 ---------------------------------------------------------------------

@bp.route("/mock/<int:mid>")
def mock_run(mid: int):
    m = S.get_mock(mid)
    if not m:
        abort(404)
    if m["finished_at"]:
        return redirect(url_for("speaking.mock_result", mid=mid))
    home = url_for("speaking.tsp_home" if m["exam"] == "tsp" else "speaking.opic_home")
    payload = {"exam": m["exam"], "mode": "mock", "mock_id": mid, "units": m["plan"], "tts": _tts(), "rubrics": _rubrics(),
               "home": home, "minutes": S.OPIC_MINUTES if m["exam"] == "opic" else None}
    title = "토익스피킹 실전 모의고사" if m["exam"] == "tsp" else f"오픽 실전 모의고사 · 난이도 {m['settings'].get('level')}"
    return render_template("speaking/run.html", payload=payload, title=title, exam=m["exam"], mock=m, home=home,
                           sub="11문항 · 약 20분 · 문항마다 준비·답변 시간이 정해져 있습니다" if m["exam"] == "tsp"
                           else "15문항 · 40분 · 질문은 소리로만 나오고 다시 듣기는 한 번")


@bp.route("/mock/<int:mid>/result")
def mock_result(mid: int):
    m = S.get_mock(mid)
    if not m or not m["finished_at"]:
        abort(404)
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT task, item_id, qidx, points, max_points, words, seconds, accuracy, response FROM speaking_attempts "
            "WHERE mock_id = ? ORDER BY id", (mid,))]
    got = {(r["task"].removeprefix("tsp:"), r["item_id"], r["qidx"]): r for r in rows}
    steps = [{**s, "row": got.get((s["ref"]["task"], s["ref"]["item_id"], s["ref"]["qidx"]))} for u in m["plan"] for s in u["steps"]]
    return render_template("speaking/mock_result.html", m=m, r=m["result"], steps=steps, TASKS=S.TSP_TASKS, LEVELS=S.TSP_LEVELS,
                           GRADES=S.OPIC_GRADES, GRADE_NAME=S.OPIC_GRADE_NAME,
                           home=url_for("speaking.tsp_home" if m["exam"] == "tsp" else "speaking.opic_home"))


# ---- API ------------------------------------------------------------------------------

@bp.route("/api/attempt", methods=["POST"])
def api_attempt():
    data = request.get_json(silent=True) or {}
    exam = data.get("exam")
    if exam not in ("tsp", "opic"):
        return jsonify(error="시험 종류 오류"), 400
    saved = S.record(sbank(), exam, data.get("rows") or [])
    if not saved:
        return jsonify(error="저장할 답변이 없습니다."), 400
    if exam == "tsp":
        est = S.tsp_estimate(S.tsp_task_stats())
        return jsonify(saved=len(saved), estimate=est)
    return jsonify(saved=len(saved), grade=S.opic_stats()["grade"])


@bp.route("/api/mock/<int:mid>/finish", methods=["POST"])
def api_mock_finish(mid: int):
    data = request.get_json(silent=True) or {}
    try:
        S.finish_mock(sbank(), mid, data.get("rows") or [], S._num(data.get("duration"), 0, 6000))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(redirect=url_for("speaking.mock_result", mid=mid))

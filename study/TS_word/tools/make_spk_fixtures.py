"""토익스피킹·오픽 로직(js/spk-core.js)을 TS 앱(Python core/speaking.py)과 대조하기 위한 정답표를 만든다.

실행:  python tools/make_spk_fixtures.py            (TS_word 폴더에서, TS 앱은 ../TS — 읽기만 함)
만드는 파일: tests/spk_fixtures.json   (tests/spk.test.js 가 읽는다)

담는 것
- consts   과제 정의·채점 기준·레벨/등급 표 등 상수 (JS 상수와 같은지)
- score / level / grade   점수·레벨·등급 환산표 (경계값 포함)
- digests  실제 문제 은행 전부의 '단계(steps)' 를 TS 앱 코드로 만든 뒤 정규화 JSON 의 sha1 (JS 가 같은 문제로 같은 단계를 만드는지)
- now_seq  scenario 동안 db.now() 가 돌려준 시각(부른 순서) — JS 의 Spk.setNow 가 그대로 받는다
- events   임시 DB 에 시각을 고정해 기록한 답변·모의고사 (JS 가 같은 순서로 다시 기록한 뒤 통계를 비교)
- expected 같은 DB 에서 TS 앱이 계산한 통계·약한 문항·추세·모의고사 결과
- shapes   무작위 모의고사 구성의 '모양'(유형 순서) 허용 집합 — JS 가 만든 구성이 TS 앱이 만들 수 있는 모양인지
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
TS = ROOT.parent / "TS"
sys.path.insert(0, str(TS))

from core import db, speaking as S                      # noqa: E402

AS_OF = "2026-10-10"
SEED = 20261010


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha(o) -> str:
    return hashlib.sha1(canon(o).encode("utf-8")).hexdigest()


class Clock:
    def __init__(self):
        self.t = datetime(2026, 8, 1, 9, 0, 0)
        self.log: list[str] = []                    # db.now() 가 돌려준 값을 부른 순서대로 (JS 가 같은 시각을 받게)

    def set(self, d: str, h: int = 9):
        y, m, dd = map(int, d.split("-"))
        self.t = datetime(y, m, dd, h, 0, 0)

    def tick(self) -> str:
        self.t += timedelta(seconds=1)
        v = self.t.isoformat(timespec="seconds")
        self.log.append(v)
        return v


clock = Clock()


def consts() -> dict:
    return {
        "TSP_TASKS": S.TSP_TASKS, "TSP_LEVELS": S.TSP_LEVELS, "TSP_TARGETS": S.TSP_TARGETS, "TSP_RUBRIC": S.TSP_RUBRIC,
        "TSP_DIRECTIONS": S.TSP_DIRECTIONS, "TSP_RAW_MAX": S.TSP_RAW_MAX, "OPIC_TOPICS": S.OPIC_TOPICS,
        "SURVEY_GROUPS": S.SURVEY_GROUPS, "OPIC_KINDS": S.OPIC_KINDS, "OPIC_GRADES": S.OPIC_GRADES,
        "OPIC_GRADE_NAME": S.OPIC_GRADE_NAME, "OPIC_RUBRIC": S.OPIC_RUBRIC, "OPIC_LEVELS": S.OPIC_LEVELS,
        "OPIC_TARGETS": S.OPIC_TARGETS, "OPIC_MINUTES": S.OPIC_MINUTES,
    }


def tables() -> dict:
    raws = [x / 4 for x in range(0, 41 * 4 + 1)] + [-3, 99]
    scores = list(range(-10, 215, 5)) + [None]
    grid_pts = [x / 10 for x in range(10, 51)] + [1.29, 1.3, 1.79, 1.8, 2.59, 2.6, 3.59, 3.6, 4.49, 4.5]
    words = [None, 0, 69, 70, 109, 110, 111, 300]
    return {
        "score": [[r, S.tsp_score(r)] for r in raws],
        "level": [[s, S.tsp_level(s)] for s in scores],
        "grade": [[p, w, S.opic_grade(p, w)] for p in grid_pts + [None] for w in words],
    }


def digests(bank: S.SpeakingBank) -> dict:
    out = {"tsp": {}, "opic_q": {}, "opic_rp": {}}
    for task, items in bank.tsp.items():
        for it in items:
            out["tsp"][f"{task}|{it['id']}"] = [sha(S.tsp_steps(task, it)), sha(S.tsp_steps(task, it, 4))]
    for q in bank.opic_q:
        out["opic_q"][q["id"]] = [sha(S.opic_q_step(q, "라벨", True)), sha(S.opic_q_step(q, "Question 3", False))]
    for r in bank.opic_rp:
        out["opic_rp"][r["id"]] = [sha(S.opic_rp_steps(r, None, True)), sha(S.opic_rp_steps(r, 5, False))]
    return out


def row_for(rng: random.Random, task: str, item_id: str, qidx: int, exam: str, skill: float) -> dict:
    mx = S.TSP_TASKS[task]["max"] if exam == "tsp" else 5
    lo = 0 if exam == "tsp" else 1
    pts = min(mx, max(lo, int(round(rng.gauss(skill * mx, 0.9)))))
    words = rng.choice([None, rng.randint(5, 160)])
    return {"task": task, "item_id": item_id, "qidx": qidx, "points": pts, "words": words,
            "seconds": round(rng.uniform(5, 90), 1), "accuracy": round(rng.random(), 3) if task == "read_aloud" else None,
            "response": "I think so. " * rng.randint(0, 3)}


def tsp_rows(rng, bank, task, it, skill):
    n = len(it["questions"]) if task in ("respond_questions", "respond_info") else 1
    return [row_for(rng, task, it["id"], i, "tsp", skill) for i in range(n)]


def opic_rows(rng, bank, kind, it, skill):
    n = len(it["steps"]) if kind == "opic_rp" else 1
    return [row_for(rng, kind, it["id"], i, "opic", skill) for i in range(n)]


def scenario(bank: S.SpeakingBank) -> tuple[list[dict], dict]:
    """임시 DB 에 일어난 일을 events 로 남기고, 그 DB 에서 계산한 expected 를 돌려준다."""
    rng = random.Random(SEED)
    events: list[dict] = []
    days = [(datetime(2026, 8, 1) + timedelta(days=d)).date().isoformat() for d in (0, 1, 30, 52, 61, 62, 70, 75, 80, 85, 88, 90, 91)]

    def rec(exam, rows, day, hour, mock_id=None):
        clock.set(day, hour)
        ts = clock.t.isoformat(timespec="seconds")
        saved = S.record(bank, exam, rows, mock_id=mock_id)
        events.append({"ts": ts, "kind": "record", "exam": exam, "rows": rows, "mock_id": mock_id, "saved": len(saved)})
        return saved

    for di, day in enumerate(days):
        skill = 0.35 + 0.05 * di
        for task in S.TSP_ORDER:
            for it in rng.sample(bank.tsp[task], 2):
                rec("tsp", tsp_rows(rng, bank, task, it, skill), day, 9 + di % 5)
        for _ in range(2):
            q = rng.choice(bank.opic_q)
            rec("opic", opic_rows(rng, bank, "opic_q", q, skill), day, 14)
        rp = rng.choice(bank.opic_rp)
        rec("opic", opic_rows(rng, bank, "opic_rp", rp, skill), day, 15)
    # 같은 문제를 다시 풀어 마지막 회차 점수가 바뀌는 경우(약한 문항 판정)
    it = bank.tsp["respond_questions"][0]
    rec("tsp", [dict(row_for(rng, "respond_questions", it["id"], i, "tsp", 0.1), points=0) for i in range(3)], AS_OF, 8)
    q = bank.opic_q[5]
    rec("opic", [dict(row_for(rng, "opic_q", q["id"], 0, "opic", 0.1), points=2)], AS_OF, 8)
    # 잘못된 행: 없는 문제·범위 밖 점수·없는 단계·문자열 숫자
    bad = [{"task": "read_aloud", "item_id": "nope", "qidx": 0, "points": 2},
           {"task": "read_aloud", "item_id": bank.tsp["read_aloud"][0]["id"], "qidx": 0, "points": "9"},
           {"task": "read_aloud", "item_id": bank.tsp["read_aloud"][1]["id"], "qidx": 0, "points": "abc"},
           {"task": "nope", "item_id": "x", "qidx": 0, "points": 1}, "문자열", None,
           {"task": "read_aloud", "item_id": bank.tsp["read_aloud"][2]["id"], "qidx": 99, "points": -4, "words": 99999, "seconds": 5000, "accuracy": 3}]
    rec("tsp", bad, AS_OF, 8)
    badop = [{"task": "opic_q", "item_id": bank.opic_q[7]["id"], "qidx": 1, "points": 3},          # 없는 단계 → 저장 안 함
             {"task": "opic_q", "item_id": bank.opic_q[8]["id"], "qidx": 0, "points": 0},          # 1점으로 올림
             {"task": "opic_rp", "item_id": bank.opic_rp[1]["id"], "qidx": 2, "points": 9}]        # 5점으로 내림
    rec("opic", badop, AS_OF, 8)

    # 모의고사 2개 (토익스피킹 1, 오픽 1) — 일부만 답함 / 자기소개 제외 확인
    clock.set(AS_OF, 10)
    plan = S.tsp_mock_plan(bank, random.Random(11))
    events.append({"kind": "create_mock", "exam": "tsp", "units": [{"task": u["task"], "item_id": u["item_id"]} for u in plan],
                   "settings": {"target": 140}, "expected_plan_sha": sha(plan)})
    mid = S.create_mock("tsp", plan, {"target": 140})
    rows = [row_for(rng, s["ref"]["task"], s["ref"]["item_id"], s["ref"]["qidx"], "tsp", 0.7) for u in plan for s in u["steps"]][:9]
    clock.set(AS_OF, 10)
    ts = (clock.t + timedelta(seconds=1)).isoformat(timespec="seconds")
    S.finish_mock(bank, mid, rows, 1100.0)
    events.append({"ts": ts, "kind": "finish_mock", "mid": mid, "rows": rows, "duration": 1100.0})

    survey = ["home", "movie", "park", "jogging", "cooking", "travel_dom"]
    plan2 = S.opic_mock_plan(bank, survey, 4, random.Random(12))
    events.append({"kind": "create_mock", "exam": "opic", "units": [{"task": u["task"], "item_id": u["item_id"]} for u in plan2],
                   "settings": {"level": 4, "survey": survey, "target": "IH"}, "expected_plan_sha": sha(plan2)})
    mid2 = S.create_mock("opic", plan2, {"level": 4, "survey": survey, "target": "IH"})
    rows2 = [row_for(rng, s["ref"]["task"], s["ref"]["item_id"], s["ref"]["qidx"], "opic", 0.7) for u in plan2 for s in u["steps"]]
    rows2 = rows2[:11]
    clock.set(AS_OF, 11)
    ts2 = (clock.t + timedelta(seconds=1)).isoformat(timespec="seconds")
    S.finish_mock(bank, mid2, rows2, 2000.0)
    events.append({"ts": ts2, "kind": "finish_mock", "mid": mid2, "rows": rows2, "duration": 2000.0})

    # ---- expected -------------------------------------------------------------------
    exp: dict = {}
    exp["tsp_stats"] = S.tsp_task_stats()
    exp["tsp_estimate"] = S.tsp_estimate(exp["tsp_stats"])
    exp["opic_stats"] = S.opic_stats()
    exp["weak"] = {"tsp": {t: S.weak_items("tsp", f"tsp:{t}") for t in S.TSP_ORDER}, "opic": S.weak_items("opic", "opic_q")}
    exp["last_seen"] = {k: S.last_seen(k) for k in [f"tsp:{t}" for t in S.TSP_ORDER] + ["opic_q", "opic_rp"]}
    exp["topic_progress"] = S.topic_progress()
    exp["recent"] = {"tsp": S.recent("tsp", 12), "opic": S.recent("opic", 12)}
    h1, n1 = S.history("tsp", 40, 0)
    h2, n2 = S.history("opic", 15, 15)
    exp["history"] = {"tsp": [h1, n1], "opic_page2": [h2, n2]}
    q = ("SELECT substr(created_at, 1, 10) d, COUNT(*) n, AVG(points / max_points) r, AVG(words) w FROM speaking_attempts "
         "WHERE exam = ? AND created_at >= ?{} GROUP BY d ORDER BY d")
    cut = (datetime.fromisoformat(AS_OF) - timedelta(days=60)).date().isoformat()
    with db.connect() as con:
        exp["trend"] = {"tsp": [dict(r) for r in con.execute(q.format(""), ("tsp", cut))],
                        "opic": [dict(r) for r in con.execute(q.format(""), ("opic", cut))],
                        "tsp_read": [dict(r) for r in con.execute(q.format(" AND task = ?"), ("tsp", cut, "tsp:read_aloud"))]}
    exp["as_of"] = AS_OF
    exp["mocks"] = {"tsp": S.list_mocks("tsp"), "opic": S.list_mocks("opic")}
    exp["mock_full"] = {str(m): S.get_mock(m) for m in (mid, mid2)}
    for m in exp["mock_full"].values():
        m.pop("plan", None)                                   # 계획은 sha 로 따로 대조
    exp["question_of"] = [[t, i, k, S.question_of(bank, t, i, k)] for t, i, k in
                          [("tsp:read_aloud", bank.tsp["read_aloud"][0]["id"], 0), ("tsp:describe_picture", bank.tsp["describe_picture"][0]["id"], 0),
                           ("tsp:respond_questions", bank.tsp["respond_questions"][0]["id"], 2), ("tsp:respond_info", bank.tsp["respond_info"][0]["id"], 1),
                           ("tsp:opinion", bank.tsp["opinion"][0]["id"], 0), ("opic_q", bank.opic_q[0]["id"], 0),
                           ("opic_rp", bank.opic_rp[0]["id"], 1), ("opic_q", "없음", 0)]]
    # 약한 문항 연습 (날짜 상관 없는 순수 함수): 낮은 것부터 나오는지
    exp["practice_weak"] = {
        "tsp_respond_questions": [u["item_id"] for u in S.tsp_practice(bank, "respond_questions", 5, weak=True)],
        "opic": [u["item_id"] for u in S.opic_practice(bank, None, None, 10, True, weak=True)],
    }
    return events, exp


def shapes(bank: S.SpeakingBank) -> dict:
    """무작위 모의고사 구성의 모양(유형 순서) 집합. 기록이 없는 상태(db 가 비었을 때)에서 여러 시드로 모은다."""
    tsp, opic = set(), {}
    surveys = [[], ["home", "movie", "park", "jogging", "cooking", "travel_dom"], ["pets", "gym", "beach", "tv"]]
    for seed in range(120):
        plan = S.tsp_mock_plan(bank, random.Random(seed))
        tsp.add("|".join(f"{s['ref']['task']}:{s['ref']['qidx']}" for u in plan for s in u["steps"]))
    for level in (1, 2, 3, 4, 5, 6):
        got = set()
        for sv in surveys:
            for seed in range(80):
                plan = S.opic_mock_plan(bank, sv, level, random.Random(seed))
                kinds = []
                for u in plan:
                    it = bank.by_id[(u["task"], u["item_id"])]
                    for s in u["steps"]:
                        kinds.append(it["kind"] if u["task"] == "opic_q" else it["steps"][s["ref"]["qidx"]]["kind"])
                got.add("|".join(kinds))
        opic[str(level)] = sorted(got)
    return {"tsp": sorted(tsp), "opic": opic}


def main() -> None:
    bank = S.SpeakingBank(TS / "content" / "speaking")
    out = {"consts": consts(), "tables": tables(), "digests": digests(bank)}
    with tempfile.TemporaryDirectory() as d:
        db.configure(Path(d) / "spk.db")
        S.ensure_schema()
        out["shapes"] = shapes(bank)                           # 기록 없는 상태
        db.now = clock.tick
        out["events"], out["expected"] = scenario(bank)
        out["now_seq"] = clock.log
    out["counts"] = {"tsp": {t: len(v) for t, v in bank.tsp.items()}, "opic_q": len(bank.opic_q), "opic_rp": len(bank.opic_rp)}
    (ROOT / "tests" / "spk_fixtures.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"tests/spk_fixtures.json: 상수·환산표·단계 해시 {sum(len(v) for v in out['digests'].values())}개·이벤트 {len(out['events'])}개")


if __name__ == "__main__":
    main()

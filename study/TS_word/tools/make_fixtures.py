"""TS 앱(Python) 로직의 결과를 JSON 으로 저장해 두고, node 테스트가 TS_word(JS) 결과와 대조하게 한다.

실행:  python tools/make_fixtures.py            (TS_word 폴더에서, TS 앱은 ../TS — 읽기만 함)
만드는 파일: tests/py_fixtures.json

하는 일
1. TS 앱의 실제 코드(core/study·stats·planner·scoring·srs)로 임시 DB 에서 약 4주치 학습을 흉내 낸다
   (파트 연습·진단·모의고사·오답 복습·단어 복습, 일부러 끝내지 않은 세션과 무응답 포함). 시계와 '오늘'은 고정한다.
2. 그 DB 를 import_ts_db.convert 로 TS_word 백업 모양으로 바꿔 저장하고,
3. 같은 DB 에서 Python 이 계산한 통계·계획·오답노트·점수 환산 결과를 '정답'으로 함께 저장한다.
JS 쪽 테스트(tests/exam.test.js)는 백업을 불러온 뒤 같은 값을 JS 로 계산해 일치하는지 본다.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
TS = ROOT.parent / "TS"
sys.path.insert(0, str(TS))
sys.path.insert(0, str(ROOT / "tools"))

from core import db, planner, scoring, srs, stats, study            # noqa: E402
from core.content import PART_INFO, Bank                              # noqa: E402
import import_ts_db as imp                                            # noqa: E402

TODAY = date(2026, 10, 7)
SEED = 20261007


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 9, 8, 0, 0)

    def set_day(self, d: date, hour: int = 8):
        self.t = datetime(d.year, d.month, d.day, hour, 0, 0)

    def tick(self) -> str:
        self.t += timedelta(seconds=1)
        return self.t.isoformat(timespec="seconds")


clock = Clock()


class FakeDate(date):
    @classmethod
    def today(cls):
        return date(clock.t.year, clock.t.month, clock.t.day)


def patch():
    db.now = clock.tick
    for mod in (stats, planner, srs):
        mod.date = FakeDate


def answer_list(bank: Bank, ref: str, rng: random.Random, p_correct: float, blank: float = 0.0):
    out = []
    for q in bank.questions(bank.item(ref)):
        if rng.random() < blank:
            chosen = -1
        elif rng.random() < p_correct:
            chosen = q.answer
        else:
            chosen = rng.choice([i for i in range(4 if q.part != 2 else 3) if i != q.answer])
        out.append({"qidx": q.qidx, "chosen": chosen, "elapsed_ms": rng.randint(4000, 70000)})
    return out


def simulate(bank: Bank, rng: random.Random):
    days = [TODAY - timedelta(days=n) for n in range(28, -1, -1)]
    all_words = [w["id"] for w in bank.vocab]
    for i, d in enumerate(days):
        if i in (3, 4, 11, 19, 20):                    # 쉬는 날 (연속 학습일이 끊기게)
            continue
        clock.set_day(d)
        skill = 0.45 + 0.012 * i + rng.uniform(-0.05, 0.05)
        # 단어 복습
        for wid in rng.sample(all_words, rng.randint(0, 12)):
            srs.review(wid, rng.choice([0, 3, 4, 5]), today=d)
        if rng.random() < 0.4:
            srs.quiz_answer(rng.choice(all_words), rng.random() < 0.6)
        # 파트 연습
        for _ in range(rng.randint(1, 3)):
            part = rng.choice([1, 2, 3, 4, 5, 5, 6, 7])
            level = rng.choice([None, 1, 2, 3, 4, 5])
            qtype = rng.choice([None, None, *bank.types(part)[:2]])
            sid = study.start_practice(bank, part, level, qtype, rng.choice([3, 5, 8, 10]), rng=random.Random(rng.random()))
            s = study.get_session(sid)
            for ref in s["items"]:
                clock.t += timedelta(seconds=rng.randint(5, 60))
                study.grade_item(bank, sid, ref, answer_list(bank, ref, rng, skill, 0.03))
                if rng.random() < 0.05:
                    break                                # 일부는 중간에 그만둠(끝내지 않은 세션으로 남기기도)
            if rng.random() < 0.8:
                study.submit_session(bank, sid, {"duration_sec": rng.randint(120, 1500)})
        # 오답 복습
        if i % 3 == 0 and study.wrong_notes("open"):
            try:
                sid = study.start_review(bank, rng.choice([None, 5]), 6)
                s = study.get_session(sid)
                for ref in s["items"]:
                    study.grade_item(bank, sid, ref, answer_list(bank, ref, rng, min(0.95, skill + 0.25)))
                study.submit_session(bank, sid, {"duration_sec": 300})
            except study.StudyError:
                pass
        # 진단·모의고사
        if i in (2, 14):
            sid = study.start_diagnostic(bank, rng=random.Random(i))
            s = study.get_session(sid)
            payload = {"items": {r: answer_list(bank, r, rng, skill, 0.02) for r in s["items"]}, "duration_sec": 900}
            study.submit_session(bank, sid, payload)
        if i in (7, 22, 27):
            form = {7: "mini", 22: "half", 27: "full"}[i]
            sid = study.start_mock(bank, form, rng=random.Random(i * 7))
            s = study.get_session(sid)
            payload = {"items": {r: answer_list(bank, r, rng, skill, 0.04) for r in s["items"]}, "duration_sec": rng.randint(1800, 7000)}
            study.submit_session(bank, sid, payload)
    # 끝내지 않은 세션: 모의고사 시작만, 진단 시작만, 연습 몇 문제만
    clock.set_day(TODAY, 18)
    study.start_mock(bank, "mini", rng=random.Random(5))
    sid = study.start_practice(bank, 5, 3, None, 8, rng=random.Random(6))
    ref = study.get_session(sid)["items"][0]
    study.grade_item(bank, sid, ref, answer_list(bank, ref, rng, 0.5))
    # 오늘 푼 기록(오늘 할 일 계산용)
    sid = study.start_practice(bank, 7, 2, None, 6, rng=random.Random(8))
    for ref in study.get_session(sid)["items"]:
        study.grade_item(bank, sid, ref, answer_list(bank, ref, rng, 0.6))
    study.submit_session(bank, sid, {"duration_sec": 200})


def plain(o):
    """JSON 으로 저장 가능한 모양으로 (튜플 키·date·dataclass 처리)"""
    if isinstance(o, dict):
        return {(",".join(map(str, k)) if isinstance(k, tuple) else str(k)): plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [plain(x) for x in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, scoring.Grade):
        return {"level": o.level, "name": o.name}
    return o


def plan_view(plan: dict) -> dict:
    p = dict(plan)
    p["grade"] = plan["grade"].level if plan["grade"] else None
    p["target_grade"] = plan["target_grade"].level if plan["target_grade"] else None
    p["guide"] = None
    p["tasks"] = [{k: v for k, v in t.items() if k != "href"} for t in plan["tasks"]]
    return plain(p)


def main() -> None:
    patch()
    rng = random.Random(SEED)
    bank = Bank(TS / "content" / "toeic")
    tmp = Path(tempfile.mkdtemp(prefix="tsfx"))
    db.configure(tmp / "fx.db")
    simulate(bank, rng)
    clock.set_day(TODAY, 20)

    backup = imp.convert(tmp / "fx.db")

    # ---- 점수 환산 (무작위 입력) ----
    r2 = random.Random(1)
    est_cases = []
    for _ in range(300):
        lc = [(r2.randint(1, 5), r2.random() < 0.6) for _ in range(r2.choice([0, 1, 5, 28, 100]))]
        rc = [(r2.randint(1, 5), r2.random() < 0.55) for _ in range(r2.choice([0, 3, 22, 100]))]
        est_cases.append({"lc": lc, "rc": rc, "out": scoring.estimate(lc, rc)})
    raw_cases = []
    for _ in range(300):
        lt, rt = r2.choice([0, 6, 20, 50, 100]), r2.choice([0, 10, 40, 100])
        lcc, rcc = r2.randint(0, lt), r2.randint(0, rt)
        raw_cases.append({"args": [lcc, lt, rcc, rt], "out": scoring.estimate_raw(lcc, lt, rcc, rt)})
    section_cases = [{"section": sec, "ratio": x / 200, "out": scoring.section_score(sec, x / 200)} for sec in ("LC", "RC") for x in range(0, 201)]
    grade_cases = [{"score": s, "level": scoring.grade_for(s).level} for s in range(0, 1001, 5)]

    # ---- 통계·계획 ----
    st = db.get_settings()
    plans = {}
    base = {**st, "daily_new_words": "20"}
    variants = {
        "default": {},
        "goal": {"target_score": "900", "exam_date": "2026-12-20"},
        "manual_later": {"current_score": "615", "current_score_at": "2026-10-07T19:00:00", "target_score": "730", "exam_date": "2026-10-20", "daily_questions": "25"},
        "manual_earlier": {"current_score": "300", "current_score_at": "2026-09-01T10:00:00"},
        "bad_date": {"exam_date": "2026-13-45"},
    }
    for name, over in variants.items():
        settings = {**base, **over}
        plans[name] = {"settings": settings, "plan": plan_view(planner.build(bank, settings, today=TODAY))}

    notes_all = {}
    for status in ("open", "cleared"):
        notes_all[status] = plain(study.wrong_notes(status))

    events = []                       # 오답노트 규칙을 JS 로 되풀이해 볼 풀이 순서
    with db.connect() as con:
        for r in con.execute("SELECT qkey, chosen, created_at FROM attempts ORDER BY id"):
            events.append([r["qkey"], r["chosen"], r["created_at"]])

    counts = {str(p): {"all": bank.count_questions(p), "levels": {str(lv): bank.count_questions(p, lv) for lv in range(1, 6)},
                       "types": {t: bank.count_questions(p, None, t) for t in bank.types(p)}} for p in PART_INFO}

    expected = {
        "today": TODAY.isoformat(),
        "part_accuracy": {"all": plain(stats.part_accuracy()), "d7": plain(stats.part_accuracy(days=7)),
                          "last60": plain(stats.part_accuracy(last_n=60)), "last100": plain(stats.part_accuracy(last_n=100))},
        "level_accuracy": plain(stats.level_accuracy()),
        "type_accuracy": plain(stats.type_accuracy()),
        "score_history": plain(stats.score_history(30)),
        "latest_estimate": plain(stats.latest_estimate()),
        "daily_counts": plain(stats.daily_counts(30)),
        "today_counts": plain(stats.today_counts()),
        "streak": stats.streak(),
        "recent_sessions": [{k: s[k] for k in ("id", "finished_at", "mode", "variant", "total", "correct", "total_est")} for s in stats.recent_sessions(10)],
        "unfinished": [{"id": s["id"], "answered": s["answered"], "mode": s["mode"]} for s in stats.unfinished_sessions()],
        "time_by_part": plain(stats.time_by_part()),
        "notes": notes_all,
        "plans": plans,
        "fresh_mock": study.fresh_mock_capacity(bank),
        "level_progress": plain(srs.level_progress(bank, TODAY)),
        "counts": counts,
        "vocab_queue_default": {k: [w["id"] for w in v] if isinstance(v, list) else v
                                for k, v in srs.queue(bank, None, 20, today=TODAY, start_level=3).items()},
        "sessions": plain([study.get_session(s["id"]) for s in stats.recent_sessions(500)]),
        "scoring": {"estimate": plain(est_cases), "estimate_raw": plain(raw_cases), "section": section_cases, "grade_for": grade_cases},
    }
    out = {"backup": backup, "events": events, "expected": expected}
    path = ROOT / "tests" / "py_fixtures.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ts = backup["ts"]
    print(f"{path.name}: 세션 {len(ts['sessions'])} · 문항 기록 {len(ts['attempts'])} · 오답노트 {len(ts['notes'])} · "
          f"단어 카드 {len(backup['cards'])} · {path.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()

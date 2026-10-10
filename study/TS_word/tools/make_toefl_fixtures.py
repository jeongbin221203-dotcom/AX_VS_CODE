"""TS 앱(Python)의 토플 로직 결과를 JSON 으로 저장해 두고, node 테스트(tests/toefl.test.js)가 TS_word(JS) 결과와 대조하게 한다.

실행:  python tools/make_toefl_fixtures.py            (TS_word 폴더에서, TS 앱은 ../TS — 읽기만 함)
만드는 파일: tests/toefl_fixtures.json

하는 일
1. TS 앱의 실제 코드(core/toefl.py)로 임시 DB 에서 토플 학습을 흉내 낸다 (과제 11개, 난이도·시각·자동/자기 평가 점수가 섞인 기록).
2. 그 DB 를 import_ts_db.convert 로 TS_word 백업 모양으로 바꿔 저장하고 (이 변환 코드도 같이 검사됨),
3. 같은 DB 에서 Python 이 계산한 밴드(영역별·종합)·최근 기록·오답 모으기·날짜별 기록·마지막으로 푼 때를 '정답'으로 저장한다.
4. 밴드 규칙(band_from_levels·half_up·overall_band)은 무작위 입력으로, 오래 안 푼 순 뽑기(pick)는 모든 문제를 서로 다른 시각에 한 번씩 푼 과제로 확인한다.
5. 모의고사 끝내기(finish_mock) 결과도 같은 입력(payload)으로 저장한다 (모의고사 구성 자체는 난수라 JS 테스트가 규칙만 확인).
"""
from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "tools"))

from core import db, toefl as T                                      # noqa: E402
import import_ts_db as imp                                            # noqa: E402

SEED = 20261010


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 20, 8, 0, 0)

    def tick(self) -> str:
        self.t += timedelta(seconds=7)
        return self.t.isoformat(timespec="seconds")


clock = Clock()


def sim_results(task: str, it: dict, rng: random.Random, skill: float) -> list[dict]:
    """과제 종류에 맞는 풀이 결과 (자동 채점은 0/1 또는 비율, 자기 평가는 0~5점/5)"""
    info = T.TASKS[task]
    if task == "r_words":
        return [{"qidx": 0, "score": rng.randint(0, 10) / 10 if rng.random() < skill else rng.randint(0, 6) / 10, "response": "x"}]
    if task == "s_repeat":
        return [{"qidx": i, "score": round(rng.random() * (0.6 + 0.4 * skill), 3), "response": "heard"} for i in range(len(it["sentences"]))]
    if "questions" in it and task != "s_interview":
        return [{"qidx": i, "score": 1 if rng.random() < skill else 0, "response": str(rng.randint(0, 3))} for i in range(len(it["questions"]))]
    if info["auto"]:
        return [{"qidx": 0, "score": 1 if rng.random() < skill else 0, "response": "a"}]
    return [{"qidx": 0, "score": rng.choice([0, 1, 2, 3, 4, 5]) / 5, "response": "my answer"}]


def simulate(bank: T.ToeflBank, rng: random.Random) -> None:
    for day in range(18):
        clock.t = datetime(2026, 9, 20, 8 + day % 5, 0, 0) + timedelta(days=day)
        for _ in range(rng.randint(2, 6)):
            task = rng.choice(list(T.TASKS))
            pool = bank.items[task]
            for it in rng.sample(pool, min(len(pool), rng.randint(1, 3))):
                skill = min(0.95, max(0.1, 0.25 + 0.15 * it["level"] * 0 + 0.03 * day + rng.uniform(-0.1, 0.35) - 0.05 * (it["level"] - 2)))
                T.record(task, it["id"], it["level"], sim_results(task, it, rng, skill))
                clock.t += timedelta(minutes=rng.randint(1, 9))
    # 같은 문제를 다시 풀어 마지막 회차 기준 오답 모으기가 달라지게 (일부는 맞히고 일부는 또 틀림)
    for task in ("r_daily", "l_conversation", "w_sentence", "w_email"):
        for it in rng.sample(bank.items[task], 6):
            for k in range(2):
                clock.t += timedelta(minutes=rng.randint(1, 30))
                T.record(task, it["id"], it["level"], sim_results(task, it, rng, 0.5 + 0.4 * k))
    # 한 과제의 모든 문제를 서로 다른 시각에 한 번씩 (오래 안 푼 순 뽑기 확인용 — 순서는 무작위)
    order = bank.items["s_interview"][:]
    rng.shuffle(order)
    for it in order:
        clock.t += timedelta(minutes=3)
        T.record("s_interview", it["id"], it["level"], sim_results("s_interview", it, rng, 0.5))


def main() -> None:
    db.now = clock.tick
    rng = random.Random(SEED)
    bank = T.ToeflBank(TS / "content" / "toefl")
    tmp = Path(tempfile.mkdtemp(prefix="tsfxf"))
    db.configure(tmp / "fx.db")
    T.ensure_schema()
    T.ensure_mock_schema()
    simulate(bank, rng)

    # ---- 밴드 규칙 (무작위 입력) ----
    r2 = random.Random(7)
    band_cases = []
    for _ in range(400):
        acc = {}
        for lv in r2.sample(range(1, 6), r2.randint(0, 5)):
            n = r2.choice([0, 1, 2, 3, 5, 12, 40])
            acc[lv] = (n, r2.choice([0.0, 0.44, 0.45, 0.449, 0.65, 0.64, 1.0, r2.random()]))
        mn = r2.choice([1, 4, 4, 4])
        band_cases.append({"acc": {str(k): list(v) for k, v in acc.items()}, "min_n": mn, "out": T.band_from_levels(acc, mn)})
    half_cases = [{"x": x / 8, "out": T.half_up(x / 8)} for x in range(0, 60)]
    overall_cases = []
    for _ in range(200):
        bands = {s: r2.choice([None, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6]) for s in "RLSW"}
        overall_cases.append({"bands": bands, "out": T.overall_band(bands)})

    # ---- 같은 DB 에서 계산한 값 ----
    last_seen = {t: T.last_seen(t) for t in T.TASKS}
    wrong = {t: T.wrong_items(t) for t in T.TASKS}
    labels = {}
    for t in T.TASKS:
        for it in bank.items[t][:3]:
            labels[f"{t}|{it['id']}"] = T.item_label(t, it)
    pick_oldest = {}
    for level, n in ((None, 5), (3, 3)):
        got = bank.pick("s_interview", level, n, random.Random(1))
        pick_oldest[f"{level}|{n}"] = [it["id"] for it in got]
    bands = T.section_bands()
    expected = {
        "bands": bands, "overall": T.overall_band(bands), "task_stats": T.task_stats(), "recent": T.recent(12),
        "recent_all": T.recent(100000), "history": T.history(36500), "last_seen": last_seen, "wrong": wrong, "labels": labels,
        "pick_oldest": pick_oldest, "last_date": T.recent(1)[0]["at"][:10],
        "counts": {t: {"all": bank.count(t), "levels": {str(lv): bank.count(t, lv) for lv in range(1, 6)}} for t in T.TASKS},
        "tasks": {t: {k: v[k] for k in ("section", "name", "en", "kind", "auto", "desc")} for t, v in T.TASKS.items()},
        "sections": T.SECTIONS, "cefr": T.CEFR, "band_old": T.BAND_OLD, "level_band": T.LEVEL_BAND, "rubric": T.RUBRIC,
        "consts": {"adapt_cut": T.ADAPT_CUT, "m1": T.M1_LEVELS, "hard": T.HARD_LEVELS, "easy": T.EASY_LEVELS, "order": T.MOCK_ORDER,
                   "read_module": T.READ_MODULE, "listen_module": T.LISTEN_MODULE, "wrong_auto": T.WRONG_CUT_AUTO, "wrong_self": T.WRONG_CUT_SELF},
        "band_cases": band_cases, "half_cases": half_cases, "overall_cases": overall_cases,
    }

    # ---- 모의고사 끝내기: 같은 입력 → 같은 결과 ----
    plan = T.build_mock(bank, 4.5, random.Random(3))
    mid = T.create_mock(bank, 4.5)
    payload_items = []
    clock.t = datetime(2026, 10, 9, 20, 0, 0)

    def add(entry, skill):
        it, task = entry["item"], entry["task"]
        payload_items.append({"task": task, "item_id": it["id"], "results": sim_results(task, it, rng, skill)})
    for key in ("R", "L"):
        sec = plan["sections"][key]
        for e in sec["modules"][0]:
            add(e, 0.8)
        for e in sec["modules"][1]["hard"]:
            add(e, 0.6)
    for key in ("S", "W"):
        for e in plan["sections"][key]["items"]:
            add(e, 0.6)
    payload = {"items": payload_items, "routes": {"R": "hard", "L": "hard"}, "duration_sec": 4321}
    payload["items"].append({"task": "r_daily", "item_id": "없는-문제", "results": [{"qidx": 0, "score": 1}]})      # 없는 문제는 건너뜀
    m = T.finish_mock(bank, mid, payload)
    expected["mock"] = {"payload": payload, "result": m["result"], "r": m["r"], "l": m["l"], "s": m["s"], "w": m["w"], "total": m["total"],
                        "bands_after": T.section_bands(), "overall_after": T.overall_band(T.section_bands()), "recent_after": T.recent(5), "task_stats_after": T.task_stats()}
    again = T.finish_mock(bank, mid, {"items": []})                    # 이미 끝난 시험이면 그대로
    expected["mock"]["again_total"] = again["total"]

    backup = imp.convert_exam(tmp / "fx.db")
    ext = backup["ext"]
    # 모의고사 끝나기 전 상태(기록 + 끝난 시험)가 아니라 최종 DB 기준이므로 expected 의 *_after 와 비교한다.
    # JS 테스트는 mock 이전 상태가 필요해 attempts 를 mock 이 쓴 개수만큼 잘라 쓴다.
    n_mock_attempts = sum(len(r["results"]) for r in payload["items"] if bank.by_id.get((r["task"], r["item_id"])))
    out = {"backup_ts": backup, "n_mock_attempts": n_mock_attempts, "expected": expected, "plan_shape": {
        k: ({"modules": [len(v["modules"][0]), {kk: len(vv) for kk, vv in v["modules"][1].items()}]} if "modules" in v else {"items": len(v["items"])})
        for k, v in plan["sections"].items()}}
    path = ROOT / "tests" / "toefl_fixtures.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: 풀이 기록 {len(ext['toefl_attempts'])}건 · 모의고사 {len(ext['toefl_mocks'])}번 · {path.stat().st_size // 1024}KB")


def display_fixtures() -> None:
    """화면 표시 규칙: TS 앱 템플릿의 Jinja `(a*100)|round|int`, `(a*5)|round(1)`, 밴드 float 출력을 실제 Jinja 로 렌더해 저장한다.
    (파이썬 round 는 .5 를 짝수로 — 62.5% → 62, 2.25점 → 2.2. 자바스크립트 Math.round 와 다르다.) → tests/toefl_display_fixtures.json"""
    import jinja2
    env = jinja2.Environment()
    t_pct = env.from_string("{{ ((a * 100) | round | int) }}")
    t_self = env.from_string("{{ ((a * 5) | round(1)) }}")
    t_band = env.from_string("{{ x }}")
    rng = random.Random(SEED)
    avgs = [k / n for n in range(1, 41) for k in range(0, n + 1)]                        # 맞힌 개수 / 문항 수
    avgs += [sum(rng.choice([0, 1, 2, 3, 4, 5]) / 5 for _ in range(n)) / n for n in range(1, 25) for _ in range(6)]   # 자기 평가 평균
    avgs = sorted(set(avgs))
    cases = [{"a": a, "pct": t_pct.render(a=a), "self": t_self.render(a=a)} for a in avgs]
    bands = [{"x": x, "out": t_band.render(x=x)} for x in (1.0, 2.0, 2.5, 4.0, 4.5, 6.0, T.half_up(5.25), T.half_up(3.8), 6.0 - 4.5)]
    path = ROOT / "tests" / "toefl_display_fixtures.json"
    path.write_text(json.dumps({"cases": cases, "bands": bands}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: display rules {len(cases)} / bands {len(bands)}")


if __name__ == "__main__":
    if "--display" in sys.argv:
        display_fixtures()
    else:
        main()

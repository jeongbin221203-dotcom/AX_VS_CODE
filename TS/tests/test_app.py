"""핵심 로직과 화면 흐름 테스트. 임시 DB 를 쓰고 실제 문제 은행(content/toeic)을 읽는다."""
from __future__ import annotations

import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from core import db, planner, scoring, srs, study  # noqa: E402
from core.content import PARTS, Bank  # noqa: E402
from tools import validate_content  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "DB_PATH": tmp_path / "ts.db"})


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def bank(app):
    return app.extensions["bank"]


def _csrf(client) -> dict:
    html = client.get("/").data.decode()
    return {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)}


def _payload(client, sid) -> dict:
    html = client.get(f"/quiz/{sid}").data.decode()
    return json.loads(re.search(r'id="payload">(.*?)</script>', html, re.S).group(1))


# ---- 문제 은행 ---------------------------------------------------------------------

def test_content_files_are_valid():
    seen_ids, words, levels_by_kind = {}, {}, {}
    for path in validate_content.all_files():
        kind = validate_content.kind_of(path.name)
        errors, levels = validate_content.validate_file(path, seen_ids.setdefault(kind, set()),
                                                        words if kind == "vocab.json" else None)
        assert errors == [], errors[:5]
        levels_by_kind.setdefault(kind, set()).update(levels)
    assert set(levels_by_kind) == set(validate_content.CHECKS)
    for kind, levels in levels_by_kind.items():          # 파일을 합쳐서 모든 등급이 있어야 함
        assert levels == {1, 2, 3, 4, 5}, f"{kind}: 모든 등급에 문제가 있어야 함"


def test_bank_loads_every_part(bank):
    for p in PARTS:
        assert bank.items[p], f"Part {p} 비어 있음"
    assert len(bank.vocab) >= 500


def test_public_item_hides_answers(bank):
    for p in PARTS:
        pub = bank.public_item(bank.ref(bank.items[p][0]))
        assert "answer" not in pub and "explanation" not in pub and "translation" not in pub
        for q in pub.get("questions", []):
            assert "answer" not in q and "explanation" not in q


# ---- 점수·등급 ---------------------------------------------------------------------

@pytest.mark.parametrize("score,name", [(10, "Orange"), (215, "Orange"), (220, "Brown"), (470, "Green"),
                                        (725, "Green"), (730, "Blue"), (860, "Gold"), (990, "Gold")])
def test_grade_boundaries(score, name):
    assert scoring.grade_for(score).name == name


def test_section_score_monotonic_and_bounded():
    prev = 0
    for i in range(101):
        s = scoring.section_score("LC", i / 100)
        assert 5 <= s <= 495 and s >= prev and s % 5 == 0
        prev = s
    assert scoring.section_score("RC", 1.0) == 495
    assert scoring.section_score("RC", 0.0) == 5


def test_harder_correct_answers_score_higher():
    easy_right = [(1, True)] * 5 + [(5, False)] * 5
    hard_right = [(1, False)] * 5 + [(5, True)] * 5
    assert scoring.weighted_ratio(hard_right) > scoring.weighted_ratio(easy_right)


# ---- 간격 반복 ---------------------------------------------------------------------

def test_srs_intervals_grow_and_reset():
    ef, iv, reps = 2.5, 0, 0
    seen = []
    for _ in range(4):
        ef, iv, reps = srs.schedule(ef, iv, reps, 4)
        seen.append(iv)
    assert seen[0] == 1 and seen[1] == 4 and seen[2] > seen[1] and seen[3] > seen[2]
    ef2, iv2, reps2 = srs.schedule(ef, iv, reps, 0)
    assert iv2 == 1 and reps2 == 0 and ef2 < ef
    with pytest.raises(ValueError):
        srs.schedule(2.5, 0, 0, 2)


def test_vocab_queue_respects_daily_limit(app, bank):
    today = date(2026, 9, 27)
    q = srs.queue(bank, 1, 5, today=today)
    assert len(q["new"]) == 5 and q["due"] == []
    for w in q["new"]:
        srs.review(w["id"], 4, today=today)
    # 다른 날짜 기준이면 새 단어 한도가 다시 열림 — 여기서는 오늘 기록이 없으므로 한도 = 5
    later = srs.queue(bank, 1, 5, today=today + timedelta(days=1))
    assert {w["id"] for w in later["due"]} == {w["id"] for w in q["new"]}


# ---- 세션·채점·오답노트 --------------------------------------------------------------

def test_practice_grading_records_and_wrong_notes(app, bank):
    sid = study.start_practice(bank, 5, 2, None, 3, rng=random.Random(1))
    s = study.get_session(sid)
    ref = s["items"][0]
    q = bank.questions(bank.item(ref))[0]
    wrong = (q.answer + 1) % 4
    res = study.grade_item(bank, sid, ref, [{"qidx": 0, "chosen": wrong}])
    assert res["results"][0]["correct"] is False
    assert res["questions"][0]["answer"] == q.answer
    notes = study.wrong_notes("open")
    assert [n["qkey"] for n in notes] == [q.qkey]
    # 같은 문제를 다시 보내도 기록이 늘지 않는다
    study.grade_item(bank, sid, ref, [{"qidx": 0, "chosen": q.answer}])
    assert len(study.session_attempts(sid)) == 1


def test_wrong_note_clears_after_two_correct_reviews(app, bank):
    sid = study.start_practice(bank, 5, 1, None, 1, rng=random.Random(2))
    ref = study.get_session(sid)["items"][0]
    q = bank.questions(bank.item(ref))[0]
    study.grade_item(bank, sid, ref, [{"qidx": 0, "chosen": (q.answer + 1) % 4}])
    for i in range(2):
        rid = study.start_review(bank, None, 10)
        assert ref in study.get_session(rid)["items"]
        study.grade_item(bank, rid, ref, [{"qidx": 0, "chosen": q.answer}])
        study.finish_session(bank, rid)
    assert study.wrong_notes("open") == []
    assert [n["qkey"] for n in study.wrong_notes("cleared")] == [q.qkey]


def test_grade_rejects_foreign_item(app, bank):
    sid = study.start_practice(bank, 5, 1, None, 1, rng=random.Random(3))
    other = bank.ref(bank.items[7][0])
    with pytest.raises(study.StudyError):
        study.grade_item(bank, sid, other, [])


@pytest.mark.parametrize("form", ["mini", "half", "full"])
def test_mock_composition_matches_form(app, bank, form):
    sid = study.start_mock(bank, form, rng=random.Random(4))
    refs = study.get_session(sid)["items"]
    assert len(refs) == len(set(refs)), "같은 문제가 두 번 나오면 안 됨"
    per_part = {}
    for r in refs:
        it = bank.item(r)
        per_part[it["part"]] = per_part.get(it["part"], 0) + len(bank.questions(it))
    f = scoring.MOCK_FORMS[form]
    for p in (1, 2, 5):
        assert per_part[p] == min(f[f"p{p}"], bank.count_questions(p))
    parts = [bank.item(r)["part"] for r in refs]
    assert parts == sorted(parts), "LC → RC, 파트 순서대로"


def test_mock_submit_estimates_score(app, bank):
    sid = study.start_mock(bank, "mini", rng=random.Random(5))
    s = study.get_session(sid)
    perfect = {}
    for r in s["items"]:
        perfect[r] = [{"qidx": q.qidx, "chosen": q.answer} for q in bank.questions(bank.item(r))]
    done = study.submit_session(bank, sid, {"items": perfect, "duration_sec": 600})
    assert done["correct"] == done["total"] > 0
    assert done["total_est"] == 990 and done["duration_sec"] == 600
    # 두 번 제출해도 그대로
    assert study.submit_session(bank, sid, {"items": {}})["total"] == done["total"]


def test_unanswered_counts_as_wrong(app, bank):
    sid = study.start_diagnostic(bank, rng=random.Random(6))
    done = study.submit_session(bank, sid, {"items": {}})
    assert done["total"] > 0 and done["correct"] == 0
    assert done["total_est"] is not None and done["total_est"] <= 20


# ---- 학습 계획 ---------------------------------------------------------------------

def test_plan_without_history_asks_for_diagnostic(app, bank):
    plan = planner.build(bank, db.get_settings())
    assert plan["score"] is None
    assert plan["tasks"][0]["kind"] == "diagnostic"


def test_plan_uses_latest_estimate_and_manual_score(app, bank):
    db.save_settings({"current_score": "600", "current_score_at": "2000-01-01T00:00:00", "target_score": "800",
                      "exam_date": (date.today() + timedelta(days=70)).isoformat()})
    sid = study.start_diagnostic(bank, rng=random.Random(7))
    study.submit_session(bank, sid, {"items": {}})
    plan = planner.build(bank, db.get_settings())
    assert plan["score_source"].startswith("추정")        # 직접 입력보다 나중의 추정치
    db.save_settings({"current_score": "650", "current_score_at": "2999-01-01T00:00:00"})
    plan = planner.build(bank, db.get_settings())
    assert plan["score"] == 650 and plan["grade"].name == "Green"
    assert plan["weekly_gain"] == 15                       # 150점 / 10주
    assert any(t["kind"] == "part" for t in plan["tasks"])


# ---- 화면 ------------------------------------------------------------------------

PAGES = ["/", "/guide", "/guide/1", "/guide/5", "/practice", "/diagnostic", "/mock", "/review",
         "/vocab", "/vocab/study", "/vocab/list?level=3&filter=new", "/vocab/quiz?level=2",
         "/dictation?part=1", "/dictation?part=2", "/dictation?part=3", "/dictation?part=4", "/stats",
         "/history", "/settings", "/vocab/listen", "/vocab/listen?level=3&set=starred", "/vocab/listen?set=new"]


@pytest.mark.parametrize("url", PAGES)
def test_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_end_to_end_practice_via_http(client):
    h = _csrf(client)
    r = client.get("/practice/start?part=3&level=2&n=3")
    sid = int(r.headers["Location"].rsplit("/", 1)[1])
    item = _payload(client, sid)["items"][0]
    r = client.post(f"/api/quiz/{sid}/grade", headers=h,
                    json={"ref": item["ref"], "answers": [{"qidx": i, "chosen": 0} for i in range(3)]})
    assert r.status_code == 200 and len(r.json["results"]) == 3
    r = client.post(f"/api/quiz/{sid}/submit", headers=h, json={})
    assert client.get(r.json["redirect"]).status_code == 200
    assert client.get(f"/quiz/{sid}").status_code == 302      # 끝난 세션은 결과로


def test_post_without_csrf_is_rejected(client):
    assert client.post("/api/vocab/review", json={"word_id": "v-0001", "grade": 4}).status_code == 400
    assert client.post("/settings", data={"target_score": "900"}).status_code == 400


def test_settings_validation(client):
    h = _csrf(client)
    tok = h["X-CSRF-Token"]
    r = client.post("/settings", data={"_csrf": tok, "target_score": "5000", "daily_new_words": "20",
                                       "daily_questions": "40", "tts_rate": "1.0", "tts_accent": "us"})
    assert "10~990" in r.data.decode()
    r = client.post("/settings", data={"_csrf": tok, "target_score": "900", "daily_new_words": "20",
                                       "daily_questions": "40", "tts_rate": "9", "tts_accent": "xx"})
    assert r.status_code == 302
    s = db.get_settings()
    assert s["target_score"] == "900" and s["tts_rate"] == "1.5" and s["tts_accent"] == "mix"


def test_listen_audio_one_hour_files(client, monkeypatch, tmp_path):
    from core import audio
    built = {}

    def fake_build(words, out_dir, **kw):
        built["n"], built["kw"] = len(words), kw
        f = tmp_path / "x.mp3"
        f.write_bytes(bytes([0xFF, 0xF3, 0x64, 0xC4]) + b"0" * 2000)
        return f
    monkeypatch.setattr(audio, "build", fake_build)
    monkeypatch.setattr(audio, "available", lambda: True)
    monkeypatch.setattr(audio, "duration_sec", lambda p: 3600)
    h = _csrf(client)
    # 한 등급(약 360단어)은 1시간이 안 되므로 다시 섞어 넣어 1시간을 채운다
    per_hour = int(3600 / audio.seconds_per_word(3, False))
    r = client.post("/api/vocab/audio/prepare", headers=h, json={"level": 2, "set": "all", "minutes": 60, "repeats": 3, "chunk": 1})
    assert r.status_code == 200
    job = r.json
    for _ in range(50):
        if job["state"] != "running":
            break
        import time
        time.sleep(0.05)
        job = client.get(f"/api/vocab/audio/status/{job['key']}").json
    assert job["state"] == "done", job
    assert built["n"] == per_hour and built["kw"]["repeats"] == 3
    r = client.get(job["download"])
    assert r.status_code == 200 and r.mimetype == "audio/mpeg" and "attachment" in r.headers["Content-Disposition"]
    assert client.get(job["url"]).headers.get("Content-Disposition", "").startswith("inline")
    page = client.get("/vocab/listen?level=2&minutes=60").data.decode()
    assert "1번 파일" in page and "2번 파일" not in page


def test_audio_plan_splits_and_fills():
    from core import audio
    words = [{"id": f"w{i}", "word": "a", "meaning": "b"} for i in range(1000)]
    per = int(3600 / audio.seconds_per_word(3, False))
    files = audio.plan(words, 60, repeats=3)
    assert all(len(f) == per for f in files) and len(files) == -(-1000 // per)
    assert [w["id"] for f in files for w in f][:1000] == [w["id"] for w in words]      # 순서대로, 빠짐없이
    assert len(audio.plan(words[:10], 10, repeats=3, fill=False)[0]) == 10


def test_audio_segments_rotate_accents():
    from core import audio
    segs = audio._segments([{"word": "submit", "meaning": "제출하다", "example": "Submit it."}], 3, True)
    voices = [v for _, v in segs]
    assert [v[:5] for v in voices[:3]] == ["en-US", "en-GB", "en-AU"] and voices[3].startswith("ko-KR") and voices[-1] == "gap"


def test_audio_text_cleaning():
    from core.audio import _clean
    assert _clean("comply with ~") == "comply with"
    assert _clean("청구서(송장), 계산서") == "청구서, 계산서"


def test_every_level_has_core_and_stretch_words(bank):
    for lv in range(1, 6):
        tiers = {w["tier"] for w in bank.vocab if w["level"] == lv}
        assert tiers == {"core", "stretch"}, f"level {lv}: {tiers}"
    stretch_first = srs.queue(bank, 2, 5, tier="stretch")["new"]
    assert stretch_first and all(w["tier"] == "stretch" for w in stretch_first)
    assert all(w["tier"] == "core" for w in srs.queue(bank, 2, 5)["new"])    # 새 단어는 필수부터


def test_vocab_quiz_records_and_collects_weak_words(client, bank):
    h = _csrf(client)
    wid = next(w["id"] for w in bank.vocab if w["level"] == 3 and w["tier"] == "stretch")
    # 한 번 틀림 → 복습 카드(내일), 최근 틀린 단어
    r = client.post("/api/vocab/quiz/answer", headers=h, json={"word_id": wid, "correct": False})
    assert r.json["scheduled"] is True
    assert srs.cards()[wid]["due"] == (date.today() + timedelta(days=1)).isoformat()
    assert wid in srs.recently_missed() and srs.fail_counts()[wid] == 1
    # 두 번 틀리면 자주 잊는 단어
    client.post("/api/vocab/quiz/answer", headers=h, json={"word_id": wid, "correct": False})
    page = client.get("/vocab/quiz?level=3&tier=stretch&set=weak").data.decode()
    assert bank.vocab_by_id[wid]["word"] in page
    # 맞히면 최근 틀린 단어에서는 빠지지만 자주 잊는 단어에는 남는다
    r = client.post("/api/vocab/quiz/answer", headers=h, json={"word_id": wid, "correct": True})
    assert r.json["scheduled"] is False
    assert wid not in srs.recently_missed() and srs.fail_counts()[wid] == 2
    assert client.post("/api/vocab/quiz/answer", headers=h, json={"word_id": "nope"}).status_code == 400


@pytest.mark.parametrize("q", ["set=weak", "set=missed", "set=starred", "set=learning", "n=50&level=2&tier=core", "n=30"])
def test_vocab_quiz_pages(client, q):
    r = client.get(f"/vocab/quiz?{q}")
    assert r.status_code == 200


def test_vocab_quiz_size_and_distractors(client):
    html = client.get("/vocab/quiz?n=30&level=4").data.decode()
    qs = json.loads(re.search(r'id="vq-data">(.*?)</script>', html, re.S).group(1))
    assert len(qs) == 30 and len({q["id"] for q in qs}) == 30
    for q in qs:
        assert len(q["options"]) == 4 and len(set(q["options"])) == 4 and q["options"][q["answer"]] == q["meaning"]


@pytest.mark.parametrize("key,home", [("toeic-speaking", "/speaking/toeic"), ("opic", "/speaking/opic")])
def test_exam_category_pages(client, key, home):
    r = client.get(f"/exam/{key}")
    assert r.status_code == 302 and r.headers["Location"].endswith(home)


def test_exam_tabs(client):
    assert client.get("/exam/toeic").status_code == 302
    assert client.get("/exam/toefl").headers["Location"].endswith("/toefl/")
    assert client.get("/exam/nope").status_code == 404
    html = client.get("/").data.decode()
    assert all(n in html for n in ("토익스피킹", "토플", "오픽"))


# ---- 토플 ----------------------------------------------------------------------------

def _mcq(n):
    return [{"q": f"Q{i}?", "choices": ["a", "b", "c", "d"], "answer": i % 4, "type": "세부 사항", "explanation": "해설"}
            for i in range(n)]


TOEFL_SAMPLE = {
    "r_words": [{"id": "rw-001", "level": 3, "topic": "생물", "translation": "번역",
                 "text": "Bees are important for many plants. " + " ".join(f"[[wor|ds]] filler{i} text and more" for i in range(10)) + " end of text here now."}],
    "r_daily": [{"id": "rd-001", "level": 2, "doc_type": "공지", "title": "T", "text": "Notice text.", "questions": _mcq(2), "translation": "번역"}],
    "r_academic": [{"id": "ra-001", "level": 4, "title": "T", "text": " ".join(["word"] * 200), "questions": _mcq(5), "translation": "번역"}],
    "l_response": [{"id": "lr-001", "level": 1, "voice": "female", "prompt": "Where is it?", "choices": ["a", "b", "c", "d"],
                    "answer": 0, "explanation": "해설", "translation": "번역"}],
    "l_conversation": [{"id": "lc-001", "level": 2, "topic": "t", "speakers": {"M": "male", "W": "female"},
                        "script": [{"s": "W" if i % 2 else "M", "t": "Hello there."} for i in range(6)], "questions": _mcq(2), "translation": "번역"}],
    "l_talk": [{"id": "lt-001", "level": 3, "kind": "announcement", "topic": "t", "voice": "male", "script": "Attention.", "questions": _mcq(2), "translation": "번역"}],
    "w_sentence": [{"id": "ws-001", "level": 2, "context": "Done?", "answer": "We still need to add the chart.",
                    "chunks": ["We", "still need", "to add", "the", "chart"], "explanation": "해설", "translation": "번역"}],
    "w_email": [{"id": "we-001", "level": 3, "situation": "s", "to": "t", "tasks": ["a", "b", "c"], "sample": "Dear", "sample_ko": "번역", "tips": ["팁"]}],
    "w_discussion": [{"id": "wd-001", "level": 4, "course": "c", "professor": "p", "students": [{"name": "A", "post": "x"}, {"name": "B", "post": "y"}],
                      "sample": "s", "sample_ko": "번역", "tips": ["팁"]}],
    "s_repeat": [{"id": "sr-001", "level": 2, "topic": "t", "voice": "female",
                  "sentences": [" ".join(["word"] * (4 + i * 2)) + "." for i in range(7)], "translations": ["번역"] * 7}],
    "vocab": [{"id": f"tv-{i:04d}", "level": 1 + i % 5, "tier": "core" if i % 3 else "stretch", "word": f"word{i}",
               "pos": "n.", "meaning": f"뜻{i}", "example": f"The word{i} appears here.", "example_ko": "예문", "tip": ""}
              for i in range(40)],
    "s_interview": [{"id": "si-001", "level": 3, "topic": "t", "intro": "Hi", "questions": ["q1", "q2", "q3", "q4"],
                     "samples": ["a"] * 4, "samples_ko": ["번역"] * 4, "tips": ["팁"]}],
}


@pytest.fixture()
def tclient(tmp_path):
    d = tmp_path / "toefl"
    d.mkdir()
    for k, v in TOEFL_SAMPLE.items():
        (d / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "ts.db", "TOEFL_CONTENT_DIR": d})
    return app.test_client()


def test_toefl_sample_passes_validator(tmp_path):
    from tools import validate_toefl
    for k, v in TOEFL_SAMPLE.items():
        f = tmp_path / f"{k}.json"
        f.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
        errors, _ = validate_toefl.validate_file(f, set())
        assert errors == [], errors


def test_toefl_real_content_is_valid():
    from tools import validate_toefl
    seen = {}
    for p in validate_toefl.all_files():
        errors, _ = validate_toefl.validate_file(p, seen.setdefault(validate_toefl.kind_of(p.name), set()))
        assert errors == [], errors[:5]


def test_toefl_pages_and_band(tclient):
    from core import toefl as T
    assert tclient.get("/toefl/").status_code == 200
    for task in T.TASKS:
        r = tclient.get(f"/toefl/practice/{task}")
        assert r.status_code == 200 and b'id="payload"' in r.data, task
    assert tclient.get("/toefl/practice/nope").status_code == 404
    h = _csrf(tclient)
    for task, items in ((k, v) for k, v in TOEFL_SAMPLE.items() if k != "vocab"):
        r = tclient.post("/toefl/api/attempt", headers=h, json={"task": task, "item_id": items[0]["id"],
                                                               "results": [{"qidx": 0, "score": 1}, {"qidx": 1, "score": 1}]})
        assert r.status_code == 200, (task, r.json)
    bands = T.section_bands()
    assert all(bands[s] is not None for s in "RLSW")
    assert T.overall_band(bands) is not None
    assert tclient.post("/toefl/api/attempt", headers=h, json={"task": "r_daily", "item_id": "nope", "results": [{}]}).status_code == 400
    html = tclient.get("/toefl/").data.decode()
    assert "종합 밴드" in html


def test_toefl_band_rules():
    from core.toefl import band_from_levels, overall_band
    assert band_from_levels({}) is None
    assert band_from_levels({3: (10, 0.7)}) == 4          # 밴드 4 난이도에서 70% → 4
    assert band_from_levels({3: (10, 0.5)}) == 3.5
    assert band_from_levels({2: (10, 0.9), 4: (10, 0.66)}) == 5
    assert overall_band({"R": 4, "L": 4.5, "S": 3.5, "W": 4}) == 4.0
    assert overall_band({"R": 4, "L": None, "S": 3.5, "W": 4}) is None


def test_toefl_mock_flow(tclient):
    from core import toefl as T
    h = _csrf(tclient)
    assert tclient.get("/toefl/mock").status_code == 200
    r = tclient.post("/toefl/mock", data={"_csrf": h["X-CSRF-Token"]})
    mid = int(r.headers["Location"].rsplit("/", 1)[1])
    page = tclient.get(f"/toefl/mock/{mid}").data.decode()
    plan = json.loads(re.search(r'id="payload">(.*?)</script>', page, re.S).group(1))["plan"]
    assert plan["order"] == ["R", "L", "S", "W"]
    assert set(plan["sections"]["R"]["modules"][1]) == {"hard", "easy"}
    # 모든 문제를 만점으로 제출
    items = []
    def full(task, it):
        n = len(it["questions"]) if "questions" in it else 1
        items.append({"task": task, "item_id": it["id"], "results": [{"qidx": q, "score": 1} for q in range(n)]})
    for k in ("R", "L"):
        for e in plan["sections"][k]["modules"][0] + plan["sections"][k]["modules"][1]["hard"]:
            full(e["task"], e["item"])
    for k in ("S", "W"):
        for e in plan["sections"][k]["items"]:
            full(e["task"], e["item"])
    r = tclient.post(f"/toefl/api/mock/{mid}/finish", headers=h, json={"items": items, "routes": {"R": "hard", "L": "hard"}})
    assert r.status_code == 200
    res = tclient.get(r.json["redirect"])
    assert res.status_code == 200 and "종합 밴드" in res.data.decode()
    m = T.get_mock(mid)
    assert all(m["result"]["bands"][s] is not None for s in "RLSW") and m["total"] is not None
    assert tclient.get(f"/toefl/mock/{mid}").status_code == 302          # 끝난 시험은 결과로
    assert tclient.get("/toefl/mock").status_code == 200


def test_toefl_vocab_pages(tclient):
    for u in ["/toefl/vocab", "/toefl/vocab/study?level=2", "/toefl/vocab/list?level=3&tier=stretch",
              "/toefl/vocab/quiz?level=1", "/toefl/vocab/listen?level=2"]:
        r = tclient.get(u)
        assert r.status_code == 200, u
    html = tclient.get("/toefl/vocab").data.decode()
    assert "토플 학술 어휘" in html and "밴드 2" in html and 'name="vocab-base" content="/toefl"' in html
    h = _csrf(tclient)
    assert tclient.post("/toefl/api/vocab/review", headers=h, json={"word_id": "tv-0001", "grade": 4}).status_code == 200
    assert tclient.post("/toefl/api/vocab/review", headers=h, json={"word_id": "v-0001", "grade": 4}).status_code == 400
    assert tclient.post("/api/vocab/review", headers=h, json={"word_id": "tv-0001", "grade": 4}).status_code == 400
    # 토익 단어 화면은 그대로
    assert "등급별 단어" in tclient.get("/vocab").data.decode()


def test_all_python_files_compile_and_gunicorn_config():
    """배포 서버(gunicorn)가 읽는 설정 파일까지 모든 .py 파일이 문법 오류 없이 읽혀야 한다."""
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
    conf = {}
    exec((ROOT / "gunicorn.conf.py").read_text(encoding="utf-8"), conf)
    assert conf["workers"] == 1 and conf["worker_class"] == "gthread" and conf["threads"] >= 2


def test_main_hub_and_tabs(client):
    """메인 = 네 시험으로 들어가는 화면, 위쪽 탭으로 바로 이동, TS 로고 = 메인."""
    html = client.get("/").data.decode()
    assert "어떤 시험을 공부할까요?" in html and 'class="exam-tabs"' in html
    for name in ("토익", "토플", "토익스피킹", "오픽"):
        assert f"<h2>{name}</h2>" in html
    for href in ("/toeic", "/toefl/", "/speaking/toeic", "/speaking/opic", "/toefl/mock", "/speaking/opic/survey"):
        assert f'href="{href}' in html
    assert "오늘의 토익" in client.get("/toeic").data.decode()
    tabs = client.get("/speaking/opic").data.decode()
    assert 'class="on" aria-current=page>오픽</a>' in tabs and 'href="/" aria-label="메인 화면"' in tabs
    # 기록이 생기면 카드에 추정치가 나온다
    from core import speaking as S
    assert S.tsp_level(S.tsp_score(35))[1] == "Advanced High"


def test_every_exam_home_menu_is_named_home(client):
    """시험마다 첫 메뉴 이름을 '홈'으로 통일 (위쪽 경로 표시 '› 홈')."""
    for url in ("/toeic", "/toefl/", "/speaking/toeic", "/speaking/opic"):
        html = client.get(url).data.decode()
        assert '<span class="muted">›</span> 홈</div>' in html, url


def test_settings_for_every_exam_and_hub_dday(client):
    """설정: 네 시험 목표·시험일 → 메인 카드에 D-day. 잘못된 값은 저장하지 않는다."""
    from datetime import date, timedelta
    html = client.get("/settings").data.decode()
    for name in ("toefl_target", "tsp_target", "opic_target", "opic_level", "toefl_exam_date", "tsp_exam_date", "opic_exam_date"):
        assert f'name="{name}"' in html
    tok = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    soon = (date.today() + timedelta(days=12)).isoformat()
    form = {"_csrf": tok, "target_score": "800", "daily_new_words": "20", "daily_questions": "40", "tts_rate": "1.0",
            "tts_accent": "mix", "exam_date": "", "toefl_target": "5.5", "toefl_exam_date": soon, "tsp_target": "160",
            "tsp_exam_date": "", "opic_target": "AL", "opic_level": "6", "opic_exam_date": ""}
    assert client.post("/settings", data=form).status_code == 302
    st = db.get_settings()
    assert (st["toefl_target"], st["tsp_target"], st["opic_target"], st["opic_level"], st["toefl_exam_date"]) == ("5.5", "160", "AL", "6", soon)
    home = client.get("/").data.decode()
    assert "D-12" in home and "밴드 5.5" in home and "160점" in home and home.count("들어가기 →") == 4
    bad = client.post("/settings", data={**form, "tsp_target": "999", "opic_level": "9", "toefl_target": "7"}).data.decode()
    assert "토익스피킹 목표 점수" in bad and "오픽 난이도" in bad and "토플 목표 밴드" in bad
    assert db.get_settings()["tsp_target"] == "160"

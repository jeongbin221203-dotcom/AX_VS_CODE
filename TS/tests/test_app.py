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


def test_listen_audio_download(client, monkeypatch, tmp_path):
    from core import audio

    def fake_build(words, out_dir, **kw):
        assert len(words) == 50 and kw["repeats"] == 2 and kw["example"] is True
        f = tmp_path / "x.wav"
        f.write_bytes(b"RIFF" + b"0" * 2000)
        return f
    monkeypatch.setattr(audio, "build", fake_build)
    monkeypatch.setattr(audio, "available", lambda: True)
    r = client.get("/vocab/audio?level=1&set=all&chunk=2&repeats=2&example=1")
    assert r.status_code == 200 and r.mimetype == "audio/wav"
    assert "0051-0100" in r.headers["Content-Disposition"]


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

"""2026-10-07 사용자·전문가·학원강사 점검에서 나온 결함의 회귀 테스트."""
from __future__ import annotations

import random
import re
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from core import db, speaking, srs, study, toefl  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "DB_PATH": tmp_path / "ts.db"})


@pytest.fixture()
def bank(app):
    return app.extensions["bank"]


def _client(tmp_path):
    app = create_app({"DB_PATH": tmp_path / "c.db"})            # CSRF 검사를 켠 앱
    c = app.test_client()
    html = c.get("/").data.decode()
    return c, {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)}


# ---- 토플 밴드 반올림 -------------------------------------------------------------

def test_toefl_band_rounds_half_up():
    assert toefl.half_up(5.25) == 5.5 and toefl.half_up(4.25) == 4.5
    assert toefl.half_up(5.2) == 5.0 and toefl.half_up(5.75) == 6.0
    assert toefl.overall_band({"R": 5.5, "L": 5.5, "S": 5, "W": 5}) == 5.5      # 평균 5.25 → 5.5 (ETS 예시)


def test_toefl_old_score_table_and_order():
    assert toefl.BAND_OLD[4.5] == "86~94" and toefl.BAND_OLD[1] == "0~11" and toefl.BAND_OLD[3] == "44~57"
    assert toefl.MOCK_ORDER == ["R", "L", "W", "S"]


# ---- 토익 실전 모의고사 문항 수 ---------------------------------------------------

@pytest.mark.parametrize("seed", range(40))
def test_full_mock_has_exactly_200_questions(app, bank, seed):
    sid = study.start_mock(bank, "full", rng=random.Random(seed))
    refs = study.get_session(sid)["items"]
    assert sum(len(bank.questions(bank.item(r))) for r in refs) == 200


# ---- 겹친 제출은 한 번만 기록 ------------------------------------------------------

def test_concurrent_submit_records_once(app, bank):
    sid = study.start_diagnostic(bank, rng=random.Random(3))
    body = {"items": {r: [{"qidx": q.qidx, "chosen": 0} for q in bank.questions(bank.item(r))]
                      for r in study.get_session(sid)["items"]}}
    errors = []

    def go():
        try:
            study.submit_session(bank, sid, body)
        except Exception as e:                                   # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert len(study.session_attempts(sid)) == sum(len(bank.questions(bank.item(r))) for r in study.get_session(sid)["items"])


def test_concurrent_grade_item_records_once(app, bank):
    sid = study.start_practice(bank, 5, 1, None, 1, rng=random.Random(1))
    ref = study.get_session(sid)["items"][0]
    q = bank.questions(bank.item(ref))[0]
    ts = [threading.Thread(target=lambda: study.grade_item(bank, sid, ref, [{"qidx": q.qidx, "chosen": 1}])) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(study.session_attempts(sid)) == 1


# ---- 단어: 처음 보고 '다시' → 내일 복습 ---------------------------------------------

def test_first_miss_goes_to_tomorrow_review(app, bank):
    wid = bank.vocab[0]["id"]
    srs.review(wid, 0)
    tomorrow = date.today() + timedelta(days=1)
    q = srs.queue(bank, None, 0, today=tomorrow)
    assert wid in [w["id"] for w in q["due"]]


def test_new_words_start_from_current_grade(app, bank):
    q = srs.queue(bank, None, 10, start_level=3)
    assert q["new"] and all(w["level"] == 3 for w in q["new"])


# ---- 빈 답안 제출 ----------------------------------------------------------------

def test_blank_submit_is_not_an_estimate(app, bank):
    sid = study.start_diagnostic(bank, rng=random.Random(2))
    done = study.submit_session(bank, sid, {"items": {}})
    assert done["total_est"] is None and study.wrong_notes("open") == []


def test_duration_is_clamped(app, bank):
    sid = study.start_practice(bank, 5, 1, None, 1, rng=random.Random(1))
    done = study.finish_session(bank, sid, duration_sec=-580)
    assert done["duration_sec"] >= 0


# ---- 오픽: 안 한 문항은 최저점 -----------------------------------------------------

def test_opic_unanswered_steps_count_as_lowest(app):
    sb = app.extensions["speaking_bank"]
    plan = speaking.opic_mock_plan(sb, [], 4, random.Random(5))
    mid = speaking.create_mock("opic", plan, {"level": 4})
    first = plan[1]["steps"][0]["ref"]                            # 자기소개 다음 첫 문항 하나만 5점
    done = speaking.finish_mock(sb, mid, [{"task": first["task"], "item_id": first["item_id"], "qidx": first["qidx"],
                                           "points": 5, "words": 150, "seconds": 60}])
    assert done["result"]["grade"] != "AL" and done["result"]["avg"] < 2


# ---- 잘못된 입력은 500 이 아니라 400/404 ---------------------------------------------

@pytest.mark.parametrize("url", ["/api/vocab/review", "/api/vocab/star", "/api/vocab/quiz/answer",
                                 "/toefl/api/attempt", "/speaking/api/attempt"])
def test_json_array_body_is_not_500(tmp_path, url):
    c, h = _client(tmp_path)
    assert c.post(url, json=[1], headers=h).status_code in (400, 404)


def test_huge_numbers_are_not_500(tmp_path):
    c, h = _client(tmp_path)
    for url in ("/quiz/99999999999999999999999", "/toefl/mock/99999999999999999999999",
                "/speaking/mock/99999999999999999999999"):
        assert c.get(url).status_code == 404
    assert c.get("/speaking/toeic/history?page=99999999999999999999999").status_code < 500
    assert c.post("/review/note", data={"qkey": "5:x:0", "status": "bogus"}, headers=h).status_code == 400


def test_csrf_with_non_ascii_token_is_400(tmp_path):
    c, _ = _client(tmp_path)
    c.get("/")
    assert c.post("/review/note", data={"_csrf": "한글", "qkey": "x"}).status_code == 400


def test_tts_rate_rejects_nan(tmp_path):
    c, h = _client(tmp_path)
    c.post("/settings", data={"tts_rate": "nan", "_csrf": h["X-CSRF-Token"], "target_score": "800",
                              "daily_new_words": "20", "daily_questions": "40"})
    assert db.get_settings()["tts_rate"] != "nan"


def test_opic_bad_qidx_does_not_break_pages(tmp_path):
    c, h = _client(tmp_path)
    r = c.post("/speaking/api/attempt", headers=h,
               json={"exam": "opic", "rows": [{"task": "opic_rp", "item_id": "rp-001", "qidx": 10, "points": 3}]})
    assert r.status_code < 500
    assert c.get("/speaking/opic").status_code == 200
    assert c.get("/speaking/opic/history").status_code == 200


# ---- 2026-10-07 2차: 이어서 풀기·메뉴·문항 수·다른 정답·404·진단 ----------------------

def test_unfinished_sessions_listed_and_resumable(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "u.db"})
    bank = app.extensions["bank"]
    from core import stats
    mock = study.start_mock(bank, "mini", rng=random.Random(1))
    prac = study.start_practice(bank, 5, 1, None, 2, rng=random.Random(1))               # 한 문제도 안 풀었으면 목록에 없음
    ids = [s["id"] for s in stats.unfinished_sessions()]
    assert mock in ids and prac not in ids
    c = app.test_client()
    assert f"/quiz/{mock}" in c.get("/toeic").get_data(as_text=True)
    assert f"/quiz/{mock}" in c.get("/mock").get_data(as_text=True)
    study.finish_session(bank, mock)
    assert mock not in [s["id"] for s in stats.unfinished_sessions()]


def test_same_conditions_again_keeps_requested_count(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "r.db"})
    bank = app.extensions["bank"]
    sid = study.start_practice(bank, 5, 1, None, 10, rng=random.Random(1))
    ref = study.get_session(sid)["items"][0]
    q = bank.questions(bank.item(ref))[0]
    study.grade_item(bank, sid, ref, [{"qidx": 0, "chosen": q.answer}])
    study.finish_session(bank, sid)                                                       # 10문항 중 1문항만 풀고 끝냄
    with app.test_request_context():
        from flask import url_for
        url = url_for("quiz.result", sid=sid)
    html = app.test_client().get(url).get_data(as_text=True)
    assert "n=10" in html


def test_quiz_page_highlights_menu_by_session_mode(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "m.db"})
    bank = app.extensions["bank"]
    c = app.test_client()
    mock = study.start_mock(bank, "mini", rng=random.Random(1))
    prac = study.start_practice(bank, 5, 1, None, 2, rng=random.Random(1))
    import re as _re

    def active(sid):
        html = c.get(f"/quiz/{sid}").get_data(as_text=True)
        return _re.findall(r'class="on" aria-current=page>([^<]+)<', html)
    assert "모의고사" in active(mock) and "파트 연습" not in active(mock)
    assert "파트 연습" in active(prac) and "모의고사" not in active(prac)


def test_sentence_alternative_orders_validated():
    import json
    from tools import validate_toefl
    for f in ("w_sentence.json", "w_sentence_2.json"):
        for it in json.load(open(Path(__file__).resolve().parent.parent / "content" / "toefl" / f, encoding="utf-8")):
            for alt in it.get("alts", []):
                assert sorted(alt.split()) == sorted(" ".join(it["chunks"]).split())
    assert hasattr(validate_toefl, "w_sentence")


def test_korean_404_page(tmp_path):
    c, _ = _client(tmp_path)
    r = c.get("/no-such-page")
    assert r.status_code == 404 and "찾을 수 없습니다" in r.get_data(as_text=True)


def test_diagnostic_has_enough_listening(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "d.db"})
    bank = app.extensions["bank"]
    sid = study.start_diagnostic(bank, rng=random.Random(4))
    refs = study.get_session(sid)["items"]
    lc = sum(len(bank.questions(bank.item(r))) for r in refs if int(r.split(":")[0]) in (1, 2, 3, 4))
    assert lc >= 25


def test_frequent_words_are_core_tier(app, bank):
    tiers = {w["word"]: w["tier"] for w in bank.vocab}
    for w in ("client", "receipt", "overtime", "inventory", "permission", "headquarters", "eligible", "reimburse"):
        assert tiers[w] == "core", w


# ---- 단어 먼저: 오늘 화면 단어 카드·메뉴 강조·연습 화면 안내 ---------------------------

def test_dashboard_leads_with_vocab_card(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "v.db"})
    c = app.test_client()
    html = c.get("/toeic").get_data(as_text=True)
    assert "오늘의 단어" in html and html.index("오늘의 단어") < html.index("오늘의 토익")      # 단어 카드가 맨 위
    assert "단어 공부 시작" in html


def test_vocab_menu_is_second_and_emphasized(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "m.db"})
    html = app.test_client().get("/toeic").get_data(as_text=True)
    import re as _re
    items = _re.findall(r'<a href="[^"]*" class="[^"]*"[^>]*>(?:📘 )?([^<]+)</a>', html.split('id="sub-nav"')[1].split("</nav>")[0])
    assert items[:2] == ["홈", "단어"] and 'emph' in html


def test_practice_nudges_when_words_are_due(tmp_path):
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "p.db"})
    bank = app.extensions["bank"]
    c = app.test_client()
    assert "복습할 단어가" not in c.get("/practice").get_data(as_text=True)
    srs.review(bank.vocab[0]["id"], 0)                                   # 틀리면 내일 복습 → 오늘은 아직 아님
    from datetime import date as _d, timedelta as _t
    with db.connect() as con:
        con.execute("UPDATE vocab_cards SET due = ?", ((_d.today() - _t(days=1)).isoformat(),))
    assert "복습할 단어가" in c.get("/practice").get_data(as_text=True)


def test_vocab_quiz_options_never_repeat(tmp_path):
    """뜻이 같은 단어가 오답으로 두 번 뽑혀 같은 보기가 둘 나오던 문제 — 여러 번 뽑아도 항상 4개가 서로 달라야 한다."""
    import json as _json
    import re as _re
    app = create_app({"TESTING": True, "DB_PATH": tmp_path / "q.db"})
    c = app.test_client()
    for lv in (1, 2, 3, 4, 5):
        for _ in range(60):                          # 옛 방식은 문제 5,000개에 1개꼴로 중복 — 1만 문제 이상 뽑아야 확실히 잡힌다
            html = c.get(f"/vocab/quiz?n=50&level={lv}").get_data(as_text=True)
            qs = _json.loads(_re.search(r'id="vq-data">(.*?)</script>', html, _re.S).group(1))
            for q in qs:
                assert len(q["options"]) == 4 and len(set(q["options"])) == 4 and q["options"][q["answer"]] == q["meaning"], q

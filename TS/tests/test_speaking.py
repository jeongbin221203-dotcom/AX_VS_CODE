"""말하기 시험(토익스피킹·오픽) 테스트. 임시 DB + 작은 표본 문제 은행을 쓴다 (실제 문제는 형식 검사만)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from core import db, speaking as S  # noqa: E402
from tools import validate_speaking as V  # noqa: E402


def _w(n: int) -> str:
    return " ".join(["word"] * n) + "."


def _two(a: int, b: int) -> dict:
    return {"sample": _w(a), "sample_ko": "번역", "sample_adv": _w(b), "sample_adv_ko": "번역"}


KINDS5 = ["describe", "routine", "past", "compare", "issue"]
SAMPLE = {
    "toeic/read_aloud": [{"id": "ra-001", "type": "광고", "text": _w(50), "marked": "*word* / " + _w(49),
                          "tricky": ["a — 팁", "b — 팁"], "translation": "번역", "tips": ["팁"]}],
    "toeic/describe_picture": [{"id": "dp-001", "setting": "사무실", "scene_ko": "장면",
                                "elements": [{"where": "가운데", "ko": "사람", "en": "A man is typing on a laptop."}] * 4,
                                **_two(50, 80), "tips": ["팁"]}],
    "toeic/respond_questions": [{"id": "rq-001", "topic": "영화", "intro": _w(20), "intro_ko": "번역",
                                 "questions": ["When did you go there?", "Who did you go there with?", "Which do you prefer and why?"],
                                 "questions_ko": ["번역"] * 3, "samples": [_w(20), _w(20), _w(50)], "samples_ko": ["번역"] * 3,
                                 "samples_adv": [_w(30), _w(30), _w(70)], "samples_adv_ko": ["번역"] * 3, "tips": ["팁"]}],
    "toeic/respond_info": [{"id": "ri-001", "doc_type": "행사 일정표", "voice": "male",
                            "info": {"title": "T", "subtitle": "S", "rows": [["9:00", "Talk"]] * 5, "notes": []},
                            "intro": _w(15), "questions": ["What time does it start?", "Is that information still right?", "Tell me all of them."],
                            "questions_ko": ["번역"] * 3, "samples": [_w(15), _w(15), _w(30)], "samples_ko": ["번역"] * 3,
                            "tips": ["팁"]}],
    "toeic/opinion": [{"id": "op-001", "topic": "직장", "question": _w(20), "question_ko": "번역",
                       "outline": ["의견", "이유", "예시"], **_two(110, 150), "tips": ["팁"]}],
    "opic/questions": [{"id": "oq-intro-001", "topic": "intro", "kind": "intro", "level": 1, "question": _w(10),
                        "question_ko": "번역", **_two(90, 170), "tips": ["팁"]}]
                      + [{"id": f"oq-{t}-{k}", "topic": t, "kind": k, "level": 1 if k in ("describe", "routine") else 5,
                          "question": _w(15), "question_ko": "번역", **_two(90, 170), "tips": ["팁"]}
                         for t in ("home", "movie", "park", "bank") for k in KINDS5],
    "opic/roleplay": [{"id": "rp-001", "topic": "hotel", "level": 4, "situation_ko": "상황",
                       "steps": [{"kind": k, "question": _w(20), "question_ko": "번역", **_two(90, 170), "tips": ["팁"]}
                                 for k in ("ask", "solve", "exp")]}],
}


for _k in ("toeic/read_aloud", "toeic/describe_picture"):          # 모의고사는 2문항씩 필요
    SAMPLE[_k] = SAMPLE[_k] + [dict(SAMPLE[_k][0], id=SAMPLE[_k][0]["id"][:-1] + "2")]


@pytest.fixture()
def app(tmp_path):
    d = tmp_path / "speaking"
    for k, v in SAMPLE.items():
        f = d / f"{k}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return create_app({"TESTING": True, "DB_PATH": tmp_path / "ts.db", "SPEAKING_CONTENT_DIR": d})


@pytest.fixture()
def client(app):
    return app.test_client()


def _csrf(client) -> dict:
    html = client.get("/").data.decode()
    return {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)}


def _payload(client, url):
    html = client.get(url).data.decode()
    return json.loads(re.search(r'<script type="application/json" id="payload">(.*?)</script>', html, re.S).group(1))


def _mid(resp) -> int:
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[1])


# ---- 문제 데이터 ------------------------------------------------------------------

def test_sample_passes_validator(tmp_path):
    for k, v in SAMPLE.items():
        f = tmp_path / f"{k}.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
        errors, _ = V.validate_file(f, set())
        assert errors == [], errors


def test_validator_catches_mistakes(tmp_path):
    bad = dict(SAMPLE["toeic/read_aloud"][0], marked="different text / *here*")
    f = tmp_path / "toeic" / "read_aloud.json"
    f.parent.mkdir()
    f.write_text(json.dumps([bad, bad], ensure_ascii=False), encoding="utf-8")
    errors, _ = V.validate_file(f, set())
    assert any("marked" in e for e in errors) and any("id 중복" in e for e in errors)


def test_real_content_is_valid():
    seen: dict = {}
    files = V.all_files()
    assert files
    for p in files:
        errors, _ = V.validate_file(p, seen.setdefault(V.kind_of(p), set()))
        assert errors == [], errors[:5]


# ---- 화면 -------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/speaking/toeic", "/speaking/toeic/guide", "/speaking/toeic/mock", "/speaking/opic", "/speaking/opic/survey",
    "/speaking/opic/guide", "/speaking/opic/mock", "/speaking/toeic/practice/read_aloud",
    "/speaking/toeic/practice/describe_picture", "/speaking/toeic/practice/respond_questions",
    "/speaking/toeic/practice/respond_info", "/speaking/toeic/practice/opinion", "/speaking/opic/practice?topic=home",
    "/speaking/opic/practice?kind=roleplay", "/speaking/opic/practice?topic=intro&text=0",
    "/speaking/opic/practice?kind=past&n=3"])
def test_pages(client, url):
    assert client.get(url).status_code == 200


def test_bad_routes(client):
    assert client.get("/speaking/toeic/practice/nope").status_code == 404
    assert client.get("/speaking/opic/practice?topic=nope").status_code == 404
    assert client.get("/speaking/opic/practice?kind=nope").status_code == 404
    assert client.get("/speaking/mock/999").status_code == 404
    assert client.get("/speaking/mock/999/result").status_code == 404


def test_nav_shows_speaking_menus(client):
    html = client.get("/speaking/opic/survey").data.decode()
    assert "설문·난이도" in html and "답변 틀·채점 기준" in html and "준비 중" not in html


# ---- 점수 -------------------------------------------------------------------------

def test_scoring_helpers():
    assert S.TSP_RAW_MAX == 35
    assert S.tsp_score(35) == 200 and S.tsp_score(0) == 0 and S.tsp_score(99) == 200
    assert S.tsp_level(200)[1] == "Advanced High" and S.tsp_level(150)[1] == "Intermediate High"
    assert S.tsp_level(130)[1] == "Intermediate Mid 3" and S.tsp_level(40)[1] == "Novice Mid / Low"
    assert S.opic_grade(4.8, 200) == "AL" and S.opic_grade(4.0, 150) == "IH"
    assert S.opic_grade(3.0, 120) == "IM3" and S.opic_grade(3.0, 80) == "IM2" and S.opic_grade(3.0, 20) == "IM1"
    assert S.opic_grade(2.0, 50) == "IL" and S.opic_grade(None, None) is None


# ---- 토익스피킹 ---------------------------------------------------------------------

def test_tsp_practice_records_and_estimates(client):
    h = _csrf(client)
    steps = _payload(client, "/speaking/toeic/practice/respond_questions")["units"][0]["steps"]
    assert [s["speak"] for s in steps] == [15, 15, 30] and steps[0]["prep"] == 3
    assert steps[0]["intro"] and not steps[1]["intro"]
    r = client.post("/speaking/api/attempt", headers=h,
                    json={"exam": "tsp", "rows": [{**s["ref"], "points": 2, "words": 30, "seconds": 14} for s in steps]})
    assert r.status_code == 200 and r.get_json()["saved"] == 3 and r.get_json()["estimate"] is None
    bad = client.post("/speaking/api/attempt", headers=h, json={"exam": "tsp", "rows": [{"task": "opinion", "item_id": "zz", "points": 3}]})
    assert bad.status_code == 400
    for task in ("read_aloud", "describe_picture", "respond_info", "opinion"):
        st = _payload(client, f"/speaking/toeic/practice/{task}")["units"][0]["steps"]
        r = client.post("/speaking/api/attempt", headers=h, json={"exam": "tsp", "rows": [{**s["ref"], "points": 99} for s in st]})
        assert r.status_code == 200
    # 9문항 만점(최대치로 잘림) + Q5~7 2/3 → (35 - 3) / 35 → 180
    assert r.get_json()["estimate"] == S.tsp_score(32)
    info = _payload(client, "/speaking/toeic/practice/respond_info")["units"][0]["steps"]
    assert info[0]["read_first"] == 45 and info[1]["read_first"] == 0 and info[2]["repeat"] == 2 and info[0]["hide_question"]
    assert "Advanced" in client.get("/speaking/toeic").data.decode()


def test_tsp_mock_flow(client):
    h = _csrf(client)
    mid = _mid(client.post("/speaking/toeic/mock", data={"_csrf": h["X-CSRF-Token"]}))
    steps = [s for u in _payload(client, f"/speaking/mock/{mid}")["units"] for s in u["steps"]]
    assert len(steps) == 11 and steps[-1]["label"] == "Question 11 of 11"
    assert [s["ref"]["task"] for s in steps] == (["read_aloud"] * 2 + ["describe_picture"] * 2 + ["respond_questions"] * 3
                                                + ["respond_info"] * 3 + ["opinion"])
    rows = [{**s["ref"], "points": s["max"], "words": 50} for s in steps[:10]]            # 11번 미채점 → 0점
    foreign = {"task": "opinion", "item_id": "x", "qidx": 0, "points": 5}                  # 시험에 없는 문항은 무시
    r = client.post(f"/speaking/api/mock/{mid}/finish", headers=h, json={"rows": rows + [foreign]})
    assert r.status_code == 200
    m = S.get_mock(mid)
    assert m["result"]["raw"] == 30 and m["score"] == str(S.tsp_score(30)) and m["result"]["answered"] == 10
    html = client.get(r.get_json()["redirect"]).data.decode()
    assert "추정 점수" in html and m["score"] in html
    assert client.get(f"/speaking/mock/{mid}").status_code == 302                         # 끝난 시험 → 결과로
    assert client.post(f"/speaking/api/mock/{mid}/finish", headers=h, json={"rows": rows}).status_code == 200
    assert S.get_mock(mid)["result"]["raw"] == 30                                          # 두 번 제출해도 그대로
    assert client.post("/speaking/toeic/mock", data={"_csrf": h["X-CSRF-Token"]}).status_code == 302


def test_mock_finish_needs_answers(client):
    h = _csrf(client)
    mid = _mid(client.post("/speaking/toeic/mock", data={"_csrf": h["X-CSRF-Token"]}))
    assert client.post(f"/speaking/api/mock/{mid}/finish", headers=h, json={"rows": []}).status_code == 400


# ---- 오픽 -------------------------------------------------------------------------

def test_opic_survey_and_mock(client, app):
    h = _csrf(client)
    tok = h["X-CSRF-Token"]
    r = client.post("/speaking/opic/survey", data={"_csrf": tok, "topic": ["movie"], "level": "5", "target": "IH"})
    assert "개 이상 고르세요" in r.data.decode()
    r = client.post("/speaking/opic/survey", data={"_csrf": tok, "topic": ["movie", "park", "music", "jogging", "travel_dom"],
                                                    "level": "5", "target": "AL"})
    assert r.status_code == 302
    st = db.get_settings()
    assert st["opic_survey"].split(",")[0] == "home" and st["opic_level"] == "5" and st["opic_target"] == "AL"
    mid = _mid(client.post("/speaking/opic/mock", data={"_csrf": tok, "level": "5"}))
    steps = [s for u in _payload(client, f"/speaking/mock/{mid}")["units"] for s in u["steps"]]
    assert steps[0]["ref"]["item_id"].startswith("oq-intro")
    assert all(s["show"]["question"] == "" for s in steps)                  # 실전: 질문 글자 숨김
    assert sum(1 for s in steps if s["ref"]["task"] == "opic_rp") == 3
    keys = {(s["ref"]["task"], s["ref"]["item_id"], s["ref"]["qidx"]) for s in steps}
    assert len(steps) >= 12 and len(keys) == len(steps)                     # 같은 문항이 두 번 나오지 않음
    bank = app.extensions["speaking_bank"]
    topics = {bank.by_id[("opic_q", s["ref"]["item_id"])]["topic"] for s in steps if s["ref"]["task"] == "opic_q"}
    assert "bank" in topics and topics <= {"intro", "home", "movie", "park", "bank"}
    r = client.post(f"/speaking/api/mock/{mid}/finish", headers=h, json={"rows": [{**s["ref"], "points": 4, "words": 150} for s in steps]})
    assert r.status_code == 200
    m = S.get_mock(mid)
    assert m["score"] == "IH" and m["result"]["level"] == 5
    assert "Intermediate High" in client.get(f"/speaking/mock/{mid}/result").data.decode()
    html = client.get("/speaking/opic").data.decode()
    assert "IH" in html and "영화 보기" in html


def test_opic_practice_combo_order_and_record(client, app):
    h = _csrf(client)
    p = _payload(client, "/speaking/opic/practice?topic=movie&n=5")
    bank = app.extensions["speaking_bank"]
    kinds = [bank.by_id[("opic_q", u["item_id"])]["kind"] for u in p["units"]]
    assert kinds == KINDS5
    assert p["units"][0]["steps"][0]["show"]["question"]                   # 연습 기본값: 질문 글자 보기
    rows = [{**u["steps"][0]["ref"], "points": 9, "words": 100} for u in p["units"]]
    r = client.post("/speaking/api/attempt", headers=h, json={"exam": "opic", "rows": rows})
    assert r.status_code == 200 and r.get_json()["grade"] == "AL"          # 9 → 5로 잘림
    assert client.post("/speaking/api/attempt", headers=h, json={"exam": "x", "rows": rows}).status_code == 400
    hidden = _payload(client, "/speaking/opic/practice?topic=movie&text=0")
    assert hidden["units"][0]["steps"][0]["show"]["question"] == ""
    rp = _payload(client, "/speaking/opic/practice?kind=roleplay")["units"][0]["steps"]
    assert [s["ref"]["qidx"] for s in rp] == [0, 1, 2] and rp[0]["show"]["situation_ko"]


def test_fresh_items_first(client):
    """푼 문제는 뒤로 — 다음 연습에는 안 푼 문제가 먼저 나온다."""
    h = _csrf(client)
    first = _payload(client, "/speaking/opic/practice?kind=describe&n=1")["units"][0]
    client.post("/speaking/api/attempt", headers=h, json={"exam": "opic", "rows": [{**first["steps"][0]["ref"], "points": 3}]})
    for _ in range(3):
        nxt = _payload(client, "/speaking/opic/practice?kind=describe&n=1")["units"][0]
        assert nxt["item_id"] != first["item_id"]


def test_weak_items_practice(client):
    h = _csrf(client)
    assert "약한 문항이 없습니다" in client.get("/speaking/toeic/practice/opinion?weak=1").data.decode()
    st = _payload(client, "/speaking/toeic/practice/opinion")["units"][0]["steps"]
    client.post("/speaking/api/attempt", headers=h, json={"exam": "tsp", "rows": [{**s["ref"], "points": 1} for s in st]})
    p = _payload(client, "/speaking/toeic/practice/opinion?weak=1")
    assert [u["item_id"] for u in p["units"]] == ["op-001"]
    assert "약한 문항 1" in client.get("/speaking/toeic").data.decode()
    # 다시 풀어 잘하면 빠진다 (마지막 회차 기준)
    client.post("/speaking/api/attempt", headers=h, json={"exam": "tsp", "rows": [{**s["ref"], "points": 5} for s in st]})
    assert S.weak_items("tsp", "tsp:opinion") == {}
    q = _payload(client, "/speaking/opic/practice?kind=past&n=1")["units"][0]
    client.post("/speaking/api/attempt", headers=h, json={"exam": "opic", "rows": [{**q["steps"][0]["ref"], "points": 2}]})
    p = _payload(client, "/speaking/opic/practice?weak=1")
    assert [u["item_id"] for u in p["units"]] == [q["item_id"]]
    assert "약한 문항 1개 다시" in client.get("/speaking/opic").data.decode()

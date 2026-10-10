"""도구(tools/*.py) 검사 — python tests/test_tools.py  (pytest 가 있으면 python -m pytest tests 로도 된다)"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import import_ts_db as imp   # noqa: E402
import make_audio as ma      # noqa: E402


def test_words_load_and_counts():
    toeic, toefl = ma.load_words("toeic"), ma.load_words("toefl")
    assert len(toeic) == 1800 and len(toefl) == 800
    assert len({w["id"] for w in toeic + toefl}) == 2600


def test_select_all_levels_and_tiers():
    ws = ma.load_words("toeic")
    got = ma.select(ws, level=1, tier="core", which="all", backup={"cards": {}, "log": []})
    assert got and all(w["level"] == 1 and w["tier"] == "core" for w in got)


def test_select_uses_backup_for_weak_new_starred_learning():
    ws = ma.load_words("toeic")
    a, b, c = ws[0]["id"], ws[1]["id"], ws[2]["id"]
    backup = {"cards": {a: {"reps": 1, "lapses": 0, "interval": 4, "starred": 0},
                        b: {"reps": 3, "lapses": 0, "interval": 40, "starred": 1}},
              "log": [{"i": c, "g": 0}, {"i": c, "g": 0}, {"i": a, "g": 0}]}
    ids = lambda which: {w["id"] for w in ma.select(ws, level=None, tier=None, which=which, backup=backup)}  # noqa: E731
    assert ids("weak") == {c}                                  # 2번 이상 틀린 단어
    assert ids("starred") == {b}
    assert ids("learning") == {a}                              # 본 적 있고 간격 21일 미만
    assert a not in ids("new") and b not in ids("new") and len(ids("new")) == len(ws) - 2


def test_plan_chunks_and_fill():
    ws = ma.load_words("toeic")[:50]
    per = int(10 * 60 / ma.seconds_per_word(3, False))
    files = ma.plan(ws, 10, repeats=3, fill=False)
    assert [len(f) for f in files][:-1] == [per] * (len(files) - 1) and sum(len(f) for f in files) == 50
    filled = ma.plan(ws, 10, repeats=3, fill=True)
    assert all(len(f) == per for f in filled)                  # 마지막 파일도 길이를 채운다
    assert ma.plan([], 10) == []


def test_clean_and_segments():
    assert ma.clean("submit (제출하다)") == "submit"
    assert ma.clean("~ing") == "ing"
    segs = ma.segments([{"word": "go", "meaning": "가다", "example": "Go home."}], 2, True)
    assert [v for _, v in segs] == [ma.en_voice(0, 0), ma.en_voice(1, 1), ma.VOICE_KO, ma.en_voice(0, 1), "gap"]


def test_file_name():
    assert ma.file_name("toeic", 1, "core", 60, 3) == "토익단어_Orange_필수_60분_03.mp3"
    assert ma.file_name("toefl", None, None, 30, 1) == "토플단어_전체_30분_01.mp3"
    assert ma.file_name("toefl", 5, "stretch", 10, 12) == "토플단어_밴드6_도전_10분_12.mp3"


def test_which_needs_backup():
    assert ma.main(["--which", "weak"]) == 1


def test_import_ts_db_converts_and_leaves_source_alone():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "ts.db"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE vocab_cards(word_id TEXT PRIMARY KEY, ef REAL, interval INTEGER, reps INTEGER, lapses INTEGER,
                due TEXT, first_seen TEXT, last_review TEXT, starred INTEGER);
            CREATE TABLE vocab_log(id INTEGER PRIMARY KEY, word_id TEXT, grade INTEGER, was_new INTEGER, reviewed_at TEXT);
            CREATE TABLE vocab_quiz_log(id INTEGER PRIMARY KEY, word_id TEXT, correct INTEGER, answered_at TEXT);
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO vocab_cards VALUES ('v-0001', 2.6, 2, 1, 0, '2026-09-29', '2026-09-27T20:36:57', '2026-09-27T20:36:57', 1);
            INSERT INTO vocab_log(word_id, grade, was_new, reviewed_at) VALUES ('v-0001', 5, 1, '2026-09-27T20:36:57');
            INSERT INTO vocab_quiz_log(word_id, correct, answered_at) VALUES ('v-0001', 0, '2026-09-28T09:00:00');
            INSERT INTO settings VALUES ('daily_new_words', '30'), ('tts_rate', '1.2'), ('tts_accent', 'uk');
        """)
        con.commit()
        con.close()
        before = db.read_bytes()
        out = Path(d) / "b.json"
        assert imp.main([str(db), "-o", str(out)]) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["cards"]["v-0001"] == {"ef": 2.6, "interval": 2, "reps": 1, "lapses": 0, "due": "2026-09-29",
                                           "first_seen": "2026-09-27T20:36:57", "last_review": "2026-09-27T20:36:57", "starred": 1}
        assert data["log"] == [{"i": "v-0001", "g": 5, "n": 1, "t": "2026-09-27T20:36:57"}]
        assert data["quiz"] == [{"i": "v-0001", "o": 0, "t": "2026-09-28T09:00:00"}]
        assert data["settings"] == {"daily_new": 30, "tts_rate": 1.2, "tts_accent": "uk"}
        assert db.read_bytes() == before                       # 원본 DB 는 그대로


def test_import_missing_db():
    assert imp.main(["/없는/경로/ts.db", "-o", str(Path(tempfile.gettempdir()) / "x.json")]) == 1


def test_convert_exam_from_ts_schema():
    """TS 앱의 실제 DB 구조(core/db.py)에서 풀이 기록을 TSStore 모양으로 바꾼다."""
    sys.path.insert(0, str(ROOT.parent / "TS"))
    sys.dont_write_bytecode = True
    from core import db as ts_db                                # noqa: E402  (TS 앱은 읽기만 함)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ts.db"
        con = sqlite3.connect(path)
        con.executescript(ts_db.SCHEMA)
        con.execute("INSERT INTO sessions(created_at, finished_at, mode, variant, part, level, items, total, correct, total_est) "
                    "VALUES ('2026-10-01T10:00:00', '2026-10-01T10:10:00', 'practice', NULL, 5, 2, '[\"5:p5-001\"]', 1, 0, NULL)")
        con.execute("INSERT INTO attempts(session_id, qkey, part, item_id, qidx, level, qtype, chosen, correct, elapsed_ms, created_at) "
                    "VALUES (1, '5:p5-001:0', 5, 'p5-001', 0, 2, '품사', 2, 0, 5000, '2026-10-01T10:01:00')")
        con.execute("INSERT INTO wrong_notes(qkey, part, item_id, qidx, level, qtype, wrong_count, right_streak, status, "
                    "first_wrong_at, last_wrong_at, last_seen_at, memo) VALUES ('5:p5-001:0', 5, 'p5-001', 0, 2, '품사', 1, 0, 'open', "
                    "'2026-10-01T10:01:00', '2026-10-01T10:01:00', '2026-10-01T10:01:00', '메모')")
        con.execute("INSERT INTO settings VALUES ('target_score', '900'), ('exam_date', '2026-12-20'), ('tts_rate', '1.2')")
        con.commit()
        con.close()
        ts = imp.convert(path)["ts"]
        assert ts["settings"] == {"target_score": "900", "exam_date": "2026-12-20"}            # 단어 쪽 설정은 따로 옮겨짐
        assert ts["sessions"][0]["items"] == ["5:p5-001"] and ts["seq"] == {"session": 1}
        assert ts["attempts"] == [{"s": 1, "k": "5:p5-001:0", "c": 2, "o": 0, "m": 5000, "t": "2026-10-01T10:01:00", "l": 2, "y": "품사"}]
        assert ts["notes"]["5:p5-001:0"]["memo"] == "메모" and "qkey" not in ts["notes"]["5:p5-001:0"]


def test_old_db_without_exam_tables():
    """단어 표만 있는 예전 DB 도 변환된다 (시험 기록은 비어 있음)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "old.db"
        con = sqlite3.connect(path)
        con.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);")
        con.commit()
        con.close()
        assert imp.convert_exam(path)["sessions"] == []


def test_pages_are_in_sync_with_generator():
    """HTML 화면 파일은 tools/make_pages.py 가 만든 그대로여야 한다 (손으로 고치면 다음 실행에서 사라짐)."""
    import make_pages as mp
    for name, (title, scripts, attrs) in mp.PAGES.items():
        assert (ROOT / name).read_text(encoding="utf-8") == mp.render(title, scripts, attrs), f"{name} 이 make_pages.py 와 다름"


def test_question_data_files():
    toeic_total = 0
    for part in range(1, 8):
        text = (ROOT / "data" / f"toeic-p{part}.js").read_text(encoding="utf-8")
        mark = f".p{part} = "
        body = text[text.index(mark) + len(mark):].rstrip().rstrip(";")
        items = json.loads(body)
        assert items and all(it["part"] == part for it in items)
        toeic_total += sum(len(it["questions"]) if "questions" in it else 1 for it in items)
    assert toeic_total >= 3500
    meta = (ROOT / "data" / "meta.js").read_text(encoding="utf-8")
    assert json.loads(meta[meta.index("window.TS_META = ") + 17:].rstrip().rstrip(";"))["toeic"]["questions"] == toeic_total


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ✓", name)
            except Exception as e:                              # noqa: BLE001
                fails += 1
                print("  ✗", name, "→", repr(e)[:200])
    print(f"\n{'실패 ' + str(fails) if fails else '모두 통과'}")
    sys.exit(1 if fails else 0)

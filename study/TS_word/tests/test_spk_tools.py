"""토익스피킹·오픽 도구 검사 — python -m pytest tests -q"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import import_ts_db as imp   # noqa: E402


def _json_of(path: Path, marker: str):
    text = path.read_text(encoding="utf-8")
    i = text.index(marker) + len(marker)
    return json.loads(text[i:].rstrip().rstrip(";"))


def test_speaking_data_files_and_meta_agree():
    tsp = _json_of(ROOT / "data" / "speaking-tsp.js", ".tsp = ")
    opic = _json_of(ROOT / "data" / "speaking-opic.js", ".opic = ")
    assert list(tsp) == ["read_aloud", "describe_picture", "respond_questions", "respond_info", "opinion"]
    ids = [it["id"] for items in tsp.values() for it in items] + [q["id"] for q in opic["questions"]] + [r["id"] for r in opic["roleplay"]]
    assert len(ids) == len(set(ids)) and len(ids) > 1000
    meta = _json_of(ROOT / "data" / "meta.js", "window.TS_META = ")["speaking"]
    assert meta["tsp"]["total"] == sum(len(v) for v in tsp.values())
    assert meta["tsp"]["tasks"] == {k: len(v) for k, v in tsp.items()}
    assert meta["opic"]["questions"] == len(opic["questions"]) and meta["opic"]["roleplay"] == len(opic["roleplay"])
    assert sum(sum(t.values()) for t in meta["opic"]["topics"].values()) == len(opic["questions"]) + len(opic["roleplay"])
    assert all(len(r["steps"]) == 3 for r in opic["roleplay"])


def test_speaking_tables_convert_to_ext():
    """TS 앱 DB 의 speaking_* 표 → ts.ext (js/spk-core.js 모양). 모의고사 계획은 문제 번호만 남는다."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ts.db"
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO settings VALUES ('opic_survey', 'home,movie'), ('tsp_target', '150'), ('opic_level', '5');
            CREATE TABLE speaking_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, exam TEXT, task TEXT, item_id TEXT,
              qidx INTEGER, points REAL, max_points REAL, words INTEGER, seconds REAL, accuracy REAL, mock_id INTEGER, response TEXT);
            CREATE TABLE speaking_mocks (id INTEGER PRIMARY KEY AUTOINCREMENT, exam TEXT, created_at TEXT, finished_at TEXT, plan TEXT,
              settings TEXT, result TEXT, score TEXT);
            INSERT INTO speaking_attempts(created_at, exam, task, item_id, qidx, points, max_points, words, seconds, accuracy, mock_id, response)
              VALUES ('2026-10-01T09:00:00', 'tsp', 'tsp:read_aloud', 'ra-001', 0, 2, 3, 40, 30.5, 0.9, NULL, 'hello'),
                     ('2026-10-01T09:05:00', 'opic', 'opic_q', 'oq-home-001', 0, 4, 5, NULL, NULL, NULL, 1, '');
            INSERT INTO speaking_mocks(exam, created_at, finished_at, plan, settings, result, score)
              VALUES ('opic', '2026-10-01T09:00:00', '2026-10-01T09:40:00',
                      '[{"task":"opic_q","item_id":"oq-home-001","steps":[{"label":"Question 1","show":{"kind":"opic","question":""}}]}]',
                      '{"level":5,"target":"IH"}', '{"grade":"IH","avg":4.0}', 'IH');
        """)
        con.commit()
        con.close()
        ts = imp.convert_exam(path)
    ext = ts["ext"]
    assert [a["id"] for a in ext["speaking_attempts"]] == [1, 2]
    assert ext["speaking_attempts"][0]["points"] == 2 and ext["speaking_attempts"][1]["mock_id"] == 1
    m = ext["speaking_mocks"][0]
    assert m["units"] == [{"task": "opic_q", "item_id": "oq-home-001"}] and "plan" not in m
    assert m["settings"] == {"level": 5, "target": "IH"} and m["result"]["grade"] == "IH" and m["score"] == "IH"
    assert ts["settings"]["tsp_target"] == "150" and ts["settings"]["opic_survey"] == "home,movie"


def test_speaking_pages_exist_and_use_one_dispatcher():
    names = ["toeic-speaking", "tsp-guide", "tsp-mock", "tsp-mock-result", "tsp-practice", "tsp-mock-run", "opic", "opic-survey", "opic-guide",
             "opic-mock", "opic-mock-result", "opic-practice", "opic-mock-run", "speaking-history"]
    for n in names:
        text = (ROOT / f"{n}.html").read_text(encoding="utf-8")
        assert f'data-spk="{n}"' in text, n
        assert re.search(r'js/spk-(pages|run)\.js', text) and "js/spk-core.js" in text
        assert "soon.js" not in text

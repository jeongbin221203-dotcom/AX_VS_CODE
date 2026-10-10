"""토플 도구 검사 — python -m pytest tests -q  (데이터 만들기·TS 기록 변환)"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
TS = ROOT.parent / "TS"
sys.path.insert(0, str(ROOT / "tools"))

import build_toefl      # noqa: E402
import import_ts_db as imp   # noqa: E402


def _ts_core():
    sys.path.insert(0, str(TS))
    from core import db, toefl           # noqa: E402
    return db, toefl


def test_build_toefl_counts_and_files():
    _, toefl = _ts_core()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        meta = build_toefl.build(out, TS)
        assert set(meta["tasks"]) == set(toefl.TASKS)
        total = 0
        for task in toefl.TASKS:
            text = (out / f"toefl-{task}.js").read_text(encoding="utf-8")
            assert text.startswith("/* 자동 생성")
            assert f".tf_{task} = [" in text
            body = text.split(f".tf_{task} = ", 1)[1].rsplit(";", 1)[0]
            items = json.loads(body)
            assert len(items) == meta["tasks"][task]["items"]
            assert sum(meta["tasks"][task]["levels"].values()) == len(items)
            total += len(items)
        assert total == meta["items"] == 960 and meta["vocab"] == 800


def test_convert_exam_moves_toefl_tables_into_ext():
    db, toefl = _ts_core()
    bank = toefl.ToeflBank(TS / "content" / "toefl")
    with tempfile.TemporaryDirectory() as tmp:
        db.configure(Path(tmp) / "t.db")
        toefl.ensure_schema()
        toefl.ensure_mock_schema()
        it = bank.items["r_daily"][0]
        toefl.record("r_daily", it["id"], it["level"], [{"qidx": 0, "score": 1, "response": "0"}, {"qidx": 1, "score": 0, "response": "2"}])
        mid = toefl.create_mock(bank, 4.5)                               # 끝나지 않은 모의고사
        ts = imp.convert_exam(Path(tmp) / "t.db")
        ext = ts["ext"]
        assert [r["qidx"] for r in ext["toefl_attempts"]] == [0, 1]
        assert ext["toefl_attempts"][0]["item_id"] == it["id"] and ext["toefl_attempts"][1]["score"] == 0
        (m,) = ext["toefl_mocks"]
        assert m["id"] == mid and m["finished_at"] is None and m["result"] is None
        plan = m["plan"]
        first = plan["sections"]["R"]["modules"][0][0]
        assert set(first) == {"task", "id"}, "문제 전체가 아니라 번호만 남긴다"
        assert set(plan["sections"]["R"]["modules"][1]) == {"hard", "easy"}
        assert set(plan["sections"]["S"]["items"][0]) == {"task", "id"}
        assert len(json.dumps(plan)) < 6000
        # 끝난 시험은 계획을 버리고 결과만
        toefl.finish_mock(bank, mid, {"items": [{"task": "r_daily", "item_id": it["id"], "results": [{"qidx": 0, "score": 1}]}], "routes": {}})
        (m2,) = imp.convert_exam(Path(tmp) / "t.db")["ext"]["toefl_mocks"]
        assert m2["plan"] is None and m2["result"]["bands"]["R"] is not None and m2["finished_at"]


def test_convert_exam_without_toefl_tables_has_no_ext():
    db, _ = _ts_core()
    with tempfile.TemporaryDirectory() as tmp:
        db.configure(Path(tmp) / "empty.db")
        assert imp.convert_exam(Path(tmp) / "empty.db")["ext"].get("toefl_attempts") is None

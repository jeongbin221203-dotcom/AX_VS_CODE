"""TS 앱의 단어 학습 기록(data/ts.db)을 TS 단어(TS_word)가 불러올 수 있는 백업 파일로 바꾼다.

실행:   python tools/import_ts_db.py                       (../TS/data/ts.db → ts-word-backup.json)
        python tools/import_ts_db.py 다른경로/ts.db -o 내파일.json

그다음 settings.html → '백업 불러오기' (기본 '합치기')로 불러온다.
TS 앱의 DB 는 읽기 전용으로만 연다(건드리지 않는다).
옮기는 것: 복습 카드(간격·일정·별표), 복습 기록, 단어 시험 기록, 하루 새 단어 수·음성 설정,
         토익 풀이 기록(세션·문항별 기록·오답노트)과 시험 목표·시험일 설정.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def convert(db_path: Path) -> dict:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        cards = {}
        for r in con.execute("SELECT * FROM vocab_cards"):
            cards[r["word_id"]] = {"ef": r["ef"], "interval": r["interval"], "reps": r["reps"], "lapses": r["lapses"],
                                   "due": r["due"], "first_seen": r["first_seen"], "last_review": r["last_review"],
                                   "starred": 1 if r["starred"] else 0}
        log = [{"i": r["word_id"], "g": r["grade"], "n": 1 if r["was_new"] else 0, "t": r["reviewed_at"]}
               for r in con.execute("SELECT word_id, grade, was_new, reviewed_at FROM vocab_log ORDER BY reviewed_at")]
        quiz = [{"i": r["word_id"], "o": 1 if r["correct"] else 0, "t": r["answered_at"]}
                for r in con.execute("SELECT word_id, correct, answered_at FROM vocab_quiz_log ORDER BY answered_at")]
        st = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    finally:
        con.close()
    settings = {}
    try:
        if st.get("daily_new_words"):
            settings["daily_new"] = int(st["daily_new_words"])
        if st.get("tts_rate"):
            settings["tts_rate"] = float(st["tts_rate"])
    except ValueError:
        pass
    if st.get("tts_accent") in ("mix", "us", "uk", "au"):
        settings["tts_accent"] = st["tts_accent"]
    return {"app": "ts-word", "v": 2, "settings": settings, "cards": cards, "log": log, "quiz": quiz,
            "ts": convert_exam(db_path)}


# TS 앱 설정 중 시험 목표·시험일 (TS_word 의 TSStore.DEFAULT_SETTINGS 와 같은 키)
EXAM_SETTING_KEYS = ("target_score", "current_score", "current_score_at", "exam_date", "daily_questions", "toefl_target",
                     "toefl_exam_date", "tsp_exam_date", "opic_exam_date", "tsp_target", "opic_target", "opic_level", "opic_survey")


def convert_exam(db_path: Path) -> dict:
    """시험 풀이 기록(sessions·attempts·wrong_notes·settings) → TSStore 모양 (js/tsstore.js 맨 위 설명 참고).
    토플 기록(toefl_attempts·toefl_mocks)은 `ext` 로 옮긴다 (toefl_ext). 토익스피킹·오픽(speaking_* 표)은 다른 담당이 같은 `ext` 에 더한다."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    def rows(sql):                                       # 예전 DB 에 표가 없으면 빈 목록
        try:
            return con.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []
    try:
        st = {r["key"]: r["value"] for r in rows("SELECT key, value FROM settings")}
        sessions = []
        for r in rows("SELECT * FROM sessions ORDER BY id"):
            d = dict(r)
            d["items"] = json.loads(d["items"])
            sessions.append(d)
        attempts = [{"s": r["session_id"], "k": r["qkey"], "c": r["chosen"], "o": r["correct"], "m": r["elapsed_ms"],
                     "t": r["created_at"], "l": r["level"], "y": r["qtype"]}
                    for r in rows("SELECT * FROM attempts ORDER BY id")]
        notes = {}
        for r in rows("SELECT * FROM wrong_notes"):
            d = dict(r)
            notes[d.pop("qkey")] = d
        ext = {}
        ext.update(toefl_ext(rows))                      # 토플: toefl_attempts·toefl_mocks → ext
        ext.update(speaking_ext(rows))                   # 토익스피킹·오픽: speaking_attempts·speaking_mocks → ext
    finally:
        con.close()
    return {"v": 1, "settings": {k: st[k] for k in EXAM_SETTING_KEYS if k in st},
            "seq": {"session": max((s["id"] for s in sessions), default=0)},
            "sessions": sessions, "attempts": attempts, "notes": notes, "ext": ext}


def toefl_ext(rows) -> dict:
    """TS 앱 토플 표(toefl_attempts·toefl_mocks) → TSStore 컬렉션 (js/toefl-core.js 가 읽는 모양).
    풀이 기록은 id 순서를 그대로(밴드 추정이 '가장 최근 N개'를 쓰므로). 모의고사 계획은 문제 전체 대신 {task,id} 번호만 남긴다."""
    attempts = [{"id": r["id"], "created_at": r["created_at"], "task": r["task"], "item_id": r["item_id"], "qidx": r["qidx"],
                 "level": r["level"], "score": r["score"], "response": r["response"]}
                for r in rows("SELECT * FROM toefl_attempts ORDER BY id")]
    def ref_list(lst):
        return [{"task": e["task"], "id": e["item"]["id"]} for e in lst]
    mocks = []
    for r in rows("SELECT * FROM toefl_mocks ORDER BY id"):
        plan = json.loads(r["plan"])
        done = bool(r["finished_at"])
        if not done:                                     # 끝난 시험은 결과만 남기고 계획은 버린다 (저장 용량)
            for sec in plan["sections"].values():
                if "modules" in sec:
                    sec["modules"] = [ref_list(sec["modules"][0]), {k: ref_list(v) for k, v in sec["modules"][1].items()}]
                else:
                    sec["items"] = ref_list(sec["items"])
        mocks.append({"id": r["id"], "created_at": r["created_at"], "finished_at": r["finished_at"],
                      "plan": None if done else plan, "result": json.loads(r["result"]) if r["result"] else None,
                      "r": r["r"], "l": r["l"], "s": r["s"], "w": r["w"], "total": r["total"]})
    out = {}
    if attempts:
        out["toefl_attempts"] = attempts
    if mocks:
        out["toefl_mocks"] = mocks
    return out


def speaking_ext(rows) -> dict:
    """TS 앱 말하기 표(speaking_attempts·speaking_mocks) → TSStore 컬렉션 (js/spk-core.js 가 읽는 모양).
    답변은 id 순서 그대로(추정 점수가 '가장 최근 N개'를 쓰므로). 모의고사 계획은 단계 전체 대신 문제 번호(units)만 남긴다 — 단계는 문제 은행에서 다시 만든다."""
    attempts = [{"id": r["id"], "created_at": r["created_at"], "exam": r["exam"], "task": r["task"], "item_id": r["item_id"], "qidx": r["qidx"],
                 "points": r["points"], "max_points": r["max_points"], "words": r["words"], "seconds": r["seconds"], "accuracy": r["accuracy"],
                 "mock_id": r["mock_id"], "response": r["response"]}
                for r in rows("SELECT * FROM speaking_attempts ORDER BY id")]
    mocks = []
    for r in rows("SELECT * FROM speaking_mocks ORDER BY id"):
        mocks.append({"id": r["id"], "created_at": r["created_at"], "exam": r["exam"], "finished_at": r["finished_at"],
                      "units": [{"task": u["task"], "item_id": u["item_id"]} for u in json.loads(r["plan"])],
                      "settings": json.loads(r["settings"] or "{}"), "result": json.loads(r["result"]) if r["result"] else None, "score": r["score"]})
    out = {}
    if attempts:
        out["speaking_attempts"] = attempts
    if mocks:
        out["speaking_mocks"] = mocks
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TS 앱 단어 기록 → TS_word 백업 파일")
    ap.add_argument("db", nargs="?", type=Path, default=HERE.parent / "TS" / "data" / "ts.db")
    ap.add_argument("-o", "--out", type=Path, default=HERE / "ts-word-backup.json")
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(f"오류: {a.db} 가 없습니다", file=sys.stderr)
        return 1
    data = convert(a.db)
    a.out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ts = data["ts"]
    print(f"{a.out} — 카드 {len(data['cards'])}개 · 복습 기록 {len(data['log'])}건 · 단어 시험 기록 {len(data['quiz'])}건")
    print(f"           토익 풀이 {len(ts['sessions'])}번 · 문항 기록 {len(ts['attempts'])}건 · 오답노트 {len(ts['notes'])}개")
    ext = ts.get("ext", {})
    if ext.get("toefl_attempts") or ext.get("toefl_mocks"):
        print(f"           토플 풀이 기록 {len(ext.get('toefl_attempts', []))}건 · 모의고사 {len(ext.get('toefl_mocks', []))}번")
    if ext.get("speaking_attempts") or ext.get("speaking_mocks"):
        print(f"           토익스피킹·오픽 답변 {len(ext.get('speaking_attempts', []))}건 · 모의고사 {len(ext.get('speaking_mocks', []))}번")
    print("설정 화면(settings.html) → 백업 불러오기 → '합치기' 로 불러오세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

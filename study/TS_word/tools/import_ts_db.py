"""TS 앱의 단어 학습 기록(data/ts.db)을 TS 단어(TS_word)가 불러올 수 있는 백업 파일로 바꾼다.

실행:   python tools/import_ts_db.py                       (../TS/data/ts.db → ts-word-backup.json)
        python tools/import_ts_db.py 다른경로/ts.db -o 내파일.json

그다음 settings.html → '백업 불러오기' (기본 '합치기')로 불러온다.
TS 앱의 DB 는 읽기 전용으로만 연다(건드리지 않는다).
옮기는 것: 복습 카드(간격·일정·별표), 복습 기록, 시험 기록, 하루 새 단어 수·음성 설정.
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
    return {"app": "ts-word", "v": 1, "settings": settings, "cards": cards, "log": log, "quiz": quiz}


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
    print(f"{a.out} — 카드 {len(data['cards'])}개 · 복습 기록 {len(data['log'])}건 · 시험 기록 {len(data['quiz'])}건")
    print("설정 화면(settings.html) → 백업 불러오기 → '합치기' 로 불러오세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

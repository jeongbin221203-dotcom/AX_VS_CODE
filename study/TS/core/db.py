"""SQLite 저장소. 학습 기록(세션·풀이·오답노트·단어 카드)과 설정을 보관한다."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

_db_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 한 번의 풀이 묶음 (파트 연습, 진단, 모의고사, 오답 복습)
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    mode         TEXT NOT NULL CHECK (mode IN ('practice', 'diagnostic', 'mock', 'review')),
    variant      TEXT,                 -- 모의고사 종류(mini/half/full), 연습 유형 필터 등
    part         INTEGER,
    level        INTEGER,
    items        TEXT NOT NULL,        -- JSON: ["5:p5-001", "3:p3-004", ...] 문제 묶음 순서
    time_limit   INTEGER,              -- RC 제한 시간(초), 없으면 NULL
    seen_before  INTEGER,              -- 모의고사: 전에 풀어 본 문제 묶음 수
    total        INTEGER NOT NULL DEFAULT 0,
    correct      INTEGER NOT NULL DEFAULT 0,
    lc_total     INTEGER NOT NULL DEFAULT 0,
    lc_correct   INTEGER NOT NULL DEFAULT 0,
    rc_total     INTEGER NOT NULL DEFAULT 0,
    rc_correct   INTEGER NOT NULL DEFAULT 0,
    lc_est       INTEGER,
    rc_est       INTEGER,
    total_est    INTEGER,
    duration_sec INTEGER
);

-- 문항 하나를 푼 기록. qkey = "파트:문제id:문항번호"
CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    qkey        TEXT NOT NULL,
    part        INTEGER NOT NULL,
    item_id     TEXT NOT NULL,
    qidx        INTEGER NOT NULL,
    level       INTEGER NOT NULL,
    qtype       TEXT NOT NULL,
    chosen      INTEGER NOT NULL,      -- -1 = 답하지 않음
    correct     INTEGER NOT NULL,
    elapsed_ms  INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
CREATE INDEX IF NOT EXISTS idx_attempts_part ON attempts(part, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_qkey ON attempts(qkey);

-- 오답노트: 틀리면 열리고, 복습에서 연속 2번 맞히면 '졸업'
CREATE TABLE IF NOT EXISTS wrong_notes (
    qkey          TEXT PRIMARY KEY,
    part          INTEGER NOT NULL,
    item_id       TEXT NOT NULL,
    qidx          INTEGER NOT NULL,
    level         INTEGER NOT NULL,
    qtype         TEXT NOT NULL,
    wrong_count   INTEGER NOT NULL DEFAULT 0,
    right_streak  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'cleared')),
    first_wrong_at TEXT NOT NULL,
    last_wrong_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    memo          TEXT NOT NULL DEFAULT ''
);

-- 단어 카드 (SM-2 간격 반복)
CREATE TABLE IF NOT EXISTS vocab_cards (
    word_id     TEXT PRIMARY KEY,
    ef          REAL NOT NULL DEFAULT 2.5,
    interval    INTEGER NOT NULL DEFAULT 0,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    due         TEXT NOT NULL,          -- YYYY-MM-DD
    first_seen  TEXT NOT NULL,
    last_review TEXT NOT NULL,
    starred     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_vocab_due ON vocab_cards(due);

CREATE TABLE IF NOT EXISTS vocab_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     TEXT NOT NULL,
    grade       INTEGER NOT NULL,       -- 0 다시, 3 어려움, 4 보통, 5 쉬움
    was_new     INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vocab_log_at ON vocab_log(reviewed_at);

-- 단어 뜻 고르기 퀴즈 기록 (틀리면 vocab_log 에도 '다시'로 남아 복습 카드에 들어간다)
CREATE TABLE IF NOT EXISTS vocab_quiz_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     TEXT NOT NULL,
    correct     INTEGER NOT NULL,
    answered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vocab_quiz_word ON vocab_quiz_log(word_id);
"""


def configure(path: Path | str) -> None:
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
        if "seen_before" not in cols:                       # 예전 DB 업그레이드
            con.execute("ALTER TABLE sessions ADD COLUMN seen_before INTEGER")
        # 예전 버전: 처음 보는 단어를 '다시'로 틀리면 reps·lapses 가 둘 다 0 이라 복습 목록에 안 들어갔다 → 틀린 기록이 있으면 1로 바로잡는다
        con.execute("UPDATE vocab_cards SET lapses = 1 WHERE reps = 0 AND lapses = 0 "
                    "AND word_id IN (SELECT word_id FROM vocab_log WHERE grade < 3)")


@contextmanager
def connect(write: bool = False) -> Iterator[sqlite3.Connection]:
    """write=True 이면 시작하자마자 쓰기 잠금을 잡는다 — '이미 했나' 확인과 기록이 한 덩어리여야 하는 곳
    (같은 제출이 겹쳐 두 번 저장되는 것을 막는다). 다른 쓰기가 끝날 때까지 최대 30초 기다린다."""
    if _db_path is None:
        raise RuntimeError("db.configure()를 먼저 호출하세요")
    con = sqlite3.connect(_db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---- 설정 ---------------------------------------------------------------

DEFAULT_SETTINGS = {
    "target_score": "800",
    "current_score": "",        # 비어 있으면 진단·모의고사 추정치 사용
    "current_score_at": "",     # 직접 입력한 시각 — 이후 추정치가 나오면 추정치를 쓴다
    "exam_date": "",
    "daily_new_words": "20",
    "daily_questions": "40",
    "tts_rate": "1.0",
    "tts_accent": "mix",        # mix | us | uk | au
    "toefl_target": "4.5",      # 토플 목표 밴드 (1~6, 0.5 단위)
    "toefl_exam_date": "",      # 시험일 (YYYY-MM-DD) — 메인 카드에 D-day
    "tsp_exam_date": "",
    "opic_exam_date": "",
    "tsp_target": "140",        # 토익스피킹 목표 점수
    "opic_target": "IH",        # 오픽 목표 등급
    "opic_level": "4",          # 오픽 설문 난이도 (1~6)
    "opic_survey": "",          # 오픽 Background Survey 로 고른 주제 (쉼표)
}


def get_settings() -> dict[str, str]:
    with connect() as con:
        rows = con.execute("SELECT key, value FROM settings").fetchall()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


def save_settings(values: dict[str, str]) -> None:
    with connect() as con:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                con.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, str(v)))

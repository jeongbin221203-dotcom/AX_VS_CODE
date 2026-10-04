"""SQLite 저장소. 수집한 공고, 지원 현황, 내 조건(설정), 수집 기록을 보관한다."""
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

-- 출처별 공고를 공통 형태로 맞춰 저장. (source, source_id) 로 중복 제거
CREATE TABLE IF NOT EXISTS postings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,          -- saramin / work24 / wanted / csv / manual / sample
    source_id           TEXT NOT NULL,
    url                 TEXT,
    title               TEXT NOT NULL,
    company             TEXT NOT NULL,
    sido                TEXT,                   -- 서울·경기 … (짧은 이름), 재택·해외·전국
    sigungu             TEXT,
    location_raw        TEXT,
    career_type         TEXT,                   -- 신입 / 경력 / 신입·경력 / 무관
    career_min          INTEGER,                -- 경력 최소 년수
    career_max          INTEGER,
    career_raw          TEXT,
    education           TEXT,                   -- 무관 / 고졸 / 초대졸 / 대졸 / 석사 / 박사
    employment_type     TEXT,                   -- 정규직·계약직 …
    salary_raw          TEXT,
    salary_min          INTEGER,                -- 연봉 환산, 만원
    salary_max          INTEGER,
    salary_negotiable   INTEGER NOT NULL DEFAULT 0,
    company_avg_salary  INTEGER,                -- 회사 평균연봉(만원, 직접 입력·CSV)
    company_info        TEXT,                   -- 기업 정보 JSON (업종·사원수·기업형태·설립일·매출액 등, 사람인 상세)
    job_category        TEXT,
    keywords            TEXT,                   -- 쉼표 구분
    description         TEXT,
    posted_at           TEXT,                   -- YYYY-MM-DD
    deadline            TEXT,                   -- YYYY-MM-DD, NULL = 상시·채용 시 마감
    fetched_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    hidden              INTEGER NOT NULL DEFAULT 0,
    saved               INTEGER NOT NULL DEFAULT 0,   -- 저장한 공고: 마감돼도 지우지 않고 다시 볼 수 있음
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_deadline ON postings(deadline);
CREATE INDEX IF NOT EXISTS idx_postings_sido ON postings(sido);

-- 공고 하나에 지원 기록 하나
CREATE TABLE IF NOT EXISTS applications (
    posting_id   INTEGER PRIMARY KEY REFERENCES postings(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
    memo         TEXT NOT NULL DEFAULT '',
    applied_at   TEXT,
    next_at      TEXT,                          -- 다음 일정(면접 등) YYYY-MM-DD
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  INTEGER NOT NULL REFERENCES postings(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- 아직 상세를 읽지 않은 공고 번호 (목록에서 찾음). 실행이 끝나도 남아 다음 실행이 이어 읽는다.
-- seq 가 작을수록 먼저 — 새로 찾은 묶음이 앞에 온다 (새 공고 먼저, 밀린 공고는 뒤에)
CREATE TABLE IF NOT EXISTS crawl_queue (
    site      TEXT NOT NULL,
    post_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    hint      TEXT,                          -- 목록에서 읽은 근무지·경력·직무 (JSON)
    added_at  TEXT NOT NULL,
    PRIMARY KEY (site, post_id)
);
CREATE INDEX IF NOT EXISTS idx_queue_seq ON crawl_queue(site, seq);

-- 목록에서 처음 본 시각 — '하루 새 공고 수(실측)'를 센다
CREATE TABLE IF NOT EXISTS list_seen (
    site        TEXT NOT NULL,
    post_id     TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (site, post_id)
);

-- 사이트맵으로 전체를 따라가는 사이트(리멤버)의 공고 번호 목록. 새로 생긴 번호만 상세를 읽는다
CREATE TABLE IF NOT EXISTS sitemap_ids (
    site        TEXT NOT NULL,
    post_id     TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    fetched_at  TEXT,                       -- 상세를 읽은 시각 (실패해도 기록해 무한 재시도 방지)
    fetch_error TEXT,
    gone        INTEGER NOT NULL DEFAULT 0, -- 사이트맵에서 빠짐 = 내려간 공고
    PRIMARY KEY (site, post_id)
);

-- 사이트가 따로 모아 둔 목록에 있던 공고 표시 (예: 사람인·잡코리아 헤드헌팅 목록 → '헤드헌팅')
CREATE TABLE IF NOT EXISTS post_flags (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    flag       TEXT NOT NULL,
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (source, source_id, flag)
);

-- 정기 크롤링 한 번의 결과 (수집 현황 화면의 실행 기록)
CREATE TABLE IF NOT EXISTS crawl_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    seconds     INTEGER NOT NULL,
    new         INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    closed      INTEGER NOT NULL DEFAULT 0,
    purged      INTEGER NOT NULL DEFAULT 0,
    requests    INTEGER NOT NULL DEFAULT 0,
    stopped     INTEGER NOT NULL DEFAULT 0,     -- 시간 예산에서 멈춤
    errors      TEXT,                           -- JSON 목록
    sites       TEXT                            -- JSON {사이트: {new, fetched, left}}
);

-- Render 사본에서 사용자가 바꾼 것 (내 PC 원본이 받아 가 반영). id = 기록 시각(µs)
CREATE TABLE IF NOT EXISTS mirror_changes (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    payload  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    query        TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    fetched      INTEGER NOT NULL DEFAULT 0,
    inserted     INTEGER NOT NULL DEFAULT 0,
    updated      INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);
"""


def configure(path: Path | str) -> None:
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        _migrate(con)


def _migrate(con: sqlite3.Connection) -> None:
    """예전에 만든 DB 에 새 열을 더한다."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(postings)")}
    if "saved" not in cols:
        con.execute("ALTER TABLE postings ADD COLUMN saved INTEGER NOT NULL DEFAULT 0")
    if "company_info" not in cols:
        con.execute("ALTER TABLE postings ADD COLUMN company_info TEXT")
    for col, typ in (("fit_score", "INTEGER NOT NULL DEFAULT 0"), ("fit_ok", "INTEGER NOT NULL DEFAULT 1"),
                     ("fit_excl", "INTEGER NOT NULL DEFAULT 0"), ("grp", "TEXT"), ("subgrp", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE postings ADD COLUMN {col} {typ}")
    con.executescript("""
        CREATE INDEX IF NOT EXISTS idx_postings_list ON postings(hidden, fit_excl, deadline, fit_score);
        CREATE INDEX IF NOT EXISTS idx_postings_fetched ON postings(fetched_at);
        CREATE INDEX IF NOT EXISTS idx_postings_src ON postings(source, source_id);
    """)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    if _db_path is None:
        raise RuntimeError("db.configure() 를 먼저 호출하세요")
    con = sqlite3.connect(_db_path, timeout=30)        # 수집 스레드·화면·동기화 복사가 겹치면 잠깐 기다림 (기본 5초는 짧음)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def path() -> Path:
    if _db_path is None:
        raise RuntimeError("db.configure() 를 먼저 호출하세요")
    return _db_path


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as con:
        con.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

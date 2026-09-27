"""토플(2026년 1월 개편) — 과제 정의, 문제 은행, 기록, 밴드 추정.

점수: 영역별 1~6 밴드(0.5 단위), 종합 = 네 영역 평균을 0.5 단위로 반올림. CEFR 과 1:1 대응.
난이도 level 1~5 는 목표 밴드 2~6 (A2~C2).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from . import db
from .content import content_files

SECTIONS = {
    "R": {"name": "Reading", "ko": "읽기", "time": "약 30분", "note": "적응형 · 35~48문항"},
    "L": {"name": "Listening", "ko": "듣기", "time": "약 29분", "note": "적응형 · 35~45문항 · 이전 문제로 못 돌아감"},
    "S": {"name": "Speaking", "ko": "말하기", "time": "약 8분", "note": "11문항: 따라 말하기 7 + 인터뷰 4"},
    "W": {"name": "Writing", "ko": "쓰기", "time": "약 23분", "note": "12문항: 문장 만들기 10 + 이메일 1 + 학술 토론 1"},
}

# kind: 화면 종류 · auto: 자동 채점 여부
TASKS = {
    "r_words": {"section": "R", "name": "빈칸 단어 완성", "en": "Complete the Words", "kind": "words", "auto": True,
                "desc": "학술 문단에서 단어의 빠진 뒷부분 철자를 채운다 (빈칸 10개)."},
    "r_daily": {"section": "R", "name": "일상 글 읽기", "en": "Read in Daily Life", "kind": "set", "auto": True,
                "desc": "공지·이메일·문자·일정표 같은 짧은 글을 읽고 2~3문항."},
    "r_academic": {"section": "R", "name": "학술 지문 읽기", "en": "Read an Academic Passage", "kind": "set", "auto": True,
                   "desc": "200단어 안팎의 학술 지문을 읽고 5문항."},
    "l_response": {"section": "L", "name": "응답 고르기", "en": "Listen and Choose a Response", "kind": "response", "auto": True,
                   "desc": "한 문장을 듣고 가장 자연스러운 대답을 고른다."},
    "l_conversation": {"section": "L", "name": "대화 듣기", "en": "Listen to a Conversation", "kind": "listen_set", "auto": True,
                       "desc": "캠퍼스·일상 대화를 듣고 2문항."},
    "l_talk": {"section": "L", "name": "안내·강의 듣기", "en": "Announcements & Academic Talks", "kind": "listen_set", "auto": True,
               "desc": "캠퍼스 안내(2문항)와 교수 강의(4문항)."},
    "s_repeat": {"section": "S", "name": "듣고 따라 말하기", "en": "Listen and Repeat", "kind": "repeat", "auto": True,
                 "desc": "안내 문장 7개를 듣고 그대로 따라 말한다 (점점 길어짐)."},
    "s_interview": {"section": "S", "name": "인터뷰", "en": "Take an Interview", "kind": "interview", "auto": False,
                    "desc": "인터뷰 질문 4개에 45초씩 답한다."},
    "w_sentence": {"section": "W", "name": "문장 만들기", "en": "Build a Sentence", "kind": "sentence", "auto": True,
                   "desc": "상대의 말에 대한 대답을 단어 덩어리로 어순에 맞게 완성한다."},
    "w_email": {"section": "W", "name": "이메일 쓰기", "en": "Write an Email", "kind": "email", "auto": False,
                "desc": "상황에 맞는 이메일을 7분 안에 쓴다 (요구 사항 3가지)."},
    "w_discussion": {"section": "W", "name": "학술 토론 글쓰기", "en": "Academic Discussion", "kind": "discussion", "auto": False,
                     "desc": "교수 질문과 학생 두 명의 글을 읽고 10분 안에 내 의견을 쓴다."},
}

CEFR = {1: "A1", 2: "A2", 3: "B1", 4: "B2", 5: "C1", 6: "C2"}


class VocabGrade:
    """토플 단어 단계 (토익 Grade 와 같은 속성 이름 — 단어 템플릿 공용)."""
    def __init__(self, level: int):
        self.level, self.band = level, level + 1
        self.name = f"밴드 {self.band}"
        self.ko = CEFR[self.band]
        self.range_text = f"CEFR {CEFR[self.band]}"
        self.low = self.high = self.band


VOCAB_GRADES = [VocabGrade(lv) for lv in range(1, 6)]
BAND_OLD = {1: "0~19", 2: "20~41", 3: "42~71", 4: "72~94", 5: "95~113", 6: "114~120"}   # 기존 0~120점 대응(참고)
LEVEL_BAND = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6}

# 쓰기·말하기 자기 평가 기준 (0~5) → 밴드 1~6
RUBRIC = [
    (5, "요구 사항을 모두 충족, 구성이 매끄럽고 어휘·문법이 다양하며 거의 오류 없음"),
    (4, "요구 사항 충족, 이해하기 쉬움. 작은 오류가 있지만 의미 전달에 문제없음"),
    (3, "대체로 충족하지만 설명이 부족하거나 오류가 눈에 띔"),
    (2, "일부만 충족, 문장이 단순하고 오류 때문에 의미가 흐려지는 곳이 있음"),
    (1, "과제와 관련은 있지만 내용이 매우 짧거나 이해하기 어려움"),
    (0, "답하지 못함 / 과제와 무관"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS toefl_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    task        TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    qidx        INTEGER NOT NULL DEFAULT 0,
    level       INTEGER NOT NULL,
    score       REAL NOT NULL,          -- 0~1 (자기 평가는 점수/5)
    response    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_toefl_task ON toefl_attempts(task, created_at);
"""


def ensure_schema() -> None:
    with db.connect() as con:
        con.executescript(SCHEMA)


class ToeflBank:
    def __init__(self, content_dir: Path):
        self.content_dir = Path(content_dir)
        self._sig = None
        self.items: dict[str, list[dict]] = {}
        self.by_id: dict[tuple[str, str], dict] = {}
        self.reload()

    def _signature(self):
        return tuple((p.name, p.stat().st_mtime_ns) for p in sorted(self.content_dir.glob("*.json")))

    def reload(self) -> None:
        self._sig = self._signature()
        items, by_id = {}, {}
        for task in TASKS:
            data = []
            for path in content_files(self.content_dir, task):
                try:
                    data += json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue                          # 작성 중인 파일은 건너뜀
            items[task] = data
            for it in data:
                by_id[(task, it["id"])] = it
        self.items, self.by_id = items, by_id
        vocab = []
        for path in content_files(self.content_dir, "vocab"):
            try:
                vocab += [dict(v) for v in json.loads(path.read_text(encoding="utf-8"))]
            except (OSError, ValueError):
                continue
        for v in vocab:
            v.setdefault("tier", "core")
        self.vocab, self.vocab_by_id = vocab, {v["id"]: v for v in vocab}

    def refresh_if_changed(self) -> None:
        if self._signature() != self._sig:
            self.reload()

    def count(self, task: str, level: int | None = None) -> int:
        return sum(1 for it in self.items.get(task, []) if not level or it["level"] == level)

    def pick(self, task: str, level: int | None, n: int, rng: random.Random | None = None) -> list[dict]:
        """안 푼 문제 → 오래전에 푼 문제 순."""
        rng = rng or random.Random()
        pool = [it for it in self.items.get(task, []) if not level or it["level"] == level]
        rng.shuffle(pool)
        seen = last_seen(task)
        pool.sort(key=lambda it: seen.get(it["id"], ""))
        return pool[:n]


# ---- 기록 --------------------------------------------------------------------------

def record(task: str, item_id: str, level: int, results: list[dict]) -> int:
    ts = db.now()
    n = 0
    with db.connect() as con:
        for r in results:
            score = max(0.0, min(1.0, float(r.get("score", 0))))
            con.execute("INSERT INTO toefl_attempts(created_at, task, item_id, qidx, level, score, response) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (ts, task, item_id, int(r.get("qidx", 0)), level, score, str(r.get("response", ""))[:5000]))
            n += 1
    return n


def last_seen(task: str) -> dict[str, str]:
    with db.connect() as con:
        return {r[0]: r[1] for r in con.execute(
            "SELECT item_id, MAX(created_at) FROM toefl_attempts WHERE task = ? GROUP BY item_id", (task,))}


def task_stats() -> dict[str, dict]:
    with db.connect() as con:
        rows = con.execute("SELECT task, COUNT(*) n, AVG(score) a FROM toefl_attempts GROUP BY task").fetchall()
    return {r["task"]: {"n": r["n"], "avg": r["a"]} for r in rows}


def recent(limit: int = 12) -> list[dict]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT task, item_id, level, MIN(created_at) at, COUNT(*) n, AVG(score) a FROM toefl_attempts "
            "GROUP BY task, item_id, created_at ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- 밴드 추정 ---------------------------------------------------------------------

def _level_acc(tasks: list[str], last_n: int = 150) -> dict[int, tuple[int, float]]:
    with db.connect() as con:
        q = ",".join("?" * len(tasks))
        rows = con.execute(f"SELECT level, score FROM toefl_attempts WHERE task IN ({q}) "
                           f"ORDER BY id DESC LIMIT ?", (*tasks, last_n)).fetchall()
    acc: dict[int, list[float]] = {}
    for r in rows:
        acc.setdefault(r["level"], []).append(r["score"])
    return {lv: (len(v), sum(v) / len(v)) for lv, v in acc.items()}


def band_from_levels(acc: dict[int, tuple[int, float]], min_n: int = 4) -> float | None:
    """난이도 L(=목표 밴드 L+1)에서 65% 이상이면 그 밴드, 45% 이상이면 반 밴드 아래로 본다."""
    if not acc or sum(n for n, _ in acc.values()) < min_n:
        return None
    est = 1.0
    for lv in range(1, 6):
        n, a = acc.get(lv, (0, 0.0))
        if n < 2:
            continue
        if a >= 0.65:
            est = max(est, LEVEL_BAND[lv])
        elif a >= 0.45:
            est = max(est, LEVEL_BAND[lv] - 0.5)
    return est


def self_band(tasks: list[str], last_n: int = 20) -> float | None:
    """자기 평가 평균(0~5) → 밴드 1~6."""
    with db.connect() as con:
        q = ",".join("?" * len(tasks))
        rows = con.execute(f"SELECT score FROM toefl_attempts WHERE task IN ({q}) ORDER BY id DESC LIMIT ?",
                           (*tasks, last_n)).fetchall()
    if not rows:
        return None
    return round((1 + 5 * sum(r[0] for r in rows) / len(rows)) * 2) / 2


def section_bands() -> dict[str, float | None]:
    auto = {s: [t for t, v in TASKS.items() if v["section"] == s and v["auto"]] for s in SECTIONS}
    manual = {s: [t for t, v in TASKS.items() if v["section"] == s and not v["auto"]] for s in SECTIONS}
    out = {}
    for s in SECTIONS:
        parts = []
        if auto[s]:
            b = band_from_levels(_level_acc(auto[s]))
            if b is not None:
                parts.append(b)
        if manual[s]:
            b = self_band(manual[s])
            if b is not None:
                parts.append(b)
        out[s] = round(sum(parts) / len(parts) * 2) / 2 if parts else None
    return out


def overall_band(bands: dict[str, float | None]) -> float | None:
    vals = [b for b in bands.values() if b is not None]
    if len(vals) < 4:
        return None
    return round(sum(vals) / 4 * 2) / 2                 # 실제 시험: 네 영역 평균을 0.5 단위로 반올림


def cefr(band: float | None) -> str:
    return CEFR.get(int(band), "") if band else ""


# ---- 실전 모의고사 ------------------------------------------------------------------
# 읽기·듣기는 2단계 적응형: 1모듈(중간 난이도) 정답률이 ADAPT_CUT 이상이면 2모듈은 어려운 문제, 아니면 쉬운 문제.
ADAPT_CUT = 0.6
M1_LEVELS = [3]            # 밴드 4
HARD_LEVELS = [4, 5]       # 밴드 5~6
EASY_LEVELS = [1, 2]       # 밴드 2~3

# (과제, 개수, 담화 종류) — 실제 시험 문항 수를 줄여 한 모듈 15분 안팎
READ_MODULE = [("r_words", 1, None), ("r_daily", 2, None), ("r_academic", 1, None)]
LISTEN_MODULE = [("l_response", 6, None), ("l_conversation", 1, None), ("l_talk", 1, "announcement"),
                 ("l_talk", 1, "academic")]
MOCK_ORDER = ["R", "L", "S", "W"]       # 개편 시험 순서 (자료에 따라 S·W 순서가 다르게 소개되기도 함)

MOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS toefl_mocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    plan        TEXT NOT NULL,
    result      TEXT,
    r REAL, l REAL, s REAL, w REAL, total REAL
);
"""


def ensure_mock_schema() -> None:
    with db.connect() as con:
        con.executescript(MOCK_SCHEMA)


def _pick_kind(bank: ToeflBank, task: str, levels: list[int], n: int, kind: str | None,
               used: set, rng: random.Random) -> list[dict]:
    seen = last_seen(task)
    ok = lambda it: it["id"] not in used and (kind is None or it.get("kind") == kind)
    pool = [it for it in bank.items.get(task, []) if it["level"] in levels and ok(it)]
    rng.shuffle(pool)
    pool.sort(key=lambda it: seen.get(it["id"], ""))
    if len(pool) < n:                    # 모자라면 가까운 난이도로 채움
        rest = [it for it in bank.items.get(task, []) if ok(it) and it not in pool]
        rest.sort(key=lambda it: min(abs(it["level"] - lv) for lv in levels))
        pool += rest
    out = pool[:n]
    used.update(it["id"] for it in out)
    return out


def _module(bank, spec, levels, used, rng) -> list[dict]:
    items = []
    for task, n, kind in spec:
        for it in _pick_kind(bank, task, levels, n, kind, used, rng):
            items.append({"task": task, "item": it})
    return items


def build_mock(bank: ToeflBank, target: float, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    used: set = set()
    lv = max(1, min(5, int(target + 0.5) - 1))          # 말하기·쓰기 난이도 = 목표 밴드 근처
    sections = {
        "R": {"name": "Reading", "minutes_per_module": 15, "modules": [
            _module(bank, READ_MODULE, M1_LEVELS, used, rng),
            {"hard": _module(bank, READ_MODULE, HARD_LEVELS, used, rng),
             "easy": _module(bank, READ_MODULE, EASY_LEVELS, used, rng)}]},
        "L": {"name": "Listening", "minutes_per_module": 14, "modules": [
            _module(bank, LISTEN_MODULE, M1_LEVELS, used, rng),
            {"hard": _module(bank, LISTEN_MODULE, HARD_LEVELS, used, rng),
             "easy": _module(bank, LISTEN_MODULE, EASY_LEVELS, used, rng)}]},
        "S": {"name": "Speaking", "items": _module(bank, [("s_repeat", 1, None), ("s_interview", 1, None)], [lv], used, rng)},
        "W": {"name": "Writing", "items": _module(bank, [("w_sentence", 10, None)], [max(1, lv - 1), lv, min(5, lv + 1)], used, rng)
              + _module(bank, [("w_email", 1, None), ("w_discussion", 1, None)], [lv], used, rng)},
    }
    return {"order": MOCK_ORDER, "sections": sections, "adapt_cut": ADAPT_CUT, "target": target, "level": lv}


def create_mock(bank: ToeflBank, target: float) -> int:
    plan = build_mock(bank, target)
    with db.connect() as con:
        cur = con.execute("INSERT INTO toefl_mocks(created_at, plan) VALUES (?, ?)",
                          (db.now(), json.dumps(plan, ensure_ascii=False)))
        return int(cur.lastrowid)


def get_mock(mid: int) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM toefl_mocks WHERE id = ?", (mid,)).fetchone()
    if not r:
        return None
    m = dict(r)
    m["plan"] = json.loads(m["plan"])
    m["result"] = json.loads(m["result"]) if m["result"] else None
    return m


def list_mocks(limit: int = 20) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, created_at, finished_at, r, l, s, w, total FROM toefl_mocks WHERE finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (limit,))]


def finish_mock(bank: ToeflBank, mid: int, payload: dict) -> dict:
    """payload = {"items": [{"task", "item_id", "results": [{qidx, score, response}]}], "routes": {"R": "hard"}}.
    풀이 기록을 저장하고(연습 밴드에도 반영) 이 시험만으로 영역별 밴드를 계산한다."""
    m = get_mock(mid)
    if not m:
        raise ValueError("모의고사가 없습니다.")
    if m["finished_at"]:
        return m
    auto: dict[str, dict[int, list[float]]] = {s: {} for s in SECTIONS}
    selfs: dict[str, list[float]] = {s: [] for s in SECTIONS}
    detail = []
    for row in payload.get("items", []) or []:
        task, iid = str(row.get("task")), str(row.get("item_id"))
        it = bank.by_id.get((task, iid))
        results = row.get("results") or []
        if not it or task not in TASKS or not isinstance(results, list) or not results:
            continue
        record(task, iid, it["level"], results)
        sec = TASKS[task]["section"]
        sc = [max(0.0, min(1.0, float(r.get("score", 0)))) for r in results]
        if TASKS[task]["auto"]:
            auto[sec].setdefault(it["level"], []).extend(sc)
        else:
            selfs[sec].extend(sc)
        detail.append({"task": task, "item_id": iid, "level": it["level"], "avg": sum(sc) / len(sc), "n": len(sc)})
    bands = {}
    for s in SECTIONS:
        parts = []
        if auto[s]:
            b = band_from_levels({lvl: (len(v), sum(v) / len(v)) for lvl, v in auto[s].items()}, min_n=1)
            if b is not None:
                parts.append(b)
        if selfs[s]:
            parts.append(round((1 + 5 * sum(selfs[s]) / len(selfs[s])) * 2) / 2)
        bands[s] = round(sum(parts) / len(parts) * 2) / 2 if parts else None
    total = overall_band(bands)
    result = {"bands": bands, "total": total, "routes": payload.get("routes") or {}, "detail": detail,
              "duration_sec": payload.get("duration_sec")}
    with db.connect() as con:
        con.execute("UPDATE toefl_mocks SET finished_at = ?, result = ?, r = ?, l = ?, s = ?, w = ?, total = ? WHERE id = ?",
                    (db.now(), json.dumps(result, ensure_ascii=False), bands["R"], bands["L"], bands["S"], bands["W"], total, mid))
    return get_mock(mid)

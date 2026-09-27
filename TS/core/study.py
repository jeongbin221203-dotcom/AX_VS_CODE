"""풀이 세션: 문제 구성 → 채점 → 기록(풀이·오답노트) → 결과 집계."""
from __future__ import annotations

import json
import random
from datetime import datetime

from . import db, scoring
from .content import LC_PARTS, PART_INFO, Bank, split_ref

WRONG_CLEAR_STREAK = 2          # 오답노트 문항을 연속 2번 맞히면 졸업


class StudyError(ValueError):
    pass


# ---- 세션 만들기 ------------------------------------------------------------------

def _attempted_refs(part: int) -> set[str]:
    with db.connect() as con:
        rows = con.execute("SELECT DISTINCT item_id FROM attempts WHERE part = ?", (part,)).fetchall()
    return {f"{part}:{r['item_id']}" for r in rows}


def _create(mode: str, refs: list[str], *, variant: str | None = None, part: int | None = None,
            level: int | None = None, time_limit: int | None = None) -> int:
    if not refs:
        raise StudyError("조건에 맞는 문제가 없습니다. 등급이나 유형 조건을 바꿔 보세요.")
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO sessions(created_at, mode, variant, part, level, items, time_limit) VALUES (?,?,?,?,?,?,?)",
            (db.now(), mode, variant, part, level, json.dumps(refs), time_limit))
        return int(cur.lastrowid)


def start_practice(bank: Bank, part: int, level: int | None, qtype: str | None, n: int,
                   rng: random.Random | None = None) -> int:
    if part not in PART_INFO:
        raise StudyError("알 수 없는 파트입니다.")
    n = max(1, min(n, 100))
    refs = bank.pick(part, n, [level] if level else None, qtype or None, rng=rng,
                     prefer_fresh=_attempted_refs(part))
    return _create("practice", refs, variant=qtype or None, part=part, level=level)


def _pick_mixed(bank: Bank, part: int, n: int, rng: random.Random, used: set[str]) -> list[str]:
    """등급 분포(MOCK_LEVEL_MIX)대로 n문항 가까이 뽑는다. 모자라면 다른 등급으로 채운다."""
    per_item = {3: 3, 4: 3, 6: 4}.get(part, 1)          # 세트형 파트는 세트 단위로 나눈다
    units = max(1, round(n / per_item))
    raw = {lv: units * share for lv, share in scoring.MOCK_LEVEL_MIX.items()}
    alloc = {lv: int(v) for lv, v in raw.items()}
    for lv in sorted(raw, key=lambda k: raw[k] - alloc[k], reverse=True)[:units - sum(alloc.values())]:
        alloc[lv] += 1                                    # 최대 나머지 방식
    out: list[str] = []
    got = 0
    for lv, k in alloc.items():
        if k <= 0:
            continue
        refs = bank.pick(part, k * per_item, [lv], exclude=used, rng=rng)
        for r in refs:
            used.add(r)
            got += len(bank.questions(bank.item(r)))
        out += refs
    if got < n:
        more = bank.pick(part, n - got, None, exclude=used, rng=rng)
        used.update(more)
        out += more
    out.sort(key=lambda r: bank.item(r)["level"])                  # 실제 시험처럼 쉬운 문제부터
    return out


def start_mock(bank: Bank, form_key: str, rng: random.Random | None = None) -> int:
    form = scoring.MOCK_FORMS.get(form_key)
    if not form:
        raise StudyError("알 수 없는 모의고사 종류입니다.")
    rng = rng or random.Random()
    used: set[str] = set()
    refs: list[str] = []
    for part in (1, 2, 3, 4, 5, 6):
        refs += _pick_mixed(bank, part, form[f"p{part}"], rng, used)
    p7 = bank.pick_p7(form["p7_single"], form["p7_double"], form["p7_triple"], None, rng)
    refs += sorted(p7[:], key=lambda r: ({"single": 0, "double": 1, "triple": 2}[bank.item(r)["kind"]],
                                         bank.item(r)["level"]))
    return _create("mock", refs, variant=form_key, time_limit=form["rc_minutes"] * 60)


def start_diagnostic(bank: Bank, rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    refs: list[str] = []
    used: set[str] = set()
    for part, per_level in scoring.DIAGNOSTIC_FORM.items():
        for lv, n in per_level.items():
            picked = bank.pick(part, n, [lv], exclude=used, rng=rng)
            used.update(picked)
            refs += picked
    return _create("diagnostic", refs, time_limit=12 * 60)


def start_review(bank: Bank, part: int | None, n: int, rng: random.Random | None = None) -> int:
    """오답노트에서 열려 있는 문항이 속한 문제를 다시 낸다 (오래 안 본 것부터)."""
    sql = "SELECT part, item_id, MIN(last_seen_at) AS seen FROM wrong_notes WHERE status = 'open'"
    args: list = []
    if part:
        sql += " AND part = ?"
        args.append(part)
    sql += " GROUP BY part, item_id ORDER BY seen"
    with db.connect() as con:
        rows = con.execute(sql, args).fetchall()
    refs, count = [], 0
    for r in rows:
        ref = f"{r['part']}:{r['item_id']}"
        item = bank.item(ref)
        if item is None:
            continue
        refs.append(ref)
        count += len(bank.questions(item))
        if count >= n:
            break
    refs.sort(key=lambda r: split_ref(r)[0])
    return _create("review", refs, part=part)


# ---- 세션 조회 ---------------------------------------------------------------------

def get_session(sid: int) -> dict | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    s["items"] = json.loads(s["items"])
    return s


def session_attempts(sid: int) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM attempts WHERE session_id = ? ORDER BY id", (sid,))]


# ---- 채점 -------------------------------------------------------------------------

def _record(con, sid: int, q, chosen: int, elapsed_ms: int | None, ts: str) -> bool:
    ok = chosen == q.answer
    con.execute(
        "INSERT INTO attempts(session_id, qkey, part, item_id, qidx, level, qtype, chosen, correct, elapsed_ms, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, q.qkey, q.part, q.item_id, q.qidx, q.level, q.qtype, chosen, int(ok), elapsed_ms, ts))
    note = con.execute("SELECT * FROM wrong_notes WHERE qkey = ?", (q.qkey,)).fetchone()
    if not ok:
        if note:
            con.execute("UPDATE wrong_notes SET wrong_count = wrong_count + 1, right_streak = 0, status = 'open', "
                        "last_wrong_at = ?, last_seen_at = ? WHERE qkey = ?", (ts, ts, q.qkey))
        else:
            con.execute("INSERT INTO wrong_notes(qkey, part, item_id, qidx, level, qtype, wrong_count, "
                        "first_wrong_at, last_wrong_at, last_seen_at) VALUES (?,?,?,?,?,?,1,?,?,?)",
                        (q.qkey, q.part, q.item_id, q.qidx, q.level, q.qtype, ts, ts, ts))
    elif note:
        streak = note["right_streak"] + 1
        status = "cleared" if streak >= WRONG_CLEAR_STREAK else note["status"]
        con.execute("UPDATE wrong_notes SET right_streak = ?, status = ?, last_seen_at = ? WHERE qkey = ?",
                    (streak, status, ts, q.qkey))
    return ok


def _parse_answers(raw) -> dict[int, tuple[int, int | None]]:
    out: dict[int, tuple[int, int | None]] = {}
    if not isinstance(raw, list):
        raise StudyError("답안 형식이 올바르지 않습니다.")
    for a in raw:
        try:
            qidx, chosen = int(a["qidx"]), int(a.get("chosen", -1))
            ms = a.get("elapsed_ms")
            ms = int(ms) if ms is not None else None
        except (KeyError, TypeError, ValueError):
            raise StudyError("답안 형식이 올바르지 않습니다.") from None
        out[qidx] = (chosen, ms)
    return out


def grade_item(bank: Bank, sid: int, ref: str, raw_answers) -> dict:
    """연습·복습: 문제 하나(세트)를 채점하고 정답·해설을 돌려준다. 같은 문제를 두 번 채점하지 않는다."""
    s = get_session(sid)
    if not s:
        raise StudyError("세션이 없습니다.")
    if ref not in s["items"]:
        raise StudyError("이 세션의 문제가 아닙니다.")
    item = bank.item(ref)
    if item is None:
        raise StudyError("문제 데이터가 없습니다.")
    answers = _parse_answers(raw_answers)
    part, iid = split_ref(ref)
    ts = db.now()
    results = []
    with db.connect() as con:
        done = con.execute("SELECT qidx, chosen, correct FROM attempts WHERE session_id = ? AND part = ? AND item_id = ?",
                           (sid, part, iid)).fetchall()
        if done:
            prev = {r["qidx"]: r for r in done}
            results = [{"qidx": q.qidx, "chosen": prev[q.qidx]["chosen"], "correct": bool(prev[q.qidx]["correct"])}
                       for q in bank.questions(item) if q.qidx in prev]
        else:
            for q in bank.questions(item):
                chosen, ms = answers.get(q.qidx, (-1, None))
                results.append({"qidx": q.qidx, "chosen": chosen, "correct": _record(con, sid, q, chosen, ms, ts)})
    out = bank.reveal(ref)
    out["results"] = results
    return out


def submit_session(bank: Bank, sid: int, payload: dict) -> dict:
    """모의고사·진단: 전체 답안을 한 번에 받아 채점. 연습·복습은 남은 문제만 마감한다."""
    s = get_session(sid)
    if not s:
        raise StudyError("세션이 없습니다.")
    if s["finished_at"]:
        return s
    items = payload.get("items") or {}
    if not isinstance(items, dict):
        raise StudyError("답안 형식이 올바르지 않습니다.")
    ts = db.now()
    with db.connect() as con:
        graded = {(r["part"], r["item_id"]) for r in
                  con.execute("SELECT DISTINCT part, item_id FROM attempts WHERE session_id = ?", (sid,))}
        if s["mode"] in ("mock", "diagnostic"):
            for ref in s["items"]:
                part, iid = split_ref(ref)
                item = bank.item(ref)
                if item is None or (part, iid) in graded:
                    continue
                answers = _parse_answers(items.get(ref, []))
                for q in bank.questions(item):
                    chosen, ms = answers.get(q.qidx, (-1, None))
                    _record(con, sid, q, chosen, ms, ts)
    return finish_session(bank, sid, duration_sec=payload.get("duration_sec"))


def finish_session(bank: Bank, sid: int, duration_sec=None) -> dict:
    rows = session_attempts(sid)
    s = get_session(sid)
    lc = [(r["level"], bool(r["correct"])) for r in rows if r["part"] in LC_PARTS]
    rc = [(r["level"], bool(r["correct"])) for r in rows if r["part"] not in LC_PARTS]
    est = {"lc_est": None, "rc_est": None, "total_est": None}
    if s["mode"] in ("mock", "diagnostic"):
        est = scoring.estimate(lc, rc)
    try:
        dur = int(duration_sec) if duration_sec is not None else None
    except (TypeError, ValueError):
        dur = None
    if dur is None:
        start = datetime.fromisoformat(s["created_at"])
        dur = int((datetime.now() - start).total_seconds())
    with db.connect() as con:
        con.execute(
            "UPDATE sessions SET finished_at = ?, total = ?, correct = ?, lc_total = ?, lc_correct = ?, "
            "rc_total = ?, rc_correct = ?, lc_est = ?, rc_est = ?, total_est = ?, duration_sec = ? WHERE id = ?",
            (db.now(), len(rows), sum(r["correct"] for r in rows), len(lc), sum(ok for _, ok in lc),
             len(rc), sum(ok for _, ok in rc), est["lc_est"], est["rc_est"], est["total_est"], dur, sid))
    return get_session(sid)


def delete_unfinished_empty() -> None:
    """한 문제도 풀지 않고 버려진 세션 정리 (하루 지난 것)."""
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE finished_at IS NULL AND created_at < datetime('now', '-1 day', 'localtime') "
                    "AND id NOT IN (SELECT DISTINCT session_id FROM attempts)")


# ---- 오답노트 ----------------------------------------------------------------------

def wrong_notes(status: str = "open", part: int | None = None, qtype: str | None = None) -> list[dict]:
    sql = "SELECT * FROM wrong_notes WHERE status = ?"
    args: list = [status]
    if part:
        sql += " AND part = ?"
        args.append(part)
    if qtype:
        sql += " AND qtype = ?"
        args.append(qtype)
    sql += " ORDER BY last_wrong_at DESC"
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, args)]


def set_note_memo(qkey: str, memo: str) -> None:
    with db.connect() as con:
        con.execute("UPDATE wrong_notes SET memo = ? WHERE qkey = ?", (memo[:2000], qkey))


def set_note_status(qkey: str, status: str) -> None:
    if status not in ("open", "cleared"):
        raise StudyError("상태 값 오류")
    with db.connect() as con:
        con.execute("UPDATE wrong_notes SET status = ?, right_streak = 0 WHERE qkey = ?", (status, qkey))

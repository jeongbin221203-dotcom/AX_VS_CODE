"""학습 통계 (정답률·풀이 시간·점수 추이·연속 학습일)."""
from __future__ import annotations

from datetime import date, timedelta

from . import db
from .content import PART_INFO


def part_accuracy(days: int | None = None, last_n: int | None = None) -> dict[int, dict]:
    """파트별 정답률. last_n 이면 파트마다 최근 n문항만."""
    out = {}
    with db.connect() as con:
        for part in PART_INFO:
            sql = "SELECT correct, elapsed_ms FROM attempts WHERE part = ? AND chosen >= 0"
            args: list = [part]
            if days:
                sql += " AND created_at >= ?"
                args.append((date.today() - timedelta(days=days - 1)).isoformat())
            sql += " ORDER BY id DESC"
            if last_n:
                sql += f" LIMIT {int(last_n)}"
            rows = con.execute(sql, args).fetchall()
            n = len(rows)
            times = [r["elapsed_ms"] for r in rows if r["elapsed_ms"]]
            out[part] = {
                "n": n,
                "correct": sum(r["correct"] for r in rows),
                "rate": (sum(r["correct"] for r in rows) / n) if n else None,
                "avg_sec": (sum(times) / len(times) / 1000) if times else None,
            }
    return out


def level_accuracy() -> dict[tuple[int, int], dict]:
    """(파트, 등급)별 정답률."""
    with db.connect() as con:
        rows = con.execute("SELECT part, level, COUNT(*) n, SUM(correct) c FROM attempts WHERE chosen >= 0 GROUP BY part, level").fetchall()
    return {(r["part"], r["level"]): {"n": r["n"], "rate": r["c"] / r["n"]} for r in rows}


def type_accuracy(min_n: int = 3) -> list[dict]:
    """유형별 정답률 (낮은 순)."""
    with db.connect() as con:
        rows = con.execute("SELECT part, qtype, COUNT(*) n, SUM(correct) c FROM attempts WHERE chosen >= 0 "
                           "GROUP BY part, qtype HAVING COUNT(*) >= ?", (min_n,)).fetchall()
    out = [{"part": r["part"], "qtype": r["qtype"], "n": r["n"], "rate": r["c"] / r["n"]} for r in rows]
    out.sort(key=lambda x: (x["rate"], -x["n"]))
    return out


def score_history(limit: int = 30) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, finished_at, mode, variant, lc_est, rc_est, total_est FROM sessions "
                           "WHERE total_est IS NOT NULL ORDER BY finished_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def latest_estimate() -> dict | None:
    h = score_history(1)
    return h[-1] if h else None


def daily_counts(days: int = 30) -> list[dict]:
    start = date.today() - timedelta(days=days - 1)
    with db.connect() as con:
        q = {r["d"]: (r["n"], r["c"]) for r in con.execute(
            "SELECT substr(created_at, 1, 10) d, COUNT(*) n, SUM(correct) c FROM attempts "
            "WHERE chosen >= 0 AND created_at >= ? GROUP BY d", (start.isoformat(),))}
        v = {r["d"]: r["n"] for r in con.execute(
            "SELECT substr(reviewed_at, 1, 10) d, COUNT(*) n FROM vocab_log WHERE reviewed_at >= ? GROUP BY d",
            (start.isoformat(),))}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        n, c = q.get(d, (0, 0))
        out.append({"date": d, "questions": n, "correct": c or 0, "words": v.get(d, 0)})
    return out


def today_counts() -> dict:
    d = date.today().isoformat()
    with db.connect() as con:
        per_part = {r["part"]: r["n"] for r in con.execute(
            "SELECT part, COUNT(*) n FROM attempts WHERE chosen >= 0 AND substr(created_at, 1, 10) = ? GROUP BY part", (d,))}
        words = con.execute("SELECT COUNT(*) FROM vocab_log WHERE substr(reviewed_at, 1, 10) = ?", (d,)).fetchone()[0]
        reviewed = con.execute("SELECT COUNT(*) FROM attempts a JOIN sessions s ON s.id = a.session_id "
                               "WHERE s.mode = 'review' AND a.chosen >= 0 AND substr(a.created_at, 1, 10) = ?", (d,)).fetchone()[0]
    return {"per_part": per_part, "questions": sum(per_part.values()), "words": words, "reviewed": reviewed}


def streak() -> int:
    """오늘(또는 어제)까지 이어진 연속 학습일."""
    with db.connect() as con:
        days = {r[0] for r in con.execute(
            "SELECT DISTINCT substr(created_at, 1, 10) FROM attempts UNION "
            "SELECT DISTINCT substr(reviewed_at, 1, 10) FROM vocab_log")}
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def recent_sessions(limit: int = 10) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM sessions WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT ?", (limit,))]


def time_by_part() -> dict[int, float | None]:
    """RC 파트별 문항당 평균 풀이 시간(초), 최근 100문항."""
    acc = part_accuracy(last_n=100)
    return {p: acc[p]["avg_sec"] for p in (5, 6, 7)}

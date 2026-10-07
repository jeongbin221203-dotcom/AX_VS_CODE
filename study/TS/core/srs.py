"""단어 간격 반복 (SM-2 변형).

평가: 0 다시(모름) · 3 어려움 · 4 보통 · 5 쉬움
- 모름: 반복 횟수 0으로, 내일 다시. (학습 화면에서는 같은 회차 끝에 한 번 더 보여 준다)
- 알면: 1일 → 4일 → 이전 간격 × 쉬움 계수(EF). '쉬움'은 간격을 1.3배 더 늘린다.
- 간격 21일 이상 = 암기 완료로 본다.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db
from .content import Bank

GRADES = {0: "다시", 3: "어려움", 4: "보통", 5: "쉬움"}
MASTERED_DAYS = 21


def schedule(ef: float, interval: int, reps: int, grade: int) -> tuple[float, int, int]:
    """(ef, interval, reps) → 다음 (ef, interval, reps)."""
    if grade not in GRADES:
        raise ValueError("grade must be one of 0, 3, 4, 5")
    if grade < 3:
        reps, interval = 0, 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 4
        else:
            interval = max(interval + 1, round(interval * ef))
        if grade == 5:
            interval = max(interval + 1, round(interval * 1.3))
    ef = max(1.3, ef + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    return round(ef, 3), interval, reps


def review(word_id: str, grade: int, today: date | None = None) -> dict:
    today = today or date.today()
    ts = db.now()
    with db.connect() as con:
        card = con.execute("SELECT * FROM vocab_cards WHERE word_id = ?", (word_id,)).fetchone()
        was_new = card is None
        ef, interval, reps, lapses = (2.5, 0, 0, 0) if was_new else \
            (card["ef"], card["interval"], card["reps"], card["lapses"])
        ef, interval, reps = schedule(ef, interval, reps, grade)
        if grade < 3:                       # 처음 보는 단어를 틀려도 '본 카드'로 세어 내일 복습에 들어가게 한다
            lapses += 1
        due = (today + timedelta(days=interval)).isoformat()
        con.execute(
            "INSERT INTO vocab_cards(word_id, ef, interval, reps, lapses, due, first_seen, last_review) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(word_id) DO UPDATE SET ef = excluded.ef, interval = excluded.interval, "
            "reps = excluded.reps, lapses = excluded.lapses, due = excluded.due, last_review = excluded.last_review",
            (word_id, ef, interval, reps, lapses, due, ts, ts))
        con.execute("INSERT INTO vocab_log(word_id, grade, was_new, reviewed_at) VALUES (?,?,?,?)",
                    (word_id, grade, int(was_new), ts))
    return {"word_id": word_id, "interval": interval, "due": due, "ef": ef}


WEAK_MIN_FAILS = 2          # 이만큼 틀리면(카드 '다시' + 퀴즈 오답) '자주 잊는 단어'


def quiz_answer(word_id: str, correct: bool) -> dict:
    """퀴즈 한 문제 기록. 틀리면 복습 카드에 '다시'로 넣어 내일 다시 나오게 한다."""
    with db.connect() as con:
        con.execute("INSERT INTO vocab_quiz_log(word_id, correct, answered_at) VALUES (?,?,?)",
                    (word_id, int(bool(correct)), db.now()))
    if correct:
        return {"scheduled": False}
    r = review(word_id, 0)
    return {"scheduled": True, "due": r["due"]}


def fail_counts() -> dict[str, int]:
    """단어별 틀린 횟수 (카드에서 '다시' + 퀴즈 오답; 퀴즈 오답은 카드 기록에도 남으므로 카드 기록만 센다)."""
    with db.connect() as con:
        return {r[0]: r[1] for r in con.execute(
            "SELECT word_id, COUNT(*) FROM vocab_log WHERE grade = 0 GROUP BY word_id")}


def recently_missed() -> set[str]:
    """마지막으로 본 결과가 '틀림'인 단어 (카드 '다시' 또는 퀴즈 오답 뒤에 아직 못 맞힌 단어)."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT word_id, at, ok FROM ("
            " SELECT word_id, reviewed_at AS at, grade > 0 AS ok FROM vocab_log"
            " UNION ALL SELECT word_id, answered_at AS at, correct AS ok FROM vocab_quiz_log"
            ") ORDER BY at").fetchall()
    last: dict[str, int] = {}
    for wid, _, ok in rows:
        last[wid] = ok
    return {w for w, ok in last.items() if not ok}


def toggle_star(word_id: str) -> bool:
    with db.connect() as con:
        card = con.execute("SELECT starred FROM vocab_cards WHERE word_id = ?", (word_id,)).fetchone()
        if card is None:
            ts = db.now()
            con.execute("INSERT INTO vocab_cards(word_id, due, first_seen, last_review, starred) VALUES (?,?,?,?,1)",
                        (word_id, date.today().isoformat(), ts, ts))
            return True
        new = 0 if card["starred"] else 1
        con.execute("UPDATE vocab_cards SET starred = ? WHERE word_id = ?", (new, word_id))
        return bool(new)


def cards() -> dict[str, dict]:
    with db.connect() as con:
        return {r["word_id"]: dict(r) for r in con.execute("SELECT * FROM vocab_cards")}


def new_learned_today(today: date | None = None, ids: set | None = None) -> int:
    """오늘 새로 본 단어 수. ids 를 주면 그 단어들만 (토익·토플 한도를 따로 센다)."""
    today = today or date.today()
    with db.connect() as con:
        rows = con.execute("SELECT word_id FROM vocab_log WHERE was_new = 1 AND substr(reviewed_at, 1, 10) = ?",
                           (today.isoformat(),)).fetchall()
    return sum(1 for r in rows if ids is None or r[0] in ids)


def queue(bank: Bank, level: int | None, daily_new: int, starred_only: bool = False,
          today: date | None = None, tier: str | None = None, start_level: int | None = None) -> dict:
    """오늘 볼 카드: 복습 예정(due) 먼저, 그다음 새 단어(하루 한도까지)."""
    today = today or date.today()
    cs = cards()
    words = [w for w in bank.vocab if (not level or w["level"] == level) and (not tier or w["tier"] == tier)]
    # 새 단어는 낮은 등급·필수 단어부터. start_level 을 주면 그 등급부터(낮은 등급은 그 뒤로) — 700점대가 1등급 단어부터 나오지 않게
    words.sort(key=lambda w: (bool(start_level) and w["level"] < start_level, w["level"], w["tier"] != "core"))
    due = [w for w in words if w["id"] in cs and cs[w["id"]]["reps"] + cs[w["id"]]["lapses"] > 0
           and cs[w["id"]]["due"] <= today.isoformat() and (not starred_only or cs[w["id"]]["starred"])]
    due.sort(key=lambda w: cs[w["id"]]["due"])
    new_left = max(0, daily_new - new_learned_today(today, set(bank.vocab_by_id)))
    fresh = [] if starred_only else \
        [w for w in words if w["id"] not in cs or cs[w["id"]]["reps"] + cs[w["id"]]["lapses"] == 0][:new_left]
    if starred_only:
        due = [w for w in words if w["id"] in cs and cs[w["id"]]["starred"]]
    return {"due": due, "new": fresh, "new_left": new_left}


def level_progress(bank: Bank, today: date | None = None) -> list[dict]:
    today = today or date.today()
    cs = cards()
    out = []
    for lv in range(1, 6):
        words = [w for w in bank.vocab if w["level"] == lv]
        seen = [cs[w["id"]] for w in words if w["id"] in cs and cs[w["id"]]["reps"] + cs[w["id"]]["lapses"] > 0]
        tiers = {}
        for t in ("core", "stretch"):
            tw = [w for w in words if w["tier"] == t]
            tc = [cs[w["id"]] for w in tw if w["id"] in cs and cs[w["id"]]["reps"] + cs[w["id"]]["lapses"] > 0]
            tiers[t] = {"total": len(tw), "seen": len(tc), "mastered": sum(1 for c in tc if c["interval"] >= MASTERED_DAYS)}
        out.append({
            "level": lv, "tiers": tiers,
            "total": len(words),
            "seen": len(seen),
            "mastered": sum(1 for c in seen if c["interval"] >= MASTERED_DAYS),
            "due": sum(1 for c in seen if c["due"] <= today.isoformat()),
        })
    return out

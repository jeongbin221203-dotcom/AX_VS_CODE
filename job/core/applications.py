"""지원 현황. 자동 제출은 하지 않고, 원문 링크로 지원한 뒤 상태·일정·메모를 기록한다."""
from __future__ import annotations

from datetime import date

from . import db

STATUSES = ["관심", "지원 예정", "지원 완료", "서류 합격", "면접", "최종 합격", "불합격", "포기"]
ACTIVE = ["관심", "지원 예정", "지원 완료", "서류 합격", "면접"]
DONE = ["최종 합격", "불합격", "포기"]


def upsert(pid: int, status: str, memo: str | None = None, applied_at: str | None = None,
           next_at: str | None = None, note: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태: {status}")
    now = db.now()
    with db.connect() as con:
        old = con.execute("SELECT * FROM applications WHERE posting_id = ?", (pid,)).fetchone()
        if status == "지원 완료" and not applied_at and not (old and old["applied_at"]):
            applied_at = date.today().isoformat()
        if old:
            con.execute("UPDATE applications SET status = ?, memo = COALESCE(?, memo), "
                        "applied_at = COALESCE(?, applied_at), next_at = ?, updated_at = ? WHERE posting_id = ?",
                        (status, memo, applied_at or None, next_at or None, now, pid))
        else:
            con.execute("INSERT INTO applications(posting_id, status, memo, applied_at, next_at, updated_at) "
                        "VALUES(?, ?, ?, ?, ?, ?)", (pid, status, memo or "", applied_at or None, next_at or None, now))
        if not old or old["status"] != status or note:
            con.execute("INSERT INTO application_events(posting_id, status, note, created_at) VALUES(?, ?, ?, ?)",
                        (pid, status, note, now))


def remove(pid: int) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM applications WHERE posting_id = ?", (pid,))
        con.execute("INSERT INTO application_events(posting_id, status, note, created_at) VALUES(?, '삭제', '', ?)",
                    (pid, db.now()))


def events(pid: int) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM application_events WHERE posting_id = ? ORDER BY id DESC", (pid,))]


def board() -> dict[str, list[dict]]:
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT a.*, p.title, p.company, p.url, p.deadline, p.sido, p.salary_min, p.salary_max, "
            "p.salary_negotiable, p.career_type FROM applications a JOIN postings p ON p.id = a.posting_id "
            "ORDER BY COALESCE(p.deadline, '9999-12-31'), a.updated_at DESC")]
    today = date.today()
    out = {s: [] for s in STATUSES}
    for r in rows:
        r["dday"] = (date.fromisoformat(r["deadline"]) - today).days if r["deadline"] else None
        out.setdefault(r["status"], []).append(r)
    return out


def counts() -> dict[str, int]:
    with db.connect() as con:
        return {r["status"]: r["n"] for r in con.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status")}

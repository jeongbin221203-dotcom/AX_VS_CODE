"""대결(결재 위임): 휴가·출장 동안 다른 사람이 내 결재를 대신 한다.

- 기간(시작~끝, 한국 날짜) 동안 to_user 는 from_user 의 결재 권한(역할·창고 범위)을 함께 갖는다.
- 대신 결재하면 결재 기록·감사로그에 '홍길동 (대결: 김팀장)'으로 남는다. 요청자 본인 결재 금지 등 직무 분리는 그대로.
- 본인이 자기 결재를 맡길 수 있고, 시스템관리자는 누구의 결재든 맡길 수 있다. 끝나기 전에 취소할 수 있다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from core import audit, auth, db, services
from core.utils import now_str


def _today() -> str:
    return date.today().isoformat()


def create(from_user_id: int, to_user_id: int, start: str, end: str, reason: str, actor: dict) -> services.Result:
    if from_user_id == to_user_id:
        return services.Result(False, "자기 자신에게 맡길 수 없습니다.")
    if actor.get("id") != from_user_id and actor.get("role") != "ADMIN":
        return services.Result(False, "본인 결재만 맡길 수 있습니다 (다른 사람 것은 시스템관리자).")
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return services.Result(False, "기간을 확인하세요.")
    if e < s or e < date.today():
        return services.Result(False, "끝나는 날은 시작일 이후, 오늘 이후여야 합니다.")
    if (e - s).days > 92:
        return services.Result(False, "대결 기간은 3개월까지입니다.")
    with db.transaction() as conn:
        users = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, role, active FROM users WHERE id IN (?, ?)",
                                                         (from_user_id, to_user_id))}
        if len(users) != 2 or not users[to_user_id]["active"]:
            return services.Result(False, "사용 중인 사용자를 고르세요.")
        if users[to_user_id]["role"] == "DATA" or not auth.has_role(users[to_user_id], "CLERK"):
            return services.Result(False, "조회·데이터 관리 역할에게는 결재를 맡길 수 없습니다 (담당자 이상).")
        clash = conn.execute("SELECT 1 FROM approval_delegations WHERE from_user_id = ? AND active = 1 "
                             "AND NOT (end_date < ? OR start_date > ?)", (from_user_id, start, end)).fetchone()
        if clash:
            return services.Result(False, "겹치는 기간에 이미 맡긴 대결이 있습니다. 먼저 취소하세요.")
        did = conn.execute("INSERT INTO approval_delegations (from_user_id, to_user_id, start_date, end_date, reason, active, "
                           "created_by, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                           (from_user_id, to_user_id, start, end, reason.strip(), actor["name"], now_str())).lastrowid
        audit.record(conn, actor, "DELEGATION", "user", from_user_id,
                     {"to": users[to_user_id]["name"], "start": start, "end": end, "reason": reason})
    return services.Result(True, f"{start} ~ {end} 동안 {users[to_user_id]['name']}님이 {users[from_user_id]['name']}님 결재를 대신합니다.",
                           tx_id=did)


def cancel(delegation_id: int, actor: dict) -> services.Result:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM approval_delegations WHERE id = ?", (delegation_id,)).fetchone()
        if row is None or not row["active"]:
            return services.Result(False, "취소할 대결이 없습니다.")
        if actor.get("id") not in (row["from_user_id"], row["to_user_id"]) and actor.get("role") != "ADMIN":
            return services.Result(False, "맡긴 사람·받은 사람·시스템관리자만 취소할 수 있습니다.")
        conn.execute("UPDATE approval_delegations SET active = 0 WHERE id = ?", (delegation_id,))
        audit.record(conn, actor, "DELEGATION", "user", row["from_user_id"], {"cancel": delegation_id})
    return services.Result(True, "대결을 취소했습니다.")


def active_for_froms(conn, from_ids: list[int]) -> list[dict]:
    """오늘 유효한 대결 중 맡긴 사람이 from_ids 안인 것."""
    if not from_ids:
        return []
    frag, params = db.in_clause(from_ids)
    t = _today()
    return [dict(r) for r in conn.execute(
        f"SELECT d.*, u.name AS from_name FROM approval_delegations d JOIN users u ON u.id = d.from_user_id "
        f"WHERE d.active = 1 AND d.start_date <= ? AND d.end_date >= ? AND d.from_user_id{frag}", (t, t, *params))]


def delegators(user_id: int) -> list[dict]:
    """오늘 나에게 결재를 맡긴 사용자들 (그 사람의 역할·창고 범위 포함)."""
    t = _today()
    df = db.query_df("SELECT u.id, u.name, u.role, u.all_warehouses FROM approval_delegations d JOIN users u ON u.id = d.from_user_id "
                     "WHERE d.active = 1 AND u.active = 1 AND d.to_user_id = ? AND d.start_date <= ? AND d.end_date >= ?",
                     (user_id, t, t))
    return df.to_dict("records")


def list_df(user_id: int | None = None) -> pd.DataFrame:
    """대결 목록 (user_id 를 주면 그 사람이 맡겼거나 받은 것)."""
    sql = ("SELECT d.id, d.start_date, d.end_date, f.name AS from_name, t.name AS to_name, d.reason, d.active, "
           "d.from_user_id, d.to_user_id, d.created_by, d.created_at FROM approval_delegations d "
           "JOIN users f ON f.id = d.from_user_id JOIN users t ON t.id = d.to_user_id")
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE d.from_user_id = ? OR d.to_user_id = ?"
        params = (user_id, user_id)
    return db.query_df(sql + " ORDER BY d.end_date DESC, d.id DESC LIMIT 200", params)

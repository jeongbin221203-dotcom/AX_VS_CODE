"""영업관리 시스템 - 엔터프라이즈 업무 로직

대기업·중견기업 영업관리에서 표준적으로 쓰이는 통제 장치를 담당한다.
  1) 조직/사용자/권한  - 본부-팀 계층, 역할별 데이터 접근범위
  2) 결재(Deal Desk)   - 할인율 구간별 승인 결재선
  3) 매출예측(Forecast) - Commit/Best Case 구분, 주간 스냅샷, 예측 정확도, 커버리지
  4) 파이프라인 분석    - 단계 전환율, 체류일, Sales Velocity, Win/Loss
  5) 채권 관리          - 결제기일 기준 aging, 여신한도 소진율

UI 의존성이 없으므로 배치/스케줄러에서도 그대로 호출할 수 있다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

from . import sales_db as db


# ============================================================================
# 1. 조직 / 사용자 / 권한
# ============================================================================
def list_orgs(db_path: str | None = None) -> pd.DataFrame:
    return db._df(
        "SELECT o.id, o.name AS 조직명, o.org_type AS 구분, p.name AS 상위조직, o.parent_id "
        "FROM orgs o LEFT JOIN orgs p ON p.id = o.parent_id "
        "ORDER BY COALESCE(o.parent_id, o.id), o.id", (), db_path)


def upsert_org(name: str, parent_id: int | None = None, org_type: str = "팀",
               org_id: int | None = None, db_path: str | None = None) -> int:
    if not str(name).strip():
        raise ValueError("조직명은 필수입니다.")
    dup = db._one("SELECT id FROM orgs WHERE name=? AND id<>?", [name.strip(), int(org_id or 0)], db_path)
    if dup:
        raise ValueError(f"'{name.strip()}' 조직이 이미 있습니다.")
    with db.get_conn(db_path) as conn:
        if org_id:
            conn.execute("UPDATE orgs SET name=?, parent_id=?, org_type=? WHERE id=?",
                         (name.strip(), parent_id, org_type, org_id))
            new_id = int(org_id)
        else:
            cur = conn.execute(
                "INSERT INTO orgs (name, parent_id, org_type, created_at) VALUES (?,?,?,?)",
                (name.strip(), parent_id, org_type, db._now()))
            new_id = int(cur.lastrowid)
    db.audit("수정" if org_id else "등록", "조직", new_id, {"조직명": name}, db_path)
    return new_id


def delete_org(org_id: int, db_path: str | None = None) -> None:
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE users SET org_id=NULL WHERE org_id=?", (org_id,))
        conn.execute("UPDATE orgs SET parent_id=NULL WHERE parent_id=?", (org_id,))
        conn.execute("DELETE FROM orgs WHERE id=?", (org_id,))
    db.audit("삭제", "조직", org_id, None, db_path)


def descendant_org_ids(org_id: int | None, db_path: str | None = None) -> list[int]:
    """해당 조직과 모든 하위 조직 id (팀장의 데이터 범위 계산에 사용)."""
    if not org_id:
        return []
    rows = db._df("SELECT id, parent_id FROM orgs", (), db_path)
    children: dict[int, list[int]] = {}
    for r in rows.itertuples():
        children.setdefault(int(r.parent_id) if pd.notna(r.parent_id) else 0, []).append(int(r.id))
    result, queue = [int(org_id)], [int(org_id)]
    while queue:
        current = queue.pop()
        for child in children.get(current, []):
            if child not in result:
                result.append(child)
                queue.append(child)
    return result


def list_users(active_only: bool = True, db_path: str | None = None) -> pd.DataFrame:
    sql = ("SELECT u.id, u.emp_no AS 사번, u.name AS 이름, u.role AS 역할코드, "
           "o.name AS 소속, u.email AS 이메일, u.active AS 사용여부, u.org_id "
           "FROM users u LEFT JOIN orgs o ON o.id = u.org_id WHERE 1=1")
    if active_only:
        sql += " AND u.active = 1"
    sql += " ORDER BY u.role DESC, u.name"
    out = db._df(sql, (), db_path)
    if not out.empty:
        out.insert(3, "역할", out["역할코드"].map(db.ROLE_LABEL).fillna(out["역할코드"]))
    return out


def get_user(user_id: int | None = None, emp_no: str | None = None,
             name: str | None = None, db_path: str | None = None) -> Optional[dict]:
    if user_id:
        return db._one("SELECT * FROM users WHERE id=?", [int(user_id)], db_path)
    if emp_no:
        return db._one("SELECT * FROM users WHERE emp_no=?", [emp_no], db_path)
    if name:
        return db._one("SELECT * FROM users WHERE name=? AND active=1", [name], db_path)
    return None


USER_FIELDS = ["emp_no", "name", "role", "org_id", "email", "active"]


def upsert_user(data: dict, db_path: str | None = None) -> int:
    """사용자 등록/수정.

    이름은 중복될 수 있다(동명이인) — 데이터는 사용자 id 로 연결되므로 섞이지 않는다.
    이름을 바꾸면 담당 기록의 표시용 이름도 함께 바꾼다.
    """
    if not str(data.get("emp_no", "")).strip():
        raise ValueError("사번은 필수입니다.")
    if not str(data.get("name", "")).strip():
        raise ValueError("이름은 필수입니다.")
    role = data.get("role") or "REP"
    if role not in db.ROLES:
        raise ValueError(f"역할 값이 올바르지 않습니다: {role}")
    record = {"emp_no": str(data["emp_no"]).strip(), "name": str(data["name"]).strip(), "role": role,
              "org_id": data.get("org_id"), "email": data.get("email"),
              "active": int(data.get("active", 1))}
    values = [record[f] for f in USER_FIELDS]
    prev = get_user(user_id=int(data["id"]), db_path=db_path) if data.get("id") else None
    dup = db._one("SELECT id FROM users WHERE emp_no=? AND id<>?",
                  [record["emp_no"], int(data.get("id") or 0)], db_path)
    if dup:
        raise ValueError(f"사번 {record['emp_no']} 은(는) 이미 사용 중입니다.")
    with db.get_conn(db_path) as conn:
        if prev:
            new_id = int(prev["id"])
            conn.execute(f"UPDATE users SET {', '.join(f'{f}=?' for f in USER_FIELDS)} WHERE id=?",
                         (*values, new_id))
            if prev["name"] != record["name"]:
                for table in db.OWNER_TABLES:
                    conn.execute(f"UPDATE {table} SET owner=? WHERE owner_id=?", (record["name"], new_id))
        else:
            cur = conn.execute(
                f"INSERT INTO users ({', '.join(USER_FIELDS)}, created_at) "
                f"VALUES ({', '.join('?' * len(USER_FIELDS))}, ?)", (*values, db._now()))
            new_id = int(cur.lastrowid)
        # 이름만 있던 이관 데이터를 새 사용자에게 연결 (이름이 한 명에게만 해당할 때)
        db.link_owner_ids(conn, record["name"])
    db.audit("수정" if prev else "등록", "사용자", new_id,
             {"사번": record["emp_no"], "이름": record["name"],
              "변경": db.diff(prev, record, USER_FIELDS) if prev else None}, db_path)
    return new_id


def delete_user(user_id: int, db_path: str | None = None) -> None:
    """실제 삭제 대신 비활성화한다(이력 보존이 원칙)."""
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    db.audit("비활성화", "사용자", user_id, None, db_path)


def visible_owners(user: dict, db_path: str | None = None) -> Optional[list[int]]:
    """역할별 데이터 접근범위를 담당자(사용자) id 목록으로 변환한다.

    None 을 반환하면 '전사 조회'를 뜻한다.
      REP     : 본인 데이터만
      MANAGER : 본인 조직 + 하위 조직 소속원 전체 (퇴사자 포함 — 남은 실적을 볼 수 있어야 한다)
      EXEC    : 전사 (읽기 중심)
      SUPPORT : 전사 (영업지원 — 데이터 점검·정리)
      ADMIN   : 전사 + 관리 기능
    """
    if not user:
        return []
    role = user.get("role", "REP")
    if role in ("EXEC", "SUPPORT", "ADMIN"):
        return None
    if role == "MANAGER":
        org_ids = descendant_org_ids(user.get("org_id"), db_path)
        if not org_ids:
            return [int(user["id"])]
        placeholders = ",".join("?" * len(org_ids))
        rows = db._df(f"SELECT id FROM users WHERE org_id IN ({placeholders})", org_ids, db_path)
        ids = [int(i) for i in rows["id"].tolist()] if not rows.empty else []
        if int(user["id"]) not in ids:
            ids.append(int(user["id"]))
        return ids
    return [int(user["id"])]


def apply_context(user: dict, db_path: str | None = None) -> None:
    """로그인 사용자 기준으로 감사로그 주체와 데이터 접근범위를 설정한다."""
    db.set_context(actor=(user or {}).get("name", "system"),
                   owner_scope=visible_owners(user, db_path),
                   actor_id=(user or {}).get("id"))


def has_role(user: dict, minimum: str) -> bool:
    role = (user or {}).get("role", "REP")
    if minimum == "SUPPORT":                       # 데이터 관리 업무: 영업지원 · 시스템관리자
        return role in db.DATA_ROLES
    return db.ROLES.get(role, 0) >= db.ROLES.get(minimum, 99)


def seed_org_demo(db_path: str | None = None) -> dict:
    """샘플 조직도와 사용자 생성. 기존 담당자 이름을 사용자로 자동 편입한다."""
    existing = {u["이름"] for _, u in list_users(False, db_path).iterrows()} \
        if not list_users(False, db_path).empty else set()
    def org(name: str, parent: int | None, kind: str) -> int:      # 이미 있으면 그 조직을 쓴다 (두 번 눌러도 오류 없음)
        row = db._one("SELECT id FROM orgs WHERE name=?", [name], db_path)
        return int(row["id"]) if row else upsert_org(name, parent, kind, db_path=db_path)

    hq = org("영업본부", None, "본부")
    team1 = org("영업1팀", hq, "팀")
    team2 = org("영업2팀", hq, "팀")

    plan = [
        ("2001", "정임원", "EXEC", hq),
        ("2002", "한팀장", "MANAGER", team1),
        ("2003", "김영업", "REP", team1),
        ("2004", "이수주", "REP", team1),
        ("2005", "서팀장", "MANAGER", team2),
        ("2006", "박고객", "REP", team2),
        ("2007", "최성과", "REP", team2),
        ("2008", "윤지원", "SUPPORT", hq),
        ("9999", "시스템관리자", "ADMIN", hq),
    ]
    created = 0
    for emp_no, name, role, org_id in plan:
        if name in existing:
            continue
        upsert_user({"emp_no": emp_no, "name": name, "role": role, "org_id": org_id,
                     "email": f"{emp_no}@company.co.kr"}, db_path)
        created += 1
    return {"orgs": 3, "users": created}


def transfer_owner(from_id: int, to_id: int, include_closed: bool = False,
                   db_path: str | None = None) -> dict:
    """담당자 이관 (퇴사·팀 이동). 거래처와 진행 중 영업기회를 넘긴다.

    매출·활동·목표·마감된 기회는 실적 귀속을 지키기 위해 원래 담당자에 남긴다.
    """
    src = get_user(user_id=from_id, db_path=db_path)
    if not src:
        raise ValueError("넘겨줄 담당자를 찾을 수 없습니다.")
    to_id, to_name = db.resolve_owner(int(to_id), db_path)
    if int(from_id) == int(to_id):
        raise ValueError("같은 담당자에게는 이관할 수 없습니다.")
    stage_cond = "" if include_closed else " AND stage NOT IN ('수주','실주')"
    with db.get_conn(db_path) as conn:
        cust = conn.execute("UPDATE customers SET owner=?, owner_id=?, updated_at=?, "
                            "row_version=COALESCE(row_version,0)+1 WHERE owner_id=?",
                            (to_name, to_id, db._now(), from_id)).rowcount
        deals = conn.execute(f"UPDATE deals SET owner=?, owner_id=?, updated_at=?, "
                             f"row_version=COALESCE(row_version,0)+1 WHERE owner_id=?{stage_cond}",
                             (to_name, to_id, db._now(), from_id)).rowcount
    result = {"거래처": cust, "영업기회": deals}
    db.audit("담당자이관", "사용자", int(from_id),
             {"보낸이": src["name"], "받는이": to_name, **result}, db_path)
    return result


def unlinked_counts(db_path: str | None = None) -> dict:
    """담당자가 사용자와 연결되지 않은 기록 수 (이관 데이터 정리용)."""
    return {t: int(db._scalar(f"SELECT COUNT(*) FROM {t} WHERE owner_id IS NULL", (), db_path))
            for t in ("customers", "deals", "activities", "sales", "targets")}


# ============================================================================
# 2. 결재 (Deal Desk) - 할인 승인 · 다단계 결재선 · 대결
# ============================================================================
# 필요권한별 결재선 (순서대로). 한 사람은 한 결재 건에서 한 단계만 결재한다.
APPROVAL_CHAIN = {"MANAGER": ["MANAGER"], "EXEC": ["MANAGER", "EXEC"], "ADMIN": ["MANAGER", "EXEC", "ADMIN"]}
def sla_hours() -> int:
    """결재 단계 기한(시간) — 회사 설정."""
    from . import company
    return int(company.get("approval_sla_hours"))
FMT = "%Y-%m-%d %H:%M:%S"


def _due(now: datetime | None = None) -> str:
    return ((now or datetime.now()) + timedelta(hours=sla_hours())).strftime(FMT)


def active_delegations(on: str | None = None, db_path: str | None = None) -> pd.DataFrame:
    """오늘 유효한 대결 지정 (from → to)."""
    on = on or date.today().isoformat()
    return db._df("SELECT d.id, d.from_user_id, d.to_user_id, f.name AS from_name, t.name AS to_name "
                  "FROM delegations d JOIN users f ON f.id = d.from_user_id JOIN users t ON t.id = d.to_user_id "
                  "WHERE d.revoked_at IS NULL AND d.start_date <= ? AND d.end_date >= ? AND t.active = 1",
                  [on, on], db_path)


class _ApproverContext:
    """결재 권한 판단에 쓰는 사용자·조직범위·대결 정보를 한 번만 읽어 둔다 (결재함·알림에서 여러 건을 볼 때)."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self.users = db._df("SELECT * FROM users WHERE active = 1", (), db_path).to_dict("records")
        self.by_id = {int(u["id"]): u for u in self.users}
        self.delegations = [(int(d.from_user_id), int(d.to_user_id))
                            for d in active_delegations(db_path=db_path).itertuples()]
        self._scopes: dict[int, set[int]] = {}

    def scope(self, user: dict) -> set[int]:
        uid = int(user["id"])
        if uid not in self._scopes:
            self._scopes[uid] = set(visible_owners(user, self.db_path) or [])
        return self._scopes[uid]

    def candidates(self, role: str, deal_owner_id: Optional[int], exclude: set[int]) -> list[dict]:
        """단계를 결재할 수 있는 사용자. 그 단계 역할의 사람이 맡고, 범위 안에 그 역할이 없을 때만 윗 역할이 맡는다.
        팀장은 담당자가 자기 조직 범위에 있어야 한다."""
        eligible = []
        for u in self.users:
            if int(u["id"]) in exclude or not has_role(u, role):
                continue
            if u["role"] == "MANAGER" and (deal_owner_id is None or int(deal_owner_id) not in self.scope(u)):
                continue
            eligible.append(u)
        exact = [u for u in eligible if u["role"] == role]
        return exact or eligible

    def approvers(self, step_role: str, deal_owner_id: Optional[int], exclude: set[int]) -> list[dict]:
        direct = self.candidates(step_role, deal_owner_id, exclude)
        out = [{"user": u, "acted_for": None} for u in direct]
        ids = {int(u["id"]) for u in direct}
        for from_id, to_id in self.delegations:
            if from_id in ids and to_id not in exclude and to_id not in ids and to_id in self.by_id:
                out.append({"user": self.by_id[to_id], "acted_for": self.by_id[from_id]})
        return out


def step_approvers(approval: dict, step: dict, db_path: str | None = None,
                   ctx: Optional[_ApproverContext] = None) -> list[dict]:
    """이 단계를 지금 결재할 수 있는 사람 [{user, acted_for}] — 대결 지정 포함."""
    ctx = ctx or _ApproverContext(db_path)
    done = db._df("SELECT approver_id FROM approval_steps WHERE approval_id=? AND status='승인'",
                  [approval["id"]], db_path)
    exclude = {int(approval.get("requested_by_id") or 0)} | {int(x) for x in done["approver_id"].dropna()}
    deal = db._one("SELECT owner_id FROM deals WHERE id=?", [approval["deal_id"]], db_path) or {}
    return ctx.approvers(step["role"], deal.get("owner_id"), exclude)


def _active_step(approval_id: int, db_path: str | None = None) -> Optional[dict]:
    return db._one("SELECT * FROM approval_steps WHERE approval_id=? AND status='대기' ORDER BY step_no LIMIT 1",
                   [approval_id], db_path)


def _notify_step(approval: dict, step: dict, title: str, body: str, db_path: str | None = None) -> None:
    from . import notify
    notify.notify([int(a["user"]["id"]) for a in step_approvers(approval, step, db_path)],
                  "결재요청", title, body, f"/approvals?aid={approval['id']}")


def request_approval(deal_id: int, requester: dict, reason: str = "",
                     db_path: str | None = None) -> int:
    """할인 결재 요청. 요청 시점의 정가·제안가·할인율을 고정하고 결재선을 만든다."""
    deal = db.get_deal(deal_id, db_path)
    db.check_record_scope(deal, "영업기회")
    if deal["stage"] in (db.STAGE_WON, db.STAGE_LOST):
        raise ValueError("마감된 영업기회는 결재를 요청할 수 없습니다.")
    list_amount = int(deal.get("list_amount") or deal.get("amount") or 0)
    final_amount = int(deal.get("amount") or 0)
    rate = float(deal.get("discount_rate") or 0)
    role = db.required_approval_role(rate)
    if not role:
        raise ValueError("할인율이 0% 이므로 승인이 필요하지 않습니다.")
    chain = APPROVAL_CHAIN[role]

    with db.get_conn(db_path) as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE deal_id=? AND status='대기'", (deal_id,)
        ).fetchone()[0]
        if pending:
            raise ValueError("이미 결재 대기 중인 건입니다.")
        cur = conn.execute(
            "INSERT INTO approvals (deal_id, kind, requested_by, requested_by_id, requested_at, "
            "list_amount, final_amount, discount_rate, required_role, reason, status, current_step) "
            "VALUES (?,?,?,?,?,?,?,?,?,?, '대기', 1)",
            (deal_id, "할인승인", requester["name"], int(requester["id"]), db._now(), list_amount,
             final_amount, rate, role, reason))
        new_id = int(cur.lastrowid)
        for n, step_role in enumerate(chain, start=1):
            conn.execute("INSERT INTO approval_steps (approval_id, step_no, role, status, activated_at, due_at) "
                         "VALUES (?,?,?,?,?,?)",
                         (new_id, n, step_role, "대기" if n == 1 else "예정",
                          db._now() if n == 1 else None, _due() if n == 1 else None))
        conn.execute("UPDATE deals SET approval_status='대기' WHERE id=?", (deal_id,))
    db.audit("결재요청", "승인", new_id,
             {"기회": deal.get("title"), "할인율": rate, "제안가": final_amount,
              "결재선": [db.ROLE_LABEL[r] for r in chain]}, db_path)
    approval = db._one("SELECT * FROM approvals WHERE id=?", [new_id], db_path)
    _notify_step(approval, _active_step(new_id, db_path), f"할인 결재 요청: {deal.get('title')}",
                 f"{requester['name']} · 할인 {rate:.1f}% · 제안가 {final_amount:,}원\n사유: {reason or '-'}", db_path)
    return new_id


def decide_approval(approval_id: int, approver: dict, approve: bool, comment: str = "",
                    db_path: str | None = None) -> None:
    """현재 단계를 결재한다. 대결 지정을 받은 사람은 원결재자를 대신해 결재한다(기록에 남는다)."""
    from . import notify
    row = db._one("SELECT * FROM approvals WHERE id=?", [approval_id], db_path)
    if not row:
        raise ValueError("존재하지 않는 결재 건입니다.")
    if row["status"] != "대기":
        raise ValueError(f"이미 처리된 건입니다(현재 상태: {row['status']}).")
    requester_id = row.get("requested_by_id")
    if (requester_id is not None and int(requester_id) == int(approver["id"])) or \
            (requester_id is None and row["requested_by"] == approver.get("name")):
        raise PermissionError("본인이 요청한 건은 본인이 결재할 수 없습니다(자기결재 금지).")
    step = _active_step(approval_id, db_path)
    if not step:
        raise ValueError("진행 중인 결재 단계가 없습니다.")
    match = next((a for a in step_approvers(row, step, db_path) if int(a["user"]["id"]) == int(approver["id"])), None)
    if not match:
        raise PermissionError(
            f"이 단계({step['step_no']}단계 · {db.ROLE_LABEL.get(step['role'], step['role'])})를 결재할 권한이 없습니다.")
    deal = db.get_deal(int(row["deal_id"]), db_path)      # 권한은 위 결재선 단계로 확인했다
    # 요청 이후 조건이 바뀌었다면 이 결재는 더 이상 유효하지 않다
    if int(deal.get("amount") or 0) != int(row["final_amount"]) or \
            abs(float(deal.get("discount_rate") or 0) - float(row["discount_rate"])) > 0.0001:
        with db.get_conn(db_path) as conn:
            conn.execute("UPDATE approvals SET status='취소', comment='[조건 변경으로 자동 취소]' WHERE id=?",
                         (approval_id,))
            conn.execute("UPDATE approval_steps SET status='취소' WHERE approval_id=? AND status IN ('대기','예정')",
                         (approval_id,))
            conn.execute("UPDATE deals SET approval_status='미요청' WHERE id=?", (row["deal_id"],))
        raise ValueError("결재 요청 이후 금액·할인율이 바뀌어 이 결재는 취소되었습니다. 다시 요청하세요.")

    acted_for = match["acted_for"]
    now = db._now()
    next_step = db._one("SELECT * FROM approval_steps WHERE approval_id=? AND step_no=?",
                        [approval_id, int(step["step_no"]) + 1], db_path)
    final = "반려" if not approve else ("승인" if not next_step else None)
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE approval_steps SET status=?, approver=?, approver_id=?, acted_for_id=?, comment=?, "
                     "decided_at=? WHERE id=?",
                     ("승인" if approve else "반려", approver.get("name"), int(approver["id"]),
                      int(acted_for["id"]) if acted_for else None, comment, now, step["id"]))
        if final:
            conn.execute("UPDATE approvals SET status=?, approver=?, approver_id=?, decided_at=?, comment=? WHERE id=?",
                         (final, approver.get("name"), int(approver["id"]), now, comment, approval_id))
            conn.execute("UPDATE deals SET approval_status=? WHERE id=?", (final, row["deal_id"]))
            if final == "반려":
                conn.execute("UPDATE approval_steps SET status='취소' WHERE approval_id=? AND status='예정'",
                             (approval_id,))
        else:
            conn.execute("UPDATE approval_steps SET status='대기', activated_at=?, due_at=? WHERE id=?",
                         (now, _due(), next_step["id"]))
            conn.execute("UPDATE approvals SET current_step=? WHERE id=?", (next_step["step_no"], approval_id))
    db.audit("승인" if approve else "반려", "승인", approval_id,
             {"기회ID": row["deal_id"], "단계": step["step_no"], "결재자": approver.get("name"),
              "대결": acted_for["name"] if acted_for else None, "할인율": row["discount_rate"],
              "제안가": row["final_amount"], "의견": comment, "최종": final}, db_path)
    if final:
        notify.notify([requester_id], "결재결과", f"할인 결재 {final}: {deal.get('title')}",
                      f"{approver.get('name')}{' (대결: ' + acted_for['name'] + ')' if acted_for else ''} · {comment or ''}",
                      f"/deals?id={row['deal_id']}&tab=edit")
    else:
        _notify_step(row, dict(next_step, status="대기"), f"할인 결재 요청({next_step['step_no']}단계): {deal.get('title')}",
                     f"{step['step_no']}단계 {approver.get('name')} 승인 · 할인 {float(row['discount_rate']):.1f}%",
                     db_path)


def approval_lines(approval_ids: list[int], db_path: str | None = None) -> dict[int, str]:
    """결재 진행 요약: '팀장 ✔한팀장 → 임원 ⏳ → 관리자'."""
    if not approval_ids:
        return {}
    marks = ",".join("?" * len(approval_ids))
    steps = db._df(f"SELECT s.approval_id, s.step_no, s.role, s.status, s.approver, u.name AS acted_for "
                   f"FROM approval_steps s LEFT JOIN users u ON u.id = s.acted_for_id "
                   f"WHERE s.approval_id IN ({marks}) ORDER BY s.approval_id, s.step_no", approval_ids, db_path)
    icon = {"승인": "✔", "반려": "✖", "대기": "⏳", "예정": "", "취소": "–"}
    out: dict[int, list[str]] = {}
    for s in steps.itertuples():
        who = f"{s.approver}" + (f"(대결:{s.acted_for})" if isinstance(s.acted_for, str) and s.acted_for else "") \
            if isinstance(s.approver, str) and s.approver else ""
        out.setdefault(int(s.approval_id), []).append(
            f"{db.ROLE_LABEL.get(s.role, s.role)} {icon.get(s.status, '')}{who}".strip())
    return {k: " → ".join(v) for k, v in out.items()}


def list_approvals(status: str = "", requested_by_id: int | None = None,
                   db_path: str | None = None, scoped: bool = True) -> pd.DataFrame:
    sql = """
        SELECT a.id, a.requested_at AS 요청일시, c.name AS 거래처, d.title AS 기회명,
               a.list_amount AS 정가, a.final_amount AS 제안가, a.discount_rate AS 할인율,
               a.required_role AS 필요권한코드, a.requested_by AS 요청자, a.reason AS 사유,
               a.status AS 상태, a.approver AS 결재자, a.decided_at AS 결재일시,
               a.comment AS 결재의견, a.deal_id, a.requested_by_id
          FROM approvals a
          JOIN deals d ON d.id = a.deal_id
          JOIN customers c ON c.id = d.customer_id
         WHERE 1=1
    """
    params: list[Any] = []
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    if requested_by_id:
        sql += " AND a.requested_by_id = ?"
        params.append(int(requested_by_id))
    scope_sql, scope_params = db._scope_clause("d") if scoped else ("", [])
    sql += scope_sql + " ORDER BY a.id DESC"
    out = db._df(sql, params + scope_params, db_path)
    if not out.empty:
        out.insert(8, "필요권한", out["필요권한코드"].map(db.ROLE_LABEL).fillna(out["필요권한코드"]))
        out = out.drop(columns=["필요권한코드"])
        lines = approval_lines([int(i) for i in out["id"]], db_path)
        out.insert(9, "결재선", out["id"].map(lambda i: lines.get(int(i), "")))
    return out


def pending_for(user: dict, db_path: str | None = None) -> pd.DataFrame:
    """내가 지금 결재할 차례인 건 (본인 권한 + 대결 지정). 본인이 요청한 건은 관리자라도 제외."""
    uid = int(user["id"])
    ctx = _ApproverContext(db_path)
    if not has_role(user, "MANAGER") and not any(to_id == uid for _f, to_id in ctx.delegations):
        return pd.DataFrame()                     # 결재자도 대결자도 아니면 조회할 것이 없다 (매 요청 호출됨)
    # 진행 단계 · 이미 승인한 사람 · 기회 담당자를 한 번에 읽는다
    steps = db._df("SELECT s.approval_id, s.step_no, s.role, a.requested_by_id, d.owner_id "
                   "FROM approval_steps s JOIN approvals a ON a.id = s.approval_id JOIN deals d ON d.id = a.deal_id "
                   "WHERE a.status='대기' AND s.status='대기'", (), db_path)
    if steps.empty:
        return pd.DataFrame()
    approved = db._df("SELECT s.approval_id, s.approver_id FROM approval_steps s JOIN approvals a "
                      "ON a.id = s.approval_id WHERE a.status='대기' AND s.status='승인'", (), db_path)
    done: dict[int, set[int]] = {}
    for r in approved.itertuples():
        if pd.notna(r.approver_id):
            done.setdefault(int(r.approval_id), set()).add(int(r.approver_id))
    mine: dict[int, str] = {}
    for st in steps.sort_values("step_no").drop_duplicates("approval_id").itertuples():
        requester = int(st.requested_by_id) if pd.notna(st.requested_by_id) else 0
        if requester == uid:
            continue
        exclude = {requester} | done.get(int(st.approval_id), set())
        owner = int(st.owner_id) if pd.notna(st.owner_id) else None
        match = next((a for a in ctx.approvers(st.role, owner, exclude) if int(a["user"]["id"]) == uid), None)
        if match:
            mine[int(st.approval_id)] = f"{st.step_no}단계" + (
                f" · {match['acted_for']['name']} 대결" if match["acted_for"] else "")
    if not mine:
        return pd.DataFrame()
    pending = list_approvals("대기", db_path=db_path, scoped=False)
    out = pending[pending["id"].isin(list(mine))].reset_index(drop=True)
    out.insert(0, "내 차례", [mine[int(i)] for i in out["id"]])
    return out


def escalate_overdue_steps(db_path: str | None = None) -> dict:
    """결재 기한이 지난 단계: 결재자·대결자에게 독촉하고, 한 단계 위 역할에 알린다 (한 번만)."""
    from . import notify
    now = datetime.now().strftime(FMT)
    steps = db._df("SELECT s.*, a.deal_id, a.requested_by_id, d.title FROM approval_steps s "
                   "JOIN approvals a ON a.id = s.approval_id JOIN deals d ON d.id = a.deal_id "
                   "WHERE s.status='대기' AND s.due_at < ? AND s.escalated_at IS NULL", [now], db_path)
    upper = {"MANAGER": "EXEC", "EXEC": "ADMIN", "ADMIN": "ADMIN"}
    ctx = _ApproverContext(db_path)
    for s in steps.to_dict("records"):
        approval = {"id": s["approval_id"], "deal_id": s["deal_id"], "requested_by_id": s["requested_by_id"]}
        ids = [int(a["user"]["id"]) for a in step_approvers(approval, s, db_path, ctx)]
        notify.notify(ids, "결재독촉", f"결재 기한 경과: {s['title']}",
                      f"{s['step_no']}단계({db.ROLE_LABEL.get(s['role'])}) 결재가 {s['due_at']} 기한을 넘겼습니다.",
                      f"/approvals?aid={s['approval_id']}")
        notify.notify_role(upper[s["role"]], "결재지연", f"결재 지연 보고: {s['title']}",
                           f"{s['step_no']}단계({db.ROLE_LABEL.get(s['role'])}) 결재가 기한을 넘겨 대기 중입니다.",
                           "/approvals?tab=history&status=대기")
        with db.get_conn(db_path) as conn:
            conn.execute("UPDATE approval_steps SET escalated_at=? WHERE id=?", (now, s["id"]))
    if len(steps):
        db.audit("결재독촉", "승인", None, {"건수": len(steps)}, db_path)
    return {"escalated": len(steps)}


# ── 대결(위임) 지정 ───────────────────────────────────────────────────────
def create_delegation(from_user_id: int, to_user_id: int, start: str, end: str, reason: str,
                      actor: dict, db_path: str | None = None) -> int:
    """부재 기간 동안 to_user 가 from_user 대신 결재한다. 본인 것은 본인이, 다른 사람 것은 관리자만 지정."""
    if int(from_user_id) != int(actor["id"]) and not has_role(actor, "ADMIN"):
        raise PermissionError("다른 사람의 대결자는 관리자만 지정할 수 있습니다.")
    if int(from_user_id) == int(to_user_id):
        raise ValueError("본인을 대결자로 지정할 수 없습니다.")
    to_user = get_user(user_id=int(to_user_id), db_path=db_path)
    if not to_user or not to_user.get("active"):
        raise ValueError("대결자는 활성 사용자여야 합니다.")
    start, end = db._d(start), db._d(end)
    if not start or not end or end < start:
        raise ValueError("대결 기간을 올바르게 입력하세요.")
    if not str(reason or "").strip():
        raise ValueError("대결 사유를 입력하세요 (예: 출장, 휴가).")
    with db.get_conn(db_path) as conn:
        cur = conn.execute("INSERT INTO delegations (from_user_id, to_user_id, start_date, end_date, reason, "
                           "created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                           (int(from_user_id), int(to_user_id), start, end, reason.strip(), actor.get("name"), db._now()))
        new_id = int(cur.lastrowid)
    db.audit("대결지정", "사용자", int(from_user_id),
             {"대결자": to_user["name"], "기간": [start, end], "사유": reason.strip()}, db_path)
    return new_id


def revoke_delegation(delegation_id: int, actor: dict, db_path: str | None = None) -> None:
    row = db._one("SELECT * FROM delegations WHERE id=?", [delegation_id], db_path)
    if not row:
        raise ValueError("대결 지정을 찾을 수 없습니다.")
    if int(row["from_user_id"]) != int(actor["id"]) and not has_role(actor, "ADMIN"):
        raise PermissionError("본인이 지정한 대결만 취소할 수 있습니다.")
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE delegations SET revoked_at=? WHERE id=?", (db._now(), delegation_id))
    db.audit("대결취소", "사용자", int(row["from_user_id"]), {"대결ID": delegation_id}, db_path)


def list_delegations(user_id: int | None = None, db_path: str | None = None) -> pd.DataFrame:
    sql = ("SELECT d.id, f.name AS 원결재자, t.name AS 대결자, d.start_date AS 시작, d.end_date AS 종료, "
           "d.reason AS 사유, d.created_by AS 지정자, CASE WHEN d.revoked_at IS NOT NULL THEN '취소' "
           "WHEN d.end_date < ? THEN '종료' WHEN d.start_date > ? THEN '예정' ELSE '진행중' END AS 상태, "
           "d.from_user_id FROM delegations d JOIN users f ON f.id = d.from_user_id "
           "JOIN users t ON t.id = d.to_user_id WHERE 1=1")
    today = date.today().isoformat()
    params: list[Any] = [today, today]
    if user_id:
        sql += " AND (d.from_user_id = ? OR d.to_user_id = ?)"
        params += [int(user_id), int(user_id)]
    return db._df(sql + " ORDER BY d.id DESC", params, db_path)


# ============================================================================
# 3. 매출예측 (Forecast)
# ============================================================================
def take_snapshot(yyyymm: str, snap_date: str | None = None, db_path: str | None = None) -> int:
    """현재 파이프라인을 담당자·예측구분별로 집계해 스냅샷으로 저장한다.

    같은 날 다시 실행하면 해당 일자 스냅샷을 갱신한다(주 1회 실행 권장).
    """
    snap = snap_date or date.today().strftime("%Y-%m-%d")
    rows = db._df(
        f"SELECT {db.OWNER_KEY} AS okey, MAX(owner_id) AS owner_id, MAX(owner) AS owner, "
        "COALESCE(forecast_category,'Pipeline') AS category, COUNT(*) AS cnt, "
        "SUM(amount) AS amount, SUM(amount*probability/100.0) AS weighted "
        "FROM deals WHERE stage NOT IN ('실주') "
        "AND substr(COALESCE(expected_close, closed_at), 1, 7) = ? "
        "GROUP BY okey, category", [yyyymm], db_path)
    if rows.empty:
        return 0
    with db.get_conn(db_path) as conn:
        for r in rows.itertuples():
            owner_id = None if pd.isna(r.owner_id) else int(r.owner_id)
            conn.execute(
                "INSERT INTO pipeline_snapshots (snap_date, yyyymm, owner, owner_id, owner_key, category, "
                "deal_cnt, amount, weighted) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(snap_date, yyyymm, owner_key, category) DO UPDATE SET "
                "deal_cnt=excluded.deal_cnt, amount=excluded.amount, weighted=excluded.weighted",
                (snap, yyyymm, r.owner, owner_id, r.okey, r.category, int(r.cnt), int(r.amount or 0),
                 int(r.weighted or 0)))
    db.audit("스냅샷", "예측", None, {"대상월": yyyymm, "일자": snap, "건수": len(rows)}, db_path)
    return len(rows)


def forecast_summary(yyyymm: str, owner_id: int | None = None, db_path: str | None = None) -> dict:
    """이번 달 예측 요약: 확정 매출 + 카테고리별 잔여 파이프라인 + 커버리지."""
    oc, op = db._owner_clause(owner_id)
    closed = db._scalar(
        f"SELECT SUM(amount) FROM sales WHERE {db.ACTIVE_SALE} AND substr(sale_date, 1, 7)=?{oc}",
        [yyyymm, *op], db_path)
    target = db._scalar(
        f"SELECT SUM(target_amount) FROM targets WHERE yyyymm=?{oc}", [yyyymm, *op], db_path)
    by_cat = db._df(
        f"SELECT COALESCE(forecast_category,'Pipeline') AS cat, SUM(amount) AS amt FROM deals "
        f"WHERE stage NOT IN ('수주','실주') AND substr(expected_close, 1, 7)=?{oc} GROUP BY 1",
        [yyyymm, *op], db_path)
    cats = {c: 0 for c in db.FORECAST_CATS}
    cats.update({r.cat: int(r.amt or 0) for r in by_cat.itertuples()})
    return _forecast_numbers(closed, target, cats)


def _forecast_numbers(closed: float, target: float, cats: dict) -> dict:
    commit_total = closed + cats["Commit"]
    best_total = commit_total + cats["Best Case"]
    open_pipeline = sum(cats[c] for c in ("Commit", "Best Case", "Pipeline"))
    gap = target - commit_total
    return {
        "target": int(target),
        "closed": int(closed),
        "commit": cats["Commit"],
        "best_case": cats["Best Case"],
        "pipeline": cats["Pipeline"],
        "omitted": cats["Omitted"],
        "commit_total": int(commit_total),
        "best_total": int(best_total),
        "gap": int(gap),
        "attainment": closed / target * 100 if target else 0.0,
        "commit_attainment": commit_total / target * 100 if target else 0.0,
        # 커버리지: 남은 목표 대비 파이프라인 배수. 통상 3배 이상을 건전하다고 본다.
        "coverage": open_pipeline / (target - closed) if (target - closed) > 0 else 0.0,
        "open_pipeline": int(open_pipeline),
    }


def forecast_by_owner(yyyymm: str, db_path: str | None = None) -> pd.DataFrame:
    """담당자별 예측 테이블 (팀장·임원이 주간 예측회의에서 보는 형태).

    담당자 수만큼 쿼리를 반복하지 않고 세 번의 묶음 쿼리로 계산한다.
    """
    sc, sp = db._scope_clause()
    key = db.OWNER_KEY
    closed = db._df(f"SELECT {key} AS k, MAX(owner) AS n, SUM(amount) AS v FROM sales "
                    f"WHERE {db.ACTIVE_SALE} AND substr(sale_date, 1, 7)=?{sc} GROUP BY 1",
                    [yyyymm, *sp], db_path)
    target = db._df(f"SELECT {key} AS k, MAX(owner) AS n, SUM(target_amount) AS v FROM targets "
                    f"WHERE yyyymm=?{sc} GROUP BY 1", [yyyymm, *sp], db_path)
    pipe = db._df(f"SELECT {key} AS k, MAX(owner) AS n, COALESCE(forecast_category,'Pipeline') AS cat, "
                  f"SUM(amount) AS v FROM deals WHERE stage NOT IN ('수주','실주') "
                  f"AND substr(expected_close, 1, 7)=?{sc} GROUP BY 1, 3",
                  [yyyymm, *sp], db_path)
    labels = db.owner_labels(db_path)
    keys: dict[str, str] = {}
    for part in (closed, target, pipe):
        for r in part.itertuples():
            keys.setdefault(r.k, labels.get(r.k, f"{r.n} (미연결)"))
    # 범위 안의 활성 담당자는 실적이 없어도 표에 보인다
    for o in db.owner_choices(assignable=True, db_path=db_path):
        keys.setdefault(str(o["id"]), labels.get(str(o["id"]), o["name"]))
    closed_map = dict(zip(closed["k"], closed["v"])) if not closed.empty else {}
    target_map = dict(zip(target["k"], target["v"])) if not target.empty else {}
    rows = []
    for k, name in keys.items():
        cats = {c: 0 for c in db.FORECAST_CATS}
        if not pipe.empty:
            for r in pipe[pipe["k"] == k].itertuples():
                cats[r.cat] = int(r.v or 0)
        f = _forecast_numbers(float(closed_map.get(k) or 0), float(target_map.get(k) or 0), cats)
        rows.append({"담당자": name, "목표": f["target"], "확정매출": f["closed"],
                     "Commit": f["commit"], "Best Case": f["best_case"],
                     "Pipeline": f["pipeline"], "Commit포함전망": f["commit_total"],
                     "목표갭": f["gap"], "달성률": round(f["commit_attainment"], 1),
                     "커버리지": round(f["coverage"], 1)})
    out = pd.DataFrame(rows)
    return out.sort_values("목표갭", ascending=False).reset_index(drop=True) if not out.empty else out


def snapshot_trend(yyyymm: str, db_path: str | None = None) -> pd.DataFrame:
    """주차별 파이프라인 변동 (예측이 흔들리는 구간을 찾는 용도)."""
    scope_sql, scope_params = db._scope_clause()
    df = db._df(
        f"SELECT snap_date AS 기준일, category AS 구분, SUM(amount) AS 금액 "
        f"FROM pipeline_snapshots WHERE yyyymm=?{scope_sql} "
        f"GROUP BY snap_date, category ORDER BY snap_date", [yyyymm, *scope_params], db_path)
    if df.empty:
        return df
    return df.pivot(index="기준일", columns="구분", values="금액").fillna(0).astype("int64").reset_index()


def forecast_accuracy(months: int = 6, db_path: str | None = None) -> pd.DataFrame:
    """예측 정확도: 각 월의 첫 스냅샷 Commit 금액 대비 실제 매출.

    100%에 가까울수록 예측 신뢰도가 높다. 대기업에서 영업 조직을 평가하는
    핵심 지표 중 하나이며, 120% 이상은 과소예측(사고 은폐), 80% 미만은
    과대예측(파이프라인 부풀리기)으로 본다.
    """
    end = date.today().replace(day=1)
    rows = []
    for i in range(months, 0, -1):
        ym = (end - pd.DateOffset(months=i)).strftime("%Y-%m")
        first = db._one(
            "SELECT snap_date FROM pipeline_snapshots WHERE yyyymm=? ORDER BY snap_date LIMIT 1",
            [ym], db_path)
        if not first:
            continue
        commit = db._scalar(
            "SELECT SUM(amount) FROM pipeline_snapshots WHERE yyyymm=? AND snap_date=? "
            "AND category='Commit'", [ym, first["snap_date"]], db_path)
        actual = db._scalar(f"SELECT SUM(amount) FROM sales WHERE {db.ACTIVE_SALE} "
                            f"AND substr(sale_date, 1, 7)=?", [ym], db_path)
        rows.append({"월": ym, "월초 Commit": int(commit), "실제 매출": int(actual),
                     "예측정확도": round(actual / commit * 100, 1) if commit else 0.0})
    return pd.DataFrame(rows)


# ============================================================================
# 4. 파이프라인 분석 - 전환율 / 체류일 / Win-Loss
# ============================================================================
def stage_conversion(days: int = 365, db_path: str | None = None) -> pd.DataFrame:
    """단계별 진입 건수와 다음 단계 전환율, 평균 체류일."""
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    # 진입건수는 to_stage 기준, 체류일은 from_stage 기준으로 집계해야 의미가 맞다.
    # (days_in_stage 는 '그 단계에 머문 일수'를 이탈 시점에 기록한 값이다)
    entered = db._df(
        "SELECT h.to_stage AS 단계, COUNT(*) AS 진입건수 "
        "FROM deal_stage_history h WHERE h.changed_at >= ? GROUP BY h.to_stage", [since], db_path)
    dwell = db._df(
        "SELECT h.from_stage AS 단계, AVG(h.days_in_stage) AS 평균체류일 "
        "FROM deal_stage_history h WHERE h.changed_at >= ? AND h.from_stage IS NOT NULL "
        "GROUP BY h.from_stage", [since], db_path)
    base = pd.DataFrame({"단계": db.STAGES})
    out = base.merge(entered, on="단계", how="left").merge(dwell, on="단계", how="left").fillna(0)
    out["진입건수"] = out["진입건수"].astype("int64")
    out["평균체류일"] = out["평균체류일"].round(1)

    # 전환율: 다음 단계 진입건수 / 현재 단계 진입건수
    # 전환율: 다음 단계 진입건수 ÷ 현재 단계 진입건수 (협상 → 수주는 곧 승률)
    rates, labels = [], []
    for i, row in out.iterrows():
        if row["단계"] in (db.STAGE_WON, db.STAGE_LOST):
            rates.append(None)
            labels.append("-")
            continue
        nxt_stage = db.STAGES[i + 1]
        nxt = out.iloc[i + 1]["진입건수"]
        rates.append(round(nxt / row["진입건수"] * 100, 1) if row["진입건수"] else 0.0)
        labels.append(f"→ {nxt_stage}")
    out["전환대상"] = labels
    out["전환율"] = rates
    return out


def sales_velocity(days: int = 180, db_path: str | None = None) -> dict:
    """영업 속도 = (기회수 × 평균 수주금액 × 승률) ÷ 평균 영업주기(일).

    '하루에 얼마를 만들어내는 조직인가'를 보는 지표로, 개선 활동의
    효과를 단일 숫자로 확인할 때 쓴다.
    """
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    scope_sql, scope_params = db._scope_clause()
    won = db._df(
        f"SELECT amount, {db.days_between('created_at', 'closed_at')} AS cycle "
        f"FROM deals WHERE stage='수주' AND closed_at >= ?{scope_sql}",
        [since, *scope_params], db_path)
    lost_cnt = db._scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage='실주' AND closed_at >= ?{scope_sql}",
        [since, *scope_params], db_path)
    open_cnt = db._scalar(
        f"SELECT COUNT(*) FROM deals WHERE stage NOT IN ('수주','실주'){scope_sql}",
        scope_params, db_path)

    # 영업주기는 양수인 건만 사용한다(데이터 이관 등으로 역전된 건 제외)
    if not won.empty:
        won = won[won["cycle"].fillna(0) > 0]
    won_cnt = len(won)
    avg_deal = float(won["amount"].mean()) if won_cnt else 0.0
    win_rate = won_cnt / (won_cnt + lost_cnt) if (won_cnt + lost_cnt) else 0.0
    cycle = float(won["cycle"].mean()) if won_cnt and won["cycle"].notna().any() else 0.0
    velocity = (open_cnt * avg_deal * win_rate / cycle) if cycle else 0.0
    return {"open_deals": int(open_cnt), "won_cnt": won_cnt, "lost_cnt": int(lost_cnt),
            "avg_deal": int(avg_deal), "win_rate": round(win_rate * 100, 1),
            "avg_cycle_days": round(cycle, 1), "velocity_per_day": int(velocity),
            "velocity_per_month": int(velocity * 30)}


def win_loss_analysis(days: int = 365, db_path: str | None = None) -> dict:
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    scope_sql, scope_params = db._scope_clause("d")
    reasons = db._df(
        f"SELECT COALESCE(d.lost_reason,'미기재') AS 실주사유, COUNT(*) AS 건수, "
        f"SUM(d.amount) AS 금액 FROM deals d WHERE d.stage='실주' AND d.closed_at >= ?{scope_sql} "
        f"GROUP BY 1 ORDER BY 건수 DESC", [since, *scope_params], db_path)
    competitor = db._df(
        f"SELECT CASE WHEN COALESCE(d.competitor,'')='' THEN '경쟁사 없음' ELSE d.competitor END AS 경쟁사, "
        f"SUM(CASE WHEN d.stage='수주' THEN 1 ELSE 0 END) AS 수주, "
        f"SUM(CASE WHEN d.stage='실주' THEN 1 ELSE 0 END) AS 실주 "
        f"FROM deals d WHERE d.stage IN ('수주','실주') AND d.closed_at >= ?{scope_sql} "
        f"GROUP BY 1", [since, *scope_params], db_path)
    if not competitor.empty:
        total = competitor["수주"] + competitor["실주"]
        competitor["승률"] = (competitor["수주"] / total.replace(0, pd.NA) * 100).fillna(0).round(1)
    source = db._df(
        f"SELECT COALESCE(d.source,'미기재') AS 유입경로, "
        f"SUM(CASE WHEN d.stage='수주' THEN 1 ELSE 0 END) AS 수주, "
        f"SUM(CASE WHEN d.stage='실주' THEN 1 ELSE 0 END) AS 실주, "
        f"SUM(CASE WHEN d.stage='수주' THEN d.amount ELSE 0 END) AS 수주금액 "
        f"FROM deals d WHERE d.stage IN ('수주','실주') AND d.closed_at >= ?{scope_sql} "
        f"GROUP BY 1 ORDER BY 수주금액 DESC", [since, *scope_params], db_path)
    return {"reasons": reasons, "competitor": competitor, "source": source}


# ============================================================================
# 5. 채권 관리 (AR Aging / 여신)
# ============================================================================
def ar_aging(db_path: str | None = None) -> pd.DataFrame:
    """미수 채권을 결제기일 경과일 기준으로 분류한다."""
    scope_sql, scope_params = db._scope_clause("s")
    df = db._df(
        f"SELECT s.id, s.sale_date AS 매출일, s.due_date AS 결제기일, c.name AS 거래처, "
        f"s.item AS 품목, COALESCE(s.total_amount, s.amount) AS \"청구액(VAT포함)\", COALESCE(s.paid_amount,0) AS 입금액, "
        f"COALESCE(s.total_amount, s.amount) - COALESCE(s.paid_amount,0) AS 미수금, s.owner AS 담당자, "
        f"s.status AS 상태, "
        f"{db.days_since('s.due_date')} AS 경과일 "
        f"FROM sales s JOIN customers c ON c.id = s.customer_id "
        f"WHERE s.status NOT IN ('입금완료','취소') AND COALESCE(s.total_amount, s.amount) > COALESCE(s.paid_amount,0){scope_sql} "
        f"ORDER BY 경과일 DESC", scope_params, db_path)
    if df.empty:
        return df

    def bucket(days: Any) -> str:
        d = int(days or 0)
        if d <= 0:
            return "정상"
        if d <= 30:
            return "1~30일"
        if d <= 60:
            return "31~60일"
        if d <= 90:
            return "61~90일"
        return "90일 초과"

    df["연체구간"] = df["경과일"].map(bucket)
    return df


def ar_summary(db_path: str | None = None, aging: pd.DataFrame | None = None) -> pd.DataFrame:
    """연체구간별 건수·미수금. 이미 계산한 ar_aging 결과가 있으면 넘겨 다시 읽지 않는다."""
    df = aging if aging is not None else ar_aging(db_path)
    if df.empty:
        return pd.DataFrame({"연체구간": db.AR_BUCKETS, "건수": 0, "미수금": 0})
    out = df.groupby("연체구간").agg(건수=("id", "count"), 미수금=("미수금", "sum")).reset_index()
    base = pd.DataFrame({"연체구간": db.AR_BUCKETS})
    return base.merge(out, on="연체구간", how="left").fillna(0).astype({"건수": "int64", "미수금": "int64"})


def credit_exposure(db_path: str | None = None) -> pd.DataFrame:
    """거래처별 여신한도 대비 미수 잔액 (한도 초과 거래처 조기 발견)."""
    scope_sql, scope_params = db._scope_clause("c")
    df = db._df(
        f"SELECT c.name AS 거래처, c.grade AS 등급, c.owner AS 담당자, "
        f"COALESCE(c.credit_limit,0) AS 여신한도, "
        f"COALESCE(SUM(CASE WHEN s.status NOT IN ('입금완료','취소') "
        f"     THEN COALESCE(s.total_amount, s.amount) - COALESCE(s.paid_amount,0) ELSE 0 END), 0) AS 미수잔액 "
        f"FROM customers c LEFT JOIN sales s ON s.customer_id = c.id "
        f"WHERE 1=1{scope_sql} GROUP BY c.id ORDER BY 미수잔액 DESC", scope_params, db_path)
    if df.empty:
        return df
    df["소진율"] = (df["미수잔액"] / df["여신한도"].replace(0, pd.NA) * 100).fillna(0).round(1)
    df["한도초과"] = df.apply(
        lambda r: "초과" if r["여신한도"] and r["미수잔액"] > r["여신한도"] else "", axis=1)
    return df


PAY_METHODS = ["계좌이체", "어음", "카드", "현금", "상계", "기타"]


def record_payment(sale_id: int, amount: int, db_path: str | None = None, source: str = "수기",
                   expected_before: int | None = None, pay_date: str | None = None, method: str = "계좌이체",
                   ref_no: str = "", memo: str = "") -> int:
    """입금 등록. 전액 입금되면 상태를 자동으로 '입금완료'로 바꾼다. 반영된 금액을 돌려준다.

    source: '수기' 또는 'ERP' (ERP 입금 대사로 반영된 경우)
    expected_before: ERP 대사처럼 '이 입금액을 기준으로 차액을 계산했다'는 값. 그 사이 입금이 바뀌었으면
                     다시 더하지 않고 오류를 낸다(같은 차액이 두 번 반영되는 것을 막는다).
    수기 입금은 다른 입금과 겹치면 최신 입금액으로 다시 계산해 최대 3번 시도한다.
    """
    for attempt in range(3):
        try:
            return _record_payment_once(sale_id, amount, db_path, source, expected_before,
                                        {"pay_date": pay_date, "method": method, "ref_no": ref_no, "memo": memo})
        except db.ConflictError:
            if expected_before is not None or attempt == 2:
                raise
    raise AssertionError("unreachable")


def _record_payment_once(sale_id: int, amount: int, db_path: str | None, source: str,
                         expected_before: int | None, info: dict | None = None, reversal_of: int | None = None) -> int:
    from . import periods
    info = info or {}
    pay_date = db._d(info.get("pay_date")) or date.today().isoformat()
    periods.check(pay_date, "입금")
    row = db.get_sale(sale_id, db_path)
    db.check_record_scope(row, "매출")
    if row["status"] == db.SALE_CANCELLED:
        raise ValueError("취소된 매출에는 입금을 등록할 수 없습니다.")
    before = int(row.get("paid_amount") or 0)
    if expected_before is not None and before != int(expected_before):
        raise db.ConflictError("대사 중 다른 입금이 들어와 이 건은 반영하지 않았습니다. 다시 대사하세요.")
    paid = before + int(amount)
    if paid < 0:
        raise ValueError("입금액 합계가 음수가 될 수 없습니다.")
    total = int(row.get("total_amount") or row["amount"])      # 채권은 부가세 포함 합계 기준
    if paid > total:
        raise ValueError(f"입금액이 미수금({total - before:,}원, 부가세 포함)을 넘습니다. "
                         f"과입금은 별도 반제 처리가 필요합니다.")
    status = "입금완료" if paid >= total else ("부분입금" if paid > 0 else "입금대기")
    with db.get_conn(db_path) as conn:
        changed = conn.execute("UPDATE sales SET paid_amount=?, status=?, row_version=COALESCE(row_version,0)+1 "
                               "WHERE id=? AND COALESCE(paid_amount,0)=? AND status <> ?",
                               (paid, status, sale_id, before, db.SALE_CANCELLED)).rowcount
        if not changed:
            raise db.ConflictError("같은 매출에 다른 입금이 동시에 들어왔습니다. 다시 시도하세요.")
        method = info.get("method") if info.get("method") in PAY_METHODS else ("기타" if info.get("method") else "계좌이체")
        conn.execute("INSERT INTO payments (sale_id, pay_date, amount, method, source, ref_no, memo, reversal_of, "
                     "created_by, created_by_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (sale_id, pay_date, int(amount), method, source, (info.get("ref_no") or "").strip() or None,
                      (info.get("memo") or "").strip() or None, reversal_of, db.current_actor(),
                      db.current_actor_id(), db._now()))
    db.audit("입금반제" if reversal_of else "입금등록", "매출", sale_id,
             {"입금액": int(amount), "입금일": pay_date, "누적": paid, "상태": status, "출처": source,
              "반제대상": reversal_of}, db_path)
    return int(amount)


NON_REVERSIBLE_SOURCES = ("반품상계", "선수금", "대손")


def reverse_payment(payment_id: int, reason: str, db_path: str | None = None) -> int:
    """반제: 잘못 넣은 입금을 지우지 않고 같은 금액의 음수 입금으로 되돌린다(오늘 날짜)."""
    if not str(reason or "").strip():
        raise ValueError("반제 사유를 입력하세요.")
    pay = db._one("SELECT * FROM payments WHERE id=?", [int(payment_id)], db_path)
    if not pay or int(pay["amount"]) <= 0 or pay.get("reversal_of"):
        raise ValueError("반제할 수 있는 입금이 아닙니다.")
    if (pay.get("source") or "") in NON_REVERSIBLE_SOURCES:
        # 반품상계·선수금 배분·대손은 돈이 들어온 입금이 아니라 내부 정리라, 반제하면 반품한 물건 값을 다시 청구하거나
        # 선수금이 사라진다 → 각자의 화면(반품 취소·선수금·대손 결재)에서 되돌린다
        raise ValueError(f"'{pay['source']}' 입금은 반제할 수 없습니다 — 해당 화면에서 정리하세요.")
    if db._one("SELECT id FROM payments WHERE reversal_of=?", [int(payment_id)], db_path):
        raise ValueError("이미 반제한 입금입니다.")
    for attempt in range(3):
        try:
            return _record_payment_once(int(pay["sale_id"]), -int(pay["amount"]), db_path, "반제", None,
                                        {"method": pay.get("method"), "ref_no": pay.get("ref_no"),
                                         "memo": f"반제: {reason.strip()}"}, reversal_of=int(payment_id))
        except db.ConflictError:
            if attempt == 2:
                raise
        except Exception as exc:                     # noqa: BLE001 - 동시에 반제한 경우 (ux_payments_reversal)
            if "unique" in str(exc).lower() or "ux_payments_reversal" in str(exc):
                raise ValueError("이미 반제한 입금입니다.") from exc
            raise
    raise AssertionError("unreachable")


def list_payments(sale_id: int, db_path: str | None = None) -> list[dict]:
    rows = db._df("SELECT p.*, (SELECT id FROM payments r WHERE r.reversal_of = p.id) AS reversed_by "
                  "FROM payments p WHERE p.sale_id=? ORDER BY p.id", [int(sale_id)], db_path)
    return rows.to_dict("records")

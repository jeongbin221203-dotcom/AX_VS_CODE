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

import sales_db as db

MODULE_VERSION = 2


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


def upsert_user(data: dict, db_path: str | None = None) -> int:
    if not str(data.get("emp_no", "")).strip():
        raise ValueError("사번은 필수입니다.")
    if not str(data.get("name", "")).strip():
        raise ValueError("이름은 필수입니다.")
    role = data.get("role") or "REP"
    if role not in db.ROLES:
        raise ValueError(f"역할 값이 올바르지 않습니다: {role}")
    fields = ["emp_no", "name", "role", "org_id", "email", "active"]
    values = [str(data["emp_no"]).strip(), str(data["name"]).strip(), role,
              data.get("org_id"), data.get("email"), int(data.get("active", 1))]
    with db.get_conn(db_path) as conn:
        if data.get("id"):
            conn.execute(f"UPDATE users SET {', '.join(f'{f}=?' for f in fields)} WHERE id=?",
                         (*values, int(data["id"])))
            new_id = int(data["id"])
        else:
            cur = conn.execute(
                f"INSERT INTO users ({', '.join(fields)}, created_at) "
                f"VALUES ({', '.join('?' * len(fields))}, ?)", (*values, db._now()))
            new_id = int(cur.lastrowid)
    db.audit("수정" if data.get("id") else "등록", "사용자", new_id,
             {"사번": values[0], "이름": values[1], "역할": role}, db_path)
    return new_id


def delete_user(user_id: int, db_path: str | None = None) -> None:
    """실제 삭제 대신 비활성화한다(이력 보존이 원칙)."""
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    db.audit("비활성화", "사용자", user_id, None, db_path)


def visible_owners(user: dict, db_path: str | None = None) -> Optional[list[str]]:
    """역할별 데이터 접근범위를 담당자 이름 목록으로 변환한다.

    None 을 반환하면 '전사 조회'를 뜻한다.
      REP     : 본인 데이터만
      MANAGER : 본인 조직 + 하위 조직 소속원 전체
      EXEC    : 전사 (읽기 중심)
      ADMIN   : 전사 + 관리 기능
    """
    if not user:
        return []
    role = user.get("role", "REP")
    if role in ("EXEC", "ADMIN"):
        return None
    if role == "MANAGER":
        org_ids = descendant_org_ids(user.get("org_id"), db_path)
        if not org_ids:
            return [user["name"]]
        placeholders = ",".join("?" * len(org_ids))
        rows = db._df(f"SELECT name FROM users WHERE org_id IN ({placeholders}) AND active=1",
                      org_ids, db_path)
        names = rows["name"].tolist() if not rows.empty else []
        if user["name"] not in names:
            names.append(user["name"])
        return names
    return [user["name"]]


def apply_context(user: dict, db_path: str | None = None) -> None:
    """로그인 사용자 기준으로 감사로그 주체와 데이터 접근범위를 설정한다."""
    db.set_context(actor=(user or {}).get("name", "system"),
                   owner_scope=visible_owners(user, db_path))


def has_role(user: dict, minimum: str) -> bool:
    return db.ROLES.get((user or {}).get("role", "REP"), 0) >= db.ROLES.get(minimum, 99)


def seed_org_demo(db_path: str | None = None) -> dict:
    """샘플 조직도와 사용자 생성. 기존 담당자 이름을 사용자로 자동 편입한다."""
    existing = {u["이름"] for _, u in list_users(False, db_path).iterrows()} \
        if not list_users(False, db_path).empty else set()
    hq = upsert_org("영업본부", None, "본부", db_path=db_path)
    team1 = upsert_org("영업1팀", hq, "팀", db_path=db_path)
    team2 = upsert_org("영업2팀", hq, "팀", db_path=db_path)

    plan = [
        ("2001", "정임원", "EXEC", hq),
        ("2002", "한팀장", "MANAGER", team1),
        ("2003", "김영업", "REP", team1),
        ("2004", "이수주", "REP", team1),
        ("2005", "서팀장", "MANAGER", team2),
        ("2006", "박고객", "REP", team2),
        ("2007", "최성과", "REP", team2),
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


# ============================================================================
# 2. 결재 (Deal Desk) - 할인 승인
# ============================================================================
def request_approval(deal_id: int, requested_by: str, reason: str = "",
                     db_path: str | None = None) -> int:
    deal = db.get_deal(deal_id, db_path)
    if not deal:
        raise ValueError("존재하지 않는 영업기회입니다.")
    list_amount = int(deal.get("list_amount") or deal.get("amount") or 0)
    final_amount = int(deal.get("amount") or 0)
    rate = float(deal.get("discount_rate") or 0)
    role = db.required_approval_role(rate)
    if not role:
        raise ValueError("할인율이 0% 이므로 승인이 필요하지 않습니다.")

    with db.get_conn(db_path) as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE deal_id=? AND status='대기'", (deal_id,)
        ).fetchone()[0]
        if pending:
            raise ValueError("이미 결재 대기 중인 건입니다.")
        cur = conn.execute(
            "INSERT INTO approvals (deal_id, kind, requested_by, requested_at, list_amount, "
            "final_amount, discount_rate, required_role, reason, status) "
            "VALUES (?,?,?,?,?,?,?,?,?, '대기')",
            (deal_id, "할인승인", requested_by, db._now(), list_amount, final_amount,
             rate, role, reason))
        conn.execute("UPDATE deals SET approval_status='대기' WHERE id=?", (deal_id,))
        new_id = int(cur.lastrowid)
    db.audit("결재요청", "승인", new_id,
             {"기회": deal.get("title"), "할인율": rate, "필요권한": db.ROLE_LABEL[role]}, db_path)
    return new_id


def decide_approval(approval_id: int, approver: dict, approve: bool, comment: str = "",
                    db_path: str | None = None) -> None:
    row = db._one("SELECT * FROM approvals WHERE id=?", [approval_id], db_path)
    if not row:
        raise ValueError("존재하지 않는 결재 건입니다.")
    if row["status"] != "대기":
        raise ValueError(f"이미 처리된 건입니다(현재 상태: {row['status']}).")
    if not has_role(approver, row["required_role"]):
        raise PermissionError(
            f"이 건은 {db.ROLE_LABEL.get(row['required_role'], row['required_role'])} "
            f"이상만 결재할 수 있습니다.")
    if approver.get("name") == row["requested_by"] and not has_role(approver, "ADMIN"):
        raise PermissionError("본인이 요청한 건은 본인이 결재할 수 없습니다(자기결재 금지).")

    status = "승인" if approve else "반려"
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE approvals SET status=?, approver=?, decided_at=?, comment=? WHERE id=?",
                     (status, approver.get("name"), db._now(), comment, approval_id))
        conn.execute("UPDATE deals SET approval_status=? WHERE id=?", (status, row["deal_id"]))
    db.audit(status, "승인", approval_id,
             {"기회ID": row["deal_id"], "할인율": row["discount_rate"], "의견": comment}, db_path)


def list_approvals(status: str = "", requested_by: str = "", db_path: str | None = None) -> pd.DataFrame:
    sql = """
        SELECT a.id, a.requested_at AS 요청일시, c.name AS 거래처, d.title AS 기회명,
               a.list_amount AS 정가, a.final_amount AS 제안가, a.discount_rate AS 할인율,
               a.required_role AS 필요권한코드, a.requested_by AS 요청자, a.reason AS 사유,
               a.status AS 상태, a.approver AS 결재자, a.decided_at AS 결재일시,
               a.comment AS 결재의견, a.deal_id
          FROM approvals a
          JOIN deals d ON d.id = a.deal_id
          JOIN customers c ON c.id = d.customer_id
         WHERE 1=1
    """
    params: list[Any] = []
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    if requested_by:
        sql += " AND a.requested_by = ?"
        params.append(requested_by)
    scope_sql, scope_params = db._scope_clause("d")
    sql += scope_sql + " ORDER BY a.id DESC"
    out = db._df(sql, params + scope_params, db_path)
    if not out.empty:
        out.insert(8, "필요권한", out["필요권한코드"].map(db.ROLE_LABEL).fillna(out["필요권한코드"]))
        out = out.drop(columns=["필요권한코드"])
    return out


def pending_for(user: dict, db_path: str | None = None) -> pd.DataFrame:
    """내가 결재해야 할 건 (권한 등급 충족 + 본인 요청 건 제외)."""
    pending = list_approvals("대기", db_path=db_path)
    if pending.empty:
        return pending
    allowed = [r for r in db.ROLES if has_role(user, r)]
    labels = [db.ROLE_LABEL[r] for r in allowed]
    out = pending[pending["필요권한"].isin(labels)]
    if not has_role(user, "ADMIN"):
        out = out[out["요청자"] != user.get("name")]
    return out.reset_index(drop=True)


# ============================================================================
# 3. 매출예측 (Forecast)
# ============================================================================
def take_snapshot(yyyymm: str, snap_date: str | None = None, db_path: str | None = None) -> int:
    """현재 파이프라인을 담당자·예측구분별로 집계해 스냅샷으로 저장한다.

    같은 날 다시 실행하면 해당 일자 스냅샷을 갱신한다(주 1회 실행 권장).
    """
    snap = snap_date or date.today().strftime("%Y-%m-%d")
    rows = db._df(
        "SELECT owner, COALESCE(forecast_category,'Pipeline') AS category, COUNT(*) AS cnt, "
        "SUM(amount) AS amount, SUM(amount*probability/100.0) AS weighted "
        "FROM deals WHERE stage NOT IN ('실주') "
        "AND strftime('%Y-%m', COALESCE(expected_close, closed_at)) = ? "
        "GROUP BY owner, category", [yyyymm], db_path)
    if rows.empty:
        return 0
    with db.get_conn(db_path) as conn:
        for r in rows.itertuples():
            conn.execute(
                "INSERT INTO pipeline_snapshots (snap_date, yyyymm, owner, category, deal_cnt, "
                "amount, weighted) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(snap_date, yyyymm, owner, category) DO UPDATE SET "
                "deal_cnt=excluded.deal_cnt, amount=excluded.amount, weighted=excluded.weighted",
                (snap, yyyymm, r.owner, r.category, int(r.cnt), int(r.amount or 0),
                 int(r.weighted or 0)))
    db.audit("스냅샷", "예측", None, {"대상월": yyyymm, "일자": snap, "건수": len(rows)}, db_path)
    return len(rows)


def forecast_summary(yyyymm: str, owner: str = "", db_path: str | None = None) -> dict:
    """이번 달 예측 요약: 확정 매출 + 카테고리별 잔여 파이프라인 + 커버리지."""
    oc, op = db._owner_clause(owner)
    closed = db._scalar(
        f"SELECT SUM(amount) FROM sales WHERE strftime('%Y-%m', sale_date)=?{oc}",
        [yyyymm, *op], db_path)
    target = db._scalar(
        f"SELECT SUM(target_amount) FROM targets WHERE yyyymm=?{oc}", [yyyymm, *op], db_path)

    cats = {}
    for cat in db.FORECAST_CATS:
        cats[cat] = int(db._scalar(
            f"SELECT SUM(amount) FROM deals WHERE stage NOT IN ('수주','실주') "
            f"AND COALESCE(forecast_category,'Pipeline')=? "
            f"AND strftime('%Y-%m', expected_close)=?{oc}", [cat, yyyymm, *op], db_path))

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
    """담당자별 예측 테이블 (팀장·임원이 주간 예측회의에서 보는 형태)."""
    names = db.owners(db_path)
    rows = []
    for name in names:
        f = forecast_summary(yyyymm, name, db_path)
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
        actual = db._scalar("SELECT SUM(amount) FROM sales WHERE strftime('%Y-%m', sale_date)=?",
                            [ym], db_path)
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
        f"SELECT amount, julianday(closed_at) - julianday(created_at) AS cycle "
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
        f"s.item AS 품목, s.amount AS 매출액, COALESCE(s.paid_amount,0) AS 입금액, "
        f"s.amount - COALESCE(s.paid_amount,0) AS 미수금, s.owner AS 담당자, s.status AS 상태, "
        f"CAST(julianday('now') - julianday(s.due_date) AS INTEGER) AS 경과일 "
        f"FROM sales s JOIN customers c ON c.id = s.customer_id "
        f"WHERE s.status <> '입금완료' AND s.amount > COALESCE(s.paid_amount,0){scope_sql} "
        f"ORDER BY 경과일 DESC", scope_params, db_path)
    if df.empty:
        return df

    def bucket(days: Any) -> str:
        d = int(days or 0)
        if d <= 0:
            return "정상"
        if d <= 30:
            return "30일 초과"
        if d <= 60:
            return "60일 초과"
        return "90일 초과"

    df["연체구간"] = df["경과일"].map(bucket)
    return df


def ar_summary(db_path: str | None = None) -> pd.DataFrame:
    df = ar_aging(db_path)
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
        f"COALESCE(SUM(CASE WHEN s.status <> '입금완료' "
        f"     THEN s.amount - COALESCE(s.paid_amount,0) ELSE 0 END), 0) AS 미수잔액 "
        f"FROM customers c LEFT JOIN sales s ON s.customer_id = c.id "
        f"WHERE 1=1{scope_sql} GROUP BY c.id ORDER BY 미수잔액 DESC", scope_params, db_path)
    if df.empty:
        return df
    df["소진율"] = (df["미수잔액"] / df["여신한도"].replace(0, pd.NA) * 100).fillna(0).round(1)
    df["한도초과"] = df.apply(
        lambda r: "초과" if r["여신한도"] and r["미수잔액"] > r["여신한도"] else "", axis=1)
    return df


def record_payment(sale_id: int, amount: int, db_path: str | None = None) -> None:
    """입금 등록. 전액 입금되면 상태를 자동으로 '입금완료'로 바꾼다."""
    row = db._one("SELECT amount, COALESCE(paid_amount,0) AS paid FROM sales WHERE id=?",
                  [sale_id], db_path)
    if not row:
        raise ValueError("존재하지 않는 매출입니다.")
    paid = int(row["paid"]) + int(amount)
    if paid < 0:
        raise ValueError("입금액 합계가 음수가 될 수 없습니다.")
    paid = min(paid, int(row["amount"]))
    status = "입금완료" if paid >= int(row["amount"]) else ("부분입금" if paid > 0 else "입금대기")
    with db.get_conn(db_path) as conn:
        conn.execute("UPDATE sales SET paid_amount=?, status=? WHERE id=?", (paid, status, sale_id))
    db.audit("입금등록", "매출", sale_id, {"입금액": int(amount), "누적": paid, "상태": status}, db_path)

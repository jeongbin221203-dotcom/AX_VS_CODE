"""결재 흐름 한곳에: 실사 조정 · 구매요청(단계 결재) · 발주 결재를 '내 차례' 기준으로 모은다.

- 내 결재 대기: 지금 내가(또는 나에게 대결을 맡긴 사람이) 처리할 차례인 결재만. 본인이 올린 것·이미 결재한 단계는 빠진다.
  구매요청은 다음 단계에 필요한 역할(1·2단계 관리자, 3단계 시스템관리자)을 가진 사람에게만 보인다.
- 대신 결재(대결)하면 기록에 '이름 (대결: 맡긴 사람)'으로 남고, 직무 분리 검사는 그대로(맡긴 사람이 요청자여도 막는다).
- 내 요청 현황: 내가 올린 결재가 어디까지 왔는지. 결재 이력: 내가 처리한 결재.
- 결재 기한(config.APPROVAL_SLA_HOURS)을 넘긴 결재는 배치(approval_remind)가 하루 한 번 결재자에게 다시 알린다.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

import config
from core import approvals, auth, db, delegation, notify, org, purchasing, services
from core.utils import now_str

KIND = {"ADJ": "실사 조정", "CANCEL": "거래 취소", "PR": "구매요청", "PO": "발주"}


def authorities(user: dict) -> list[dict]:
    """내가 결재할 수 있는 자격: 나 + 오늘 나에게 결재를 맡긴 사람들(그 사람의 역할·창고 범위)."""
    out = [{"id": user["id"], "name": user["name"], "role": user["role"], "wh_ids": org.allowed_warehouses(user), "via": None}]
    for d in delegation.delegators(user["id"]):
        out.append({"id": int(d["id"]), "name": d["name"], "role": d["role"], "wh_ids": org.allowed_warehouses(d),
                    "via": d["name"]})
    return out


def _in(wh_ids, wh) -> bool:
    return wh_ids is None or int(wh) in wh_ids


def _hours(ts: str) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(str(ts)[:19])).total_seconds() / 3600
    except ValueError:
        return 0.0


def queue(user: dict) -> list[dict]:
    """지금 내 차례인 결재 (오래된 순)."""
    auths = authorities(user)
    me = user["id"]
    items: list[dict] = []

    def pick(blocked: set, wh: int, need: str):
        for a in auths:
            if a["id"] in blocked or me in blocked:
                continue
            if auth.has_role(a, need) and _in(a["wh_ids"], wh):
                return a
        return None

    import json
    for r in approvals.requests_df("PENDING").to_dict("records"):
        a = pick({r["requested_by_id"]}, r["warehouse_id"], "MANAGER")
        if a:
            extra = json.loads(r["payload"] or "{}")
            title = (f"거래 #{extra.get('tx_id')} ({extra.get('tx_date')} {extra.get('tx_type')}) [{r['code']}] {r['name']} "
                     f"{float(r['qty']):,.2f} {r['unit']} · {r['wh_code']} — 사유: {extra.get('reason', '')}"
                     if r["kind"] == "CANCEL" else
                     f"[{r['code']}] {r['name']} {float(r['qty']):+,.2f} {r['unit']} · {r['wh_code']}")
            items.append({"kind": r["kind"] if r["kind"] in KIND else "ADJ", "id": int(r["id"]), "no": f"#{r['id']}",
                          "warehouse_id": int(r["warehouse_id"]), "title": title,
                          "amount": float(r["amount"]), "requested_by": r["requested_by"], "requested_at": r["requested_at"],
                          "step": "", "link": "/approvals/?tab=adj", "via": a["via"]})
    prs = db.query_df("""
        SELECT p.*, w.code AS wh_code FROM purchase_requests p JOIN warehouses w ON w.id = p.warehouse_id
        WHERE p.status = 'PENDING' ORDER BY p.requested_at""")
    done = db.query_df("SELECT pr_id, approver_id FROM pr_approvals WHERE decision = 'APPROVE'")
    for r in prs.to_dict("records"):
        approved = set(done.loc[done["pr_id"] == r["id"], "approver_id"].dropna().astype(int))
        step = len(approved) + 1
        a = pick({r["requested_by_id"], *approved}, r["warehouse_id"], purchasing.step_role(step))
        if a:
            items.append({"kind": "PR", "id": int(r["id"]), "no": r["pr_no"], "warehouse_id": int(r["warehouse_id"]),
                          "title": f"{r['wh_code']} · {r['reason']}", "amount": float(r["total_amount"]),
                          "requested_by": r["requested_by"], "requested_at": r["requested_at"],
                          "step": f"{step}/{int(r['required_steps'])}단계", "link": f"/purchase/pr/{r['id']}", "via": a["via"]})
    pos = db.query_df("""
        SELECT o.*, r.requested_by_id, w.code AS wh_code FROM purchase_orders o JOIN warehouses w ON w.id = o.warehouse_id
        LEFT JOIN purchase_requests r ON r.id = o.pr_id WHERE o.status = 'PENDING_APPROVAL' ORDER BY o.created_at""")
    for r in pos.to_dict("records"):
        blocked = {r["created_by_id"], r["requested_by_id"]} - {None}
        a = pick({int(x) for x in blocked if x == x}, r["warehouse_id"], "MANAGER")
        if a:
            items.append({"kind": "PO", "id": int(r["id"]), "no": r["po_no"], "warehouse_id": int(r["warehouse_id"]),
                          "title": f"{r['wh_code']} · {r['supplier']} (요청 승인액 초과)", "amount": float(r["total_amount"]),
                          "requested_by": r["created_by"], "requested_at": r["created_at"], "step": "발주 결재",
                          "link": f"/purchase/po/{r['id']}", "via": a["via"]})
    for it in items:
        it["hours"] = _hours(it["requested_at"])
        it["late"] = it["hours"] > config.APPROVAL_SLA_HOURS
    return sorted(items, key=lambda x: x["requested_at"])


def count(user: dict) -> int:
    return len(queue(user))


def decide(user: dict, kind: str, item_id: int, approve: bool, comment: str, ip: str = "") -> services.Result:
    """내 결재 대기의 한 건을 승인·반려 (대결이면 맡긴 사람의 역할·범위로, 기록은 '이름 (대결: …)')."""
    item = next((x for x in queue(user) if x["kind"] == kind and x["id"] == item_id), None)
    if item is None:
        return services.Result(False, "지금 처리할 수 있는 결재가 아닙니다 (이미 처리됐거나 차례가 아님).")
    auths = {a["via"]: a for a in authorities(user)}
    a = auths[item["via"]]
    who = {"id": user["id"], "role": a["role"] if auth.level(a["role"]) > auth.level(user["role"]) else user["role"],
           "name": user["name"] + (f" (대결: {item['via']})" if item["via"] else ""), "ip": ip}
    if kind in ("ADJ", "CANCEL"):
        return approvals.decide(item_id, approve, comment, who, wh_ids=a["wh_ids"])
    if kind == "PR":
        r = purchasing.decide_pr(item_id, approve, comment, who, wh_ids=a["wh_ids"])
        return services.Result(r.ok, r.message)
    if kind == "PO":
        if not approve:
            return services.Result(False, "발주 반려는 발주 화면에서 '발주 취소'로 합니다.")
        r = purchasing.approve_po(item_id, who, wh_ids=a["wh_ids"])
        return services.Result(r.ok, r.message)
    return services.Result(False, "알 수 없는 결재입니다.")


def decide_many(user: dict, keys: list[str], ip: str = "") -> tuple[int, list[str]]:
    """여러 건 한꺼번에 승인 (keys: 'PR:12'). (승인한 건수, 실패 사유들). 반려는 사유가 필요해 한 건씩."""
    done, problems = 0, []
    for k in keys:
        kind, _, rid = k.partition(":")
        if kind not in KIND or not rid.isdigit():
            problems.append(f"{k}: 알 수 없는 결재")
            continue
        r = decide(user, kind, int(rid), True, "일괄 승인", ip)
        if r.ok:
            done += 1
        else:
            problems.append(f"{KIND[kind]} {rid}: {r.message}")
    return done, problems


def my_requests(user_id: int) -> pd.DataFrame:
    """내가 올린 결재가 어디까지 왔는지 (최근 200건)."""
    adj = db.query_df("""
        SELECT a.kind AS kind, a.id, '#' || a.id AS no, a.requested_at, a.status, a.amount, m.code || ' ' || m.name AS title,
               a.decided_by, a.comment, '' AS step
        FROM approval_requests a JOIN materials m ON m.id = a.material_id WHERE a.requested_by_id = ?""", (user_id,))
    pr = db.query_df("""
        SELECT 'PR' AS kind, p.id, p.pr_no AS no, p.requested_at, p.status, p.total_amount AS amount, p.reason AS title,
               (SELECT MAX(x.approver) FROM pr_approvals x WHERE x.pr_id = p.id AND x.step = (SELECT MAX(step) FROM pr_approvals y WHERE y.pr_id = p.id)) AS decided_by,
               (SELECT MAX(x.comment) FROM pr_approvals x WHERE x.pr_id = p.id AND x.decision = 'REJECT') AS comment,
               (SELECT COUNT(*) FROM pr_approvals x WHERE x.pr_id = p.id AND x.decision = 'APPROVE') || '/' || p.required_steps AS step
        FROM purchase_requests p WHERE p.requested_by_id = ?""", (user_id,))
    df = pd.concat([d for d in (adj, pr) if not d.empty] or [adj], ignore_index=True)
    if df.empty:
        return df
    label = {**approvals.STATUS, **purchasing.PR_STATUS}
    df["status"] = [("결재 대기" if k in ("ADJ", "CANCEL") else purchasing.PR_STATUS.get(s, s)) if s == "PENDING" else label.get(s, s)
                    for k, s in zip(df["kind"], df["status"])]
    df["kind"] = df["kind"].map(KIND)
    return df.sort_values("requested_at", ascending=False).head(200).reset_index(drop=True)


def history(user: dict) -> pd.DataFrame:
    """내가 처리한 결재 (대결 포함, 최근 200건)."""
    adj = db.query_df("""
        SELECT a.kind AS kind, a.id, '#' || a.id AS no, a.decided_at AS at, a.status AS decision, a.amount,
               m.code || ' ' || m.name AS title, a.requested_by, a.decided_by AS approver, a.comment
        FROM approval_requests a JOIN materials m ON m.id = a.material_id WHERE a.decided_by_id = ?""", (user["id"],))
    pr = db.query_df("""
        SELECT 'PR' AS kind, p.id, p.pr_no AS no, x.at, x.decision, p.total_amount AS amount, p.reason AS title,
               p.requested_by, x.approver, x.comment
        FROM pr_approvals x JOIN purchase_requests p ON p.id = x.pr_id WHERE x.approver_id = ?""", (user["id"],))
    po = db.query_df("""
        SELECT 'PO' AS kind, o.id, o.po_no AS no, o.approved_at AS at, 'APPROVE' AS decision, o.total_amount AS amount,
               o.supplier AS title, o.created_by AS requested_by, o.approved_by AS approver, '' AS comment
        FROM purchase_orders o WHERE o.approved_by = ? OR o.approved_by LIKE ?""", (user["name"], user["name"] + " (대결:%"))
    df = pd.concat([d for d in (adj, pr, po) if not d.empty] or [adj], ignore_index=True)
    if df.empty:
        return df
    df["decision"] = df["decision"].map({"APPROVED": "승인", "REJECTED": "반려", "APPROVE": "승인", "REJECT": "반려"}).fillna(df["decision"])
    df["kind"] = df["kind"].map(KIND)
    return df.sort_values("at", ascending=False).head(200).reset_index(drop=True)


# ── 결재 독촉 (배치) ──────────────────────────────────────────
def remind_overdue() -> str:
    """결재 기한을 넘긴 결재를 결재할 수 있는 사람에게 다시 알린다 (같은 결재는 하루 한 번)."""
    sla = config.APPROVAL_SLA_HOURS
    if sla <= 0:
        return "결재 기한 없음 (APPROVAL_SLA_HOURS=0)"
    today = date.today().isoformat()
    sent = 0
    with db.transaction() as conn:
        pending = []
        for r in conn.execute("SELECT id, kind, warehouse_id, requested_by_id, requested_at, amount FROM approval_requests "
                              "WHERE status = 'PENDING'"):
            pending.append((r["kind"] if r["kind"] in KIND else "ADJ", int(r["id"]), f"#{r['id']}", int(r["warehouse_id"]), "MANAGER", {r["requested_by_id"]},
                            r["requested_at"], float(r["amount"]), "/approvals/"))
        for r in conn.execute("SELECT * FROM purchase_requests WHERE status = 'PENDING'"):
            done = {x[0] for x in conn.execute("SELECT approver_id FROM pr_approvals WHERE pr_id = ? AND decision = 'APPROVE'",
                                               (r["id"],))}
            pending.append(("PR", int(r["id"]), r["pr_no"], int(r["warehouse_id"]), purchasing.step_role(len(done) + 1),
                            {r["requested_by_id"], *done}, r["updated_at"] or r["requested_at"], float(r["total_amount"]),
                            f"/purchase/pr/{r['id']}"))
        for r in conn.execute("SELECT o.*, p.requested_by_id FROM purchase_orders o LEFT JOIN purchase_requests p ON p.id = o.pr_id "
                              "WHERE o.status = 'PENDING_APPROVAL'"):
            pending.append(("PO", int(r["id"]), r["po_no"], int(r["warehouse_id"]), "MANAGER",
                            {r["created_by_id"], r["requested_by_id"]}, r["created_at"], float(r["total_amount"]),
                            f"/purchase/po/{r['id']}"))
        for kind, rid, no, wh, need, blocked, since, amount, path in pending:
            hours = _hours(since)
            if hours <= sla:
                continue
            ref = f"remind:{kind}:{rid}"
            last = conn.execute("SELECT sent_at, count FROM approval_reminders WHERE ref = ?", (ref,)).fetchone()
            if last and str(last[0])[:10] == today:
                continue
            to = notify.recipients(conn, need, wh, list(blocked))
            notify.queue(conn, to, f"[독촉] {KIND[kind]} {no} 결재가 {int(hours)}시간째 기다립니다",
                         [f"결재 기한({sla}시간)을 넘겼습니다. 처리해 주세요.", f"금액 ₩{amount:,.0f}"], path, ref)
            if last:
                conn.execute("UPDATE approval_reminders SET sent_at = ?, count = count + 1 WHERE ref = ?", (now_str(), ref))
            else:
                conn.execute("INSERT INTO approval_reminders (ref, sent_at, count) VALUES (?, ?, 1)", (ref, now_str()))
            sent += 1
    return f"독촉 {sent}건"

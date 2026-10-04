"""인사(HR) 시스템 연동 — 조직도·사원 정보를 인사 원장에 맞춘다.

  원천: SALES_HR_SOURCE = JSON 파일 경로 또는 http(s) 주소 (SALES_HR_TOKEN 이 있으면 Bearer 로 보낸다)
    {"orgs": [{"code": "S100", "name": "영업본부", "parent": null, "type": "본부"}, ...],
     "employees": [{"emp_no": "1001", "name": "김영업", "email": "...", "org": "S110",
                    "position": "사원", "status": "재직", "sales_role": "REP"}, ...]}
    sales_role 은 선택. 없으면 새 사원만 직위로 역할을 정한다(SALES_HR_ROLE_MAP, 기존 사원의 역할은 유지).

  규칙
    - 조직은 조직코드로 맞춘다. 코드가 없던 기존 조직은 같은 이름이면 코드를 붙인다. 피드에서 빠진 조직은 '폐지'(active=0).
    - 사원은 사번으로 맞춘다. 퇴직/퇴사 또는 피드에서 빠진 인사 연동 사원은 비활성화하고,
      그 사람의 대결 지정은 취소하며, 담당 거래처·영업기회가 남아 있으면 관리자에게 이관 요청 알림을 보낸다.
    - 안전장치: 피드 인원이 현재 인사 연동 활성 인원의 SALES_HR_MIN_RATIO(기본 0.8) 미만이면 멈춘다(잘린 파일 방지).
      마지막 활성 관리자는 비활성화하지 않는다. 로컬 계정(source=local)은 피드에 없어도 건드리지 않는다.
    - apply=False 로 먼저 미리보기(변경 목록)를 만들고, apply=True 로 반영한다. 반영 내역은 감사로그에 남는다.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional

from . import enterprise as ent
from . import sales_db as db

LEAVER_STATUS = {"퇴직", "퇴사", "terminated", "inactive"}
DEFAULT_ROLE_MAP = {"팀장": "MANAGER", "파트장": "MANAGER", "본부장": "EXEC", "상무": "EXEC", "전무": "EXEC",
                    "부사장": "EXEC", "사장": "EXEC"}


def source() -> str:
    return os.environ.get("SALES_HR_SOURCE", "").strip()


def role_map() -> dict[str, str]:
    raw = os.environ.get("SALES_HR_ROLE_MAP", "")
    return {**DEFAULT_ROLE_MAP, **(json.loads(raw) if raw else {})}


def load_feed(src: Optional[str] = None) -> dict:
    src = src or source()
    if not src:
        raise ValueError("인사 연동 원천이 설정되지 않았습니다 (SALES_HR_SOURCE).")
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"Accept": "application/json"})
        token = os.environ.get("SALES_HR_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as res:      # noqa: S310 - 관리자가 설정한 주소
            return parse_feed(res.read())
    return parse_feed(Path(src).read_bytes())


def parse_feed(raw: bytes | str | dict) -> dict:
    data = raw if isinstance(raw, dict) else json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    orgs = [{"code": str(o["code"]).strip(), "name": str(o["name"]).strip(),
             "parent": (str(o["parent"]).strip() if o.get("parent") else None), "type": o.get("type") or "팀"}
            for o in data.get("orgs", [])]
    emps = []
    for e in data.get("employees", []):
        emp_no, name = str(e.get("emp_no") or "").strip(), str(e.get("name") or "").strip()
        if not emp_no or not name:
            raise ValueError(f"사번·이름이 없는 사원 행이 있습니다: {e}")
        role = e.get("sales_role")
        if role and role not in db.ROLES:
            raise ValueError(f"{emp_no}: sales_role 값이 올바르지 않습니다({role}).")
        emps.append({"emp_no": emp_no, "name": name, "email": (e.get("email") or None),
                     "org": (str(e["org"]).strip() if e.get("org") else None), "position": e.get("position"),
                     "status": str(e.get("status") or "재직").strip(), "sales_role": role})
    if len({e["emp_no"] for e in emps}) != len(emps):
        raise ValueError("피드에 중복된 사번이 있습니다.")
    if len({o["code"] for o in orgs}) != len(orgs):
        raise ValueError("피드에 중복된 조직코드가 있습니다.")
    return {"orgs": orgs, "employees": emps}


# ---------------------------------------------------------------------------
# 비교 → 변경 계획
# ---------------------------------------------------------------------------
def plan(feed: dict) -> dict:
    orgs = db._df("SELECT * FROM orgs", ()).to_dict("records")
    by_code = {o["code"]: o for o in orgs if o.get("code")}
    by_name = {o["name"]: o for o in orgs}
    feed_codes = {o["code"] for o in feed["orgs"]}
    out: dict[str, list] = {"orgs_new": [], "orgs_update": [], "orgs_close": [], "users_new": [],
                            "users_update": [], "users_leave": [], "warnings": []}
    for o in feed["orgs"]:
        cur = by_code.get(o["code"]) or (by_name.get(o["name"]) if not by_name.get(o["name"], {}).get("code") else None)
        if not cur:
            out["orgs_new"].append(o)
        else:
            changes = {k: v for k, v in (("name", o["name"]), ("org_type", o["type"]), ("code", o["code"]),
                                         ("active", 1)) if cur.get(k) != v}
            parent_code = next((p.get("code") for p in orgs if p["id"] == cur.get("parent_id")), None)
            if parent_code != o["parent"]:
                changes["parent"] = o["parent"]
            if changes:
                out["orgs_update"].append({"id": cur["id"], "code": o["code"], "name": o["name"], "changes": changes})
    for o in orgs:
        if o.get("code") and o["code"] not in feed_codes and int(o["active"] if o.get("active") is not None else 1):
            out["orgs_close"].append({"id": o["id"], "code": o["code"], "name": o["name"]})

    users = {u["emp_no"]: u for u in db._df("SELECT * FROM users", ()).to_dict("records")}
    rmap = role_map()
    seen = set()
    for e in feed["employees"]:
        seen.add(e["emp_no"])
        cur = users.get(e["emp_no"])
        leaving = e["status"] in LEAVER_STATUS
        if not cur:
            if not leaving:
                role = e["sales_role"] or rmap.get(e["position"] or "", "REP")
                out["users_new"].append({**e, "role": role})
            continue
        if leaving:
            if int(cur.get("active") or 0):
                out["users_leave"].append({"id": cur["id"], "emp_no": e["emp_no"], "name": cur["name"],
                                           "reason": f"인사 상태 {e['status']}"})
            continue
        changes: dict[str, Any] = {}
        for key, new in (("name", e["name"]), ("email", e["email"]), ("position", e["position"])):
            if new is not None and cur.get(key) != new:
                changes[key] = new
        if e["org"] is not None:
            org_now = next((o.get("code") for o in orgs if o["id"] == cur.get("org_id")), None)
            if org_now != e["org"]:
                changes["org"] = e["org"]
        if e["sales_role"] and e["sales_role"] != cur["role"]:
            changes["role"] = e["sales_role"]
        if not int(cur.get("active") or 0):
            changes["active"] = 1
        if cur.get("source") != "hr":
            changes["source"] = "hr"
        if changes:
            out["users_update"].append({"id": cur["id"], "emp_no": e["emp_no"], "name": cur["name"],
                                        "changes": changes})
    hr_active = [u for u in users.values() if u.get("source") == "hr" and int(u.get("active") or 0)]
    for u in hr_active:
        if u["emp_no"] not in seen:
            out["users_leave"].append({"id": u["id"], "emp_no": u["emp_no"], "name": u["name"],
                                       "reason": "인사 피드에 없음"})
    ratio = float(os.environ.get("SALES_HR_MIN_RATIO", "0.8"))
    active_in_feed = sum(1 for e in feed["employees"] if e["status"] not in LEAVER_STATUS)
    if hr_active and active_in_feed < len(hr_active) * ratio:
        out["blocked"] = (f"피드 재직 인원({active_in_feed}명)이 현재 인사 연동 인원({len(hr_active)}명)의 "
                          f"{ratio:.0%} 미만입니다. 원천 파일이 잘렸을 수 있어 반영하지 않습니다.")
    # 마지막 관리자 보호
    admins = [u for u in users.values() if u["role"] == "ADMIN" and int(u.get("active") or 0)]
    losing = {x["id"] for x in out["users_leave"]} | {x["id"] for x in out["users_update"]
                                                     if x["changes"].get("role") not in (None, "ADMIN")}
    if admins and all(a["id"] in losing for a in admins):
        keep = admins[0]
        out["users_leave"] = [x for x in out["users_leave"] if x["id"] != keep["id"]]
        for x in out["users_update"]:
            if x["id"] == keep["id"]:
                x["changes"].pop("role", None)
        out["warnings"].append(f"마지막 활성 관리자({keep['name']})는 비활성화·권한 변경하지 않았습니다.")
    for e in out["users_new"]:
        if e["org"] and e["org"] not in feed_codes and e["org"] not in by_code:
            out["warnings"].append(f"{e['emp_no']} {e['name']}: 조직코드 {e['org']} 가 없어 소속 없이 등록합니다.")
    out["summary"] = {k: len(v) for k, v in out.items() if isinstance(v, list) and k != "warnings"}
    return out


# ---------------------------------------------------------------------------
# 반영
# ---------------------------------------------------------------------------
def _unique_org_name(name: str, code: str, exclude_id: int | None = None) -> str:
    dup = db._one("SELECT id FROM orgs WHERE name=? AND id<>?", [name, int(exclude_id or 0)])
    return f"{name} ({code})" if dup else name


def apply_plan(feed: dict, p: dict) -> dict:
    if p.get("blocked"):
        raise ValueError(p["blocked"])
    now = db._now()
    with db.get_conn() as conn:
        for o in p["orgs_new"]:
            conn.execute("INSERT INTO orgs (name, code, org_type, active, hr_synced_at, created_at) VALUES (?,?,?,1,?,?)",
                         (_unique_org_name(o["name"], o["code"]), o["code"], o["type"], now, now))
        for o in p["orgs_update"]:
            ch = o["changes"]
            conn.execute("UPDATE orgs SET name=?, code=?, org_type=COALESCE(?, org_type), active=1, hr_synced_at=? "
                         "WHERE id=?", (_unique_org_name(o["name"], o["code"], o["id"]), o["code"],
                                        ch.get("org_type"), now, o["id"]))
        for o in p["orgs_close"]:
            conn.execute("UPDATE orgs SET active=0, hr_synced_at=? WHERE id=?", (now, o["id"]))
        # 상위 조직은 코드가 모두 생긴 뒤에 연결
        ids = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM orgs WHERE code IS NOT NULL").fetchall()}
        for o in feed["orgs"]:
            conn.execute("UPDATE orgs SET parent_id=? WHERE code=?", (ids.get(o["parent"]) if o["parent"] else None,
                                                                     o["code"]))
    org_ids = {r["code"]: int(r["id"]) for r in db._df("SELECT id, code FROM orgs WHERE code IS NOT NULL", ())
               .to_dict("records")}

    for e in p["users_new"]:
        uid = ent.upsert_user({"emp_no": e["emp_no"], "name": e["name"], "role": e["role"],
                               "org_id": org_ids.get(e["org"]), "email": e["email"], "active": 1})
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET source='hr', position=?, hr_synced_at=? WHERE id=?", (e["position"], now, uid))
    for u in p["users_update"]:
        cur = ent.get_user(user_id=u["id"])
        ch = u["changes"]
        ent.upsert_user({"id": cur["id"], "emp_no": cur["emp_no"], "name": ch.get("name", cur["name"]),
                         "role": ch.get("role", cur["role"]),
                         "org_id": org_ids.get(ch["org"]) if "org" in ch else cur["org_id"],
                         "email": ch.get("email", cur["email"]), "active": 1})
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET source='hr', position=COALESCE(?, position), hr_synced_at=? WHERE id=?",
                         (ch.get("position"), now, u["id"]))
    handovers = []
    for u in p["users_leave"]:
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET active=0, hr_synced_at=? WHERE id=?", (now, u["id"]))
            conn.execute("UPDATE delegations SET revoked_at=? WHERE revoked_at IS NULL AND (from_user_id=? OR to_user_id=?)",
                         (now, u["id"], u["id"]))
        open_customers = int(db._scalar("SELECT COUNT(*) FROM customers WHERE owner_id=? AND status <> '종료'", [u["id"]]))
        open_deals = int(db._scalar("SELECT COUNT(*) FROM deals WHERE owner_id=? AND stage NOT IN (?, ?)",
                                    [u["id"], db.STAGE_WON, db.STAGE_LOST]))
        if open_customers or open_deals:
            handovers.append(f"{u['name']}({u['emp_no']}): 거래처 {open_customers} · 진행 영업기회 {open_deals}")
    if handovers:
        from . import notify
        notify.notify_role("ADMIN", "담당이관", "퇴직·이동자 담당 이관 필요",
                           "\n".join(handovers) + "\n조직·사용자 > 담당 이관에서 처리하세요.", "/admin/org?tab=transfer")
    result = {**p["summary"], "handovers": len(handovers)}
    db.audit("인사연동", "사용자", None, {**result, "경고": p["warnings"] or None})
    return result


def sync(feed: dict, apply: bool = False) -> dict:
    feed = parse_feed(feed)                      # 원본 dict 도 받는다 (검증·정규화는 여러 번 해도 같다)
    p = plan(feed)
    if apply:
        p["applied"] = apply_plan(feed, p)
    return p


def sync_from_source(apply: bool = True, src: Optional[str] = None) -> dict:
    """배치(hr.sync)와 관리자 화면에서 부른다."""
    db.set_context("HR연동", None)
    return sync(load_feed(src), apply=apply)

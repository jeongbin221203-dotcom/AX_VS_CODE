"""조직(플랜트 · 창고)과 사용자 데이터 범위.

- 창고 하나 = SAP 플랜트 하나 + 저장위치 하나. SAP 매핑은 자재가 아니라 창고에 둔다.
- 사용자 범위: all_warehouses=1이면 전체, 0이면 user_scopes에 준 플랜트(그 아래 모든 창고, 나중에 생긴 창고 포함)
  또는 개별 창고만. 시스템관리자는 항상 전체.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

import config

from core import audit, db, version
from core.utils import now_str

CODE_RE = re.compile(r"^[A-Z0-9_-]{1,20}$")


@dataclass
class OrgResult:
    ok: bool
    message: str


# ── 조회 ─────────────────────────────────────────────────────
def plants_df() -> pd.DataFrame:
    return db.query_df("SELECT * FROM plants ORDER BY code")


def warehouses_df(active_only: bool = False) -> pd.DataFrame:
    where = "WHERE w.active = 1 AND p.active = 1" if active_only else ""
    return db.query_df(f"""
        SELECT w.id, w.code, w.name, w.sap_sloc, w.active, w.plant_id,
               p.code AS plant_code, p.name AS plant_name, p.sap_plant
        FROM warehouses w JOIN plants p ON p.id = w.plant_id {where}
        ORDER BY p.code, w.code""")


def get_warehouse(warehouse_id: int, conn=None) -> dict | None:
    sql = """SELECT w.*, p.code AS plant_code, p.name AS plant_name, p.sap_plant, p.active AS plant_active
             FROM warehouses w JOIN plants p ON p.id = w.plant_id WHERE w.id = ?"""
    if conn is not None:
        row = conn.execute(sql, (warehouse_id,)).fetchone()
        return dict(row) if row else None
    df = db.query_df(sql, (warehouse_id,))
    return None if df.empty else df.iloc[0].to_dict()


def warehouse_options(wh_ids=None, active_only: bool = True) -> dict[int, str]:
    df = warehouses_df(active_only=active_only)
    return {int(r.id): f"{r.plant_code} / {r.code} {r.name}" for r in df.itertuples()
            if wh_ids is None or int(r.id) in wh_ids}


# ── 사용자 범위 ──────────────────────────────────────────────
def allowed_warehouses(user: dict | None) -> set[int] | None:
    """None = 모든 창고. 집합 = 그 창고만."""
    if not user:
        return set()
    if user.get("role") in ("ADMIN", "DATA") or int(user.get("all_warehouses", 1) or 0) == 1:   # 데이터 관리는 전사 조회
        return None
    rows = db.query_df("""
        SELECT DISTINCT w.id FROM user_scopes s
        JOIN warehouses w ON w.id = s.warehouse_id OR w.plant_id = s.plant_id
        WHERE s.user_id = ? AND (s.valid_to = '' OR s.valid_to IS NULL OR s.valid_to >= ?)""",     # 기한이 지난 임시 범위는 자동으로 빠진다
                       (user["id"], date.today().isoformat()))
    return {int(v) for v in rows["id"]}


def user_scope(user_id: int) -> tuple[set[int], set[int]]:
    """기본(기한 없는) 범위. 임시 범위는 temp_scopes()."""
    df = db.query_df("SELECT plant_id, warehouse_id FROM user_scopes WHERE user_id = ? AND (valid_to = '' OR valid_to IS NULL)",
                     (user_id,))
    plants = {int(v) for v in df["plant_id"].dropna()}
    whs = {int(v) for v in df["warehouse_id"].dropna()}
    return plants, whs


def set_user_scope(user_id: int, all_warehouses: bool, plant_ids: list[int], warehouse_ids: list[int],
                   actor: dict | None, expected: str | None = None) -> OrgResult:
    if actor and actor.get("id") == user_id:
        return OrgResult(False, "본인의 데이터 범위는 다른 관리자가 바꿔야 합니다.")
    if not all_warehouses and not plant_ids and not warehouse_ids:
        return OrgResult(False, "전체 창고가 아니면 플랜트나 창고를 하나 이상 고르세요.")
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            return OrgResult(False, "사용자가 없습니다.")
        if expected and version.scope_now(conn, user_id) != expected:
            return OrgResult(False, version.STALE)
        if not all_warehouses and not _targets_exist(conn, plant_ids, warehouse_ids):
            return OrgResult(False, "없는 플랜트나 창고가 들어 있습니다. 화면을 새로 고친 뒤 다시 고르세요.")
        # 기본 범위만 바꾼다 (임시 범위는 그대로). 전체 창고로 바꾸면 임시 범위는 필요 없으므로 함께 지운다
        conn.execute("DELETE FROM user_scopes WHERE user_id = ?" + ("" if all_warehouses else " AND (valid_to = '' OR valid_to IS NULL)"),
                     (user_id,))
        if not all_warehouses:
            conn.executemany("INSERT INTO user_scopes (user_id, plant_id, warehouse_id, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                             [(user_id, p, None, (actor or {}).get("name", ""), now_str()) for p in plant_ids]
                             + [(user_id, None, w, (actor or {}).get("name", ""), now_str()) for w in warehouse_ids])
        conn.execute("UPDATE users SET all_warehouses = ?, updated_at = ? WHERE id = ?",
                     (1 if all_warehouses else 0, now_str(), user_id))
        audit.record(conn, actor, "USER_SCOPE", "user", user_id,
                     {"all": all_warehouses, "plants": plant_ids, "warehouses": warehouse_ids})
    return OrgResult(True, "데이터 범위를 저장했습니다.")


# ── 임시 범위 (기한·사유) ─────────────────────────────────────
def _targets_exist(conn, plant_ids, warehouse_ids) -> bool:
    for table, ids in (("plants", plant_ids), ("warehouses", warehouse_ids)):
        ids = {int(i) for i in ids}
        if ids:
            frag, params = db.in_clause(sorted(ids))
            if int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id{frag}", tuple(params)).fetchone()[0]) != len(ids):
                return False
    return True


def add_temp_scope(user_id: int, plant_ids: list[int], warehouse_ids: list[int], valid_to: str, reason: str,
                   actor: dict | None) -> OrgResult:
    """휴직 대행·타 창고 지원처럼 기한이 있는 범위를 기본 범위에 더한다. 기한(포함)이 지나면 자동으로 빠지고, 사유는 필수."""
    reason = (reason or "").strip()
    if actor and actor.get("id") == user_id:
        return OrgResult(False, "본인의 데이터 범위는 다른 관리자가 바꿔야 합니다.")
    if len(reason) < 2:
        return OrgResult(False, "사유를 입력하세요 (예: 인천 창고 휴가 대행).")
    try:
        end = date.fromisoformat((valid_to or "").strip())
    except ValueError:
        return OrgResult(False, "기한을 날짜로 입력하세요.")
    if end < date.today():
        return OrgResult(False, "기한은 오늘 이후여야 합니다.")
    if end > date.today() + timedelta(days=config.SCOPE_TEMP_MAX_DAYS):
        return OrgResult(False, f"임시 범위는 최대 {config.SCOPE_TEMP_MAX_DAYS}일까지입니다. 더 오래 필요하면 기본 범위로 바꾸세요.")
    if not plant_ids and not warehouse_ids:
        return OrgResult(False, "플랜트나 창고를 하나 이상 고르세요.")
    with db.transaction() as conn:
        user = conn.execute("SELECT id, role, all_warehouses FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            return OrgResult(False, "사용자가 없습니다.")
        if user["role"] in ("ADMIN", "DATA") or int(user["all_warehouses"] or 0):
            return OrgResult(False, "이 사용자는 이미 모든 창고를 볼 수 있어 임시 범위가 필요 없습니다.")
        if not _targets_exist(conn, plant_ids, warehouse_ids):
            return OrgResult(False, "없는 플랜트나 창고가 들어 있습니다. 화면을 새로 고친 뒤 다시 고르세요.")
        who, ts = (actor or {}).get("name", ""), now_str()
        conn.executemany("INSERT INTO user_scopes (user_id, plant_id, warehouse_id, valid_to, reason, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(user_id, p, None, end.isoformat(), reason, who, ts) for p in plant_ids]
                         + [(user_id, None, w, end.isoformat(), reason, who, ts) for w in warehouse_ids])
        audit.record(conn, actor, "SCOPE_TEMP", "user", user_id,
                     {"add": True, "plants": plant_ids, "warehouses": warehouse_ids, "valid_to": end.isoformat(), "reason": reason})
    return OrgResult(True, f"{end.isoformat()}까지 임시 범위를 추가했습니다 (기한이 지나면 자동으로 빠집니다).")


def revoke_temp_scope(scope_id: int, actor: dict | None) -> OrgResult:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM user_scopes WHERE id = ? AND valid_to <> ''", (scope_id,)).fetchone()
        if row is None:
            return OrgResult(False, "임시 범위가 없습니다.")
        if actor and actor.get("id") == row["user_id"]:
            return OrgResult(False, "본인의 데이터 범위는 다른 관리자가 바꿔야 합니다.")
        conn.execute("DELETE FROM user_scopes WHERE id = ?", (scope_id,))
        audit.record(conn, actor, "SCOPE_TEMP", "user", row["user_id"],
                     {"revoke": True, "plant": row["plant_id"], "warehouse": row["warehouse_id"], "valid_to": row["valid_to"]})
    return OrgResult(True, "임시 범위를 회수했습니다.")


def temp_scopes(user_id: int | None = None) -> list[dict]:
    """임시 범위 목록 (기한 지난 것 포함, 최근 30일 안에 끝난 것까지)."""
    cut = (date.today() - timedelta(days=30)).isoformat()
    df = db.query_df("""
        SELECT s.id, s.user_id, s.valid_to, s.reason, s.created_by, s.created_at,
               p.code AS plant_code, w.code AS wh_code, wp.code AS wh_plant
        FROM user_scopes s LEFT JOIN plants p ON p.id = s.plant_id
        LEFT JOIN warehouses w ON w.id = s.warehouse_id LEFT JOIN plants wp ON wp.id = w.plant_id
        WHERE s.valid_to <> '' AND s.valid_to >= ?""" + (" AND s.user_id = ?" if user_id is not None else "") + " ORDER BY s.valid_to, s.id",
                       (cut, user_id) if user_id is not None else (cut,))
    today = date.today().isoformat()
    out = []
    for r in df.to_dict("records"):
        out.append({**r, "target": f"{r['plant_code']} (플랜트 전체)" if r["plant_code"] else f"{r['wh_plant']}/{r['wh_code']}",
                    "expired": r["valid_to"] < today})
    return out


# ── 등록 · 변경 (시스템관리자) ─────────────────────────────────
def _code(value: str) -> str:
    return value.strip().upper()


def create_plant(code: str, name: str, sap_plant: str, actor: dict | None) -> OrgResult:
    code, name, sap_plant = _code(code), name.strip(), sap_plant.strip().upper()
    if not CODE_RE.match(code) or not name:
        return OrgResult(False, "플랜트 코드(영문 대문자·숫자·-_ 20자 이내)와 이름을 입력하세요.")
    try:
        with db.transaction() as conn:
            pid = conn.execute("INSERT INTO plants (code, name, sap_plant, created_at) VALUES (?, ?, ?, ?)",
                               (code, name, sap_plant, now_str())).lastrowid
            audit.record(conn, actor, "ORG_CHANGE", "plant", pid, {"code": code, "name": name, "sap_plant": sap_plant})
    except db.IntegrityError:
        return OrgResult(False, f"플랜트 코드 '{code}'는 이미 있습니다.")
    return OrgResult(True, f"플랜트 {code} {name} 등록")


def create_warehouse(plant_id: int, code: str, name: str, sap_sloc: str, actor: dict | None) -> OrgResult:
    code, name, sap_sloc = _code(code), name.strip(), sap_sloc.strip().upper()
    if not CODE_RE.match(code) or not name:
        return OrgResult(False, "창고 코드(영문 대문자·숫자·-_ 20자 이내)와 이름을 입력하세요.")
    try:
        with db.transaction() as conn:
            if conn.execute("SELECT 1 FROM plants WHERE id = ?", (plant_id,)).fetchone() is None:
                return OrgResult(False, "플랜트를 고르세요.")
            wid = conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) "
                               "VALUES (?, ?, ?, ?, ?)", (plant_id, code, name, sap_sloc, now_str())).lastrowid
            audit.record(conn, actor, "ORG_CHANGE", "warehouse", wid,
                         {"code": code, "name": name, "plant_id": plant_id, "sap_sloc": sap_sloc})
    except db.IntegrityError:
        return OrgResult(False, f"창고 코드 '{code}'는 이미 있습니다.")
    return OrgResult(True, f"창고 {code} {name} 등록")


def update_plant(plant_id: int, name: str, sap_plant: str, active: bool, actor: dict | None,
                 expected: str | None = None) -> OrgResult:
    with db.transaction() as conn:
        before = conn.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
        if before is None:
            return OrgResult(False, "플랜트가 없습니다.")
        if version.stale("plants", before, expected):
            return OrgResult(False, version.STALE)
        after = {"name": name.strip() or before["name"], "sap_plant": sap_plant.strip().upper(), "active": int(active)}
        if not active and _last_active_warehouse(conn, exclude_plant=plant_id):
            return OrgResult(False, "사용 중인 창고가 모두 없어지므로 중지할 수 없습니다.")
        conn.execute("UPDATE plants SET name = ?, sap_plant = ?, active = ? WHERE id = ?",
                     (after["name"], after["sap_plant"], after["active"], plant_id))
        audit.record(conn, actor, "ORG_CHANGE", "plant", plant_id, audit.changes(dict(before), after, after))
    return OrgResult(True, "플랜트를 저장했습니다.")


def update_warehouse(warehouse_id: int, name: str, sap_sloc: str, active: bool, actor: dict | None,
                     expected: str | None = None) -> OrgResult:
    with db.transaction() as conn:
        before = conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
        if before is None:
            return OrgResult(False, "창고가 없습니다.")
        if version.stale("warehouses", before, expected):
            return OrgResult(False, version.STALE)
        after = {"name": name.strip() or before["name"], "sap_sloc": sap_sloc.strip().upper(), "active": int(active)}
        if not active and _last_active_warehouse(conn, exclude_wh=warehouse_id):
            return OrgResult(False, "마지막으로 남은 사용 창고는 중지할 수 없습니다.")
        conn.execute("UPDATE warehouses SET name = ?, sap_sloc = ?, active = ? WHERE id = ?",
                     (after["name"], after["sap_sloc"], after["active"], warehouse_id))
        audit.record(conn, actor, "ORG_CHANGE", "warehouse", warehouse_id, audit.changes(dict(before), after, after))
    return OrgResult(True, "창고를 저장했습니다.")


def _last_active_warehouse(conn, exclude_wh: int | None = None, exclude_plant: int | None = None) -> bool:
    n = conn.execute("""
        SELECT COUNT(*) FROM warehouses w JOIN plants p ON p.id = w.plant_id
        WHERE w.active = 1 AND p.active = 1 AND w.id <> ? AND p.id <> ?""",
        (exclude_wh or -1, exclude_plant or -1)).fetchone()[0]
    return n == 0

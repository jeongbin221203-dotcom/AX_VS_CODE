"""SAP 마스터 자동 동기화: 자재(MATMAS)와 원가센터를 SAP(또는 사내 연계서버)에서 받아온다.

- 배치 작업 `sap_master_sync`(1시간)가 마지막 동기화 이후 바뀐 것만 받는다(changedSince).
- 자재는 SAP 자재번호로 맞춘다. 있으면 이름·단위·자재그룹(분류)·표준단가·삭제표시를 갱신하고, 없으면 새로 만든다.
  이 시스템만의 항목(안전재고·보관위치·공급처)은 건드리지 않는다.
- 동기화된 자재는 SAP가 원본이다: config.SAP_MASTER_READONLY이면 화면에서 SAP 항목을 고칠 수 없다.
- 원가센터 목록이 들어오면 출고 때 원가센터가 SAP에 있는지 확인한다(목록이 비어 있으면 검사하지 않음).

연계서버 규격(http 모드):
  GET {MM_SAP_ENDPOINT}/master/materials?changedSince=YYYY-MM-DD HH:MM:SS
      → [{"material", "description", "unit", "materialGroup", "standardPrice", "deleted", "batchManaged",
          "shelfLifeManaged"}]
  GET {MM_SAP_ENDPOINT}/master/cost-centers?changedSince=...
      → [{"costCenter", "name", "active"}]
"""

import json
import urllib.parse
import urllib.request

import config
from core import audit, db, sap
from core.utils import now_str

# mock 모드용 SAP 마스터 (시연·테스트)
MOCK_MATERIALS = [
    {"material": "PKG001", "description": "수출용 목재 팔레트 1100x1100", "unit": "EA", "materialGroup": "포장재",
     "standardPrice": 18500, "deleted": False, "batchManaged": False, "shelfLifeManaged": False},
    {"material": "CHM001", "description": "방청제 (VCI 오일)", "unit": "L", "materialGroup": "화학",
     "standardPrice": 12000, "deleted": False, "batchManaged": True, "shelfLifeManaged": True},
    {"material": "OLD999", "description": "단종 라벨", "unit": "EA", "materialGroup": "라벨/소모품",
     "standardPrice": 50, "deleted": True, "batchManaged": False, "shelfLifeManaged": False},
]
MOCK_COST_CENTERS = [{"costCenter": "CC-4100", "name": "포장1팀", "active": True},
                     {"costCenter": "CC-4200", "name": "포장2팀", "active": True},
                     {"costCenter": "CC-9000", "name": "폐쇄 부서", "active": False}]


def enabled() -> bool:
    return sap.enabled() and config.SAP_MASTER_SYNC


def cost_center_problem(conn, code: str) -> str:
    if not code:
        return ""
    if conn.execute("SELECT 1 FROM cost_centers LIMIT 1").fetchone() is None:
        return ""                                   # 원가센터 목록을 받기 전에는 검사하지 않는다
    row = conn.execute("SELECT active FROM cost_centers WHERE code = ?", (code.upper(),)).fetchone()
    if row is None:
        return f"원가센터 {code}가 SAP에 없습니다."
    if not row["active"]:
        return f"원가센터 {code}는 SAP에서 사용이 막혔습니다."
    return ""


def _get(path: str, since: str) -> list[dict]:
    sap.HttpClient._check_endpoint()
    url = f"{config.SAP_ENDPOINT.rstrip('/')}{path}?{urllib.parse.urlencode({'changedSince': since})}"
    headers = {"Authorization": f"Bearer {config.SAP_TOKEN}"} if config.SAP_TOKEN else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=config.SAP_TIMEOUT) as res:
        return json.loads(res.read().decode() or "[]")


def fetch(since: str) -> tuple[list[dict], list[dict]]:
    if config.SAP_MODE == "mock":
        return MOCK_MATERIALS, MOCK_COST_CENTERS
    return _get("/master/materials", since), _get("/master/cost-centers", since)


def _setting(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def sync(actor: dict | None = None) -> dict:
    """한 번 동기화. {'created', 'updated', 'deactivated', 'cost_centers'}"""
    with db.get_conn() as conn:
        since = _setting(conn, "master_synced_at")
    started = now_str()
    materials, centers = fetch(since)
    counts = {"created": 0, "updated": 0, "deactivated": 0, "cost_centers": 0, "skipped": 0}
    with db.transaction() as conn:
        for m in materials:
            matnr = str(m.get("material") or "").strip().upper()
            if not matnr:
                counts["skipped"] += 1
                continue
            fields = {"name": str(m.get("description") or matnr).strip(), "unit": str(m.get("unit") or "EA").strip(),
                      "category": str(m.get("materialGroup") or config.DEFAULT_CATEGORY).strip(),
                      "unit_price": max(float(m.get("standardPrice") or 0), 0.0),
                      "active": 0 if m.get("deleted") else 1}
            row = conn.execute("SELECT * FROM materials WHERE sap_matnr = ?", (matnr,)).fetchone()
            if row is None:
                if m.get("deleted"):
                    continue
                code = matnr
                if conn.execute("SELECT 1 FROM materials WHERE code = ?", (code,)).fetchone():
                    code = f"SAP-{matnr}"
                conn.execute(
                    "INSERT INTO materials (code, name, unit, category, unit_price, sap_matnr, lot_managed, "
                    "expiry_managed, active, sap_synced_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (code, fields["name"], fields["unit"], fields["category"], fields["unit_price"], matnr,
                     1 if m.get("batchManaged") else 0, 1 if m.get("shelfLifeManaged") else 0, started, started, started))
                counts["created"] += 1
                continue
            changed = {k: v for k, v in fields.items() if str(row[k]) != str(v)}
            if changed:
                sets = ", ".join(f"{k} = ?" for k in changed)
                conn.execute(f"UPDATE materials SET {sets}, sap_synced_at = ?, updated_at = ? WHERE id = ?",
                             (*changed.values(), started, started, row["id"]))
                counts["deactivated" if changed.get("active") == 0 else "updated"] += 1
            else:
                conn.execute("UPDATE materials SET sap_synced_at = ? WHERE id = ?", (started, row["id"]))
        for c in centers:
            code = str(c.get("costCenter") or "").strip().upper()
            if not code:
                continue
            conn.execute("INSERT INTO cost_centers (code, name, active, synced_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (code) DO UPDATE SET name = excluded.name, active = excluded.active, "
                         "synced_at = excluded.synced_at",
                         (code, str(c.get("name") or ""), 1 if c.get("active", True) else 0, started))
            counts["cost_centers"] += 1
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('master_synced_at', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (started,))
        audit.record(conn, actor, "MATERIAL_SYNC", "material", "", {**counts, "since": since or "처음"})
    return counts


def status() -> dict:
    with db.get_conn() as conn:
        return {"synced_at": _setting(conn, "master_synced_at"),
                "synced_materials": conn.execute("SELECT COUNT(*) FROM materials WHERE sap_synced_at <> ''").fetchone()[0],
                "cost_centers": conn.execute("SELECT COUNT(*) FROM cost_centers WHERE active = 1").fetchone()[0]}

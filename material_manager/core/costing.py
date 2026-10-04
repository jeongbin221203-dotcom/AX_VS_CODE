"""원가: 작업 달력 · 표준원가(롤업) · 작업지시 원가 차이 · 오더 정산 · 구매 가격차이.

표준원가 (생산 → 📐 원가 탭에서 '표준원가 다시 산정')
  - 구매품(BOM 없음): 재료 = 자재 마스터 기준단가.
  - 제품(BOM 있음): 재료 = Σ 부품 표준원가 × 1개당 소요량(손실률 포함), 노무·경비 = 공정 표준시간(분/개) × 임률·배부율.
    부품이 다시 제품이면 아래 단계부터 계산해 올린다(다단계 롤업). 산정할 때마다 기록이 바뀌고 감사로그에 남는다.

작업지시 원가 차이 (완료된 작업지시) — 표준 = 양품 수량 × 표준원가
  재료 가격차이 = Σ (실제 투입 단가 − 부품 표준원가) × 실제 투입량
  재료 수량차이 = Σ (실제 투입량 − 양품 × 1개당 표준 소요량) × 부품 표준원가   (불량으로 더 쓴 부품이 여기에)
  노무 능률차이 = (실제 작업시간 − 양품 × 표준시간) × 임률,  경비 차이 = 같은 시간 × 배부율
  합계 = 실제 원가(재료 + 노무 + 경비) − 표준 원가.  (+ 불리 / − 유리)

오더 정산: 완료된 작업지시의 차이를 확정해 남긴다(이후 그 작업지시 원가는 바뀌지 않음, 정산 목록·엑셀).
SAP 생산오더 방식(MM_SAP_PRODUCTION_MODE=order)이면 SAP 에서 같은 오더를 KO88 로 정산할 때 이 차이를 대조한다.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pandas as pd

import config
from core import audit, db
from core.utils import now_str

# ── 작업 달력 ────────────────────────────────────────────────
_holidays = {"at": 0.0, "days": set()}


def holidays() -> set[str]:
    if time.time() - _holidays["at"] > 30:
        try:
            days = {str(r[0]) for r in db.query_df("SELECT day FROM work_calendar").itertuples(index=False)}
        except db.DBError:                              # 리비전 0008 전
            days = set()
        _holidays.update(at=time.time(), days=days)
    return _holidays["days"]


def is_workday(d: date) -> bool:
    if config.WEEKEND_OFF and d.weekday() >= 5:
        return False
    return d.isoformat() not in holidays()


def prev_workday(d: date) -> date:
    for _ in range(30):
        if is_workday(d):
            return d
        d -= timedelta(days=1)
    return d


def sub_workdays(day: str, n: int) -> str:
    """day 에서 근무일 n 일 전 (리드타임 역산). 결과가 쉬는 날이면 그 앞 근무일."""
    d = prev_workday(date.fromisoformat(day))
    left = int(n or 0)
    guard = 0
    while left > 0 and guard < 3660:
        d -= timedelta(days=1)
        guard += 1
        if is_workday(d):
            left -= 1
    return d.isoformat()


def calendar_df() -> pd.DataFrame:
    return db.query_df("SELECT day, name, created_by, created_at FROM work_calendar ORDER BY day")


def add_holiday(day: str, name: str, actor: dict) -> tuple[bool, str]:
    try:
        d = date.fromisoformat(day).isoformat()
    except ValueError:
        return False, "날짜를 고르세요."
    with db.transaction() as conn:
        conn.execute("INSERT INTO work_calendar (day, name, created_by, created_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT (day) DO UPDATE SET name = excluded.name", (d, name.strip()[:40], actor["name"], now_str()))
        audit.record(conn, actor, "CALENDAR", "settings", d, {"holiday": name.strip()[:40] or "휴일"})
    _holidays["at"] = 0.0
    return True, f"{d} 을(를) 쉬는 날로 넣었습니다."


def remove_holiday(day: str, actor: dict) -> tuple[bool, str]:
    with db.transaction() as conn:
        n = conn.execute("DELETE FROM work_calendar WHERE day = ?", (day,)).rowcount
        if n:
            audit.record(conn, actor, "CALENDAR", "settings", day, {"removed": True})
    _holidays["at"] = 0.0
    return bool(n), (f"{day} 을(를) 근무일로 되돌렸습니다." if n else "없는 날짜입니다.")


# ── 표준원가 ─────────────────────────────────────────────────
def standard(material_id: int, conn=None) -> dict | None:
    sql = "SELECT * FROM standard_costs WHERE material_id = ?"
    row = (conn.execute(sql, (material_id,)).fetchone() if conn is not None else
           (lambda df: None if df.empty else df.iloc[0])(db.query_df(sql, (material_id,))))
    return dict(row) if row is not None else None


def rollup(actor: dict, update_price: bool = False) -> tuple[bool, str]:
    """모든 자재의 표준원가를 다시 계산한다 (구매품 = 기준단가, 제품 = BOM·공정 롤업).
    update_price: 만드는 제품의 기준단가(재고 현황·수불부 금액)도 표준원가로 맞춘다 (SAP 에서 받는 자재는 제외)."""
    from core import mrp
    labor, overhead = float(config.LABOR_RATE or 0), float(config.OVERHEAD_RATE or 0)
    with db.transaction() as conn:
        level, boms = mrp._levels(conn)
        mats = {int(r["id"]): dict(r) for r in conn.execute("SELECT id, code, unit_price FROM materials")}
        minutes = {int(r[0]): float(r[1] or 0) for r in conn.execute(
            "SELECT product_id, SUM(std_minutes) FROM routings GROUP BY product_id")}
        cost: dict[int, dict] = {}
        for mid in sorted(mats, key=lambda m: -level.get(m, 0)):          # 가장 아래 부품부터
            m = mats[mid]
            if mid in boms:
                mat = sum(cost.get(c, {"total": float(mats.get(c, {}).get("unit_price") or 0)})["total"] * per
                          for c, per, _ in boms[mid]["items"])
                mins = minutes.get(mid, 0.0)
                basis = {"bom": [{"code": mats[c]["code"] if c in mats else c, "per": round(per, 6),
                                  "std": round(cost.get(c, {"total": 0})["total"], 4)} for c, per, _ in boms[mid]["items"]],
                         "minutes": mins, "labor_rate": labor, "overhead_rate": overhead}
                cost[mid] = {"material": mat, "labor": mins * labor, "overhead": mins * overhead, "minutes": mins, "basis": basis}
            else:
                cost[mid] = {"material": float(m["unit_price"] or 0), "labor": 0.0, "overhead": 0.0, "minutes": 0.0,
                             "basis": {"purchase": "기준단가"}}
            cost[mid]["total"] = cost[mid]["material"] + cost[mid]["labor"] + cost[mid]["overhead"]
        ts = now_str()
        before = {int(r["material_id"]): dict(r) for r in conn.execute("SELECT * FROM standard_costs")}
        try:
            conn.execute("INSERT INTO standard_cost_history (material_id, material, labor, overhead, total, minutes, basis, "
                         "set_by, set_at, replaced_by, replaced_at) SELECT material_id, material, labor, overhead, total, minutes, "
                         "basis, set_by, set_at, ?, ? FROM standard_costs", (actor["name"], ts))
        except db.DBError:                              # 리비전 0009 전
            pass
        changed = {mats[m]["code"]: [round(float(before[m]["total"]), 4), round(c["total"], 4)]
                   for m, c in cost.items() if m in before and abs(float(before[m]["total"]) - c["total"]) > 0.005}
        conn.execute("DELETE FROM standard_costs")
        conn.executemany("INSERT INTO standard_costs (material_id, material, labor, overhead, total, minutes, basis, set_by, set_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(mid, round(c["material"], 4), round(c["labor"], 4), round(c["overhead"], 4), round(c["total"], 4),
                           c["minutes"], json.dumps(c["basis"], ensure_ascii=False), actor["name"], ts) for mid, c in cost.items()])
        made = sum(1 for m in cost if m in boms)
        repriced = {}
        if update_price:
            for m in cost:
                if m not in boms:
                    continue
                row = conn.execute("SELECT code, unit_price, COALESCE(sap_synced_at, '') AS synced FROM materials WHERE id = ?",
                                   (m,)).fetchone()
                new = round(cost[m]["total"], 2)
                if row is None or row["synced"] or abs(float(row["unit_price"] or 0) - new) < 0.005:
                    continue
                conn.execute("UPDATE materials SET unit_price = ?, updated_at = ? WHERE id = ?", (new, ts, m))
                repriced[row["code"]] = [float(row["unit_price"] or 0), new]
        audit.record(conn, actor, "STD_COST", "material", "", {"materials": len(cost), "products": made,
                                                              "labor_rate": labor, "overhead_rate": overhead,
                                                              "changed": dict(list(changed.items())[:200]),
                                                              "changed_count": len(changed),
                                                              "repriced": dict(list(repriced.items())[:200])})
    extra = f" · 제품 기준단가 {len(repriced)}종을 표준원가로 맞춤" if update_price else ""
    return True, (f"표준원가를 다시 산정했습니다 — 자재 {len(cost)}종 (제품 {made}종은 BOM·공정 롤업, "
                  f"임률 {labor:g}·배부율 {overhead:g} 원/분){extra}.")


def standards_df() -> pd.DataFrame:
    return db.query_df("""
        SELECT m.code, m.name, m.unit, s.material, s.labor, s.overhead, s.total, s.minutes, s.set_at, s.set_by,
               CASE WHEN EXISTS (SELECT 1 FROM boms b WHERE b.product_id = m.id AND b.active = 1) THEN 1 ELSE 0 END AS made
        FROM standard_costs s JOIN materials m ON m.id = s.material_id
        ORDER BY made DESC, m.code""")


def _stale(conn, product_id: int, set_at: str) -> str:
    """표준원가 산정 뒤에 BOM·공정이 바뀌었으면 사유 (그 표준으로 정산하면 차이가 틀린다)."""
    bom = conn.execute("SELECT updated_at FROM boms WHERE product_id = ? AND active = 1", (product_id,)).fetchone()
    if bom is not None and str(bom["updated_at"] or "") > str(set_at or ""):
        return "BOM 이 표준원가 산정 뒤에 바뀜"
    routing = conn.execute("SELECT MAX(at) FROM audit_log WHERE action = 'ROUTING_SAVE' AND entity_id = ?", (str(product_id),)).fetchone()
    if routing and routing[0] and str(routing[0]) > str(set_at or ""):
        return "공정이 표준원가 산정 뒤에 바뀜"
    std = conn.execute("SELECT basis FROM standard_costs WHERE material_id = ?", (product_id,)).fetchone()
    basis = json.loads(std["basis"] or "{}") if std else {}
    if basis.get("minutes"):
        if abs(float(basis.get("labor_rate") or 0) - float(config.LABOR_RATE or 0)) > 1e-9 or \
           abs(float(basis.get("overhead_rate") or 0) - float(config.OVERHEAD_RATE or 0)) > 1e-9:
            return "임률·배부율이 표준원가 산정 뒤에 바뀜"
    for comp in basis.get("bom", []):                  # 구매품 부품의 기준단가가 바뀌었는지
        row = conn.execute("SELECT m.unit_price, s.basis FROM materials m LEFT JOIN standard_costs s ON s.material_id = m.id "
                           "WHERE m.code = ?", (comp.get("code"),)).fetchone()
        if row is not None and row["basis"] and '"purchase"' in row["basis"] and \
                abs(float(row["unit_price"] or 0) - float(comp.get("std") or 0)) > 0.005:
            return f"부품 {comp.get('code')} 기준단가가 표준원가 산정 뒤에 바뀜"
    return ""


# ── 작업지시 원가 ────────────────────────────────────────────
def actual_minutes(conn, pid: int, good: float, scrap: float) -> float:
    """실제 작업시간: 공정 실적(분)의 합. 실적이 없으면 표준시간 × (양품 + 불량)."""
    ops = conn.execute("SELECT COALESCE(SUM(minutes), 0), COUNT(*) FROM wo_operations WHERE production_id = ? AND status = 'DONE'",
                       (pid,)).fetchone()
    if ops and int(ops[1] or 0) and float(ops[0] or 0) > 0:
        return float(ops[0])
    p = conn.execute("SELECT product_id FROM productions WHERE id = ?", (pid,)).fetchone()
    std = conn.execute("SELECT COALESCE(SUM(std_minutes), 0) FROM routings WHERE product_id = ?", (p["product_id"],)).fetchone()[0]
    return float(std or 0) * (good + scrap)


def conversion(conn, pid: int, good: float, scrap: float) -> tuple[float, float, float]:
    """(작업시간, 노무비, 경비) = 실제 작업시간 × 임률·배부율."""
    mins = actual_minutes(conn, pid, good, scrap)
    return mins, round(mins * float(config.LABOR_RATE or 0), 2), round(mins * float(config.OVERHEAD_RATE or 0), 2)


def variance(pid: int) -> dict | None:
    """완료된 작업지시의 표준 대비 차이 (정산했으면 정산 때 값)."""
    with db.get_conn() as conn:
        p = conn.execute("SELECT p.*, m.code, m.name, m.unit FROM productions p JOIN materials m ON m.id = p.product_id "
                         "WHERE p.id = ?", (pid,)).fetchone()
        if p is None or p["status"] != "DONE":
            return None
        if p["settlement"]:
            return {**json.loads(p["settlement"]), "settled": True}
        good, scrap = float(p["good_qty"] or 0), float(p["scrap_qty"] or 0)
        std = standard(int(p["product_id"]), conn)
        if std is None:
            return {"missing": True, "prod_no": p["prod_no"], "code": p["code"], "name": p["name"], "good": good}
        stale = _stale(conn, int(p["product_id"]), std["set_at"])
        snap = json.loads(p["bom_snapshot"] or "[]") if p["bom_snapshot"] else []
        planned = float(p["qty"] or 0) or 1.0
        per_std: dict[int, float] = {}
        for it in snap if isinstance(snap, list) else []:           # 등록 당시 BOM: 작업지시 수량 전체 소요량(손실 포함)
            per_std[int(it["component_id"])] = per_std.get(int(it["component_id"]), 0.0) + float(it["need"]) / planned
        lines = conn.execute("SELECT component_id, SUM(issued_qty) AS q, SUM(issued_cost) AS c FROM production_lines "
                             "WHERE production_id = ? GROUP BY component_id", (pid,)).fetchall()
        price_var = qty_var = 0.0
        comp_rows = []
        for ln in lines:
            cid, q, c = int(ln["component_id"]), float(ln["q"] or 0), float(ln["c"] or 0)
            sc = standard(cid, conn)
            sp = float(sc["total"]) if sc else (c / q if q else 0.0)
            allowed = per_std.get(cid, 0.0) * good
            pv, qv = (c - sp * q), (q - allowed) * sp
            price_var += pv
            qty_var += qv
            comp_rows.append({"component_id": cid, "actual_qty": q, "allowed_qty": allowed, "actual_cost": c,
                              "std_price": sp, "price_var": pv, "qty_var": qv})
        mins = actual_minutes(conn, pid, good, scrap)
        std_mins = float(std["minutes"] or 0) * good
        actual_labor = float(p["labor_cost"] or 0)            # 완료할 때 기록한 값 (그때 임률)
        actual_oh = float(p["overhead_cost"] or 0)
        actual = float(p["material_cost"] or 0) + actual_labor + actual_oh
        std_total = float(std["total"]) * good
        std_labor, std_oh = float(std["labor"] or 0) * good, float(std["overhead"] or 0) * good   # 산정 때 임률·배부율
        if not stale and actual_labor + actual_oh == 0 and std_labor + std_oh > 0:
            stale = "완료 때 임률·공정이 없어 노무비·경비를 넣지 않은 작업지시 — 지금 표준과 비교할 수 없음"
        return {"prod_no": p["prod_no"], "code": p["code"], "name": p["name"], "unit": p["unit"], "good": good, "scrap": scrap,
                "std_unit": float(std["total"]), "std_total": std_total, "actual_total": actual,
                "actual_material": float(p["material_cost"] or 0), "actual_labor": actual_labor, "actual_overhead": actual_oh,
                "price_var": price_var, "qty_var": qty_var,
                "labor_var": actual_labor - std_labor, "overhead_var": actual_oh - std_oh,     # 능률 + 임률 차이
                "other_var": (actual - std_total) - (price_var + qty_var + actual_labor - std_labor + actual_oh - std_oh),
                "minutes": mins, "std_minutes": std_mins, "total_var": actual - std_total, "lines": comp_rows,
                "settled": False, "stale": stale}


def variances_df(wh_ids=None, settled: bool | None = None) -> pd.DataFrame:
    frag, wp = db.in_clause(wh_ids)
    ids = db.query_df(f"SELECT id FROM productions WHERE status = 'DONE'{' AND issue_wh_id' + frag if frag else ''} "
                      "ORDER BY completed_at DESC LIMIT 300", wp)["id"]
    rows = []
    for pid in ids:
        v = variance(int(pid))
        if not v or (settled is not None and v.get("settled", False) != settled):
            continue
        note = "표준원가 없음 — 산정 필요" if v.get("missing") else (v.get("stale") or "")
        rows.append({"id": int(pid), **{k: v.get(k, 0.0) for k in ("prod_no", "code", "name", "good", "std_total", "actual_total",
                                                                  "price_var", "qty_var", "labor_var", "overhead_var", "other_var",
                                                                  "total_var")},
                     "settled": bool(v.get("settled")), "note": "" if v.get("settled") else note,
                     "can_settle": not v.get("settled") and not note})
    return pd.DataFrame(rows)


def settle(pid: int, actor: dict, wh_ids=None) -> tuple[bool, str]:
    """오더 정산: 완료된 작업지시의 차이를 확정해 남긴다 (한 번만)."""
    v = variance(pid)
    if v is None:
        return False, "완료된 작업지시만 정산할 수 있습니다."
    if v.get("missing"):
        return False, "제품의 표준원가가 없습니다. 먼저 '표준원가 다시 산정'을 누르세요."
    if v["settled"]:
        return False, "이미 정산한 작업지시입니다."
    if v.get("stale"):
        return False, f"{v['stale']} — '표준원가 다시 산정' 뒤에 정산하세요 (지금 표준으로는 차이가 틀립니다)."
    with db.transaction() as conn:
        p = conn.execute("SELECT issue_wh_id, receipt_wh_id, settled_at FROM productions WHERE id = ?", (pid,)).fetchone()
        if wh_ids is not None and not ({p["issue_wh_id"], p["receipt_wh_id"]} - {None}) <= set(wh_ids):
            return False, "이 작업지시의 창고 권한이 없습니다."
        if p["settled_at"]:
            return False, "이미 정산한 작업지시입니다."
        conn.execute("UPDATE productions SET settled_at = ?, settled_by = ?, settlement = ? WHERE id = ?",
                     (now_str(), actor["name"], json.dumps({**v, "settled": True}, ensure_ascii=False, default=float), pid))
        audit.record(conn, actor, "WO_SETTLE", "production", pid,
                     {k: round(v[k], 2) for k in ("std_total", "actual_total", "price_var", "qty_var", "labor_var",
                                                  "overhead_var", "total_var")})
    sap_note = " SAP 에서 같은 생산오더를 정산(KO88)할 때 이 차이와 맞춰 보세요." if config.SAP_PRODUCTION_MODE == "order" else ""
    return True, f"{v['prod_no']} 정산 — 차이 합계 ₩{v['total_var']:+,.0f} (표준 ₩{v['std_total']:,.0f} · 실제 ₩{v['actual_total']:,.0f}).{sap_note}"


def purchase_price_variance(start: str, end: str, wh_ids=None) -> pd.DataFrame:
    """구매 가격차이: 입고(발주·무발주) 단가 − 표준원가(구매품 = 기준단가) × 입고량."""
    frag, wp = db.in_clause(wh_ids)
    df = db.query_df(f"""
        SELECT t.id, t.tx_date, m.code, m.name, m.unit, t.qty, t.unit_price, COALESCE(s.total, m.unit_price) AS std_price,
               t.po_no, t.partner
        FROM transactions t JOIN materials m ON m.id = t.material_id LEFT JOIN standard_costs s ON s.material_id = m.id
        WHERE t.tx_type = 'IN' AND t.transfer_no = '' AND t.production_id IS NULL AND t.reversal_of IS NULL
          AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.reversal_of = t.id)
          AND t.tx_date BETWEEN ? AND ?{' AND t.warehouse_id' + frag if frag else ''}
        ORDER BY t.tx_date DESC, t.id DESC""", (start, end, *wp))
    if df.empty:
        return df.assign(variance=[])
    df["variance"] = (df["unit_price"] - df["std_price"]) * df["qty"]
    return df[df["variance"].abs() > 0.5]

"""MRP (자재 소요 계획): 언제 무엇을 얼마나 사거나 만들어야 하는지 계산한다. 플랜트(공장) 단위.

입력
  수요      MRP 수요(판매·출하 계획: 제품·수량·납기) + 진행 중 작업지시의 아직 투입하지 않은 부품 + 안전재고
  공급      플랜트 창고의 현재고 + 들어올 발주 잔량(필요일 = 구매요청 필요일) + 진행 중 작업지시의 완제품
  기준정보  BOM(다단계, 손실률), 리드타임(일), 최소 발주량, 발주 배수
계산 (lot-for-lot, 날짜 순)
  1) BOM 단계(low-level code)를 구해 위 단계(완제품)부터 아래 단계(원자재)로 내려간다.
  2) 품목마다 수요·공급을 날짜 순으로 놓고 가용 재고를 깎아 가다 모자라는 날 = 필요일, 모자란 만큼 계획 주문.
     계획 수량은 최소 발주량 이상, 발주 배수로 올림.
  3) 발주·착수일 = 필요일 - 리드타임. 오늘보다 앞이면 '지연'(이미 늦음).
  4) BOM이 있으면 생산 계획(MAKE) → 착수일에 부품 수요를 만든다(다음 단계로 전개). 없으면 구매 계획(BUY).
결과는 실행 기록(mrp_runs·mrp_plans)으로 남고, 고른 계획을 구매요청(창고별)·작업지시로 바꾼다.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd

import config
from core import audit, db, production, purchasing, services
from core.utils import fmt_qty, now_str

KIND = {"BUY": "구매", "MAKE": "생산"}


# ── 수요 ─────────────────────────────────────────────────────
def add_demand(plant_id: int, material_id: int, qty: float, due_date: str, note: str, actor: dict | None) -> services.Result:
    if not services._finite(qty) or qty <= 0:
        return services.Result(False, "수량은 0보다 커야 합니다.")
    try:
        date.fromisoformat(due_date)
    except ValueError:
        return services.Result(False, "납기를 확인하세요.")
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM plants WHERE id = ?", (plant_id,)).fetchone():
            return services.Result(False, "플랜트가 없습니다.")
        m = conn.execute("SELECT code, active FROM materials WHERE id = ?", (material_id,)).fetchone()
        if m is None or not m["active"]:
            return services.Result(False, "사용 중인 자재를 고르세요.")
        did = conn.execute("INSERT INTO mrp_demands (plant_id, material_id, qty, due_date, note, active, created_by, created_at) "
                           "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                           (plant_id, material_id, qty, due_date, note.strip(), (actor or audit.SYSTEM)["name"], now_str())).lastrowid
        audit.record(conn, actor, "MRP_DEMAND", "mrp", did, {"material": m["code"], "qty": qty, "due": due_date})
    return services.Result(True, f"수요 등록: {m['code']} {fmt_qty(qty)} · 납기 {due_date}", tx_id=did)


def close_demand(demand_id: int, actor: dict | None, wh_ids=None) -> services.Result:
    with db.transaction() as conn:
        d = conn.execute("SELECT plant_id, active FROM mrp_demands WHERE id = ?", (demand_id,)).fetchone()
        if d is None or not d["active"]:
            return services.Result(False, "열린 수요가 아닙니다.")
        if wh_ids is not None:
            frag, wp = db.in_clause(list(wh_ids) or [-1])
            if not conn.execute(f"SELECT 1 FROM warehouses WHERE plant_id = ? AND id{frag}", (d["plant_id"], *wp)).fetchone():
                return services.Result(False, "이 플랜트의 권한이 없습니다.")
        conn.execute("UPDATE mrp_demands SET active = 0 WHERE id = ?", (demand_id,))
        audit.record(conn, actor, "MRP_DEMAND", "mrp", demand_id, {"closed": True})
    return services.Result(True, "수요를 닫았습니다 (다음 MRP부터 빠짐).")


def demands_df(plant_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT d.id, d.due_date, m.code, m.name, d.qty, m.unit, d.note, d.created_by, d.created_at
        FROM mrp_demands d JOIN materials m ON m.id = d.material_id
        WHERE d.plant_id = ? AND d.active = 1 ORDER BY d.due_date, d.id""", (plant_id,))


# ── 계산 ─────────────────────────────────────────────────────
def _levels(conn) -> tuple[dict[int, int], dict[int, dict]]:
    """품목별 BOM 단계(완제품 0, 그 부품 1, …)와 BOM {제품: {base, items:[(부품, 1개당, 창고)]}}."""
    boms: dict[int, dict] = {}
    for b in conn.execute("SELECT id, product_id, base_qty FROM boms WHERE active = 1"):
        items = [(int(i["component_id"]), float(i["qty"]) * (1 + float(i["scrap_pct"] or 0) / 100) / float(b["base_qty"]),
                  int(i["issue_wh_id"]) if i["issue_wh_id"] is not None else None)
                 for i in conn.execute("SELECT * FROM bom_items WHERE bom_id = ?", (b["id"],))]
        boms[int(b["product_id"])] = {"items": items}
    level: dict[int, int] = defaultdict(int)
    changed, guard = True, 0
    while changed and guard < 30:                       # 부품의 단계 = 그것을 쓰는 제품 단계 + 1 중 가장 깊은 것
        changed, guard = False, guard + 1
        for prod, b in boms.items():
            for comp, _, _ in b["items"]:
                if level[comp] < level[prod] + 1:
                    level[comp] = level[prod] + 1
                    changed = True
    return level, boms


COUNT_UNITS = {"EA", "PCS", "PAIR", "SET", "BOX", "ROLL", "CAN", "BAG", "PACK", "UNIT", "개", "장", "대"}


def _lot(qty: float, moq: float, mult: float, unit: str = "") -> float:
    """계획 수량: 최소 발주량 이상, 발주 배수로 올림. 낱개 단위(EA 등)는 정수로 올림."""
    q = max(qty, moq or 0)
    if mult and mult > 0:
        q = math.ceil(q / mult - 1e-9) * mult
    if (unit or "").upper() in COUNT_UNITS:
        q = math.ceil(q - 1e-9)
    return round(q, 4)


def run(plant_id: int, actor: dict | None, horizon_days: int = 90) -> services.Result:
    today = date.today()
    horizon = (today + timedelta(days=horizon_days)).isoformat()
    t0 = today.isoformat()
    with db.get_conn() as conn:
        whs = [int(r[0]) for r in conn.execute("SELECT id FROM warehouses WHERE plant_id = ? AND active = 1", (plant_id,))]
        if not whs:
            return services.Result(False, "이 플랜트에 사용 중인 창고가 없습니다.")
        frag, wp = db.in_clause(whs)
        mats = {int(r["id"]): dict(r) for r in conn.execute(
            "SELECT id, code, name, unit, safety_stock, lead_time_days, min_order_qty, order_multiple, unit_price "
            "FROM materials WHERE active = 1")}
        stock = {int(r[0]): float(r[1]) for r in conn.execute(
            f"SELECT material_id, {db.STOCK_EXPR} FROM transactions t WHERE t.warehouse_id{frag} GROUP BY material_id", wp)}
        # 안전재고는 자재마다 하나라서, 이 플랜트에서 다룬 적 있는 자재(창고에 거래가 있음)만 안전재고를 채운다
        plant_items = set(stock)
        level, boms = _levels(conn)
        events: dict[int, list] = defaultdict(list)      # 품목 → [(날짜, +공급/-수요, 설명)]
        for d in conn.execute("SELECT * FROM mrp_demands WHERE plant_id = ? AND active = 1 AND due_date <= ?", (plant_id, horizon)):
            events[int(d["material_id"])].append((max(d["due_date"], t0), -float(d["qty"]), f"수요 #{d['id']} ({d['due_date']})"))
        # 들어올 발주 잔량
        for r in conn.execute(f"""
                SELECT i.material_id, i.qty - COALESCE((SELECT SUM(t.qty) FROM transactions t WHERE t.po_no = o.po_no
                       AND t.po_item = CAST(i.line_no AS TEXT) AND t.tx_type = 'IN'), 0) AS remain,
                       COALESCE(NULLIF(r.need_date, ''), ?) AS due, o.po_no
                FROM po_items i JOIN purchase_orders o ON o.id = i.po_id LEFT JOIN purchase_requests r ON r.id = o.pr_id
                WHERE o.status IN ('OPEN', 'PARTIAL', 'PENDING_APPROVAL') AND o.warehouse_id{frag}""", (t0, *wp)):
            if float(r["remain"]) > 1e-9:
                events[int(r["material_id"])].append((max(r["due"], t0), float(r["remain"]), f"발주 {r['po_no']}"))
        # 승인된(아직 발주 전) 구매요청도 들어올 것으로 본다
        for r in conn.execute(f"""
                SELECT i.material_id, i.qty, COALESCE(NULLIF(p.need_date, ''), ?) AS due, p.pr_no
                FROM pr_items i JOIN purchase_requests p ON p.id = i.pr_id
                WHERE p.status IN ('PENDING', 'APPROVED') AND p.warehouse_id{frag}""", (t0, *wp)):
            events[int(r["material_id"])].append((max(r["due"], t0), float(r["qty"]), f"구매요청 {r['pr_no']}"))
        # 진행 중 작업지시: 완제품은 공급, 아직 투입 안 한 부품은 수요
        for p in conn.execute(f"SELECT * FROM productions WHERE status IN ('PLANNED', 'RELEASED') AND issue_wh_id{frag}", wp):
            if p["receipt_wh_id"] is not None:                       # 입고 창고가 없으면 완제품이 재고로 들어오지 않는다
                events[int(p["product_id"])].append((max(p["due_date"] or t0, t0), float(p["qty"]), f"작업지시 {p['prod_no']}"))
            for ln in conn.execute("SELECT component_id, planned_qty - issued_qty AS rest FROM production_lines "
                                   "WHERE production_id = ?", (p["id"],)):
                if float(ln["rest"]) > 1e-9:
                    events[int(ln["component_id"])].append((max(p["start_date"] or t0, t0), -float(ln["rest"]),
                                                             f"작업지시 {p['prod_no']} 부품"))
        default_wh = {int(r[0]): int(r[1]) for r in conn.execute(
            f"SELECT material_id, warehouse_id FROM transactions WHERE tx_type = 'IN' AND warehouse_id{frag} "
            f"GROUP BY material_id, warehouse_id ORDER BY COUNT(*)", wp)}          # 가장 많이 입고한 창고가 마지막에 남는다

    plans = []
    wanted = set(events) | {m for m, v in mats.items() if m in plant_items and float(v["safety_stock"] or 0) > 0
                            and stock.get(m, 0) < float(v["safety_stock"] or 0)}
    pending_wh: dict[int, int] = {}
    todo = sorted(wanted, key=lambda m: level.get(m, 0))
    seen: set[int] = set()
    while todo:
        mid = todo.pop(0)
        if mid in seen or mid not in mats:
            continue
        seen.add(mid)
        m = mats[mid]
        safety = float(m["safety_stock"] or 0) if mid in plant_items else 0.0
        avail = stock.get(mid, 0.0) - safety                              # 안전재고는 늘 남겨 둔다
        evs = list(events.get(mid, []))
        if avail < -1e-9:                                                 # 지금 안전재고 미달 → 오늘 필요 (오늘 들어올 것 먼저)
            evs.append((t0, 0.0, f"안전재고 {fmt_qty(safety)} 미달"))
        evs.sort(key=lambda e: (e[0], -e[1]))                             # 날짜 순, 같은 날은 공급 먼저
        reasons = []
        for when, q, why in evs:
            avail += q
            if q < 0 or why.startswith("안전재고"):
                reasons.append(why)
            if avail < -1e-9:
                short = -avail
                qty = _lot(short, float(m["min_order_qty"] or 0), float(m["order_multiple"] or 0), m["unit"])
                order = (date.fromisoformat(when) - timedelta(days=int(m["lead_time_days"] or 0))).isoformat()
                kind = "MAKE" if mid in boms else "BUY"
                wh = pending_wh.get(mid) or default_wh.get(mid) or whs[0]
                plans.append({"kind": kind, "material_id": mid, "qty": qty, "need_date": when, "order_date": order,
                              "warehouse_id": wh, "level": level.get(mid, 0), "pegging": " · ".join(reasons[-3:])})
                reasons = []                                              # 다음 계획의 근거는 이 뒤의 수요만
                avail += qty
                if kind == "MAKE":                                        # 착수일에 부품 수요 → 다음 단계
                    for comp, per, comp_wh in boms[mid]["items"]:
                        events[comp].append((max(order, t0), -round(per * qty, 4), f"{KIND[kind]} {m['code']} {fmt_qty(qty)}"))
                        if comp_wh:
                            pending_wh.setdefault(comp, comp_wh)
                        if comp not in seen:
                            todo.append(comp)
                    todo.sort(key=lambda x: level.get(x, 0))
    with db.transaction() as conn:
        late = sum(1 for p in plans if p["order_date"] < t0)
        summary = {"plans": len(plans), "buy": sum(1 for p in plans if p["kind"] == "BUY"),
                   "make": sum(1 for p in plans if p["kind"] == "MAKE"), "late": late}
        rid = conn.execute("INSERT INTO mrp_runs (plant_id, horizon, run_by, run_at, summary) VALUES (?, ?, ?, ?, ?)",
                           (plant_id, horizon, (actor or audit.SYSTEM)["name"], now_str(), json.dumps(summary))).lastrowid
        conn.executemany("INSERT INTO mrp_plans (run_id, kind, material_id, qty, need_date, order_date, warehouse_id, level, pegging) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(rid, p["kind"], p["material_id"], p["qty"], p["need_date"], p["order_date"], p["warehouse_id"],
                           p["level"], p["pegging"][:500]) for p in plans])
        audit.record(conn, actor, "MRP_RUN", "mrp", rid, {"plant_id": plant_id, **summary})
    msg = f"MRP 실행 — 계획 {len(plans)}건 (구매 {summary['buy']} · 생산 {summary['make']})"
    if late:
        msg += f" · 이미 늦은 계획 {late}건(발주·착수일이 오늘 이전)"
    return services.Result(True, msg, tx_id=rid, qty=len(plans))


NIGHTLY = {"id": None, "name": "MRP 자동 실행", "role": "ADMIN", "ip": ""}


def nightly_plants() -> list[int]:
    """밤 자동 실행 대상: 사용 중인 창고가 있고, 열린 수요가 있거나 그 플랜트 창고에서 쓰는 BOM 이 있는 플랜트."""
    return [int(r[0]) for r in db.query_df("""
        SELECT p.id FROM plants p
        WHERE p.active = 1 AND EXISTS (SELECT 1 FROM warehouses w WHERE w.plant_id = p.id AND w.active = 1)
          AND (EXISTS (SELECT 1 FROM mrp_demands d WHERE d.plant_id = p.id AND d.active = 1)
               OR EXISTS (SELECT 1 FROM mrp_runs r WHERE r.plant_id = p.id))
        ORDER BY p.id""").itertuples(index=False)]


def nightly(now: datetime | None = None) -> str:
    """배치 mrp_nightly: 정한 시각(MM_MRP_HOUR)이 지났고 오늘 자동 실행이 없으면 플랜트마다 한 번 실행한다.
    결과는 MRP 화면의 '최근 실행'으로 보이고, 담당자는 아침에 계획을 골라 구매요청·작업지시로 바꾼다."""
    hour = config.MRP_NIGHTLY_HOUR
    if hour < 0:
        return "꺼짐 (MM_MRP_HOUR=-1)"
    now = now or datetime.now()
    if now.hour < hour:
        return f"{hour}시 이후에 실행"
    today = now.date().isoformat()
    done, msgs = 0, []
    for pid in nightly_plants():
        ran = db.scalar("SELECT COUNT(*) FROM mrp_runs WHERE plant_id = ? AND run_by = ? AND run_at >= ?",
                        (pid, NIGHTLY["name"], today))
        if ran:
            continue
        r = run(pid, NIGHTLY)
        done += 1
        msgs.append(f"플랜트 {pid}: {r.message}")
    return " · ".join(msgs) if done else "오늘 실행함 (또는 대상 플랜트 없음)"


def latest_run(plant_id: int) -> dict | None:
    df = db.query_df("SELECT * FROM mrp_runs WHERE plant_id = ? ORDER BY id DESC LIMIT 1", (plant_id,))
    if df.empty:
        return None
    r = df.iloc[0].to_dict()
    r["summary"] = json.loads(r["summary"] or "{}")
    return r


def plans_df(run_id: int) -> pd.DataFrame:
    return db.query_df("""
        SELECT p.id, p.kind, p.level, m.code, m.name, p.qty, m.unit, p.order_date, p.need_date, w.code AS wh_code,
               m.lead_time_days, p.pegging, p.status, p.ref, p.material_id, p.warehouse_id, m.unit_price
        FROM mrp_plans p JOIN materials m ON m.id = p.material_id LEFT JOIN warehouses w ON w.id = p.warehouse_id
        WHERE p.run_id = ? ORDER BY p.order_date, p.level, m.code""", (run_id,))


def convert(run_id: int, plan_ids: list[int], *, actor: dict, wh_ids=None) -> services.Result:
    """고른 계획 → 구매 계획은 창고별 구매요청 하나씩, 생산 계획은 작업지시 하나씩."""
    plant = db.scalar("SELECT plant_id FROM mrp_runs WHERE id = ?", (run_id,))
    if plant is None:
        return services.Result(False, "MRP 실행을 찾을 수 없습니다.")
    if wh_ids is not None:
        frag, wp = db.in_clause(list(wh_ids) or [-1])
        if not db.scalar(f"SELECT COUNT(*) FROM warehouses WHERE plant_id = ? AND id{frag}", (plant, *wp)):
            return services.Result(False, "이 플랜트의 권한이 없습니다.")
    latest = latest_run(int(plant))
    if latest and int(latest["id"]) != int(run_id):         # 밤 자동 실행 등으로 새 계획이 생겼다 → 옛 화면에서 바꾸면 이중 발주
        return services.Result(False, f"더 새로운 MRP 실행(#{latest['id']}, {latest['run_at'][:16]})이 있습니다. "
                                      "화면을 새로 고쳐 최신 계획에서 바꾸세요.")
    df = plans_df(run_id)
    df = df[df["id"].isin(plan_ids) & (df["status"] == "OPEN")]
    if df.empty:
        return services.Result(False, "바꿀 계획을 고르세요 (이미 바꾼 계획은 빠짐).")
    msgs, made = [], 0
    for wh, grp in df[df["kind"] == "BUY"].groupby("warehouse_id"):
        merged = grp.groupby("material_id", as_index=False).agg(qty=("qty", "sum"), unit_price=("unit_price", "first"))
        r = purchasing.create_pr(int(wh), [(int(x.material_id), float(x.qty), float(x.unit_price or 0))      # 같은 자재는 한 줄
                                           for x in merged.itertuples()],
                                 str(grp["need_date"].min()), f"MRP 실행 #{run_id} 구매 계획 {len(grp)}건", actor, wh_ids)
        if not r.ok:
            return services.Result(False, f"구매요청을 만들지 못했습니다: {r.message}" + (f" (먼저 바꾼 것: {'; '.join(msgs)})" if msgs else ""))
        pr_no = db.scalar("SELECT pr_no FROM purchase_requests WHERE id = ?", (r.id,))
        _mark(grp["id"].tolist(), pr_no, actor)
        msgs.append(f"구매요청 {pr_no}")
        made += len(grp)
    for x in df[df["kind"] == "MAKE"].itertuples():
        bom_wh = int(x.warehouse_id)
        r = production.create_wo(int(x.material_id), float(x.qty), bom_wh, due_date=str(x.need_date), actor=actor,
                                 wh_ids=wh_ids, receipt_wh_id=bom_wh, source="MRP", note=f"MRP 실행 #{run_id}")
        if not r.ok:
            return services.Result(False, f"{x.code} 작업지시를 만들지 못했습니다: {r.message}")
        no = db.scalar("SELECT prod_no FROM productions WHERE id = ?", (r.tx_id,))
        _mark([int(x.id)], no, actor)
        msgs.append(f"작업지시 {no}")
        made += 1
    audit.log(actor, "MRP_CONVERT", "mrp", run_id, {"plans": made, "refs": msgs})
    return services.Result(True, f"계획 {made}건을 바꿨습니다: " + ", ".join(msgs), qty=made)


def _mark(ids: list[int], ref: str, actor) -> None:
    frag, params = db.in_clause(ids)
    db.execute(f"UPDATE mrp_plans SET status = 'CONVERTED', ref = ? WHERE id{frag}", (ref, *params))

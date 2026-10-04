"""재고 평가: 이동평균(MAVG) · 선입선출(FIFO). 평가 단위는 SAP처럼 자재 × 플랜트.

거래를 (일자, ID) 순서로 다시 돌려(replay) 금액을 계산한다. 결과를 거래에 저장하지 않으므로
평가 방법을 바꿔도, 과거 일자 거래가 들어와도 언제나 같은 규칙으로 다시 계산된다.
월 마감 때 두 방법의 평가 상태를 valuation_snapshots에 저장해 두고, 다음 계산은 거기서부터 한다.

규칙
  입고            입고 단가로 들어온다 (단가 0이면 현재 평균 → 자재 기준단가).
  출고·조정(−)    MAVG: 현재 평균단가.  FIFO: 먼저 들어온 층부터 소진.
  조정(+)         현재 평균단가(없으면 자재 기준단가)로 들어온다.
  취소 거래       원거래 금액을 정확히 되돌린다 (FIFO 출고 취소는 소진했던 층을 앞으로 되돌린다).
  창고 간 이동    같은 플랜트 안이면 평가 변화 없음. 플랜트 간이면 보내는 쪽 원가 그대로 받는 쪽에 들어간다.
재고가 없는데 출고되는 경우(과거 일자 입력 등)는 마지막 단가로 평가하고 경고로 남긴다.
"""

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

import config
from core import db, repository as repo
from core.utils import month_end

EPS = 1e-9


@dataclass
class Area:
    qty: float = 0.0
    value: float = 0.0
    layers: deque = field(default_factory=deque)     # FIFO: [수량, 단가]
    last_price: float = 0.0

    def avg(self, fallback: float) -> float:
        if self.qty > EPS:
            return self.value / self.qty
        return self.last_price or fallback


@dataclass
class Stats:
    receipts: float = 0.0
    issues: float = 0.0          # 출고 원가 (양수)
    adjustments: float = 0.0     # 조정 금액 (부호 포함)
    transfers: float = 0.0       # 플랜트 간 이동 (부호 포함)


def _consume(area: Area, qty: float, fallback: float) -> tuple[float, list]:
    """FIFO로 qty를 꺼낸다. (금액, 꺼낸 층들)."""
    taken, left, value = [], qty, 0.0
    while left > EPS and area.layers:
        lq, lp = area.layers[0]
        use = min(lq, left)
        value += use * lp
        taken.append([use, lp])
        left -= use
        if lq - use <= EPS:
            area.layers.popleft()
        else:
            area.layers[0] = [lq - use, lp]
    if left > EPS:                                   # 층이 모자람 → 마지막 단가
        price = area.last_price or fallback
        value += left * price
        taken.append([left, price])
    return value, taken


def _remove_at_price(area: Area, qty: float, price: float, fallback: float) -> tuple[float, list]:
    """입고 취소: 같은 단가의 층(최근 것부터)에서 먼저 빼고, 모자라면 FIFO."""
    taken, left, value = [], qty, 0.0
    for i in range(len(area.layers) - 1, -1, -1):
        if left <= EPS:
            break
        lq, lp = area.layers[i]
        if abs(lp - price) > EPS:
            continue
        use = min(lq, left)
        value += use * lp
        taken.append([use, lp])
        left -= use
        area.layers[i] = [lq - use, lp]
    area.layers = deque(x for x in area.layers if x[0] > EPS)
    if left > EPS:
        v, t = _consume(area, left, fallback)
        value += v
        taken += t
    return value, taken


class Engine:
    def __init__(self, method: str):
        self.method = method
        self.areas: dict[tuple[int, int], Area] = defaultdict(Area)
        self.tx_value: dict[int, float] = {}         # 거래별 평가 금액 (부호 = 재고금액 증감)
        self.tx_layers: dict[int, list] = {}         # FIFO: 출고 때 꺼낸 층
        self.transit: dict[tuple, tuple] = {}        # 플랜트 간 이동: 출고 쪽 금액·층
        # 생산: 작업지시별로 실제로 빠져나간 부품 원가(투입 − 반납). 완제품 입고는 이 금액으로 평가한다
        # → 생산으로 재고금액이 생기거나 없어지지 않는다 (투입 단가를 화면 계산 단가가 아니라 평가 원가로)
        self.wo_cost: dict[int, float] = defaultdict(float)
        self.warnings: list[str] = []

    def load(self, rows) -> None:
        for r in rows:
            a = self.areas[(int(r["material_id"]), int(r["plant_id"]))]
            a.qty, a.value = float(r["qty"]), float(r["value"])
            a.layers = deque(json.loads(r["layers"] or "[]"))
            a.last_price = a.value / a.qty if a.qty > EPS else 0.0

    def apply(self, t, same_plant_transfer: bool, stats: Stats | None) -> None:
        if same_plant_transfer:
            return
        key = (int(t["material_id"]), int(t["plant_id"]))
        a = self.areas[key]
        fallback = float(t["master_price"] or 0)
        q = float(t["qty"]) if t["tx_type"] != "OUT" else -float(t["qty"])   # 재고 증감(부호)
        fifo = self.method == "FIFO"
        orig = t["reversal_of"]
        tkey = (t["transfer_no"], t["lot_no"], orig is None) if t["transfer_no"] else None

        old_value = a.value
        if orig is not None and int(orig) not in self.tx_value:
            # 원거래가 마감 스냅샷 이전이라 평가 금액을 모른다 → 입고 취소는 원거래 단가, 출고 취소는 현재 평균으로
            if q > 0:
                price = a.avg(fallback)
                v = q * price
                if fifo:
                    a.layers.appendleft([q, price])
            else:
                price = float(t["unit_price"] or 0) if t["tx_type"] == "IN" and not t["transfer_no"] else 0.0
                if price <= 0:
                    price = a.avg(fallback)
                if fifo:
                    removed, _ = _remove_at_price(a, -q, price, fallback)
                    v = -removed
                else:
                    v = q * price
            self.warnings.append(f"거래 #{t['id']}: 마감 이전 거래 #{int(orig)}의 취소 — 원거래 금액 대신 "
                                 f"단가 {price:,.2f}로 평가")
            kind = ("transfer" if t["transfer_no"] else "adjust" if t["tx_type"] == "ADJ" else
                    "receipt" if t["tx_type"] == "IN" else "issue")
        elif orig is not None and int(orig) in self.tx_value:
            orig = int(orig)
            v = -self.tx_value[orig]
            if fifo:
                if q > 0:                             # 출고 취소 → 꺼냈던 층을 앞으로 되돌림
                    for layer in reversed(self.tx_layers.get(orig, [])):
                        a.layers.appendleft(list(layer))
                else:                                 # 입고 취소 → 그 단가 층에서 뺌 (실제로 뺀 금액)
                    price = self.tx_value[orig] / -q if abs(q) > EPS else 0
                    removed, _ = _remove_at_price(a, -q, price, fallback)
                    v = -removed
            kind = "transfer" if tkey else ("adjust" if t["tx_type"] == "ADJ" else
                                            ("receipt" if t["tx_type"] == "IN" else "issue"))
        elif tkey and t["tx_type"] == "IN":           # 플랜트 간 이동 입고: 보낸 쪽 원가 그대로
            v, layers = self.transit.pop(tkey, (q * a.avg(fallback), [[q, a.avg(fallback)]]))
            v = abs(v)
            if fifo:
                a.layers.extend([list(x) for x in layers])
            kind = "transfer"
        elif q > 0:                                   # 입고 · 조정(+)
            pid = t["production_id"] if "production_id" in t.keys() else None
            price = float(t["unit_price"] or 0) if t["tx_type"] == "IN" else 0.0
            kind = "receipt" if t["tx_type"] == "IN" else "adjust"
            if pid is not None and t["tx_type"] == "IN":
                if t["product_id"] is not None and int(t["product_id"]) == int(t["material_id"]):
                    spent = self.wo_cost.pop(int(pid), None)     # 완제품 입고 = 그 작업지시가 쓴 부품 원가
                    conv = float(t["labor_cost"] or 0) + float(t["overhead_cost"] or 0)   # + 노무비·경비 배부
                    if spent is not None and spent + conv > 0:
                        price = (spent + conv) / q
                else:                                 # 부품 반납 = 출고를 되돌림 (매입 아님): 지금 평균 단가로
                    price = a.avg(fallback)
                    self.wo_cost[int(pid)] -= q * price
                    kind = "issue"
            if price <= 0:
                price = a.avg(fallback)
            v = q * price
            if fifo:
                a.layers.append([q, price])
            a.last_price = price
        else:                                         # 출고 · 조정(−) · 이동 출고
            need = -q
            if a.qty < need - EPS:
                self.warnings.append(f"거래 #{t['id']}: 평가 시점 재고({a.qty:,.2f})보다 많이 출고 — 마지막 단가로 평가")
            if fifo:
                cost, taken = _consume(a, need, fallback)
                self.tx_layers[int(t["id"])] = taken
            else:
                cost = need * a.avg(fallback)
                taken = [[need, a.avg(fallback)]]
            v = -cost
            if tkey:
                self.transit[tkey] = (cost, taken)
            if "production_id" in t.keys() and t["production_id"] is not None:
                self.wo_cost[int(t["production_id"])] += cost
            kind = "transfer" if tkey else ("adjust" if t["tx_type"] == "ADJ" else "issue")

        a.qty += q
        if abs(a.qty) <= EPS:                         # 재고가 0이면 금액도 0 (반올림 찌꺼기 제거)
            a.qty, a.value = 0.0, 0.0
            a.layers.clear()
        elif fifo and a.qty > 0:
            a.value = sum(lq * lp for lq, lp in a.layers)
        else:
            a.value += v
        residual = a.value - (old_value + v)          # 위에서 금액을 맞추며 생긴 차이 → 조정으로 잡아 표가 맞게
        if stats is not None and abs(residual) > 0.005:
            stats.adjustments += residual
        if a.qty > EPS:
            a.last_price = a.value / a.qty
        self.tx_value[int(t["id"])] = v
        if stats is not None:
            if kind == "receipt":
                stats.receipts += v
            elif kind == "issue":
                stats.issues += -v
            elif kind == "adjust":
                stats.adjustments += v
            else:
                stats.transfers += v


def _base(conn, method: str, as_of: str) -> str:
    """as_of 이전에 끝난 마감월 중 평가 스냅샷이 있는 가장 최근 달."""
    closed = repo.closed_through(conn)
    if not closed:
        return ""
    rows = conn.execute("SELECT DISTINCT ym FROM valuation_snapshots WHERE method = ? AND ym <= ? ORDER BY ym DESC",
                        (method, closed)).fetchall()
    for r in rows:
        if month_end(r["ym"]) <= as_of:
            return r["ym"]
    return ""


def _events(conn, after: str, until: str, material_id: int | None = None):
    only = " AND t.material_id = ?" if material_id is not None else ""
    rows = conn.execute(f"""
        SELECT t.id, t.tx_date, t.tx_type, t.qty, t.unit_price, t.material_id, t.reversal_of, t.transfer_no,
               t.lot_no, w.plant_id, m.unit_price AS master_price, t.production_id, p.product_id,
               p.labor_cost, p.overhead_cost
        FROM transactions t JOIN warehouses w ON w.id = t.warehouse_id JOIN materials m ON m.id = t.material_id
        LEFT JOIN productions p ON p.id = t.production_id
        WHERE t.tx_date > ? AND t.tx_date <= ?{only}
        ORDER BY t.tx_date, t.id
        """, (after, until, *([material_id] if material_id is not None else []))).fetchall()
    # 같은 플랜트 안의 이동은 평가 대상이 아니다 → (이동번호, 로트, 원/취소) 묶음의 플랜트가 모두 같으면 건너뜀
    plants = defaultdict(set)
    for r in rows:
        if r["transfer_no"]:
            plants[(r["transfer_no"], r["lot_no"], r["reversal_of"] is None)].add(int(r["plant_id"]))
    same = {k for k, v in plants.items() if len(v) == 1}
    return [(r, bool(r["transfer_no"]) and (r["transfer_no"], r["lot_no"], r["reversal_of"] is None) in same)
            for r in rows]


def run(conn, method: str, start: str, end: str) -> tuple[Engine, Engine, Stats]:
    """(기초 시점 엔진 복사본, 기말 엔진, 기간 통계). start 전날까지가 기초."""
    opening_day = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    base = _base(conn, method, opening_day)
    eng = Engine(method)
    if base:
        eng.load(conn.execute("SELECT * FROM valuation_snapshots WHERE ym = ? AND method = ?", (base, method)))
    for t, same in _events(conn, month_end(base), opening_day):
        eng.apply(t, same, None)
    opening = {k: (a.qty, a.value) for k, a in eng.areas.items()}
    stats: dict[tuple, Stats] = defaultdict(Stats)
    for t, same in _events(conn, opening_day, end):
        eng.apply(t, same, stats[(int(t["material_id"]), int(t["plant_id"]))])
    return opening, eng, stats


def unit_cost(conn, material_id: int, plant_id: int, as_of: str, method: str | None = None) -> float | None:
    """그 자재·플랜트의 as_of 시점 평가 단가 (재고가 없으면 None). 생산 투입 단가 — 재고 평가에서 빠지는 금액과 같게.
    그 자재의 거래만 마지막 마감 스냅샷부터 다시 계산한다(호출하는 쪽 트랜잭션 안에서, 방금 넣은 거래 포함)."""
    method = method if method in config.VALUATION_METHODS else config.VALUATION_DEFAULT
    base = _base(conn, method, as_of)
    eng = Engine(method)
    if base:
        eng.load(conn.execute("SELECT * FROM valuation_snapshots WHERE ym = ? AND method = ? AND material_id = ?",
                              (base, method, material_id)))
    for t, same in _events(conn, month_end(base), as_of, material_id):
        eng.apply(t, same, None)
    a = eng.areas.get((int(material_id), int(plant_id)))
    if a is None or a.qty <= EPS:
        return None
    if method == "FIFO" and a.layers:                 # 선입선출: 지금 꺼낼 층의 단가(맨 앞 층)
        return float(a.layers[0][1])
    return a.value / a.qty


def report(start: str, end: str, method: str | None = None, plant_ids=None) -> tuple[pd.DataFrame, list[str]]:
    """자재 × 플랜트별 기초·입고·출고원가·조정·이동·기말 금액."""
    method = method if method in config.VALUATION_METHODS else config.VALUATION_DEFAULT
    with db.get_conn() as conn:
        opening, eng, stats = run(conn, method, start, end)
        mats = {int(r["id"]): r for r in conn.execute("SELECT id, code, name, unit FROM materials")}
        plants = {int(r["id"]): r for r in conn.execute("SELECT id, code, name FROM plants")}
    rows = []
    for key in sorted(set(opening) | set(eng.areas)):
        mid, pid = key
        if plant_ids is not None and pid not in plant_ids:
            continue
        oq, ov = opening.get(key, (0.0, 0.0))
        a = eng.areas.get(key, Area())
        st = stats.get(key, Stats())
        if max(abs(oq), abs(ov), abs(a.qty), abs(a.value), abs(st.receipts), abs(st.issues)) < EPS:
            continue
        m, p = mats[mid], plants[pid]
        rows.append({"code": m["code"], "name": m["name"], "unit": m["unit"], "plant": p["code"],
                     "open_qty": oq, "open_value": ov, "receipts": st.receipts, "issues": st.issues,
                     "adjustments": st.adjustments, "transfers": st.transfers,
                     "close_qty": a.qty, "close_value": a.value,
                     "unit_cost": a.value / a.qty if a.qty > EPS else 0.0})
    return pd.DataFrame(rows), eng.warnings


def tx_values(method: str, start: str, end: str) -> dict[int, float]:
    with db.get_conn() as conn:
        _, eng, _ = run(conn, method, start, end)
    return eng.tx_value


def snapshot(conn, ym: str) -> None:
    """월 마감 때 두 방법의 평가 상태를 저장한다 (마감 트랜잭션 안에서)."""
    end = month_end(ym)
    for method in config.VALUATION_METHODS:
        base = _base(conn, method, end)
        eng = Engine(method)
        if base:
            eng.load(conn.execute("SELECT * FROM valuation_snapshots WHERE ym = ? AND method = ?", (base, method)))
        for t, same in _events(conn, month_end(base), end):
            eng.apply(t, same, None)
        conn.executemany(
            "INSERT INTO valuation_snapshots (ym, method, material_id, plant_id, qty, value, layers) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (ym, method, material_id, plant_id) "
            "DO UPDATE SET qty = excluded.qty, value = excluded.value, layers = excluded.layers",
            [(ym, method, mid, pid, a.qty, a.value, json.dumps([list(x) for x in a.layers]))
             for (mid, pid), a in eng.areas.items()])

"""데모용 샘플 데이터 (포워딩 창고 포장·고박 자재).

seed()            최근 30일 거래만 (테스트가 이 숫자에 맞춰져 있다 — 바꾸지 말 것)
seed(history=True) 화면의 '샘플 데이터 생성': 위에 더해 지난 11개월 거래(달마다 단가가 조금씩 다름),
                  오래 쓰지 않은 자재(장기 미사용), 실사 감모를 넣어 대시보드·재고평가·수불부를 실제처럼 볼 수 있게 한다.
                  지난 달들의 거래는 자재마다 입고=출고라 지금 재고는 seed()와 같다.
"""

import random
from datetime import date, timedelta

from core import db, repository as repo
from core.utils import now_str

MATERIALS = [
    ("PKG-001", "수출용 목재 팔레트", "1100x1100 훈증", "EA", "포장재", 50, 18000, "A-01", "대한팔레트"),
    ("PKG-002", "스트레치 필름", "500mm x 300m", "ROLL", "포장재", 30, 9500, "A-02", "한국필름"),
    ("PKG-003", "5겹 골판지 박스", "600x400x400", "EA", "포장재", 200, 1200, "A-03", "동양포장"),
    ("LSH-001", "라싱 벨트", "50mm 5T 9m", "EA", "고박자재", 40, 15000, "B-01", "세이프라싱"),
    ("LSH-002", "에어백(던니지백)", "900x1800", "EA", "고박자재", 60, 7000, "B-02", "세이프라싱"),
    ("LBL-001", "취급주의 라벨", "100x150", "EA", "라벨/소모품", 500, 80, "C-01", "라벨코리아"),
    ("SFT-001", "안전장갑", "코팅 L", "PAIR", "안전용품", 100, 1500, "C-02", "안전나라"),
]

MOVEMENTS = [
    ("PKG-001", "IN", 120, 25), ("PKG-001", "OUT", 45, 18), ("PKG-001", "OUT", 40, 6),
    ("PKG-002", "IN", 40, 20), ("PKG-002", "OUT", 22, 4),
    ("PKG-003", "IN", 500, 28), ("PKG-003", "OUT", 180, 15), ("PKG-003", "OUT", 150, 3),
    ("LSH-001", "IN", 80, 22), ("LSH-001", "OUT", 30, 10),
    ("LSH-002", "IN", 100, 19), ("LSH-002", "OUT", 70, 2),
    ("LBL-001", "IN", 2000, 27), ("LBL-001", "OUT", 900, 12),
    ("SFT-001", "IN", 150, 24), ("SFT-001", "OUT", 60, 8), ("SFT-001", "ADJ", -3, 1),
]


# 장기 미사용(마지막 출고가 오래된) 자재: (자재, 입고 수량, 며칠 전 입고, 며칠 전 마지막 출고, 출고 수량)
IDLE = [
    (("PKG-009", "방습제(실리카겔) 1kg", "1kg 파우치", "EA", "포장재", 20, 4500, "A-09", "드라이팩"), 120, 200, 150, 30),
    (("LSH-009", "와이어 로프 슬링", "12mm 3m", "EA", "고박자재", 5, 32000, "B-09", "세이프라싱"), 20, 240, None, 0),
]


def seed(history: bool = False) -> None:
    ts = now_str()
    with db.transaction() as conn:
        for m in MATERIALS:
            conn.execute(
                """
                INSERT INTO materials
                    (code, name, spec, unit, category, safety_stock, unit_price,
                     location, supplier, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT (code) DO NOTHING
                """,
                (*m, ts, ts),
            )
        ids = {r["code"]: (r["id"], r["unit_price"])
               for r in conn.execute("SELECT id, code, unit_price FROM materials")}
        for code, tx_type, qty, days_ago in MOVEMENTS:
            mid, price = ids[code]
            repo.insert_transaction(conn, {
                "material_id": mid, "tx_type": tx_type, "qty": qty, "unit_price": price,
                "tx_date": (date.today() - timedelta(days=days_ago)).isoformat(),
                "ref_no": f"SAMPLE-{code}", "partner": "샘플거래처",
                "note": "샘플 데이터", "created_by": "system",
            })
    if history:
        _history()


def _history() -> None:
    """지난 11개월 거래 · 장기 미사용 자재 · 실사 감모 (화면의 '샘플 데이터 생성'만)."""
    rng = random.Random(7)
    ts = now_str()
    today = date.today()
    with db.transaction() as conn:
        for m, *_ in IDLE:
            conn.execute(
                "INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, supplier, "
                "active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT (code) DO NOTHING",
                (*m, ts, ts))
        rows = {r["code"]: (r["id"], float(r["unit_price"]))
                for r in conn.execute("SELECT id, code, unit_price FROM materials")}

        def add(code, tx_type, qty, price, when, note="샘플 데이터"):
            repo.insert_transaction(conn, {
                "material_id": rows[code][0], "tx_type": tx_type, "qty": qty, "unit_price": round(price),
                "tx_date": when.isoformat(), "ref_no": f"SAMPLE-{code}", "partner": "샘플거래처",
                "note": note, "created_by": "system"})

        # 지난 11개월: 자재마다 달마다 입고 후 같은 수량 출고 (단가는 기준단가 ±8%로 흔들림)
        for code, _tx, qty, _d in [mv for mv in MOVEMENTS if mv[1] == "IN"]:
            base = rows[code][1]
            for months_ago in range(11, 0, -1):
                first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
                for _ in range(months_ago - 1):
                    first = (first - timedelta(days=1)).replace(day=1)
                q = max(1, round(qty * rng.uniform(0.3, 0.8)))
                price = base * rng.uniform(0.92, 1.08)
                add(code, "IN", q, price, first + timedelta(days=rng.randint(0, 9)))
                add(code, "OUT", q, price, first + timedelta(days=rng.randint(12, 26)))
        # 로트·유효기한 자재: 지난 로트 · 30일 안에 끝나는 로트 · 넉넉한 로트
        ts2 = now_str()
        conn.execute("INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, "
                     "supplier, lot_managed, expiry_managed, active, created_at, updated_at) VALUES "
                     "('CHM-901', '방청제(VCI 스프레이)', '420ml', 'EA', '포장재', 24, 6800, 'C-09', '케미칼원', 1, 1, 1, ?, ?) "
                     "ON CONFLICT (code) DO NOTHING", (ts2, ts2))
        rows["CHM-901"] = (int(conn.execute("SELECT id FROM materials WHERE code = 'CHM-901'").fetchone()[0]), 6800.0)
        for lot, exp_days, in_ago, in_qty, out_qty in (("VCI-2501", -10, 160, 40, 34), ("VCI-2507", 20, 70, 36, 20),
                                                       ("VCI-2509", 300, 20, 48, 6)):
            repo.ensure_lot(conn, rows["CHM-901"][0], lot, (today + timedelta(days=exp_days)).isoformat())
            for tx_type, qty, ago in (("IN", in_qty, in_ago), ("OUT", out_qty, max(in_ago - 15, 1))):
                repo.insert_transaction(conn, {
                    "material_id": rows["CHM-901"][0], "tx_type": tx_type, "qty": qty, "unit_price": 6800,
                    "tx_date": (today - timedelta(days=ago)).isoformat(), "lot_no": lot, "ref_no": "SAMPLE-CHM-901",
                    "partner": "샘플거래처", "note": "샘플 데이터", "created_by": "system"})

        # 두 번째 창고와 창고 간 이동 (출고·입고 두 행, 같은 이동번호)
        plant = int(conn.execute("SELECT plant_id FROM warehouses ORDER BY id LIMIT 1").fetchone()[0])
        conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, 'WH2', '부산 신항 창고', '', ?) "
                     "ON CONFLICT (code) DO NOTHING", (plant, ts2))
        wh1 = int(conn.execute("SELECT id FROM warehouses ORDER BY id LIMIT 1").fetchone()[0])
        wh2 = int(conn.execute("SELECT id FROM warehouses WHERE code = 'WH2'").fetchone()[0])
        when = (today - timedelta(days=5)).isoformat()
        for tx_type, wh, partner in (("OUT", wh1, "→ WH2"), ("IN", wh2, "← WH1")):
            repo.insert_transaction(conn, {
                "material_id": rows["PKG-003"][0], "tx_type": tx_type, "qty": 30, "unit_price": rows["PKG-003"][1],
                "tx_date": when, "warehouse_id": wh, "transfer_no": "TRF-SAMPLE-0001", "movement_type": "311",
                "ref_no": "SAMPLE-TRF", "partner": partner, "note": "샘플 데이터", "created_by": "system"})

        # 필요일이 지났는데 아직 다 들어오지 않은 발주 (구매요청 → 발주)
        need = (today - timedelta(days=3)).isoformat()
        pr = conn.execute("INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, reason, status, total_amount, "
                          "required_steps, requested_by, requested_at, updated_at) VALUES "
                          "('PR-SAMPLE-0001', ?, ?, '성수기 대비 팔레트 보충', 'ORDERED', 1440000, 2, 'system', ?, ?)",
                          (wh1, need, ts2, ts2)).lastrowid
        conn.execute("INSERT INTO pr_items (pr_id, line_no, material_id, qty, est_price) VALUES (?, 10, ?, 80, 18000)",
                     (pr, rows["PKG-001"][0]))
        po = conn.execute("INSERT INTO purchase_orders (po_no, pr_id, supplier, warehouse_id, status, total_amount, "
                          "created_by, created_at, approved_by, approved_at) VALUES "
                          "('PO-SAMPLE-0001', ?, '대한팔레트', ?, 'PARTIAL', 1440000, 'system', ?, 'system', ?)",
                          (pr, wh1, ts2, ts2)).lastrowid
        conn.execute("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, 10, ?, 80, 18000)",
                     (po, rows["PKG-001"][0]))
        repo.insert_transaction(conn, {
            "material_id": rows["PKG-001"][0], "tx_type": "IN", "qty": 30, "unit_price": 18000,
            "tx_date": (today - timedelta(days=4)).isoformat(), "po_no": "PO-SAMPLE-0001", "po_item": "10",
            "movement_type": "101", "ref_no": "PO-SAMPLE-0001", "partner": "대한팔레트", "note": "샘플 데이터 (분할 입고)",
            "created_by": "system"})

        # 결재 대기: 금액이 큰 실사 조정 요청
        conn.execute("INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status, "
                     "requested_by, requested_at) VALUES ('ADJ', ?, ?, ?, -12, 180000, '{}', 'PENDING', '샘플 담당자', ?)",
                     (rows["LSH-001"][0], wh1, (today - timedelta(days=1)).isoformat(), ts2))

        # 장기 미사용 자재
        for m, in_qty, in_ago, out_ago, out_qty in IDLE:
            code, price = m[0], m[6]
            add(code, "IN", in_qty, price, today - timedelta(days=in_ago))
            if out_ago:
                add(code, "OUT", out_qty, price, today - timedelta(days=out_ago))

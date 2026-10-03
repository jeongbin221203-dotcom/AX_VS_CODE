"""제조 공장 샘플 데이터 (데이터 관리 → '제조 샘플 추가'). 이미 다른 데이터가 있어도 한 번만 추가된다.

창원 제조공장(P-CW) 아래 원자재·부품·화학소모품 창고 3곳, 자재 20종, 지난 12개월 거래:
  - 매달 구매 입고(공급처별, 단가가 조금씩 오르내림)와 생산팀별 출고(원가센터 C100 생산1팀 · C200 생산2팀 · C300 품질 · C400 설비보전)
  - 원자재 창고 → 부품 창고 이동, 분기마다 실사 감모
  - 도료·접착제는 로트·유효기한 관리 (기한 지난 로트 · 30일 안에 끝나는 로트 포함)
  - 구매요청 → 발주(필요일 지난 분할 입고 1건), 거래명세서 3장, 결재 대기 1건
재고가 음수가 되지 않게 매달 남은 재고 안에서만 출고한다. 같은 난수 씨앗을 써서 언제 만들어도 같은 모양이다.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from core import db, repository as repo, statements
from core.utils import now_str

PLANT = ("P-CW", "창원 제조공장")
WAREHOUSES = [("CW-RM", "원자재 창고"), ("CW-PT", "부품 창고"), ("CW-CH", "화학·소모품 창고")]

# (코드, 이름, 규격, 단위, 분류, 안전재고, 기준단가, 보관위치, 공급처, 창고, 월 입고량, 월 사용 비율, 로트관리)
MATERIALS = [
    ("RM-STL-001", "냉간압연 강판", "SPCC 1.2t × 1219 × 2438", "KG", "원자재", 3000, 1150, "R-01", "포스코스틸", "CW-RM", 9000, 0.85, 0),
    ("RM-STL-002", "아연도금 강판", "SGCC 0.8t", "KG", "원자재", 1500, 1380, "R-02", "포스코스틸", "CW-RM", 4000, 0.8, 0),
    ("RM-AL-001", "알루미늄 판재", "A5052 2.0t", "KG", "원자재", 600, 4200, "R-03", "한국알루미늄", "CW-RM", 1500, 0.75, 0),
    ("RM-RES-001", "PP 수지", "사출용 MI 12", "KG", "원자재", 1000, 1850, "R-04", "대한유화", "CW-RM", 2500, 0.9, 0),
    ("PT-BLT-001", "육각볼트", "M8 × 25 SUS304", "EA", "부품", 5000, 45, "P-01", "동아체결", "CW-PT", 20000, 0.8, 0),
    ("PT-NUT-001", "육각너트", "M8 SUS304", "EA", "부품", 5000, 18, "P-02", "동아체결", "CW-PT", 20000, 0.8, 0),
    ("PT-BRG-001", "깊은홈 볼베어링", "6204ZZ", "EA", "부품", 300, 3200, "P-03", "한일베어링", "CW-PT", 800, 0.85, 0),
    ("PT-SEL-001", "오링", "NBR P-22", "EA", "부품", 1000, 120, "P-04", "세원실링", "CW-PT", 3000, 0.7, 0),
    ("PT-MTR-001", "BLDC 모터", "24V 60W", "EA", "부품", 120, 38000, "P-05", "성진모터", "CW-PT", 260, 0.9, 0),
    ("PT-PCB-001", "제어 PCB", "MCU 보드 Rev.C", "EA", "부품", 150, 21000, "P-06", "넥스트전자", "CW-PT", 280, 0.9, 0),
    ("PT-HRN-001", "와이어 하네스", "6P 300mm", "EA", "부품", 300, 2600, "P-07", "넥스트전자", "CW-PT", 700, 0.85, 0),
    ("CH-PNT-001", "분체도료", "에폭시 회색 RAL7035", "KG", "화학", 200, 9800, "C-01", "케미칼원", "CW-CH", 400, 0.8, 1),
    ("CH-ADH-001", "구조용 접착제", "2액형 에폭시 400ml", "EA", "화학", 60, 23000, "C-02", "케미칼원", "CW-CH", 120, 0.75, 1),
    ("CH-OIL-001", "수용성 절삭유", "20L", "CAN", "화학", 10, 68000, "C-03", "대양윤활", "CW-CH", 20, 0.8, 0),
    ("CS-WLD-001", "CO2 용접 와이어", "Ø1.2 20kg", "ROLL", "소모품", 15, 52000, "C-04", "고려용접", "CW-CH", 30, 0.85, 0),
    ("CS-GLV-001", "니트릴 코팅장갑", "L", "PAIR", "소모품", 400, 1300, "C-05", "안전나라", "CW-CH", 1000, 0.9, 0),
    ("TL-DRL-001", "초경 드릴", "Ø8.5 × 117", "EA", "공구", 20, 31000, "C-06", "한국공구", "CW-CH", 40, 0.7, 0),
    ("TL-INS-001", "선삭 인서트", "CNMG120408", "EA", "공구", 100, 7400, "C-07", "한국공구", "CW-CH", 200, 0.8, 0),
    ("PK-BOX-001", "제품 포장박스", "B골 520 × 420 × 300", "EA", "포장재", 500, 1650, "P-08", "동양포장", "CW-PT", 1600, 0.85, 0),
    ("PK-PAL-002", "플라스틱 팔레트", "1100 × 1100 4방향", "EA", "포장재", 30, 42000, "P-09", "대한팔레트", "CW-PT", 60, 0.6, 0),
]
COST_CENTERS = [("C100", 0.45), ("C200", 0.35), ("C300", 0.08), ("C400", 0.12)]


def exists() -> bool:
    return bool(db.scalar("SELECT COUNT(*) FROM plants WHERE code = ?", (PLANT[0],)))


def seed_manufacturing() -> dict:
    if exists():
        return {"skipped": True}
    rng = random.Random(11)
    today = date.today()
    ts = now_str()
    counts = {"materials": 0, "transactions": 0, "statements": 0}
    with db.transaction() as conn:
        pid = conn.execute("INSERT INTO plants (code, name, sap_plant, created_at) VALUES (?, ?, '', ?)",
                           (*PLANT, ts)).lastrowid
        whs = {}
        for code, name in WAREHOUSES:
            whs[code] = conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) "
                                     "VALUES (?, ?, ?, '', ?)", (pid, code, name, ts)).lastrowid
        mats = {}
        for (code, name, spec, unit, cat, safety, price, loc, supplier, _wh, _q, _u, lot) in MATERIALS:
            conn.execute(
                "INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, supplier, "
                "lot_managed, expiry_managed, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT (code) DO NOTHING",
                (code, name, spec, unit, cat, safety, price, loc, supplier, lot, lot, ts, ts))
            # ON CONFLICT 문은 PostgreSQL 에서 새 번호를 돌려주지 않는다 → 코드로 다시 찾는다
            mats[code] = int(conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()[0])
            counts["materials"] += 1

        def tx(code, tx_type, qty, price, when, wh_code, **extra):
            repo.insert_transaction(conn, {
                "material_id": mats[code], "tx_type": tx_type, "qty": qty, "unit_price": round(price),
                "tx_date": when.isoformat(), "warehouse_id": whs[wh_code], "ref_no": extra.pop("ref_no", "MFG-SAMPLE"),
                "partner": extra.pop("partner", ""), "note": extra.pop("note", "제조 샘플"), "created_by": "system", **extra})
            counts["transactions"] += 1

        start = (today.replace(day=1) - timedelta(days=330)).replace(day=1)
        for (code, _n, _s, _unit, _c, safety, price, _l, supplier, wh_code, monthly, use, lot) in MATERIALS:
            balance = 0.0
            month = start
            lots: list[list] = []                       # [로트, 남은 수량, 유효기한] — 유효기한 빠른 순으로 꺼낸다(FEFO)
            while month <= today:
                p = price * (1 + rng.uniform(-0.06, 0.08) + (month - start).days / 3650)   # 완만한 단가 상승
                received = round(monthly * rng.uniform(0.8, 1.2))
                in_day = month + timedelta(days=rng.randint(1, 6))
                if in_day >= today:
                    break
                lot_no = ""
                if lot:
                    lot_no = f"{code.split('-')[1]}-{month:%y%m}"                       # 예: PNT-2604
                    expiry = month + timedelta(days=200)                                   # 6~7개월 사용기한
                    repo.ensure_lot(conn, mats[code], lot_no, expiry.isoformat())
                    lots.append([lot_no, float(received), expiry])
                tx(code, "IN", received, p, in_day, wh_code, partner=supplier, ref_no=f"GR-{month:%y%m}-{code[-3:]}",
                   lot_no=lot_no, movement_type="101")
                balance += received
                # 이번 달 말 재고가 안전재고의 0.4~1.8배가 되도록 생산 출고 (일부는 안전재고 미달·소진 임박)
                target_out = max(0.0, balance - safety * rng.uniform(0.4, 1.8))
                for week in range(4):                                                      # 주마다 생산 출고
                    day = in_day + timedelta(days=4 + week * 7 + rng.randint(0, 2))
                    if day >= today:
                        break
                    qty = min(round(target_out / 4 * rng.uniform(0.8, 1.2), 2), round(balance * 0.95, 2))
                    if qty <= 0:
                        continue
                    cc = rng.choices([c for c, _ in COST_CENTERS], [w for _, w in COST_CENTERS])[0]
                    if not lot:
                        tx(code, "OUT", qty, p, day, wh_code, partner=f"생산 {cc}", cost_center=cc,
                           ref_no=f"MO-{day:%y%m%d}", movement_type="201")
                    else:
                        left = qty                       # 기한이 지나지 않은 로트 중 기한 빠른 것부터
                        for entry in sorted(lots, key=lambda e: e[2]):
                            if left <= 1e-9:
                                break
                            if entry[1] <= 1e-9 or entry[2] < day:
                                continue
                            use_q = round(min(entry[1], left), 2)
                            tx(code, "OUT", use_q, p, day, wh_code, partner=f"생산 {cc}", cost_center=cc,
                               ref_no=f"MO-{day:%y%m%d}", lot_no=entry[0], movement_type="201")
                            entry[1] -= use_q
                            left -= use_q
                        qty -= left                      # 쓸 수 있는 로트가 모자라면 그만큼만
                    balance -= qty
                if month.month in (3, 6, 9, 12) and not lot and balance > 10:              # 분기 실사 감모
                    loss = -round(max(1.0, balance * rng.uniform(0.002, 0.01)), 2)
                    tx(code, "ADJ", loss, p, min(month + timedelta(days=27), today - timedelta(days=1)), wh_code,
                       note="분기 실사 감모 (제조 샘플)", movement_type="702")
                    balance += loss
                if lot:                                  # 기한이 지난 로트는 폐기(실사 조정)로 정리
                    for entry in lots:
                        end_of_month = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                        if entry[1] > 1e-9 and entry[2] < end_of_month < today:
                            tx(code, "ADJ", -round(entry[1], 2), p, end_of_month, wh_code, lot_no=entry[0],
                               note="유효기한 경과 폐기 (제조 샘플)", movement_type="702")
                            balance -= entry[1]
                            entry[1] = 0.0
                month = (month + timedelta(days=32)).replace(day=1)

        # 기한 지난 로트 하나 (재고가 남은 채 기한이 지남) · 곧 끝나는 로트 하나
        tx("CH-PNT-001", "IN", 25, 9800, today - timedelta(days=230), "CW-CH", partner="케미칼원", lot_no="PNT-OLD",
           ref_no="GR-OLD-PNT", movement_type="101")
        repo.ensure_lot(conn, mats["CH-PNT-001"], "PNT-OLD", (today - timedelta(days=12)).isoformat())
        tx("CH-ADH-001", "IN", 18, 23000, today - timedelta(days=150), "CW-CH", partner="케미칼원", lot_no="ADH-SOON",
           ref_no="GR-SOON-ADH", movement_type="101")
        repo.ensure_lot(conn, mats["CH-ADH-001"], "ADH-SOON", (today + timedelta(days=18)).isoformat())

        # 원자재 창고 → 부품 창고 이동 (같은 이동번호의 출고·입고)
        for n, (code, qty) in enumerate((("RM-AL-001", 120), ("RM-RES-001", 300)), 1):
            when = today - timedelta(days=9 + n)
            for tx_type, wh_code, partner in (("OUT", "CW-RM", "→ CW-PT"), ("IN", "CW-PT", "← CW-RM")):
                tx(code, tx_type, qty, dict((m[0], m[6]) for m in MATERIALS)[code], when, wh_code,
                   partner=partner, transfer_no=f"TRF-MFG-{n:04d}", movement_type="311")

        # 구매요청 → 발주 (필요일 지남 · 분할 입고)
        need = today - timedelta(days=4)
        pr = conn.execute("INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, reason, status, total_amount, "
                          "required_steps, requested_by, requested_at, updated_at) VALUES "
                          "('PR-MFG-0001', ?, ?, '라인 증설 모터 확보', 'ORDERED', 15200000, 3, 'system', ?, ?)",
                          (whs["CW-PT"], need.isoformat(), ts, ts)).lastrowid
        conn.execute("INSERT INTO pr_items (pr_id, line_no, material_id, qty, est_price) VALUES (?, 10, ?, 400, 38000)",
                     (pr, mats["PT-MTR-001"]))
        po = conn.execute("INSERT INTO purchase_orders (po_no, pr_id, supplier, warehouse_id, status, total_amount, created_by, "
                          "created_at, approved_by, approved_at) VALUES ('PO-MFG-0001', ?, '성진모터', ?, 'PARTIAL', "
                          "15200000, 'system', ?, 'system', ?)", (pr, whs["CW-PT"], ts, ts)).lastrowid
        conn.execute("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, 10, ?, 400, 38000)",
                     (po, mats["PT-MTR-001"]))
        tx("PT-MTR-001", "IN", 150, 38000, today - timedelta(days=6), "CW-PT", partner="성진모터",
           po_no="PO-MFG-0001", po_item="10", ref_no="PO-MFG-0001", movement_type="101")

        # 결재 대기: 금액이 큰 실사 조정
        conn.execute("INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status, "
                     "requested_by, requested_at) VALUES ('ADJ', ?, ?, ?, -4, 152000, '{}', 'PENDING', '제조 샘플 담당자', ?)",
                     (mats["PT-MTR-001"], whs["CW-PT"], (today - timedelta(days=1)).isoformat(), ts))

    # 거래명세서 3장 (입고 2 · 출고 1) — 같은 규칙(services)으로 등록
    for kind, no, partner, wh_code, rows, days in (
            ("IN", "DA-2610-031", "동아체결", "CW-PT", [("PT-BLT-001", 5000, 46), ("PT-NUT-001", 5000, 18)], 3),
            ("IN", "NX-1007", "넥스트전자", "CW-PT", [("PT-PCB-001", 60, 21500), ("PT-HRN-001", 120, 2600)], 2),
            ("OUT", "SH-2610-07", "협력사 경남정밀", "CW-RM", [("RM-STL-001", 500, 1180)], 1)):
        lines = [statements.Line(no=i, code=code, qty=float(q), unit_price=float(p)) for i, (code, q, p) in enumerate(rows, 1)]
        statements.check(kind, lines)
        r = statements.register({"kind": kind, "warehouse_id": str(whs[wh_code]), "tx_date": (today - timedelta(days=days)).isoformat(),
                                 "partner": partner, "partner_biz_no": "", "statement_no": no,
                                 "cost_center": "C100" if kind == "OUT" else "", "note": "제조 샘플"}, lines, actor=None)
        counts["statements"] += int(r.ok)
    return counts

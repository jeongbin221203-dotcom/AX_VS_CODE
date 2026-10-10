"""추가 데이터 팩 — 데이터 관리 '추가 데이터 목록'에서 골라 넣는 복잡한 샘플.

기본 샘플(core/seed_clean.py)은 수량이 적고 깔끔하다. 여기 팩들은 그 위에 한 번씩 더하는 복잡한 상황이다.
팩마다 자기 플랜트·창고·자재(코드 접두어가 달라 서로 겹치지 않음)를 만들고, 한 번 넣었다는 표시를
app_settings('sample_pack:<키>')에 남겨 두 번 들어가지 않는다. 시연·교육용 DB에만 쓸 것.

  lots         로트·유효기한 복합   자재 8종 × 로트 5개: 기한 지난·임박·넉넉한 로트, 선입선출 출고
  multiplant   다중 플랜트·이동     플랜트 2곳·창고 4곳, 매달 창고 간·플랜트 간 이동, 실사 조정(결재 대기·반려), 취소 거래
  procurement  구매·입고 복합       여러 줄 구매요청, 결재 단계별 상태, 분할 입고·단가 다른 입고·납기 지연 발주
  large        대규모 1년           자재 40여 종·거래 수천 건·인천/평택 + 담당자·월 마감 (core/seed_demo.py)
  mfg          제조 공장            창원 제조공장 20종 12개월 (core/seed_mfg.py, 기본 샘플에 이미 들어 있음)
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from core import audit, db, periods, repository as repo
from core.utils import now_str


@dataclass
class Pack:
    key: str
    title: str
    summary: str
    run: Callable[[], dict]
    exists: Callable[[], bool] | None = None


def _marker(key: str) -> str:
    return f"sample_pack:{key}"


def done(key: str) -> bool:
    pack = next((p for p in PACKS if p.key == key), None)
    if pack and pack.exists and pack.exists():
        return True
    return bool(db.scalar("SELECT COUNT(*) FROM app_settings WHERE key = ?", (_marker(key),)))


def _mark(conn, key: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (_marker(key), date.today().isoformat()))


def _people(conn) -> dict:
    """결재·요청에 쓸 사람: 있는 사용자(담당자·관리자·관리자급)를 쓰고, 없으면 이름만 남는다."""
    def pick(roles):
        row = conn.execute(f"SELECT id, name FROM users WHERE active = 1 AND role IN ({','.join('?' * len(roles))}) ORDER BY id LIMIT 3",
                           roles).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in row]
    clerks, managers, admins = pick(("CLERK",)), pick(("MANAGER",)), pick(("ADMIN",))
    fallback = {"id": None, "name": "샘플 담당자"}
    approver = {"id": None, "name": "샘플 결재자"}
    return {"clerks": clerks or [fallback], "managers": managers or admins or [approver], "admin": (admins or managers or [approver])[0]}


def _ensure_site(conn, plant_code: str, plant_name: str, whs: list[tuple[str, str]]) -> dict[str, int]:
    ts = now_str()
    conn.execute("INSERT INTO plants (code, name, sap_plant, created_at) VALUES (?, ?, '', ?) ON CONFLICT (code) DO NOTHING",
                 (plant_code, plant_name, ts))
    pid = int(conn.execute("SELECT id FROM plants WHERE code = ?", (plant_code,)).fetchone()[0])
    out = {}
    for code, name in whs:
        conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, ?, ?, '', ?) "
                     "ON CONFLICT (code) DO NOTHING", (pid, code, name, ts))
        out[code] = int(conn.execute("SELECT id FROM warehouses WHERE code = ?", (code,)).fetchone()[0])
    return out


def _ensure_materials(conn, rows: list[tuple], lot: int = 0) -> dict[str, int]:
    ts = now_str()
    ids = {}
    for code, name, spec, unit, cat, safety, price, loc, supplier in rows:
        conn.execute(
            "INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, supplier, lot_managed, "
            "expiry_managed, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT (code) DO NOTHING",
            (code, name, spec, unit, cat, safety, price, loc, supplier, lot, lot, ts, ts))
        ids[code] = int(conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()[0])
    return ids


def _tx(conn, mid, tx_type, qty, price, d, wh, who, **extra) -> int:
    data = {"material_id": mid, "tx_type": tx_type, "qty": qty, "unit_price": round(price), "tx_date": d.isoformat(),
            "warehouse_id": wh, "created_by": who["name"], "created_by_id": who["id"],
            "movement_type": extra.pop("movement_type", "101" if tx_type == "IN" else "201" if tx_type == "OUT" else ""),
            "ref_no": extra.pop("ref_no", ""), "partner": extra.pop("partner", ""), "note": extra.pop("note", ""), **extra}
    return repo.insert_transaction(conn, data)


def _round_price(rng, base, spread=0.04):
    step = 1000 if base >= 100000 else 100
    return int(round(base * (1 + rng.uniform(-spread, spread)) / step) * step)


# ── 1. 로트·유효기한 ──────────────────────────────────────────
LOT_ROWS = [
    ("QL-RES-01", "에폭시 수지 A제", "20kg 캔", "CAN", "화학", 6, 168000, "Q-01", "케미칼원"),
    ("QL-HRD-01", "에폭시 경화제 B제", "10kg 캔", "CAN", "화학", 6, 142000, "Q-02", "케미칼원"),
    ("QL-ADH-02", "구조용 접착제", "400ml × 12", "CASE", "화학", 5, 236000, "Q-03", "본드테크"),
    ("QL-PRM-01", "금속 프라이머", "18L", "CAN", "화학", 4, 98000, "Q-04", "본드테크"),
    ("QL-SEA-01", "실란트 카트리지", "310ml × 24", "CASE", "화학", 4, 176000, "Q-05", "본드테크"),
    ("QL-LUB-03", "윤활 그리스", "400g × 24", "CASE", "화학", 4, 124000, "Q-06", "대양윤활"),
    ("QL-SOL-04", "세척 솔벤트", "200L 드럼", "DRUM", "화학", 3, 310000, "Q-07", "케미칼원"),
    ("QL-BIO-05", "시약 키트", "냉장 2~8℃ · 20회분", "BOX", "시약", 3, 480000, "Q-08", "바이오랩"),
]
LOT_PLAN = [(-24, 150, 8, 7), (-3, 110, 8, 5), (12, 80, 10, 4), (45, 45, 10, 3), (180, 12, 12, 1)]   # (기한까지 남은 날, 입고 며칠 전, 입고, 출고)


def _add_lots() -> dict:
    rng = random.Random(31)
    today = date.today()
    n = 0
    with db.transaction() as conn:
        who = _people(conn)["clerks"][0]
        whs = _ensure_site(conn, "P-QL", "품질·로트 센터", [("QL-A", "상온 자재 창고"), ("QL-B", "냉장 창고")])
        ids = _ensure_materials(conn, LOT_ROWS, lot=1)
        for row in LOT_ROWS:
            code, supplier, price = row[0], row[8], row[6]
            wh = whs["QL-B"] if code == "QL-BIO-05" else whs["QL-A"]
            for i, (exp_days, in_ago, in_qty, out_qty) in enumerate(LOT_PLAN, 1):
                lot = f"{code[3:6]}-{(today - timedelta(days=in_ago)):%y%m}-{i}"
                repo.ensure_lot(conn, ids[code], lot, (today + timedelta(days=exp_days + rng.randint(-2, 2))).isoformat())
                p = _round_price(rng, price)
                _tx(conn, ids[code], "IN", in_qty, p, today - timedelta(days=in_ago), wh, who, partner=supplier, lot_no=lot,
                    ref_no=f"GR-{lot}")
                for k in range(2):                                   # 출고 2번 (기한 전에)
                    q = out_qty // 2 + (out_qty % 2 if k == 0 else 0)
                    ago = max(in_ago - 14 - k * 9, 1)
                    if q > 0:
                        _tx(conn, ids[code], "OUT", q, p, today - timedelta(days=ago), wh, who, partner="품질팀", cost_center="C300",
                            lot_no=lot, ref_no=f"MI-{lot}-{k + 1}")
                n += 1
        _mark(conn, "lots")
    return {"materials": len(LOT_ROWS), "lots": n}


# ── 2. 다중 플랜트 · 이동 · 실사 조정 · 취소 ────────────────────
MP_ROWS = [
    ("MP-CNV-01", "컨베이어 모터", "3상 1.5kW", "EA", "정비부품", 4, 520000, "M-01", "한국모터"),
    ("MP-GBX-01", "감속기", "1/30 1.5kW", "EA", "정비부품", 3, 880000, "M-02", "한국모터"),
    ("MP-INV-01", "인버터", "3상 2.2kW", "EA", "전기자재", 3, 640000, "M-03", "한빛전기"),
    ("MP-SNS-01", "광전센서", "확산반사형 M18", "BOX", "전기자재", 4, 190000, "M-04", "오토센서"),
    ("MP-CYL-01", "공압 실린더", "Ø63 × 200", "EA", "유압/공압", 4, 270000, "M-05", "대성공압"),
    ("MP-VLV-01", "솔레노이드 밸브", "5/2way 24V · 5개입", "BOX", "유압/공압", 4, 340000, "M-06", "대성공압"),
    ("MP-BLT-01", "컨베이어 벨트", "PVC 800W · 10m", "ROLL", "정비부품", 3, 450000, "M-07", "동일벨트"),
    ("MP-LGT-01", "LED 투광등", "200W", "EA", "전기자재", 5, 150000, "M-08", "밝은조명"),
    ("MP-PLC-01", "PLC 입출력 모듈", "DI/DO 32점", "EA", "전기자재", 2, 760000, "M-09", "한빛전기"),
    ("MP-HOS-01", "유압호스 세트", "1/2\" × 2m · 10개입", "BOX", "유압/공압", 3, 230000, "M-10", "한국유압"),
]


def _add_multiplant() -> dict:
    rng = random.Random(43)
    today = date.today()
    cnt = {"transactions": 0, "transfers": 0}
    with db.transaction() as conn:
        ppl = _people(conn)
        clerk, mgr = ppl["clerks"][0], ppl["managers"][0]
        a = _ensure_site(conn, "P-SE", "서울 허브센터", [("SE-A", "서울 허브 1창고"), ("SE-B", "서울 허브 2창고")])
        b = _ensure_site(conn, "P-DG", "대구 지역센터", [("DG-A", "대구 1창고"), ("DG-B", "대구 2창고")])
        whs = {**a, **b}
        ids = _ensure_materials(conn, MP_ROWS)
        first = (today.replace(day=1) - timedelta(days=150)).replace(day=1)
        stock = {}

        def add(code, tx_type, qty, d, wh, **extra):
            p = _round_price(rng, dict((r[0], r[6]) for r in MP_ROWS)[code])
            _tx(conn, ids[code], tx_type, qty, p, d, whs[wh], clerk, **extra)
            stock[(code, wh)] = stock.get((code, wh), 0) + (qty if tx_type == "IN" else -qty if tx_type == "OUT" else qty)
            cnt["transactions"] += 1

        for code, *_r in MP_ROWS:
            supplier = dict((r[0], r[8]) for r in MP_ROWS)[code]
            add(code, "IN", rng.randint(10, 16), first + timedelta(days=1), "SE-A", partner=supplier, ref_no=f"GR-{code}-0")
            add(code, "IN", rng.randint(4, 8), first + timedelta(days=2), "DG-A", partner=supplier, ref_no=f"GR-{code}-1")
        months = []
        d = first
        while d <= today:
            months.append(d)
            d = (d + timedelta(days=32)).replace(day=1)
        trf = 0
        for ms in months:
            for code, *_r in MP_ROWS:
                for wh in ("SE-A", "SE-B", "DG-A", "DG-B"):
                    day = ms + timedelta(days=rng.randint(5, 25))
                    q = rng.randint(1, 3)
                    if day < today - timedelta(days=1) and stock.get((code, wh), 0) >= q + 1 and rng.random() < 0.45:
                        add(code, "OUT", q, day, wh, partner="설비보전팀", cost_center="M100", ref_no=f"MI-{day:%y%m%d}-{trf}")
            # 이동: 같은 플랜트 안(311) · 플랜트 사이(301) — 출고·입고 두 행, 같은 이동번호
            for code, src, dst, mv in (("MP-CNV-01", "SE-A", "SE-B", "311"), ("MP-SNS-01", "SE-A", "DG-A", "301"),
                                       ("MP-CYL-01", "DG-A", "DG-B", "311"), ("MP-LGT-01", "SE-A", "DG-B", "301")):
                day = ms + timedelta(days=rng.randint(2, 4))
                q = rng.randint(1, 2)
                if day < today - timedelta(days=1) and stock.get((code, src), 0) >= q + 1:
                    trf += 1
                    no = f"TRF-{day:%y%m}-{trf:03d}"
                    price = _round_price(rng, dict((r[0], r[6]) for r in MP_ROWS)[code], 0)
                    _tx(conn, ids[code], "OUT", q, price, day, whs[src], clerk, partner=f"→ {dst}", transfer_no=no, movement_type=mv, ref_no=no)
                    _tx(conn, ids[code], "IN", q, price, day, whs[dst], clerk, partner=f"← {src}", transfer_no=no, movement_type=mv, ref_no=no)
                    stock[(code, src)] -= q
                    stock[(code, dst)] = stock.get((code, dst), 0) + q
                    cnt["transactions"] += 2
                    cnt["transfers"] += 1
        # 잘못 등록한 출고 → 다음 날 취소 거래 → 바른 수량 재등록
        code = "MP-INV-01"
        wrong_day = today - timedelta(days=9)
        price = _round_price(rng, 640000, 0)
        wid = _tx(conn, ids[code], "OUT", 3, price, wrong_day, whs["SE-A"], clerk, partner="설비보전팀", cost_center="M100", ref_no="MI-WRONG-01")
        _tx(conn, ids[code], "OUT", -3, price, wrong_day + timedelta(days=1), whs["SE-A"], mgr, partner="설비보전팀", cost_center="M100",
            ref_no="MI-WRONG-01", reversal_of=wid, movement_type="202", note="취소: 수량 오입력")
        _tx(conn, ids[code], "OUT", 1, price, wrong_day + timedelta(days=1), whs["SE-A"], clerk, partner="설비보전팀", cost_center="M100",
            ref_no="MI-RIGHT-01", note="MI-WRONG-01 수량 정정 후 재등록")
        # 실사 조정: 승인된 큰 조정 · 결재 대기 · 반려
        for code, wh, qty, ago, status, comment in (("MP-GBX-01", "SE-A", -2, 40, "APPROVED", "실사표 확인"),
                                                    ("MP-PLC-01", "DG-A", -1, 2, "PENDING", ""),
                                                    ("MP-HOS-01", "SE-B", -3, 1, "PENDING", ""),
                                                    ("MP-LGT-01", "DG-B", -2, 25, "REJECTED", "재실사 후 다시 올릴 것")):
            dd = today - timedelta(days=ago)
            price = dict((r[0], r[6]) for r in MP_ROWS)[code]
            result = None
            if status == "APPROVED":
                result = _tx(conn, ids[code], "ADJ", qty, price, dd, whs[wh], clerk, ref_no=f"ADJ-{code}", approved_by=mgr["name"],
                             movement_type="702", note="분기 실사 차이 (결재 승인)")
                cnt["transactions"] += 1
            decided = (mgr["id"], mgr["name"], f"{dd} 15:00:00") if status != "PENDING" else (None, "", "")
            conn.execute(
                "INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status, "
                "requested_by_id, requested_by, requested_at, decided_by_id, decided_by, decided_at, comment, result_tx_id) "
                "VALUES ('ADJ', ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ids[code], whs[wh], dd.isoformat(), qty, abs(qty) * price, status, clerk["id"], clerk["name"], f"{dd} 10:00:00",
                 *decided, comment, result))
        _mark(conn, "multiplant")
    return {"materials": len(MP_ROWS), "warehouses": 4, **cnt}


# ── 3. 구매 · 입고 복합 ───────────────────────────────────────
PC_ROWS = [
    ("PC-STL-01", "H빔", "H-200×100 · 6m", "EA", "원자재", 20, 310000, "S-01", "동국철강"),
    ("PC-PLT-01", "후판", "SS275 12t · 4×8", "EA", "원자재", 10, 1180000, "S-02", "동국철강"),
    ("PC-WLD-01", "용접봉", "E7018 5kg × 4", "BOX", "소모품", 12, 120000, "S-03", "고려용접"),
    ("PC-PNT-01", "에폭시 도료", "20L", "CAN", "화학", 10, 210000, "S-04", "케미칼원"),
    ("PC-BLT-01", "고장력 볼트 세트", "M20 × 80 · 100개입", "BOX", "부품", 15, 160000, "S-05", "동아체결"),
    ("PC-ELC-01", "분전반", "STS 600×800", "EA", "전기자재", 4, 940000, "S-06", "한빛전기"),
]


def _add_procurement() -> dict:
    rng = random.Random(59)
    today = date.today()
    cnt = {"purchase_requests": 0, "purchase_orders": 0, "receipts": 0}
    with db.transaction() as conn:
        ppl = _people(conn)
        clerks, managers, admin = ppl["clerks"], ppl["managers"], ppl["admin"]
        whs = _ensure_site(conn, "P-PC", "구매 시나리오 센터", [("PC-A", "구매 입고 창고")])
        ids = _ensure_materials(conn, PC_ROWS)
        info = {r[0]: r for r in PC_ROWS}
        wh = whs["PC-A"]
        for code in info:                                                 # 처음 재고
            _tx(conn, ids[code], "IN", rng.randint(8, 20), info[code][6], today - timedelta(days=170), wh, clerks[0],
                partner=info[code][8], ref_no=f"GR-{code}-0", note="기초 입고")
        no = {"n": 0}

        def pr(lines, ago, status, done_steps, reason, reject="", po=None, recv=(), price_gap=0.0, delivery_in=None):
            """lines: [(코드, 수량)] 여러 줄. po: None|OPEN|PARTIAL|CLOSED. recv: 입고 비율 목록(분할 입고)."""
            d0 = today - timedelta(days=ago)
            amount = sum(q * info[c][6] for c, q in lines)
            steps = 1 if amount <= 1_000_000 else 2 if amount <= 10_000_000 else 3
            no["n"] += 1
            who = clerks[no["n"] % len(clerks)]
            pr_no = f"PR-{d0:%Y%m}-P{no['n']:03d}"
            pr_id = conn.execute(
                "INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, reason, status, total_amount, required_steps, "
                "requested_by_id, requested_by, requested_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pr_no, wh, (d0 + timedelta(days=7)).isoformat(), reason, status, amount, steps, who["id"], who["name"],
                 f"{d0} 09:30:00", f"{d0} 09:30:00")).lastrowid
            for i, (c, q) in enumerate(lines, 1):
                conn.execute("INSERT INTO pr_items (pr_id, line_no, material_id, qty, est_price) VALUES (?, ?, ?, ?, ?)",
                             (pr_id, i * 10, ids[c], q, info[c][6]))
            n = steps if done_steps is None else done_steps
            for s in range(1, n + 1):
                a = admin if s >= 3 else managers[(s - 1) % len(managers)]
                conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                             "VALUES (?, ?, ?, ?, 'APPROVE', '', ?)", (pr_id, s, a["id"], a["name"], f"{d0 + timedelta(days=min(s, 2))} 14:00:00"))
            if reject:
                a = admin if n + 1 >= 3 else managers[n % len(managers)]
                conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                             "VALUES (?, ?, ?, ?, 'REJECT', ?, ?)", (pr_id, n + 1, a["id"], a["name"], reject, f"{d0 + timedelta(days=1)} 14:00:00"))
            cnt["purchase_requests"] += 1
            if not po:
                return
            supplier = info[lines[0][0]][8]
            po_no = f"PO-{d0:%Y%m}-P{no['n']:03d}"
            delivery = (today + timedelta(days=delivery_in)) if delivery_in is not None else d0 + timedelta(days=7)
            po_id = conn.execute(
                "INSERT INTO purchase_orders (po_no, pr_id, supplier, warehouse_id, status, total_amount, created_by_id, created_by, "
                "created_at, approved_by, approved_at, delivery_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)",
                (po_no, pr_id, supplier, wh, po, amount, managers[0]["id"], managers[0]["name"], f"{d0 + timedelta(days=1)} 10:00:00",
                 delivery.isoformat())).lastrowid
            for i, (c, q) in enumerate(lines, 1):
                conn.execute("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, ?, ?, ?, ?)",
                             (po_id, i * 10, ids[c], q, info[c][6]))
            cnt["purchase_orders"] += 1
            for k, ratio in enumerate(recv):
                when = d0 + timedelta(days=5 + k * 6)
                if when >= today:
                    break
                for i, (c, q) in enumerate(lines, 1):
                    got = max(1, round(q * ratio))
                    p = round(info[c][6] * (1 + price_gap))
                    _tx(conn, ids[c], "IN", got, p, when, wh, clerks[0], partner=supplier, po_no=po_no, po_item=str(i * 10),
                        ref_no=po_no, note="분할 입고" if len(recv) > 1 else "발주 입고")
                    cnt["receipts"] += 1

        # 결재 단계별
        pr([("PC-STL-01", 40), ("PC-PLT-01", 12)], 1, "PENDING", 0, "신규 구조물 제작 자재")
        pr([("PC-ELC-01", 8)], 2, "PENDING", 1, "라인 증설 분전반")
        pr([("PC-PNT-01", 30), ("PC-WLD-01", 40)], 3, "PENDING", 0, "정기 보충")
        pr([("PC-BLT-01", 50)], 5, "APPROVED", None, "정기 보충")
        pr([("PC-PLT-01", 20)], 33, "REJECTED", 1, "소진 예상", reject="예산 초과 — 수량 줄여서 재요청")
        pr([("PC-ELC-01", 4)], 48, "REJECTED", 0, "설비 교체", reject="단가 재견적 후 다시 올릴 것")
        pr([("PC-WLD-01", 24)], 70, "CANCELLED", 0, "안전재고 미달 보충")
        # 발주 상태별 · 입고 방식별
        pr([("PC-STL-01", 60), ("PC-WLD-01", 30), ("PC-BLT-01", 40)], 52, "ORDERED", None, "철골 구조물 3동", po="CLOSED", recv=(0.5, 0.5))
        pr([("PC-PLT-01", 16)], 40, "ORDERED", None, "후판 정기 입고", po="CLOSED", recv=(1.0,), price_gap=0.04)    # 단가 4% 높게 입고
        pr([("PC-PNT-01", 50), ("PC-BLT-01", 60)], 18, "ORDERED", None, "정비 일정 자재", po="PARTIAL", recv=(0.4, 0.3))
        pr([("PC-ELC-01", 6)], 14, "ORDERED", None, "분전반 교체", po="PARTIAL", recv=(0.5,), delivery_in=-3)         # 납기 지났는데 남음
        pr([("PC-STL-01", 30)], 6, "ORDERED", None, "정기 보충", po="OPEN", delivery_in=9)
        pr([("PC-WLD-01", 36), ("PC-PNT-01", 20)], 4, "ORDERED", None, "용접 작업 대비", po="OPEN", delivery_in=-2)    # 납기 지남
        _mark(conn, "procurement")
    return {"materials": len(PC_ROWS), **cnt}


# ── 4. 대규모 1년 · 5. 제조 공장 (기존 생성기) ─────────────────
def _add_large() -> dict:
    """seed_demo.seed_large — 거래 수천 건. 이미 마감한 달이 있으면 푼 뒤(스냅샷 삭제) 다시 마감한다."""
    from core import seed_demo
    reopened = 0
    while periods.reopen("대규모 샘플 추가를 위한 마감 해제", audit.SYSTEM).ok:
        reopened += 1
        if reopened > 24:
            break
    counts = seed_demo.seed_large()
    with db.transaction() as conn:
        _mark(conn, "large")
    return counts


def _add_mfg() -> dict:
    from core import production, seed_mfg
    counts = seed_mfg.seed_manufacturing()
    if not counts.get("skipped"):
        counts["bom"] = production.seed_sample()
    return counts


PACKS = [
    Pack("lots", "로트·유효기한 복합", "화학·시약 자재 8종 × 로트 5개씩(기한 지난 로트 2 · 임박 1 · 넉넉 2), 선입선출 출고, 냉장 창고", _add_lots),
    Pack("multiplant", "다중 플랜트·창고 이동", "플랜트 2곳·창고 4곳, 자재 10종, 6개월간 창고 간(311)·플랜트 간(301) 이동, "
         "취소 거래·정정 재등록, 실사 조정(승인·결재 대기·반려)", _add_multiplant),
    Pack("procurement", "구매·입고 복합", "여러 줄 구매요청 13건: 결재 중·승인·반려·취소, 발주 완료/부분 입고/미입고, "
         "분할 입고·단가가 다른 입고·납기 지난 발주", _add_procurement),
    Pack("large", "대규모 1년 (인천·평택)", "자재 44종, 지난 12개월 날마다의 입출고(거래 수천 건), 계절 수요·재주문 발주, 담당자 8명·월 마감. "
         "수십 초 걸리고 화면이 무거워집니다", _add_large),
    Pack("mfg", "제조 공장 (창원)", "원자재·부품·화학소모품 20종, 12개월 구매 입고·생산 출고·이동·실사 감모, 거래명세서 3장, BOM·생산 투입",
         _add_mfg, exists=lambda: bool(db.scalar("SELECT COUNT(*) FROM plants WHERE code = 'P-CW'"))),
]


def add(key: str, actor: dict | None = None) -> tuple[bool, str]:
    """팩 하나를 넣는다. 이미 넣었으면 건너뛴다."""
    pack = next((p for p in PACKS if p.key == key), None)
    if pack is None:
        return False, "알 수 없는 데이터 팩입니다."
    if done(key):
        return False, f"'{pack.title}'은(는) 이미 추가되어 있습니다."
    with audit.quiet():
        counts = pack.run()
    if counts.get("skipped"):
        return False, f"'{pack.title}'을(를) 추가하지 못했습니다(필요한 사용자가 없거나 이미 있음)."
    audit.log(actor, "SEED", "material", f"pack:{key}", counts)
    detail = " · ".join(f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}" for k, v in counts.items())
    return True, f"'{pack.title}'을(를) 추가했습니다 — {detail}"


# ── 시연 서버: 오래 걸리는 팩은 요청을 붙잡지 않고 백그라운드에서 ─────────────
_running: set[str] = set()
HEAVY = {"large", "mfg"}


def running(key: str | None = None):
    return (key in _running) if key else bool(_running)


def add_background(key: str, actor: dict | None = None) -> tuple[bool, str]:
    """HEAVY 팩은 스레드에서 넣고 바로 돌려준다(무료 서버의 요청 제한 시간 안에 끝나지 않을 수 있다). 한 번에 하나만."""
    import threading
    pack = next((p for p in PACKS if p.key == key), None)
    if pack is None:
        return False, "알 수 없는 데이터 팩입니다."
    if key not in HEAVY:
        return add(key, actor)
    if done(key):
        return False, f"'{pack.title}'은(는) 이미 추가되어 있습니다."
    if _running:
        return False, "다른 데이터 팩을 추가하는 중입니다. 끝난 뒤 다시 시도하세요."
    _running.add(key)

    def work():
        try:
            add(key, actor)
        except Exception:                    # 실패해도 서버는 계속 — 다시 눌러 볼 수 있다
            import logging
            logging.getLogger("app").exception("데이터 팩 추가 실패: %s", key)
        finally:
            _running.discard(key)

    threading.Thread(target=work, daemon=True, name=f"pack-{key}").start()
    return True, f"'{pack.title}'을(를) 백그라운드에서 추가합니다. 1~2분 뒤 새로 고치면 '추가됨'으로 바뀝니다."

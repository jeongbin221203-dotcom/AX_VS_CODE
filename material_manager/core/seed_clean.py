"""시연 기본 샘플 — 수량은 적고 금액은 큰 깔끔한 데이터 (core/demo.py 가 빈 DB에서 한 번만 부른다).

부산 물류센터(포장·고박 자재, 창고 2곳)와 창원 제조공장(원자재·부품·화학·완제품 창고 4곳), 자재 약 20종.
재고는 자재마다 한 자리~두 자리 수량이고 단가가 커서(수만~수백만 원) 금액이 크게 보인다.
지난 6개월 거래는 달마다 입고 1번·출고 2번으로 단순하고, 화면마다 보여 줄 상황을 하나씩만 넣는다:
  안전재고 미달 · 유효기한 임박/경과 로트 · 장기 미사용 · 창고 간 이동 · 구매요청(결재 중·승인·반려·취소)
  · 발주(발주·부분 입고·입고 완료) · 결재 대기 실사 조정 · 월 마감 · BOM/작업지시/MRP(core/demo.py)
더 복잡한 데이터는 데이터 관리의 '추가 데이터 목록'(core/seed_packs.py)에서 골라 넣는다.
난수 씨앗이 고정이라 언제 만들어도 같은 모양이다.
"""
from __future__ import annotations

import random
import secrets
from datetime import date, timedelta

from core import audit, auth, db, periods, repository as repo
from core.utils import now_str

USERS = [  # (아이디, 이름, 역할, 담당 지역)
    ("kim.mj", "김민준", "CLERK", "BS"), ("lee.sy", "이서연", "CLERK", "CW"), ("han.jw", "한지우", "CLERK", "BS"),
    ("park.jh", "박지훈", "MANAGER", ""), ("choi.yj", "최유진", "MANAGER", ""), ("jung.hn", "정하늘", "ADMIN", ""),
    ("kang.dy", "강도윤", "VIEWER", ""), ("yoon.jw", "윤지원", "DATA", ""),
]
PLANTS = [("P-CW", "창원 제조공장", [("CW-RM", "원자재 창고"), ("CW-PT", "부품 창고"), ("CW-CH", "화학 창고"), ("CW-FG", "완제품·반제품 창고")])]
COST_CENTERS = {"BS": [("L100", "입출고팀", 0.55), ("L200", "수출포장팀", 0.45)],
                "CW": [("C100", "생산1팀", 0.6), ("C200", "생산2팀", 0.4)]}
EXTRA_CENTERS = [("M100", "설비보전팀")]

# (코드, 이름, 규격, 단위, 분류, 안전재고, 단가, 위치, 공급처, 창고, 처음 재고, 월 입고, 월 출고)
MATERIALS = [
    ("PKG-001", "수출용 목재 팔레트", "1100×1100 훈증 · 20매 묶음", "BUNDLE", "포장재", 6, 380000, "A-01", "대한팔레트", "WH1", 14, 6, 6),
    ("PKG-002", "스트레치 필름", "500mm×300m · 6롤 박스", "BOX", "포장재", 5, 148000, "A-02", "한국필름", "WH1", 12, 5, 5),
    ("PKG-003", "5겹 골판지 박스", "600×400×400 · 100매 묶음", "BUNDLE", "포장재", 8, 185000, "A-03", "동양포장", "WH1", 20, 8, 8),
    ("LSH-001", "라싱 벨트", "50mm 5T 9m · 20개입", "BOX", "고박자재", 4, 420000, "B-01", "세이프라싱", "WH1", 10, 4, 4),
    ("LSH-002", "에어백(던니지백)", "900×1800 · 50개입", "BOX", "고박자재", 3, 650000, "B-02", "세이프라싱", "WH1", 8, 3, 3),
    ("SFT-001", "안전장갑", "코팅 L · 12켤레", "BOX", "안전용품", 4, 96000, "C-02", "안전나라", "WH1", 9, 3, 3),
    ("RM-STL-002", "아연도금 강판", "SGCC 0.8t", "TON", "원자재", 3, 1380000, "R-02", "포스코스틸", "CW-RM", 9, 4, 4),
    ("RM-AL-001", "알루미늄 판재", "A5052 2.0t", "TON", "원자재", 2, 4200000, "R-03", "한국알루미늄", "CW-RM", 5, 1, 1),
    ("PT-MTR-001", "BLDC 모터", "24V 60W", "EA", "부품", 10, 380000, "P-05", "성진모터", "CW-PT", 30, 12, 12),
    ("PT-PCB-001", "제어 PCB", "MCU 보드 Rev.C", "EA", "부품", 10, 210000, "P-06", "넥스트전자", "CW-PT", 30, 12, 12),
    ("PT-HRN-001", "와이어 하네스", "6P 300mm 어셈블리", "EA", "부품", 12, 86000, "P-07", "넥스트전자", "CW-PT", 32, 12, 12),
    ("PT-BRG-001", "깊은홈 볼베어링", "6204ZZ", "EA", "부품", 20, 38000, "P-03", "한일베어링", "CW-PT", 36, 24, 24),
    ("PT-FRM-001", "송풍기 프레임", "SGCC 가공품", "EA", "부품", 8, 95000, "P-08", "동아정밀", "CW-PT", 24, 10, 10),
]
# 장기 미사용: (자재, 입고 수량, 며칠 전 입고, 며칠 전 마지막 출고, 출고 수량)
IDLE = [
    (("PKG-009", "방습제(실리카겔)", "1kg 파우치 · 50개입", "BOX", "포장재", 3, 90000, "A-09", "드라이팩", "WH1"), 8, 200, 150, 3),
    (("LSH-009", "와이어 로프 슬링", "12mm 3m", "EA", "고박자재", 2, 320000, "B-09", "세이프라싱", "WH1"), 6, 240, None, 0),
]
# 로트·유효기한 자재: (자재, [(로트, 기한까지 남은 날(음수=지남), 며칠 전 입고, 입고 수량, 출고 수량)])
LOT_MATERIALS = [
    (("CHM-901", "방청제(VCI 스프레이)", "420ml · 12개입", "BOX", "포장재", 4, 280000, "C-09", "케미칼원", "WH1"),
     [("VCI-2504", -10, 160, 8, 6), ("VCI-2508", 20, 70, 10, 4), ("VCI-2511", 300, 20, 12, 2)]),
    (("CH-PNT-001", "분체도료", "에폭시 회색 RAL7035 · 20kg", "CAN", "화학", 4, 196000, "C-01", "케미칼원", "CW-CH"),
     [("PNT-2504", -12, 230, 6, 4), ("PNT-2509", 18, 150, 8, 3), ("PNT-2512", 260, 30, 10, 2)]),
]
DIP = {"LSH-002": 2, "PT-HRN-001": 9, "RM-AL-001": 1}      # 이 자재는 안전재고 아래까지 내려가 있다 (남길 수량)


def _price(rng: random.Random, base: int) -> int:
    step = 1000 if base >= 100000 else 100
    return int(round(base * (1 + rng.uniform(-0.03, 0.04)) / step) * step)


def seed_clean() -> dict:
    rng = random.Random(5)
    today = date.today()
    ts = now_str()
    counts = {"materials": 0, "transactions": 0, "purchase_requests": 0, "purchase_orders": 0}

    users = {}
    for username, name, role, area in USERS:
        r = auth.create_user(username, name, role, "Demo" + secrets.token_hex(8) + "1", audit.SYSTEM,
                             must_change_pw=False)
        if r.ok:
            users[username] = {"id": r.user["id"], "name": name, "role": role, "area": area}
    clerks = {a: [u for u in users.values() if u["role"] == "CLERK" and u["area"] == a] for a in ("BS", "CW")}
    managers = [u for u in users.values() if u["role"] == "MANAGER"]
    admin = next((u for u in users.values() if u["role"] == "ADMIN"), None)
    if not clerks["BS"] or not clerks["CW"] or len(managers) < 2 or admin is None:
        return {"skipped": True}

    def stamp(d: date) -> str:
        return f"{d.isoformat()} {rng.randint(9, 17):02d}:{rng.randint(0, 59):02d}:00"

    with db.transaction() as conn:
        conn.execute("UPDATE plants SET name = '부산 물류센터' WHERE code = 'P1' AND name = '기본 공장'")
        conn.execute("UPDATE warehouses SET name = '부산 본 창고' WHERE code = 'WH1' AND name = '기본 창고'")
        whs = {"WH1": int(conn.execute("SELECT id FROM warehouses WHERE code = 'WH1'").fetchone()[0])}
        p1 = int(conn.execute("SELECT plant_id FROM warehouses WHERE code = 'WH1'").fetchone()[0])
        conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, 'WH2', '부산 신항 창고', '', ?) "
                     "ON CONFLICT (code) DO NOTHING", (p1, ts))
        whs["WH2"] = int(conn.execute("SELECT id FROM warehouses WHERE code = 'WH2'").fetchone()[0])
        for pcode, pname, wlist in PLANTS:
            conn.execute("INSERT INTO plants (code, name, sap_plant, created_at) VALUES (?, ?, '', ?) ON CONFLICT (code) DO NOTHING",
                         (pcode, pname, ts))
            pid = int(conn.execute("SELECT id FROM plants WHERE code = ?", (pcode,)).fetchone()[0])
            for wcode, wname in wlist:
                conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, ?, ?, '', ?) "
                             "ON CONFLICT (code) DO NOTHING", (pid, wcode, wname, ts))
                whs[wcode] = int(conn.execute("SELECT id FROM warehouses WHERE code = ?", (wcode,)).fetchone()[0])
        for area in COST_CENTERS.values():
            for code, name, _w in area:
                conn.execute("INSERT INTO cost_centers (code, name, active, synced_at) VALUES (?, ?, 1, '') ON CONFLICT (code) DO NOTHING",
                             (code, name))
        for code, name in EXTRA_CENTERS:
            conn.execute("INSERT INTO cost_centers (code, name, active, synced_at) VALUES (?, ?, 1, '') ON CONFLICT (code) DO NOTHING",
                         (code, name))

        mats: dict[str, int] = {}
        price_of: dict[str, int] = {}
        wh_of: dict[str, str] = {}

        def material(m, lot=0):
            code, name, spec, unit, cat, safety, price, loc, supplier, wh = m[:10]
            conn.execute(
                "INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, supplier, "
                "lot_managed, expiry_managed, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT (code) DO NOTHING", (code, name, spec, unit, cat, safety, price, loc, supplier, lot, lot, ts, ts))
            mats[code] = int(conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()[0])
            price_of[code], wh_of[code] = price, wh
            counts["materials"] += 1

        for m in MATERIALS:
            material(m)
        for m, *_ in IDLE:
            material(m)
        for m, _lots in LOT_MATERIALS:
            material(m, 1)

        seq = {"n": 0}

        def ref(prefix: str, d: date) -> str:
            seq["n"] += 1
            return f"{prefix}-{d:%y%m%d}-{seq['n']:03d}"

        def tx(code, tx_type, qty, price, d, who, **extra):
            repo.insert_transaction(conn, {
                "material_id": mats[code], "tx_type": tx_type, "qty": qty, "unit_price": price, "tx_date": d.isoformat(),
                "warehouse_id": whs[extra.pop("wh", wh_of[code])], "created_by": who["name"], "created_by_id": who["id"],
                "ref_no": extra.pop("ref_no", ""), "partner": extra.pop("partner", ""), "note": extra.pop("note", ""),
                "movement_type": extra.pop("movement_type", "101" if tx_type == "IN" else "201" if tx_type == "OUT" else ""),
                **extra})
            counts["transactions"] += 1

        def area_of(code):
            return "CW" if wh_of[code].startswith("CW") else "BS"

        # ── 지난 6개월: 처음 재고 → 달마다 입고 1번 · 출고 2번 ──
        first = (today.replace(day=1) - timedelta(days=150)).replace(day=1)
        months = []
        d = first
        while d <= today:
            months.append(d)
            d = (d + timedelta(days=32)).replace(day=1)
        stock: dict[str, int] = {}
        for m in MATERIALS:
            code, supplier, open_qty, m_in, m_out = m[0], m[8], m[10], m[11], m[12]
            who = clerks[area_of(code)][0]
            tx(code, "IN", open_qty, price_of[code], first + timedelta(days=1), who, partner=supplier, ref_no=ref("GR", first),
               note="기초 입고")
            have = open_qty
            for ms in months:
                p = _price(rng, price_of[code])
                for day, kind in ((3, "IN"), (11, "OUT"), (24, "OUT")):
                    when = ms + timedelta(days=day - 1)
                    if when >= today - timedelta(days=1):
                        continue
                    if kind == "IN":
                        q = max(1, m_in + rng.choice((-1, 0, 0, 0, 1)))
                        tx(code, "IN", q, p, when, rng.choice(clerks[area_of(code)]), partner=supplier, ref_no=ref("GR", when))
                        have += q
                    else:
                        q = min(have, max(1, round(m_out / 2) + rng.choice((-1, 0, 0, 1))))
                        if q <= 0:
                            continue
                        cc, cc_name, _w = rng.choices(COST_CENTERS[area_of(code)],
                                                      [c[2] for c in COST_CENTERS[area_of(code)]])[0]
                        tx(code, "OUT", q, p, when, rng.choice(clerks[area_of(code)]), partner=cc_name, cost_center=cc,
                           ref_no=ref("MI", when))
                        have -= q
            stock[code] = have
        # 안전재고 아래로 내려간 자재 (최근 출고)
        for code, keep in DIP.items():
            extra = stock[code] - keep
            if extra > 0:
                cc, cc_name, _w = COST_CENTERS[area_of(code)][0]
                tx(code, "OUT", extra, price_of[code], today - timedelta(days=2), clerks[area_of(code)][0], partner=cc_name,
                   cost_center=cc, ref_no=ref("MI", today))
                stock[code] = keep

        # 로트·유효기한 자재
        for m, lots in LOT_MATERIALS:
            code = m[0]
            for lot, exp_days, in_ago, in_qty, out_qty in lots:
                repo.ensure_lot(conn, mats[code], lot, (today + timedelta(days=exp_days)).isoformat())
                tx(code, "IN", in_qty, price_of[code], today - timedelta(days=in_ago), clerks[area_of(code)][0], partner=m[8],
                   lot_no=lot, ref_no=ref("GR", today))
                cc, cc_name, _w = COST_CENTERS[area_of(code)][0]
                tx(code, "OUT", out_qty, price_of[code], today - timedelta(days=max(in_ago - 15, 1)), clerks[area_of(code)][0],
                   partner=cc_name, cost_center=cc, lot_no=lot, ref_no=ref("MI", today))
        # 장기 미사용
        for m, in_qty, in_ago, out_ago, out_qty in IDLE:
            code = m[0]
            tx(code, "IN", in_qty, price_of[code], today - timedelta(days=in_ago), clerks["BS"][0], partner=m[8], ref_no=ref("GR", today))
            if out_ago:
                tx(code, "OUT", out_qty, price_of[code], today - timedelta(days=out_ago), clerks["BS"][0], partner="입출고팀",
                   cost_center="L100", ref_no=ref("MI", today))
        # 분기 실사 차이 (작게)
        for code, ago in (("PKG-003", 40), ("PT-BRG-001", 38), ("SFT-001", 35)):
            tx(code, "ADJ", -1, price_of[code], today - timedelta(days=ago), clerks[area_of(code)][0], ref_no=ref("ADJ", today),
               movement_type="702", note="분기 실사 차이")
        # 창고 간 이동 (출고·입고 두 행, 같은 이동번호)
        for n, (code, qty, ago) in enumerate((("PKG-003", 4, 5), ("SFT-001", 2, 12)), 1):
            no = f"TRF-{today:%y%m}-{n:03d}"
            who = clerks["BS"][0]
            for tx_type, wh, partner in (("OUT", "WH1", "→ WH2"), ("IN", "WH2", "← WH1")):
                tx(code, tx_type, qty, price_of[code], today - timedelta(days=ago), who, wh=wh, partner=partner, transfer_no=no,
                   movement_type="311", ref_no=no)

        # ── 구매요청 · 발주 ──
        pr_n = {"n": 0}

        def make_pr(code, qty, ago, status, done, reason, reject="", po=None, received=0):
            """po: None | 'OPEN' | 'PARTIAL' | 'CLOSED'. received: 입고 수량(발주에 연결)."""
            d0 = today - timedelta(days=ago)
            price = price_of[code]
            amount = qty * price
            steps = 1 if amount <= 1_000_000 else 2 if amount <= 10_000_000 else 3
            pr_n["n"] += 1
            pr_no = f"PR-{d0:%Y%m}-C{pr_n['n']:03d}"
            who = clerks[area_of(code)][-1]
            wh = whs[wh_of[code]]
            pr_id = conn.execute(
                "INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, reason, status, total_amount, required_steps, "
                "requested_by_id, requested_by, requested_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pr_no, wh, (d0 + timedelta(days=7)).isoformat(), reason, status, amount, steps, who["id"], who["name"],
                 stamp(d0), stamp(d0))).lastrowid
            conn.execute("INSERT INTO pr_items (pr_id, line_no, material_id, qty, est_price) VALUES (?, 10, ?, ?, ?)",
                         (pr_id, mats[code], qty, price))
            n = steps if done is None else done
            for step in range(1, n + 1):
                a = admin if step >= 3 else managers[(step - 1) % len(managers)]
                conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                             "VALUES (?, ?, ?, ?, 'APPROVE', '', ?)", (pr_id, step, a["id"], a["name"], stamp(d0 + timedelta(days=min(step, 2)))))
            if reject:
                a = admin if n + 1 >= 3 else managers[n % len(managers)]
                conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                             "VALUES (?, ?, ?, ?, 'REJECT', ?, ?)", (pr_id, n + 1, a["id"], a["name"], reject, stamp(d0 + timedelta(days=1))))
            counts["purchase_requests"] += 1
            if po:
                buyer = managers[1]
                po_no = f"PO-{d0:%Y%m}-C{pr_n['n']:03d}"
                delivery = (d0 + timedelta(days=7 if po != "OPEN" else 21)).isoformat()
                po_id = conn.execute(
                    "INSERT INTO purchase_orders (po_no, pr_id, supplier, warehouse_id, status, total_amount, created_by_id, created_by, "
                    "created_at, approved_by, approved_at, delivery_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)",
                    (po_no, pr_id, MATERIALS_BY[code][8], wh, po, amount, buyer["id"], buyer["name"], stamp(d0), delivery)).lastrowid
                conn.execute("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, 10, ?, ?, ?)",
                             (po_id, mats[code], qty, price))
                counts["purchase_orders"] += 1
                if received:
                    tx(code, "IN", received, price, d0 + timedelta(days=6), who, partner=MATERIALS_BY[code][8], po_no=po_no,
                       po_item="10", ref_no=po_no, note="분할 입고" if po == "PARTIAL" else "발주 입고")

        MATERIALS_BY = {m[0]: m for m in MATERIALS}
        make_pr("PKG-002", 20, 1, "PENDING", 0, "성수기 출하 대비")
        make_pr("PT-MTR-001", 40, 2, "PENDING", 1, "라인 증설 모터 확보")
        make_pr("RM-STL-002", 6, 4, "APPROVED", None, "정기 보충")
        make_pr("RM-AL-001", 3, 30, "REJECTED", 1, "소진 예상", reject="재고 충분 — 다음 달 재검토")
        make_pr("SFT-001", 10, 60, "CANCELLED", 0, "안전재고 미달 보충")
        make_pr("PKG-001", 10, 12, "ORDERED", None, "성수기 대비 팔레트 보충", po="PARTIAL", received=6)
        make_pr("PT-PCB-001", 10, 25, "ORDERED", None, "설비 정비 일정", po="CLOSED", received=10)
        make_pr("PT-BRG-001", 40, 3, "ORDERED", None, "정기 보충", po="OPEN")

        # 결재 대기 · 반려 실사 조정
        for code, qty, ago, status, comment in (("PT-MTR-001", -2, 1, "PENDING", ""), ("LSH-001", -1, 2, "PENDING", ""),
                                                ("PKG-001", -2, 20, "REJECTED", "재실사 후 다시 올릴 것")):
            who = clerks[area_of(code)][0]
            dd = today - timedelta(days=ago)
            decided = (managers[1]["id"], managers[1]["name"], stamp(dd)) if status != "PENDING" else (None, "", "")
            conn.execute(
                "INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status, "
                "requested_by_id, requested_by, requested_at, decided_by_id, decided_by, decided_at, comment) "
                "VALUES ('ADJ', ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?)",
                (mats[code], whs[wh_of[code]], dd.isoformat(), qty, abs(qty) * price_of[code], status, who["id"], who["name"],
                 stamp(dd), *decided, comment))
        audit.record(conn, audit.SYSTEM, "SEED", "material", "demo", {"materials": counts["materials"], "clean": True})

    # 지난 달들 월 마감 (2달 전까지)
    close_through = (today.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    for _ in range(14):
        ym = periods.next_closable()
        if not ym or ym > close_through.strftime("%Y-%m"):
            break
        if not periods.close_month(ym, {**admin, "ip": ""}).ok:
            break
    _grant_scopes(users)
    return counts


def _grant_scopes(users: dict) -> None:
    """샘플 계정에 데이터 범위를 준다. 범위가 없는 계정은 일부러 아무것도 보이지 않게 해 두었으므로(안전한 기본값),
    이걸 안 주면 시연 '다른 역할로 보기'의 관리자·담당자·조회 화면이 비어 보인다.
    관리자·조회는 전체 창고, 담당자는 맡은 지역의 플랜트(부산 = 기본 플랜트 P1, 창원 = P-CW)."""
    from core import org
    plant_of = {"BS": "P1", "CW": "P-CW"}
    ids = {r.code: int(r.id) for r in db.query_df("SELECT id, code FROM plants").itertuples()}
    for u in users.values():
        if u["role"] in ("MANAGER", "VIEWER"):
            org.set_user_scope(u["id"], True, [], [], audit.SYSTEM)
        elif u["role"] == "CLERK" and plant_of.get(u["area"]) in ids:
            org.set_user_scope(u["id"], False, [ids[plant_of[u["area"]]]], [], audit.SYSTEM)

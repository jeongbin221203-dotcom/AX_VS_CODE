"""포트폴리오 시연용 대량 샘플 (core/demo.py 가 빈 DB에서 한 번만 부른다).

seed.seed(history=True) · seed_mfg 위에 더한다:
  - 담당자·관리자·조회 계정 6명 (거래·결재·발주에 실제 이름이 남는다)
  - 인천 물류센터(상온·화학 창고) · 평택 부품센터(전기·MRO 창고), 원가센터 이름
  - 자재 44종(포장재·안전용품·청소·IT·지게차·화학·전기·정비·유압공압), 지난 12개월 날마다의 입출고
    · 계절 수요(4분기 성수기), 재주문점 아래로 내려가면 발주 → 며칠 뒤 입고(일부는 아직 미입고·분할 입고)
    · 로트·유효기한 자재(선입선출 출고, 기한 지난 로트 폐기), 창고 간 이동, 분기 실사 조정(큰 조정은 결재)
    · 잘못 등록한 출고의 취소 거래 + 바른 수량 재등록
  - 구매요청 결재 단계별 상태(결재 중·승인·반려·취소·발주 완료), 발주 상태(발주·부분 입고·입고 완료)
  - 지난 달들 월 마감(월말 재고·재고평가 스냅샷)
재고가 음수가 되지 않게 남은 재고 안에서만 출고한다. 난수 씨앗이 고정이라 언제 만들어도 같은 모양이다.
"""
from __future__ import annotations

import random
import secrets
from datetime import date, timedelta

from core import audit, auth, db, periods, repository as repo
from core.utils import now_str

USERS = [  # (아이디, 이름, 역할, 담당 창고 접두어)
    ("kim.mj", "김민준", "CLERK", "IC"),
    ("lee.sy", "이서연", "CLERK", "PT"),
    ("han.jw", "한지우", "CLERK", "IC"),
    ("park.jh", "박지훈", "MANAGER", ""),
    ("choi.yj", "최유진", "MANAGER", ""),
    ("jung.hn", "정하늘", "ADMIN", ""),
    ("kang.dy", "강도윤", "VIEWER", ""),
    ("yoon.jw", "윤지원", "DATA", ""),          # 데이터 관리: 기준정보·데이터 점검 (입출고·결재 없음)
]
PLANTS = [("P-IC", "인천 물류센터", [("IC-A", "인천 상온 창고"), ("IC-B", "인천 화학·위험물 창고")]),
          ("P-PT", "평택 부품센터", [("PT-01", "평택 전기·전자 창고"), ("PT-02", "평택 MRO 창고")])]
COST_CENTERS = {
    "IC": [("L100", "입출고팀", 0.45), ("L200", "수출포장팀", 0.35), ("L300", "지게차운영", 0.12), ("G100", "총무팀", 0.08)],
    "PT": [("M100", "설비보전팀", 0.45), ("M200", "전기팀", 0.30), ("P100", "조립라인", 0.25)],
}
# (코드, 이름, 규격, 단위, 분류, 안전재고, 기준단가, 위치, 공급처, 창고, 월 사용량, 로트관리)
MATERIALS = [
    ("PK-TAPE-001", "OPP 박스테이프", "48mm × 80m 투명", "ROLL", "포장재", 300, 1100, "A-11", "대한테이프", "IC-A", 1500, 0),
    ("PK-WRAP-002", "에어캡", "1000mm × 50m", "ROLL", "포장재", 40, 14500, "A-12", "한국필름", "IC-A", 160, 0),
    ("PK-BOX-010", "3겹 골판지 박스", "400 × 300 × 250", "EA", "포장재", 800, 650, "A-13", "동양포장", "IC-A", 5000, 0),
    ("PK-BOX-011", "5겹 골판지 박스", "800 × 600 × 600", "EA", "포장재", 300, 2900, "A-14", "동양포장", "IC-A", 1500, 0),
    ("PK-PAL-010", "목재 팔레트(국내용)", "1200 × 1000", "EA", "포장재", 80, 12500, "A-15", "대한팔레트", "IC-A", 350, 0),
    ("PK-PAL-011", "플라스틱 팔레트", "1100 × 1100 4방향", "EA", "포장재", 40, 39000, "A-16", "대한팔레트", "IC-A", 90, 0),
    ("PK-BND-001", "PP 밴드", "15mm × 1000m", "ROLL", "포장재", 30, 18000, "A-17", "한국결속", "IC-A", 100, 0),
    ("PK-CRN-001", "모서리 보호대", "50 × 50 × 1000", "EA", "포장재", 500, 420, "A-18", "동양포장", "IC-A", 3000, 0),
    ("PK-LBL-020", "바코드 라벨", "100 × 70 3000매", "ROLL", "라벨/소모품", 40, 16000, "A-19", "라벨코리아", "IC-A", 160, 0),
    ("PK-RIB-001", "열전사 리본", "110mm × 300m", "ROLL", "라벨/소모품", 20, 9000, "A-20", "라벨코리아", "IC-A", 80, 0),
    ("SF-HLM-001", "안전모", "ABS 일반형", "EA", "안전용품", 30, 8500, "S-01", "안전나라", "IC-A", 40, 0),
    ("SF-SHO-001", "안전화", "270 발등보호", "PAIR", "안전용품", 20, 42000, "S-02", "안전나라", "IC-A", 25, 0),
    ("SF-VST-001", "형광 안전조끼", "FREE", "EA", "안전용품", 40, 6500, "S-03", "안전나라", "IC-A", 60, 0),
    ("SF-MSK-001", "방진마스크", "KF94 개별포장", "EA", "안전용품", 500, 450, "S-04", "안전나라", "IC-A", 2500, 0),
    ("CL-TWL-001", "산업용 와이퍼", "200매 × 12팩", "PK", "청소/위생", 50, 7800, "S-05", "크린텍", "IC-A", 200, 0),
    ("CL-DET-001", "다목적 세정제", "18L", "CAN", "청소/위생", 10, 32000, "S-06", "크린텍", "IC-A", 30, 0),
    ("IT-TON-001", "레이저 토너", "표준형 3000매", "EA", "IT소모품", 6, 89000, "O-01", "오피스플러스", "IC-A", 12, 0),
    ("IT-PPR-001", "A4 복사용지", "80g 2500매", "BOX", "사무용품", 30, 28000, "O-02", "오피스플러스", "IC-A", 90, 0),
    ("IT-SCN-001", "바코드 스캐너 배터리", "3.7V 5200mAh", "EA", "IT소모품", 10, 35000, "O-03", "스캔테크", "IC-A", 15, 0),
    ("FL-TIR-001", "지게차 솔리드 타이어", "6.50-10", "EA", "지게차부품", 4, 185000, "F-01", "한국지게차", "IC-A", 6, 0),
    ("FL-OIL-001", "유압작동유", "ISO VG46 20L", "CAN", "지게차부품", 8, 72000, "F-02", "대양윤활", "IC-A", 16, 0),
    ("FL-WTR-001", "배터리 보충액", "증류수 20L", "CAN", "지게차부품", 6, 9000, "F-03", "대양윤활", "IC-A", 20, 0),
    ("CH-GRS-010", "산업용 그리스", "NLGI 2 15kg", "CAN", "화학", 6, 95000, "H-01", "대양윤활", "IC-B", 12, 1),
    ("CH-SOL-001", "세척용 솔벤트", "18L", "CAN", "화학", 8, 54000, "H-02", "케미칼원", "IC-B", 20, 1),
    ("CH-ALC-002", "알코올 소독제", "에탄올 70% 4L", "EA", "청소/위생", 20, 12000, "H-03", "크린텍", "IC-B", 60, 1),
    ("CH-LPG-001", "지게차 LPG", "20kg 용기", "EA", "연료", 20, 46000, "H-04", "인천가스", "IC-B", 120, 0),
    ("EL-CBL-001", "VCT 케이블", "2.5SQ × 3C 100m", "ROLL", "전기자재", 10, 145000, "E-01", "한국케이블", "PT-01", 25, 0),
    ("EL-ELB-001", "누전차단기", "30A 2P", "EA", "전기자재", 20, 18500, "E-02", "한빛전기", "PT-01", 35, 0),
    ("EL-MCB-001", "배선용 차단기", "50AF 3P", "EA", "전기자재", 15, 26000, "E-03", "한빛전기", "PT-01", 25, 0),
    ("EL-LED-001", "LED 투광등", "150W", "EA", "전기자재", 10, 68000, "E-04", "밝은조명", "PT-01", 14, 0),
    ("EL-RLY-001", "릴레이", "24VDC 4C", "EA", "전기자재", 50, 7200, "E-05", "세진전자부품", "PT-01", 140, 0),
    ("EL-SNS-001", "근접센서", "M18 PNP", "EA", "전기자재", 30, 23000, "E-06", "오토센서", "PT-01", 60, 0),
    ("EL-PLC-001", "PLC 입력모듈", "DC 16점", "EA", "전기자재", 4, 210000, "E-07", "한빛전기", "PT-01", 5, 0),
    ("EL-PWR-001", "SMPS", "24V 10A", "EA", "전기자재", 10, 54000, "E-08", "파워텍", "PT-01", 18, 0),
    ("MR-BLT-010", "컨베이어 벨트", "PVC 600W", "M", "정비부품", 30, 32000, "M-01", "동일벨트", "PT-02", 60, 0),
    ("MR-ROL-001", "컨베이어 롤러", "Ø50 × 600", "EA", "정비부품", 40, 14500, "M-02", "한국컨베이어", "PT-02", 80, 0),
    ("MR-BRG-010", "볼베어링", "6205ZZ", "EA", "정비부품", 100, 3800, "M-03", "한일베어링", "PT-02", 220, 0),
    ("MR-VBT-001", "V벨트", "B-60", "EA", "정비부품", 20, 9500, "M-04", "동일벨트", "PT-02", 40, 0),
    ("MR-CYL-001", "공압 실린더", "Ø40 × 100", "EA", "유압/공압", 6, 68000, "M-05", "대성공압", "PT-02", 8, 0),
    ("MR-SOL-001", "솔레노이드 밸브", "5/2way 24V", "EA", "유압/공압", 10, 42000, "M-06", "대성공압", "PT-02", 14, 0),
    ("MR-HOS-001", "유압호스", "1/2\" × 1m", "EA", "유압/공압", 20, 18000, "M-07", "한국유압", "PT-02", 40, 0),
    ("MR-FLT-001", "에어필터 엘리먼트", "5μm", "EA", "유압/공압", 15, 12500, "M-08", "대성공압", "PT-02", 30, 0),
    ("MR-LUB-001", "체인 윤활 스프레이", "420ml", "EA", "정비부품", 24, 8800, "M-09", "대양윤활", "PT-02", 60, 0),
    ("MR-TOL-001", "육각렌치 세트", "1.5~10mm 9pcs", "EA", "공구", 3, 35000, "M-10", "한국공구", "PT-02", 2, 0),
]
# 창고 간 이동: (자재, 보내는 창고, 받는 창고, 월 이동 비율)
TRANSFERS = [("SF-MSK-001", "IC-A", "PT-02", 0.25), ("SF-VST-001", "IC-A", "PT-02", 0.3),
             ("CL-TWL-001", "IC-A", "PT-02", 0.2), ("MR-BRG-010", "PT-02", "PT-01", 0.15)]
SEASON = {1: 0.9, 2: 0.8, 3: 1.0, 4: 1.0, 5: 1.05, 6: 1.0, 7: 0.95, 8: 0.9, 9: 1.1, 10: 1.15, 11: 1.3, 12: 1.2}
DEAD_AFTER = {"MR-TOL-001": 130, "EL-PLC-001": 100}     # 이 날수 전부터 출고 없음(장기 미사용)
REJECT_REASONS = ["재고 충분 — 다음 달 재검토", "단가 재견적 후 다시 올릴 것", "예산 초과 — 수량 줄여서 재요청"]
PR_REASONS = ["안전재고 미달 보충", "성수기 출하 대비", "정기 보충", "설비 정비 일정", "신규 라인 준비", "소진 예상"]


def seed_large() -> dict:
    rng = random.Random(23)
    today = date.today()
    start = today - timedelta(days=365)
    ts = now_str()
    counts = {"materials": 0, "transactions": 0, "purchase_requests": 0, "purchase_orders": 0}

    users = {}
    for username, name, role, area in USERS:
        r = auth.create_user(username, name, role, "Demo" + secrets.token_hex(8) + "1", audit.SYSTEM, must_change_pw=False)
        if r.ok:
            users[username] = {"id": r.user["id"], "name": name, "role": role, "area": area}
        else:                                  # 기본 샘플(core/seed_clean.py)이 같은 사용자를 이미 만들었다
            row = db.query_df("SELECT id, name, role FROM users WHERE username = ?", (username,))
            if not row.empty:
                users[username] = {"id": int(row.iloc[0]["id"]), "name": row.iloc[0]["name"], "role": row.iloc[0]["role"], "area": area}
    clerks = {area: [u for u in users.values() if u["role"] == "CLERK" and u["area"] == area] for area in ("IC", "PT")}
    managers = [u for u in users.values() if u["role"] == "MANAGER"]
    admin = next((u for u in users.values() if u["role"] == "ADMIN"), None)
    if not clerks["IC"] or not clerks["PT"] or len(managers) < 2 or admin is None:
        return {"skipped": True}

    rows: list[tuple] = []              # 한 번에 넣을 거래
    later: list[dict] = []              # id가 필요한 거래(취소·결재 연결) — 나중에 하나씩
    seq = {"n": 0}

    def ref(prefix: str, d: date) -> str:
        seq["n"] += 1
        return f"{prefix}-{d:%y%m%d}-{seq['n']:05d}"

    def stamp(d: date) -> str:
        return f"{d.isoformat()} {rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"

    with db.transaction() as conn:
        # 기본 조직 이름을 시연에 맞게 (처음 만든 그대로일 때만)
        conn.execute("UPDATE plants SET name = '부산 물류센터' WHERE code = 'P1' AND name = '기본 공장'")
        conn.execute("UPDATE warehouses SET name = '부산 본 창고' WHERE code = 'WH1' AND name = '기본 창고'")
        whs = {}
        for pcode, pname, wlist in PLANTS:
            conn.execute("INSERT INTO plants (code, name, sap_plant, created_at) VALUES (?, ?, '', ?) ON CONFLICT (code) DO NOTHING",
                         (pcode, pname, ts))
            pid = conn.execute("SELECT id FROM plants WHERE code = ?", (pcode,)).fetchone()[0]
            for wcode, wname in wlist:
                conn.execute("INSERT INTO warehouses (plant_id, code, name, sap_sloc, created_at) VALUES (?, ?, ?, '', ?) "
                             "ON CONFLICT (code) DO NOTHING", (pid, wcode, wname, ts))
                whs[wcode] = int(conn.execute("SELECT id FROM warehouses WHERE code = ?", (wcode,)).fetchone()[0])
        for area in COST_CENTERS.values():
            for code, name, _w in area:
                conn.execute("INSERT INTO cost_centers (code, name, active, synced_at) VALUES (?, ?, 1, '') "
                             "ON CONFLICT (code) DO NOTHING", (code, name))
        for code, name in (("C100", "생산1팀"), ("C200", "생산2팀"), ("C300", "품질팀"), ("C400", "설비보전(창원)")):
            conn.execute("INSERT INTO cost_centers (code, name, active, synced_at) VALUES (?, ?, 1, '') "
                         "ON CONFLICT (code) DO NOTHING", (code, name))

        mats = {}
        for (code, name, spec, unit, cat, safety, price, loc, supplier, _wh, _m, lot) in MATERIALS:
            conn.execute(
                "INSERT INTO materials (code, name, spec, unit, category, safety_stock, unit_price, location, supplier, "
                "lot_managed, expiry_managed, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT (code) DO NOTHING",
                (code, name, spec, unit, cat, safety, price, loc, supplier, lot, lot, ts, ts))
            mats[code] = int(conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()[0])
            counts["materials"] += 1

        def add(code, tx_type, qty, price, d, wh_code, who, **extra):
            data = {"material_id": mats[code], "tx_type": tx_type, "qty": qty, "unit_price": round(price),
                    "tx_date": d.isoformat(), "ref_no": "", "partner": "", "note": "", "created_by": who["name"],
                    "reversal_of": None, "po_no": "", "po_item": "", "cost_center": "", "movement_type": "",
                    "warehouse_id": whs[wh_code], "transfer_no": "", "created_by_id": who["id"], "approved_by": "",
                    "lot_no": "", "statement_id": None, "partner_id": None, "production_id": None, "batch_no": "",
                    "entry_unit": "", "entry_qty": None,
                    **extra}
            rows.append((*[data[f] for f in repo.TX_FIELDS], stamp(d)))
            counts["transactions"] += 1

        # ── 구매요청·발주 (입고와 함께 만든다) ──
        pr_seq: dict[str, int] = {}

        def next_no(prefix: str, d: date) -> str:
            key = f"{prefix}-{d:%Y%m}"
            pr_seq[key] = pr_seq.get(key, 0) + 1
            return f"{key}-D{pr_seq[key]:03d}"

        def make_pr(code, wh_code, qty, price, d, status, requester, reason, approvals=None, reject=""):
            amount = qty * price
            steps = 1 if amount <= 1_000_000 else 2 if amount <= 10_000_000 else 3
            pr_no = next_no("PR", d)
            pr_id = conn.execute(
                "INSERT INTO purchase_requests (pr_no, warehouse_id, need_date, reason, status, total_amount, required_steps, "
                "requested_by_id, requested_by, requested_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pr_no, whs[wh_code], (d + timedelta(days=7)).isoformat(), reason, status, amount, steps,
                 requester["id"], requester["name"], stamp(d), stamp(d))).lastrowid
            conn.execute("INSERT INTO pr_items (pr_id, line_no, material_id, qty, est_price) VALUES (?, 10, ?, ?, ?)",
                         (pr_id, mats[code], qty, round(price)))
            n = steps if approvals is None else approvals
            for step in range(1, n + 1):
                who = admin if step >= 3 else managers[(step - 1) % len(managers)]
                conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                             "VALUES (?, ?, ?, ?, 'APPROVE', '', ?)", (pr_id, step, who["id"], who["name"],
                                                                        stamp(d + timedelta(days=min(step, 2)))))
            if reject:
                who = managers[n % len(managers)] if n + 1 < 3 else admin
                conn.execute("INSERT INTO pr_approvals (pr_id, step, approver_id, approver, decision, comment, at) "
                             "VALUES (?, ?, ?, ?, 'REJECT', ?, ?)", (pr_id, n + 1, who["id"], who["name"], reject,
                                                                      stamp(d + timedelta(days=1))))
            counts["purchase_requests"] += 1
            return pr_id, amount

        def make_po(pr_id, code, wh_code, qty, price, d, supplier, status):
            buyer = managers[1]
            po_no = next_no("PO", d)
            po_id = conn.execute(
                "INSERT INTO purchase_orders (po_no, pr_id, supplier, warehouse_id, status, total_amount, created_by_id, "
                "created_by, created_at, approved_by, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '')",
                (po_no, pr_id, supplier, whs[wh_code], status, qty * price, buyer["id"], buyer["name"], stamp(d))).lastrowid
            conn.execute("INSERT INTO po_items (po_id, line_no, material_id, qty, price) VALUES (?, 10, ?, ?, ?)",
                         (po_id, mats[code], qty, round(price)))
            counts["purchase_orders"] += 1
            return po_no

        # ── 날마다 시뮬레이션 ──
        stock = {}                       # (자재, 창고) → 재고
        lots: dict[str, list[list]] = {}  # 자재 → [[로트, 남은 수량, 유효기한], ...]
        incoming: list[tuple] = []       # (입고일, 자재, 창고, 수량, 단가, 공급처, 발주번호, 담당)
        spec = {m[0]: m for m in MATERIALS}
        transfer_dest = {t[0]: t for t in TRANSFERS}
        dead_from = {code: today - timedelta(days=days) for code, days in DEAD_AFTER.items()}
        int_units = {"EA", "PAIR", "ROLL", "CAN", "BOX", "PK"}

        def rnd(code, q):
            return max(1, round(q)) if spec[code][3] in int_units else round(max(q, 0.5), 1)

        def area(wh_code):
            return wh_code.split("-")[0]

        def price_on(code, d):
            base = spec[code][6]
            return base * (1 + 0.05 * (d - start).days / 365 + rng.uniform(-0.03, 0.03))

        def receive(code, wh_code, qty, price, d, supplier, po_no, who, part=False):
            lot_no = ""
            if spec[code][11]:
                lot_no = f"{code.split('-')[1]}-{d:%y%m%d}"
                expiry = d + timedelta(days=rng.randint(150, 330))
                repo.ensure_lot(conn, mats[code], lot_no, expiry.isoformat())
                lots.setdefault(code, []).append([lot_no, float(qty), expiry])
            add(code, "IN", qty, price, d, wh_code, who, partner=supplier, po_no=po_no, po_item="10" if po_no else "",
                ref_no=po_no or ref("GR", d), movement_type="101", lot_no=lot_no,
                note="분할 입고" if part else ("발주 입고" if po_no else "직접 입고"))
            stock[(code, wh_code)] = stock.get((code, wh_code), 0) + qty

        def issue(code, wh_code, qty, price, d, who, cc, cc_name, note="", **extra):
            """출고(로트 자재는 기한 빠른 로트부터). 실제로 나간 수량을 돌려준다."""
            have = stock.get((code, wh_code), 0)
            qty = min(qty, have)
            if qty <= 0:
                return 0
            pieces = [("", qty)]
            if spec[code][11] and wh_code == spec[code][9]:
                pieces, left = [], qty
                for entry in sorted(lots.get(code, []), key=lambda e: e[2]):
                    if left <= 1e-9:
                        break
                    if entry[1] <= 1e-9 or entry[2] < d:
                        continue
                    use = round(min(entry[1], left), 2)
                    pieces.append((entry[0], use))
                    entry[1] -= use
                    left -= use
                qty -= left
            for lot_no, q in pieces:
                if q > 0:
                    add(code, "OUT", q, price, d, wh_code, who, partner=cc_name, cost_center=cc, ref_no=extra.pop("ref_no", None)
                        or ref("MI", d), movement_type="201", lot_no=lot_no, note=note, **extra)
            stock[(code, wh_code)] = have - qty
            return qty

        # 기초 재고 (1년 전)
        opening_clerk = clerks["IC"][0]
        for code in mats:
            m = spec[code]
            receive(code, m[9], rnd(code, m[10] * rng.uniform(1.2, 1.8)), m[6], start, m[8], "",
                    clerks[area(m[9])][0] if area(m[9]) in clerks else opening_clerk)
        last_out: dict[str, date] = {}
        d = start + timedelta(days=1)
        while d <= today:
            weekday = d.weekday() < 5
            # 입고 예정분 도착
            for item in [x for x in incoming if x[0] == d]:
                incoming.remove(item)
                _d, code, wh_code, qty, price, supplier, po_no, who, part = item
                receive(code, wh_code, qty, price, d, supplier, po_no, who, part)
            if weekday:
                for code, m in spec.items():
                    wh_code, monthly = m[9], m[10]
                    ar = area(wh_code)
                    events = 3 if monthly >= 100 else 2 if monthly >= 20 else 1
                    if rng.random() > events / 5 * (0.45 if monthly < 10 else 1):
                        continue
                    if code in dead_from and d >= dead_from[code]:
                        continue
                    qty = rnd(code, monthly / (events * 4.33) * SEASON[d.month] * rng.uniform(0.5, 1.5))
                    cc, cc_name, _w = rng.choices(COST_CENTERS[ar], [c[2] for c in COST_CENTERS[ar]])[0]
                    who = rng.choice(clerks[ar])
                    p = price_on(code, d)
                    if (rng.random() < 0.004 and not m[11] and d < today - timedelta(days=3) and stock.get((code, wh_code), 0) >= qty):
                        # 잘못 등록 → 다음 날 취소 → 바른 수량으로 다시 출고
                        wrong = ref("MI", d)
                        issue(code, wh_code, qty, p, d, who, cc, cc_name, note="", ref_no=wrong)
                        later.append({"kind": "reverse", "ref": wrong, "d": d + timedelta(days=1), "who": managers[0],
                                      "code": code, "wh": wh_code, "qty": qty})
                        stock[(code, wh_code)] += qty
                        right = rnd(code, qty * rng.choice([0.5, 0.8, 0.9]))
                        issue(code, wh_code, right, p, d + timedelta(days=1), who, cc, cc_name, note=f"{wrong} 수량 정정 후 재등록")
                    else:
                        if issue(code, wh_code, qty, p, d, who, cc, cc_name):
                            last_out[code] = d
                # 이동 창고의 출고 (받은 만큼 나눠서)
                for code, src, dst, _ratio in TRANSFERS:
                    if rng.random() < 0.35 and stock.get((code, dst), 0) > 0:
                        ar = area(dst)
                        cc, cc_name, _w = rng.choices(COST_CENTERS[ar], [c[2] for c in COST_CENTERS[ar]])[0]
                        issue(code, dst, rnd(code, stock[(code, dst)] * rng.uniform(0.2, 0.5)), price_on(code, d), d,
                              rng.choice(clerks[ar]), cc, cc_name)
            # 매월 5일 무렵 창고 간 이동
            if d.day == 5:
                for n, (code, src, dst, ratio) in enumerate(TRANSFERS, 1):
                    q = rnd(code, min(spec[code][10] * ratio, stock.get((code, src), 0) * 0.5))
                    if stock.get((code, src), 0) >= q > 0:
                        no = f"TRF-{d:%y%m}-{n:03d}"
                        who = rng.choice(clerks[area(src)])
                        p = spec[code][6]
                        add(code, "OUT", q, p, d, src, who, partner=f"→ {dst}", transfer_no=no, movement_type="311", ref_no=no)
                        add(code, "IN", q, p, d, dst, who, partner=f"← {src}", transfer_no=no, movement_type="311", ref_no=no)
                        stock[(code, src)] -= q
                        stock[(code, dst)] = stock.get((code, dst), 0) + q
            # 재주문: 재고 + 입고 예정이 재주문점 아래면 발주
            for code, m in spec.items():
                wh_code, safety, monthly = m[9], m[5], m[10]
                if code in dead_from:
                    continue
                on_order = sum(x[3] for x in incoming if x[1] == code)
                if stock.get((code, wh_code), 0) + on_order > safety + monthly * 0.35:
                    continue
                if rng.random() < 0.25:         # 일부는 늦게 주문 → 안전재고 미달이 생긴다
                    continue
                qty = rnd(code, max(monthly * rng.uniform(0.9, 1.5), safety * 1.5))
                p = price_on(code, d)
                lead = rng.randint(2, 9)
                ar = area(wh_code)
                who = rng.choice(clerks[ar])
                if qty * p >= 300_000 and rng.random() < 0.7:
                    pr_id, _amount = make_pr(code, wh_code, qty, p, d - timedelta(days=2), "ORDERED", who,
                                             rng.choice(PR_REASONS))
                    arrive = d + timedelta(days=lead)
                    split = rng.random() < 0.18
                    status = "CLOSED"
                    if arrive > today:
                        status = "OPEN"
                    elif split and arrive + timedelta(days=6) > today:
                        status = "PARTIAL"
                    po_no = make_po(pr_id, code, wh_code, qty, p, d, m[8], status)
                    if split:
                        first = rnd(code, qty * 0.6)
                        incoming.append((arrive, code, wh_code, first, p, m[8], po_no, who, True))
                        incoming.append((arrive + timedelta(days=6), code, wh_code, qty - first, p, m[8], po_no, who, True))
                    else:
                        incoming.append((arrive, code, wh_code, qty, p, m[8], po_no, who, False))
                else:
                    incoming.append((d + timedelta(days=lead), code, wh_code, qty, p, m[8], "", who, False))
            # 분기말 실사 조정 · 기한 지난 로트 폐기
            month_end = (d + timedelta(days=1)).day == 1
            if month_end and d.month in (3, 6, 9, 12) and d < today:
                for code, m in spec.items():
                    wh_code = m[9]
                    have = stock.get((code, wh_code), 0)
                    if m[11] or have <= 5 or rng.random() < 0.55:
                        continue
                    diff = -rnd(code, have * rng.uniform(0.003, 0.02)) if rng.random() < 0.8 else rnd(code, have * 0.005)
                    if abs(diff) > have:
                        continue
                    p = price_on(code, d)
                    amount = abs(diff) * p
                    who = rng.choice(clerks[area(wh_code)])
                    if amount >= 500_000:          # 큰 조정은 결재를 거쳐 반영
                        r = ref("ADJ", d)
                        approver = managers[0]
                        add(code, "ADJ", diff, p, d, wh_code, who, ref_no=r, approved_by=approver["name"],
                            movement_type="702" if diff < 0 else "701", note="분기 실사 차이 (결재 승인)")
                        later.append({"kind": "approval", "ref": r, "d": d, "who": who, "approver": approver,
                                      "code": code, "wh": wh_code, "qty": diff, "amount": amount})
                    else:
                        add(code, "ADJ", diff, p, d, wh_code, who, ref_no=ref("ADJ", d),
                            movement_type="702" if diff < 0 else "701", note="분기 실사 차이")
                    stock[(code, wh_code)] = have + diff
            if month_end:
                for code, entries in lots.items():
                    for entry in entries:
                        if entry[1] > 1e-9 and entry[2] < d and rng.random() < 0.85:
                            wh_code = spec[code][9]
                            add(code, "ADJ", -round(entry[1], 2), spec[code][6], d, wh_code, clerks["IC"][0],
                                lot_no=entry[0], movement_type="702", ref_no=ref("ADJ", d), note="유효기한 경과 폐기")
                            stock[(code, wh_code)] -= entry[1]
                            entry[1] = 0.0
            d += timedelta(days=1)

        conn.executemany(
            f"INSERT INTO transactions ({', '.join(repo.TX_FIELDS)}, created_at) "
            f"VALUES ({', '.join('?' * (len(repo.TX_FIELDS) + 1))})", rows)

        # 취소 거래 · 결재 연결 (원거래 id 필요)
        for item in later:
            orig = conn.execute("SELECT * FROM transactions WHERE ref_no = ? ORDER BY id LIMIT 1", (item["ref"],)).fetchone()
            if orig is None:
                continue
            if item["kind"] == "reverse":
                who = item["who"]
                repo.insert_transaction(conn, {
                    "material_id": orig["material_id"], "tx_type": orig["tx_type"], "qty": -orig["qty"],
                    "unit_price": orig["unit_price"], "tx_date": item["d"].isoformat(), "ref_no": orig["ref_no"],
                    "partner": orig["partner"], "note": "취소: 수량 오입력", "created_by": who["name"],
                    "created_by_id": who["id"], "reversal_of": orig["id"], "cost_center": orig["cost_center"],
                    "movement_type": "202", "warehouse_id": orig["warehouse_id"], "lot_no": orig["lot_no"]})
                counts["transactions"] += 1
            else:
                conn.execute(
                    "INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status, "
                    "requested_by_id, requested_by, requested_at, decided_by_id, decided_by, decided_at, comment, result_tx_id) "
                    "VALUES ('ADJ', ?, ?, ?, ?, ?, '{}', 'APPROVED', ?, ?, ?, ?, ?, ?, '실사표 확인', ?)",
                    (orig["material_id"], orig["warehouse_id"], orig["tx_date"], orig["qty"], item["amount"],
                     item["who"]["id"], item["who"]["name"], stamp(item["d"]), item["approver"]["id"],
                     item["approver"]["name"], stamp(item["d"]), orig["id"]))

        # 결재 대기·반려 실사 조정 (최근)
        for code, wh_code, qty, days_ago, status, comment in (
                ("PK-PAL-011", "IC-A", -14, 2, "PENDING", ""), ("EL-CBL-001", "PT-01", -4, 1, "PENDING", ""),
                ("FL-TIR-001", "IC-A", -3, 20, "REJECTED", "재실사 후 다시 올릴 것"),
                ("MR-CYL-001", "PT-02", -8, 45, "REJECTED", "출고 누락 확인됨 — 출고로 처리")):
            who = clerks[area(wh_code)][0]
            dd = today - timedelta(days=days_ago)
            decided = (managers[1]["id"], managers[1]["name"], stamp(dd)) if status != "PENDING" else (None, "", "")
            conn.execute(
                "INSERT INTO approval_requests (kind, material_id, warehouse_id, tx_date, qty, amount, payload, status, "
                "requested_by_id, requested_by, requested_at, decided_by_id, decided_by, decided_at, comment) "
                "VALUES ('ADJ', ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?)",
                (mats[code], whs[wh_code], dd.isoformat(), qty, abs(qty) * spec[code][6], status, who["id"], who["name"],
                 stamp(dd), *decided, comment))

        # 진행 중·끝난 구매요청 여러 상태
        samples = [  # (자재, 수량, 며칠 전, 상태, 결재한 단계 수, 반려 사유)
            ("PK-PAL-011", 120, 1, "PENDING", 0, ""), ("EL-PLC-001", 60, 2, "PENDING", 1, ""),
            ("MR-BLT-010", 400, 3, "PENDING", 2, ""), ("SF-SHO-001", 40, 1, "PENDING", 0, ""),
            ("IT-TON-001", 10, 4, "APPROVED", None, ""), ("EL-LED-001", 30, 6, "APPROVED", None, ""),
            ("FL-TIR-001", 12, 30, "REJECTED", 0, REJECT_REASONS[0]), ("CH-SOL-001", 60, 75, "REJECTED", 1, REJECT_REASONS[1]),
            ("MR-CYL-001", 30, 120, "REJECTED", 0, REJECT_REASONS[2]), ("PK-BND-001", 50, 50, "CANCELLED", 0, ""),
            ("IT-SCN-001", 20, 160, "CANCELLED", 0, ""),
        ]
        for code, qty, days_ago, status, done, reject in samples:
            m = spec[code]
            make_pr(code, m[9], qty, m[6], today - timedelta(days=days_ago), status, clerks[area(m[9])][-1],
                    rng.choice(PR_REASONS), approvals=done, reject=reject)

        audit.record(conn, audit.SYSTEM, "SEED", "material", "demo",
                     {"materials": counts["materials"], "transactions": counts["transactions"]})

    # 지난 달들 월 마감 (2달 전까지 — 지난 달은 아직 열어 둔다)
    close_through = (today.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    for _ in range(14):
        ym = periods.next_closable()
        if not ym or ym > close_through.strftime("%Y-%m"):
            break
        if not periods.close_month(ym, {**admin, "ip": ""}).ok:
            break
    return counts

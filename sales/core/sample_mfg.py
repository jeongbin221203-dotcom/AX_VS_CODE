"""제조업 샘플 데이터 — 부품·원자재를 반복 구매하고 설비를 프로젝트로 사는 제조업 거래처 (개발·시연용)

  품목 마스터(원자재·부품·설비·가공 서비스·소모품), 제조업 거래처(올바른 사업자번호·여신·결제조건·ERP 코드),
  거래처별 담당자 여럿(구매·품질·회계·생산), 영업기회(설비 교체·연간 단가 계약·스마트팩토리), 견적 → 수주 → 분할 납품 매출,
  매달 반복 구매 매출, 부분 입금·연체, 불량 반품, 공장 방문·샘플 테스트 같은 영업활동.
  모두 화면과 같은 저장 함수를 거치므로 입금 내역·감사로그·ERP 대기열이 실제처럼 쌓인다.
  여러 번 실행하면 그만큼 더 쌓인다(거래처 이름 뒤 번호가 이어짐). 월 마감한 달의 날짜는 건너뛴다.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from . import sales_db as db

PRODUCTS = [  # 코드, 품목명, 규격, 분류, 단위, 정가, 과세
    ("RM-SUS304", "스테인리스 판재 SUS304", "2t × 1219 × 2438", "원자재", "톤", 4_850_000, "과세"),
    ("RM-AL6061", "알루미늄 압출재 6061", "T6, 6m", "원자재", "톤", 5_600_000, "과세"),
    ("RM-SS400", "일반구조용 강판 SS400", "6t", "원자재", "톤", 1_050_000, "과세"),
    ("PT-BRG6205", "베어링 6205ZZ", "25×52×15", "부품", "EA", 6_800, "과세"),
    ("PT-SRV1K", "AC 서보모터 1kW", "220V, 3000rpm", "부품", "EA", 1_380_000, "과세"),
    ("PT-PLC-CPU", "PLC CPU 모듈", "I/O 256점", "부품", "EA", 2_150_000, "과세"),
    ("PT-SENS-PX", "근접 센서", "M18, PNP", "부품", "EA", 48_000, "과세"),
    ("EQ-CNC-5AX", "5축 CNC 머시닝센터", "테이블 650mm", "설비", "대", 385_000_000, "과세"),
    ("EQ-ROBOT-6", "6축 산업용 로봇", "가반하중 20kg", "설비", "대", 92_000_000, "과세"),
    ("EQ-PANEL", "자동화 제어반", "주문 제작", "설비", "식", 28_000_000, "과세"),
    ("SV-MOLD", "사출 금형 제작", "2캐비티", "가공서비스", "벌", 34_000_000, "과세"),
    ("SV-MACH", "정밀 가공 임가공", "시간당", "가공서비스", "시간", 95_000, "과세"),
    ("SV-PM", "설비 정기 점검", "분기 1회", "유지보수", "회", 1_800_000, "과세"),
    ("CS-OIL-20", "절삭유 수용성", "20L", "소모품", "통", 128_000, "과세"),
    ("CS-TOOL", "초경 엔드밀 세트", "Ø6~12, 10종", "소모품", "세트", 420_000, "과세"),
    ("EX-PKG-MFG", "수출용 부품 키트", "선적 단위", "부품", "식", 18_000_000, "영세"),
]

NAMES = ["한성정밀", "대성금속", "세진오토텍", "동양기계", "우진테크", "삼화공업", "태성산업", "경일엠텍", "신화정공",
         "부광메탈", "영신하이텍", "대원오토", "미래소재", "제일프레스", "광명기공", "현대정밀부품", "성우테크", "동아다이캐스팅",
         "한국베어링", "진흥전기", "서울사출", "유성엔지니어링", "청림오토메이션", "금강스틸", "정우로보틱스"]
REGIONS = ["경기 화성시", "경기 안산시 반월공단", "인천 남동공단", "충남 아산시", "경남 창원시", "경북 구미시", "울산 북구",
           "대구 달서구 성서공단", "광주 광산구 하남산단", "전북 군산시"]
DEAL_TITLES = [("노후 CNC 설비 교체", ["EQ-CNC-5AX"]), ("조립라인 로봇 도입", ["EQ-ROBOT-6", "EQ-PANEL"]),
               ("연간 베어링·센서 단가 계약", ["PT-BRG6205", "PT-SENS-PX"]), ("스마트팩토리 제어 고도화", ["PT-PLC-CPU", "EQ-PANEL"]),
               ("신규 차종 사출 금형", ["SV-MOLD"]), ("원자재 연간 공급", ["RM-SUS304", "RM-AL6061"]),
               ("설비 유지보수 계약", ["SV-PM"])]
ACTIVITIES = ["공장 방문 — 생산라인 확인", "샘플 테스트 결과 협의", "구매팀 단가 협상", "품질 이슈 대응 회의", "시운전 참관",
              "설비 사양 검토 회의", "연간 계약 갱신 협의", "납기 일정 조율", "기술 세미나 초청", "현장 불량 원인 분석"]
CONTACT_ROLES = [("구매", "구매팀", "과장"), ("회계·세금계산서", "재무팀", "대리"), ("현업", "생산기술팀", "차장"),
                 ("의사결정", "공장장실", "상무"), ("기술", "품질보증팀", "책임")]
PERSON = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
GIVEN = ["민수", "지훈", "서연", "현우", "수진", "도윤", "예린", "성호", "은지", "태영", "하늘", "준호"]


def _biz(rng: random.Random) -> str:
    from .documents import valid_biz_no
    while True:
        head = f"{rng.randint(101, 899)}{rng.randint(81, 88)}{rng.randint(1000, 9999)}"
        for c in range(10):
            if valid_biz_no(head + str(c)):
                return f"{head[:3]}-{head[3:5]}-{head[5:]}{c}"


def _safe_day(day: date) -> Optional[str]:
    from . import periods
    iso = day.isoformat()
    return None if periods.date_problem(iso) else iso


def seed(customers: int = 20, months: int = 12, rnd_seed: Optional[int] = None) -> dict:
    from . import catalog, contacts, orders, returns
    from . import enterprise as ent
    from . import quotes as qt
    rng = random.Random(rnd_seed)
    today = date.today()
    db.set_context("system", None)
    reps = [u for u in (ent.get_user(name=n) for n in ("김영업", "이수주", "박고객", "최성과")) if u]
    if not reps:
        raise ValueError("담당자 계정이 없습니다. '샘플 조직·계정 생성'을 먼저 실행하세요.")
    out = {"products": 0, "customers": 0, "contacts": 0, "deals": 0, "quotes": 0, "orders": 0, "sales": 0,
           "payments": 0, "returns": 0, "activities": 0, "skipped_closed": 0}

    if "제조" not in db.INDUSTRIES:
        from . import company
        company.save({"industries": [*db.INDUSTRIES, "제조"]}, "샘플")
    pids = {}
    for code, name, spec, cat, unit, price, tax in PRODUCTS:
        row = db._one("SELECT id FROM products WHERE code=?", [code])
        if not row:
            catalog.upsert_product({"code": code, "name": name, "spec": spec, "category": cat, "unit": unit,
                                    "list_price": price, "tax_type": tax, "erp_material": code})
            out["products"] += 1
            row = db._one("SELECT id FROM products WHERE code=?", [code])
        pids[code] = int(row["id"])

    start_no = int(db._scalar("SELECT COUNT(*) FROM customers WHERE memo LIKE '제조업 샘플%'"))
    for n in range(customers):
        rep = rng.choice(reps)
        ent.apply_context(ent.get_user(user_id=rep["id"]))
        base = NAMES[(start_no + n) % len(NAMES)]
        suffix = (start_no + n) // len(NAMES)
        name = f"(주){base}" + (f" {suffix + 1}공장" if suffix else "")
        grade = rng.choices(["VIP", "A", "B", "C"], [1, 3, 4, 2])[0]
        limit = {"VIP": 800_000_000, "A": 300_000_000, "B": 120_000_000, "C": 40_000_000}[grade]
        try:
            cid = db.upsert_customer({"name": name, "biz_no": _biz(rng), "industry": "제조", "grade": grade,
                                      "owner_id": rep["id"], "credit_limit": limit,
                                      "payment_terms": rng.choice([30, 45, 60, 90]),
                                      "address": f"{rng.choice(REGIONS)} 산업로 {rng.randint(10, 400)}",
                                      "erp_code": f"M{rng.randint(100000, 999999)}", "status": "활성",
                                      "memo": "제조업 샘플 데이터"})
        except ValueError:
            continue
        out["customers"] += 1
        for i, (role, dept, title) in enumerate(rng.sample(CONTACT_ROLES, rng.randint(2, 4))):
            person = rng.choice(PERSON) + rng.choice(GIVEN)
            contacts.save(cid, {"name": person, "dept": dept, "title": title, "role": role,
                                "phone": f"010-{rng.randint(2000, 9999)}-{rng.randint(1000, 9999)}",
                                "email": f"{role[:2].encode().hex()[:4]}{rng.randint(10, 99)}@{base.encode().hex()[:6]}.co.kr",
                                "is_primary": i == 0})
            out["contacts"] += 1

        # 매달 반복 구매 (부품·원자재·소모품)
        regular = rng.sample(["RM-SUS304", "RM-AL6061", "RM-SS400", "PT-BRG6205", "PT-SENS-PX", "CS-OIL-20", "CS-TOOL",
                              "SV-MACH"], 3)
        for m in range(months, 0, -1):
            if rng.random() < 0.25:
                continue
            first = (today.replace(day=1) - timedelta(days=30 * m)).replace(day=1)
            day = _safe_day(first + timedelta(days=rng.randint(0, 25)))
            if not day:
                out["skipped_closed"] += 1
                continue
            code = rng.choice(regular)
            price = catalog.price_for(cid, pids[code], day)
            qty = rng.randint(2, 40) if price["unit_price"] < 1_000_000 else rng.randint(1, 6)
            sid = db.upsert_sale({"customer_id": cid, "sale_date": day, "item": price["name"], "item_code": code,
                                  "product_id": pids[code], "qty": qty, "unit_price": price["unit_price"],
                                  "tax_type": price["tax_type"], "owner_id": rep["id"], "memo": "정기 구매"})
            out["sales"] += 1
            sale = db.get_sale(sid)
            total = int(sale["total_amount"])
            due = sale["due_date"]
            roll = rng.random()
            pay_day = min(date.fromisoformat(due) + timedelta(days=rng.randint(-10, 20)), today).isoformat()
            if due < today.isoformat() and roll < 0.75 and not periods_blocked(pay_day):
                amount = total if roll < 0.62 else int(round(total * rng.uniform(0.3, 0.8), -3))
                ent.record_payment(sid, amount, pay_date=max(pay_day, day), method=rng.choice(["계좌이체", "계좌이체", "어음"]))
                out["payments"] += 1
            if roll > 0.97 and qty > 2 and _safe_day(today):
                returns.create(sid, "반품", "입고 검사 불량", qty=rng.randint(1, max(1, qty // 4)))
                out["returns"] += 1

        # 영업기회 · 견적 · 수주 · 분할 납품
        title, codes = rng.choice(DEAL_TITLES)
        stage = rng.choice(["제안", "견적", "협상", "수주"])
        close = (today + timedelta(days=rng.randint(-20, 90))).isoformat()
        did = db.upsert_deal({"customer_id": cid, "title": f"{title} ({base})", "stage": "협상" if stage == "수주" else stage,
                              "owner_id": rep["id"], "list_amount": 0, "amount": 0, "expected_close": close,
                              "source": rng.choice(db.LEAD_SOURCES), "competitor": rng.choice(["", "A사", "B사", "해외 OEM"]),
                              **{f: 1 for f in db.MEDDIC_FIELDS}}, force=True, force_reason="제조업 샘플")
        out["deals"] += 1
        q_day = _safe_day(today - timedelta(days=rng.randint(5, 40)))
        if q_day:
            items = [{"product_id": pids[c], "qty": (rng.randint(1, 2) if PRODUCTS[[p[0] for p in PRODUCTS].index(c)][4]
                                                    in ("대", "식", "벌") else rng.randint(20, 200))} for c in codes]
            qid = qt.save_quote({"customer_id": cid, "deal_id": did, "title": title, "issue_date": q_day}, items)
            out["quotes"] += 1
            if stage == "수주":
                q = qt.get_quote(qid)
                with db.get_conn() as conn:          # 시연 데이터: 결재·발송 과정 없이 수락 상태로
                    conn.execute("UPDATE quotes SET status='수락', decided_at=? WHERE id=?", (db._now(), qid))
                oid = orders.from_quote(qid, {"delivery_date": (today + timedelta(days=30)).isoformat(),
                                              "customer_po": f"PO-{rng.randint(10000, 99999)}"})
                out["orders"] += 1
                o = orders.get(oid)
                first_lot = {int(it["id"]): max(1, int(it["qty"]) // 2) for it in o["items"]}
                if _safe_day(today):
                    out["sales"] += len(orders.deliver(oid, first_lot, today.isoformat()))
                assert q
        for _ in range(rng.randint(2, 5)):
            a_day = _safe_day(today - timedelta(days=rng.randint(0, 120)))
            if not a_day:
                continue
            db.add_activity({"customer_id": cid, "deal_id": did, "act_date": a_day, "act_type": rng.choice(db.ACT_TYPES),
                             "owner_id": rep["id"], "summary": rng.choice(ACTIVITIES),
                             "next_action": rng.choice(["견적 수정 발송", "샘플 재제출", "현장 재방문", ""]) or None,
                             "next_date": (today + timedelta(days=rng.randint(1, 14))).isoformat()})
            out["activities"] += 1
    db.set_context("system", None)
    db.audit("샘플데이터", "시스템", None, {"종류": "제조업", **out})
    return out


def periods_blocked(day: str) -> bool:
    from . import periods
    return bool(periods.date_problem(day))

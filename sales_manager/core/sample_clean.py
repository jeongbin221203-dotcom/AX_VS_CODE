"""기본 샘플 데이터 — 처음 화면에 보이는 데이터를 깔끔하게: 적은 수량(1~5), 큰 금액(수백만~수억), 딱 떨어지는 숫자.

  거래처 12곳 · 영업기회 20건 · 견적/수주/분할 납품 · 매출 약 70건 · 입금(전액·절반) · 반품·단가 정정 각 1건 · 선수금 1건 ·
  활동 40건 · 월 목표(담당자별 10백만 원 단위).  난수 대신 정해 둔 표로 만들어 매번 같은 이름·금액이 나온다(날짜만 오늘 기준).

  복잡하고 다양한 데이터(업종별·시나리오별)는 관리자 > 데이터 관리 > '추가 데이터' 에서 따로 더한다 (sample_industry · sample_complex).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from . import sales_db as db

TAG = "기본 샘플"

# (이름, 업종, 등급, 여신한도, 결제조건, 지역, 사업자번호 앞 9자리, 담당자)
CUSTOMERS = [
    ("(주)한빛정밀", "제조", "A", 300_000_000, 30, "경기 화성시 동탄산단로 120", "214-81-1234", "김영업"),
    ("(주)대성전자", "제조", "VIP", 500_000_000, 45, "경기 평택시 진위산단로 55", "129-86-2345", "이수주"),
    ("삼정물산", "유통", "A", 300_000_000, 30, "서울 강남구 테헤란로 211", "110-81-3456", "박고객"),
    ("(주)미래로지스", "유통", "B", 200_000_000, 45, "인천 중구 항동 7가 12", "120-87-4567", "최성과"),
    ("(주)세아건설", "건설", "VIP", 800_000_000, 60, "서울 영등포구 여의대로 108", "107-86-5678", "김영업"),
    ("청림엔지니어링", "건설", "B", 200_000_000, 60, "대전 유성구 테크노로 77", "305-81-6789", "이수주"),
    ("(주)우진소프트", "IT/SW", "A", 300_000_000, 30, "서울 금천구 가산디지털로 134", "119-86-7890", "박고객"),
    ("코어시스템즈", "IT/SW", "B", 150_000_000, 30, "경기 성남시 판교역로 235", "220-81-8901", "최성과"),
    ("한솔메디칼", "의료", "A", 300_000_000, 45, "서울 송파구 올림픽로 300", "113-86-9012", "김영업"),
    ("정우교육재단", "교육", "B", 100_000_000, 30, "서울 마포구 월드컵북로 396", "104-82-0123", "이수주"),
    ("제일금융서비스", "금융", "VIP", 500_000_000, 30, "서울 중구 세종대로 55", "101-81-1357", "박고객"),
    ("가온시청", "공공", "A", 500_000_000, 45, "세종특별자치시 한누리대로 2130", "131-83-2468", "최성과"),
]

# 거래처별로 사 가는 품목 (품목코드, 수량 후보) — 수량은 1~5, 가격은 품목 마스터 정가(큰 금액)
BASKET = {
    "(주)한빛정밀": [("HW-SRV-01", (1, 2, 3)), ("SV-MNT-01", (2, 3)), ("HW-PRT-01", (2, 4))],
    "(주)대성전자": [("SW-ERP-01", (1, 2)), ("SV-DEV-01", (1,)), ("SV-MNT-01", (3, 5))],
    "삼정물산": [("SW-CLD-01", (3, 5)), ("SV-CON-01", (3, 5)), ("HW-PRT-01", (1, 3))],
    "(주)미래로지스": [("HW-SRV-01", (1, 2)), ("SV-MNT-01", (2, 3)), ("SV-TRN-01", (1, 2))],
    "(주)세아건설": [("SV-DEV-01", (1, 2)), ("SW-ERP-01", (1, 2, 3)), ("SV-CON-01", (5,))],
    "청림엔지니어링": [("SV-CON-01", (3, 5)), ("SV-TRN-01", (1, 2)), ("HW-PRT-01", (2,))],
    "(주)우진소프트": [("SW-CLD-01", (3, 5)), ("SV-DEV-01", (1, 2)), ("SV-MNT-01", (2, 3))],
    "코어시스템즈": [("SW-CLD-01", (2, 3)), ("SV-CON-01", (2, 3)), ("SV-TRN-01", (1,))],
    "한솔메디칼": [("HW-SRV-01", (1, 2)), ("SV-MNT-01", (3, 5)), ("SV-EDU-01", (2, 4))],
    "정우교육재단": [("SV-EDU-01", (3, 5)), ("SV-TRN-01", (1, 2)), ("SW-CLD-01", (2, 3))],
    "제일금융서비스": [("SW-ERP-01", (1, 2)), ("SV-CON-01", (5,)), ("SV-MNT-01", (3, 5))],
    "가온시청": [("SV-DEV-01", (1, 2)), ("HW-SRV-01", (2, 3)), ("SV-MNT-01", (3, 5))],
}

# 영업기회: (거래처, 제목, 단계, [(품목코드, 수량)], 할인율 %)
DEALS = [
    ("(주)한빛정밀", "생산관리 서버 증설", "수주", [("HW-SRV-01", 2)], 0),
    ("(주)대성전자", "ERP 라이선스 갱신", "수주", [("SW-ERP-01", 2)], 5),
    ("(주)세아건설", "현장관리 시스템 구축", "수주", [("SV-DEV-01", 2), ("SV-CON-01", 5)], 5),
    ("삼정물산", "클라우드 구독 전환", "협상", [("SW-CLD-01", 5)], 8),
    ("(주)우진소프트", "맞춤형 개발 2차", "협상", [("SV-DEV-01", 1)], 10),
    ("한솔메디칼", "병원 정보 서버 교체", "협상", [("HW-SRV-01", 2), ("SV-MNT-01", 5)], 5),
    ("(주)미래로지스", "물류 서버 이중화", "견적", [("HW-SRV-01", 2)], 0),
    ("제일금융서비스", "ERP 고도화", "견적", [("SW-ERP-01", 2), ("SV-CON-01", 5)], 12),
    ("가온시청", "정보시스템 유지관리", "견적", [("SV-MNT-01", 5)], 0),
    ("(주)대성전자", "공정 데이터 개발", "제안", [("SV-DEV-01", 2)], 15),
    ("청림엔지니어링", "현장 교육 프로그램", "제안", [("SV-TRN-01", 2)], 0),
    ("코어시스템즈", "클라우드 컨설팅", "제안", [("SW-CLD-01", 3), ("SV-CON-01", 3)], 5),
    ("정우교육재단", "교직원 교육 과정", "접촉", [("SV-EDU-01", 5)], 0),
    ("(주)세아건설", "본사 ERP 도입", "접촉", [("SW-ERP-01", 3)], 18),
    ("제일금융서비스", "보안 컨설팅", "리드", [("SV-CON-01", 5)], 0),
    ("가온시청", "서버 교체 사업", "리드", [("HW-SRV-01", 3)], 0),
    ("삼정물산", "물류 부품 공급", "실주", [("HW-PRT-01", 3)], 0),
    ("청림엔지니어링", "설계 자동화 개발", "실주", [("SV-DEV-01", 1)], 10),
]

ACTIVITIES = ["담당자 미팅 — 요구사항 확인", "제품 데모 시연", "견적서 발송 및 설명", "예산·일정 협의", "계약 조건 협의",
              "경쟁사 비교 자료 전달", "도입 일정 조율", "현장 방문 — 환경 점검", "기술 검토 회의", "검수 일정 확인"]
NEXT = ["수정 견적 발송", "재방문 일정 조율", "기술 검토 회신", "계약서 초안 전달"]


OWNER_OF = {c[0]: c[7] for c in CUSTOMERS}


def _day(offset: int) -> Optional[str]:
    from .sample_industry import _safe_day
    return _safe_day(date.today() + timedelta(days=offset))


def seed(rnd_seed: int = 7) -> dict:
    """깔끔한 기본 샘플을 만든다. 이미 있는 거래처·품목은 건너뛰므로 두 번 눌러도 안전하다."""
    import random
    from . import advances, catalog, contacts, orders, returns
    from . import enterprise as ent
    from . import quotes as qt
    rng = random.Random(rnd_seed)
    today = date.today()
    db.set_context("system", None)
    reps = {n: ent.get_user(name=n) for n in ("김영업", "이수주", "박고객", "최성과")}
    if not all(reps.values()):
        raise ValueError("담당자 계정이 없습니다. '샘플 조직·계정 생성'을 먼저 실행하세요.")
    out = {"products": catalog.seed_products(), "customers": 0, "contacts": 0, "deals": 0, "quotes": 0, "orders": 0,
           "sales": 0, "payments": 0, "returns": 0, "advances": 0, "activities": 0, "targets": 0}
    prod = {r["code"]: r for r in db._df("SELECT id, code, name, list_price, tax_type FROM products").to_dict("records")}

    if db._scalar("SELECT COUNT(*) FROM customers WHERE name IN (%s)" % ",".join("?" * len(CUSTOMERS)),
                  [c[0] for c in CUSTOMERS]) == len(CUSTOMERS):
        return {"skipped": "이미 기본 샘플이 있습니다."}
    cust_id: dict[str, int] = {}
    for n, (name, industry, grade, limit, terms, addr, biz, owner) in enumerate(CUSTOMERS):
        existing = db._one("SELECT id FROM customers WHERE name=?", [name])
        if existing:
            cust_id[name] = int(existing["id"])
            continue
        cust_id[name] = db.upsert_customer({
            "name": name, "biz_no": db._with_check_digit(biz + "0000"[:5 - len(biz.split("-")[-1])] + "00000"),
            "industry": industry, "grade": grade, "owner_id": reps[owner]["id"], "credit_limit": limit,
            "payment_terms": terms, "address": addr, "status": "활성", "memo": TAG,
            "erp_code": f"C{100100 + n * 10}"})
        out["customers"] += 1
        for i, (role, dept, title) in enumerate([("구매", "구매팀", "과장"), ("의사결정", "경영지원팀", "이사")]):
            contacts.save(cust_id[name], {"name": f"{'김이박최정강조윤'[(n + i) % 8]}{['민수', '서연', '지훈', '수진'][(n + i) % 4]}",
                                          "dept": dept, "title": title, "role": role,
                                          "phone": f"010-{2000 + n * 37:04d}-{1000 + i * 111 + n:04d}",
                                          "email": f"contact{i + 1}@c{n + 1:02d}.example.kr", "is_primary": i == 0})
            out["contacts"] += 1
    for name in ("(주)대성전자", "(주)세아건설", "제일금융서비스"):          # 핵심 거래처는 큰 품목에 특가 10%
        for code in ("SW-ERP-01", "SV-DEV-01"):
            p = prod[code]
            catalog.set_customer_price(cust_id[name], int(p["id"]), int(p["list_price"] * 0.9),
                                       (today - timedelta(days=365)).isoformat())

    # ── 매출·입금: 거래처마다 최근 9개월에 5~6건 (수량 1~5, 정가 그대로) ──────────────────
    sales_by_owner: dict[str, int] = {}
    unpaid_old = {"청림엔지니어링", "정우교육재단"}                                # 연체 미수를 보여 줄 거래처
    for n, (name, _i, _g, _l, terms, _a, _b, owner) in enumerate(CUSTOMERS):
        if db._one("SELECT id FROM sales WHERE customer_id=? LIMIT 1", [cust_id[name]]):
            continue
        basket = BASKET[name]
        months_ago = sorted(rng.sample(range(0, 9), 6), reverse=True)
        for k, m in enumerate(months_ago):
            day = _day(-(m * 30 + rng.randint(2, 20)))
            if not day or day > today.isoformat():
                continue
            code, qtys = basket[k % len(basket)]
            p = prod[code]
            qty = rng.choice(qtys)
            if name in ("(주)대성전자", "(주)세아건설", "제일금융서비스") and code in ("SW-ERP-01", "SV-DEV-01"):
                unit = int(p["list_price"] * 0.9)
            else:
                unit = int(p["list_price"])
            sid = db.upsert_sale({"customer_id": cust_id[name], "sale_date": day, "item": p["name"], "item_code": code,
                                  "product_id": int(p["id"]), "qty": qty, "unit_price": unit, "tax_type": p["tax_type"],
                                  "owner_id": reps[owner]["id"], "memo": ""})
            out["sales"] += 1
            sales_by_owner[owner] = sales_by_owner.get(owner, 0) + qty * unit
            sale = db.get_sale(sid)
            total, due = int(sale["total_amount"]), sale["due_date"]
            overdue_days = (today - date.fromisoformat(due)).days
            if overdue_days < 0:
                continue                                                           # 결제기일 전 — 아직 미수
            if name in unpaid_old and overdue_days > 20:
                if k == len(months_ago) - 3:                                       # 절반만 입금된 건 하나
                    half = total // 2 // 10_000 * 10_000
                    ent.record_payment(sid, half, pay_date=min(today, date.fromisoformat(due) + timedelta(days=12)).isoformat(),
                                       method="계좌이체")
                    out["payments"] += 1
                continue                                                           # 나머지는 연체로 남김
            pay_day = min(today, date.fromisoformat(due) + timedelta(days=rng.choice((0, 3, 5, 7))))
            if pay_day.isoformat() >= day:
                ent.record_payment(sid, total, pay_date=pay_day.isoformat(), method="계좌이체")
                out["payments"] += 1

    db.set_context("system", None)
    # ── 영업기회 · 견적 · 수주 · 분할 납품 ────────────────────────────────────────
    won_done = 0
    for n, (cname, title, stage, lines, discount) in enumerate(DEALS):
        cid = cust_id[cname]
        owner_id = reps[OWNER_OF[cname]]["id"]
        ent.apply_context(ent.get_user(user_id=owner_id))
        list_amount = sum(int(prod[c]["list_price"]) * q for c, q in lines)
        amount = list_amount * (100 - discount) // 100 // 10_000 * 10_000
        open_stage = stage if stage not in ("수주", "실주") else db.OPEN_STAGES[4]
        close = (today + timedelta(days=15 + n * 4)).isoformat()
        did = db.upsert_deal({"customer_id": cid, "title": title, "stage": open_stage, "owner_id": owner_id,
                              "list_amount": list_amount, "amount": amount, "discount_rate": discount,
                              "expected_close": close, "source": db.LEAD_SOURCES[n % 6],
                              "forecast_category": {"리드": "Pipeline", "접촉": "Pipeline", "제안": "Pipeline",
                                                    "견적": "Best Case", "협상": "Commit"}.get(stage, "Pipeline"),
                              **{f: 1 for f in db.MEDDIC_FIELDS}}, force=True, force_reason="기본 샘플")
        out["deals"] += 1
        if stage in ("견적", "협상", "수주", "실주"):
            q_day = _day(-(70 + n) if stage in ("수주", "실주") else -(4 + n))
            if q_day:
                items = [{"product_id": int(prod[c]["id"]), "qty": q,
                          "unit_price": int(prod[c]["list_price"]) * (100 - discount) // 100 // 10_000 * 10_000}
                         for c, q in lines]
                qid = qt.save_quote({"customer_id": cid, "deal_id": did, "title": title, "issue_date": q_day,
                                     "owner_id": owner_id}, items)
                out["quotes"] += 1
                if stage == "수주":
                    order_day = date.fromisoformat(q_day) + timedelta(days=6)
                    with db.get_conn() as conn:
                        conn.execute("UPDATE quotes SET status='수락', decided_at=? WHERE id=?",
                                     (f"{order_day.isoformat()} 10:00:00", qid))
                    oid = orders.from_quote(qid, {"delivery_date": (order_day + timedelta(days=45)).isoformat(),
                                                  "customer_po": f"PO-2026-{1000 + n}"})
                    with db.get_conn() as conn:
                        conn.execute("UPDATE sales_orders SET order_date=? WHERE id=?", (order_day.isoformat(), oid))
                        conn.execute("UPDATE deals SET stage='수주', probability=100, closed_at=?, stage_since=? WHERE id=?",
                                     (f"{order_day.isoformat()} 10:00:00", order_day.isoformat(), did))
                    out["orders"] += 1
                    o = orders.get(oid)
                    first = {int(it["id"]): max(1, int(it["qty"]) // 2) for it in o["items"]}        # 1차: 절반
                    d1 = _day(min((order_day - today).days + 14, -1))
                    if d1:
                        for sid in orders.deliver(oid, first, d1):
                            out["sales"] += 1
                            sale = db.get_sale(sid)
                            if date.fromisoformat(sale["due_date"]) < today:
                                ent.record_payment(sid, int(sale["total_amount"]), method="계좌이체",
                                                   pay_date=min(today, date.fromisoformat(sale["due_date"]) + timedelta(days=3)).isoformat())
                                out["payments"] += 1
                        won_done += 1
                        if won_done == 1:                                                   # 첫 수주는 2차(잔량) 납품도 끝
                            rest = {int(it["id"]): int(it["remain"]) for it in orders.get(oid)["items"] if int(it["remain"]) > 0}
                            d2 = _day(min((date.fromisoformat(d1) - today).days + 35, -1))
                            if rest and d2:
                                for sid in orders.deliver(oid, rest, d2):
                                    out["sales"] += 1
                elif stage == "실주":
                    lost = date.fromisoformat(q_day) + timedelta(days=12)
                    with db.get_conn() as conn:
                        conn.execute("UPDATE quotes SET status='거절', decided_at=? WHERE id=?", (f"{lost.isoformat()} 10:00:00", qid))
                        conn.execute("UPDATE deals SET stage='실주', probability=0, closed_at=?, stage_since=?, lost_reason=? WHERE id=?",
                                     (f"{lost.isoformat()} 10:00:00", lost.isoformat(), db.LOST_REASONS[n % 3], did))
        if discount > 10 and stage in ("제안", "접촉", "견적"):        # 할인 결재 대기 (팀장 10% / 임원 20%)
            try:
                ent.request_approval(did, ent.get_user(user_id=owner_id), f"{cname} 대형 건 — 경쟁 입찰 대응")
            except ValueError:
                pass
        for j in range(2):
            a_day = _day(-(3 + j * 9 + n * 2))
            if a_day:
                db.add_activity({"customer_id": cid, "deal_id": did, "act_date": a_day, "act_type": db.ACT_TYPES[(n + j) % 4],
                                 "owner_id": owner_id, "summary": ACTIVITIES[(n + j * 3) % len(ACTIVITIES)],
                                 "next_action": NEXT[(n + j) % len(NEXT)] if j == 0 else None,
                                 "next_date": (today + timedelta(days=3 + n)).isoformat() if j == 0 else None})
                out["activities"] += 1

    # ── 반품 · 단가 정정 · 선수금 각 1건 (원장·채권 화면에서 바로 볼 수 있게) ─────────────
    db.set_context("system", None)
    big = db._df("SELECT id, qty, unit_price FROM sales WHERE sale_kind IS NULL OR sale_kind='매출' "
                 "AND qty >= 3 ORDER BY id LIMIT 4").to_dict("records")
    try:
        if big:
            returns.create(int(big[0]["id"]), "반품", "납품 후 사양 변경으로 1건 반품", qty=1, day=_day(-5))
            out["returns"] += 1
        if len(big) > 1:
            returns.create(int(big[1]["id"]), "정정", "계약 단가 협의 인하",
                           new_unit_price=int(big[1]["unit_price"]) * 95 // 100 // 10_000 * 10_000, day=_day(-8))
            out["returns"] += 1
        advances.add(cust_id["(주)세아건설"], 30_000_000, "입금", _day(-6), method="계좌이체", memo="신규 현장 계약금")
        out["advances"] += 1
    except ValueError:
        pass

    # ── 월 목표: 담당자별 평균 월 매출을 10백만 원 단위로 올림 ────────────────────────────
    for owner, total in sales_by_owner.items():
        monthly = max(total // 9, 1)
        target = -(-int(monthly * 1.1) // 10_000_000) * 10_000_000
        for i in range(12):
            ym = (today.replace(day=1) - timedelta(days=30 * i)).strftime("%Y-%m")
            db.upsert_target(ym, reps[owner]["id"], target)
            out["targets"] += 1
    db.audit("샘플데이터", "시스템", None, {"종류": "기본 샘플(깔끔)", **out})
    return out

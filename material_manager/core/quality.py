"""데이터 점검 (🩺): 업무 데이터에서 고쳐야 할 곳을 찾아 '고치러 가기' 링크와 함께 보여 준다. 읽기만 한다.

core/doctor.py 가 서버·연결(인프라)을 본다면, 여기는 데이터 품질을 본다:
거래처 중복·사업자번호 오류, 음수 재고, 발주보다 많이 받은 입고, BOM 문제(사용중지 부품·리드타임 없음), 단가 없는 재고,
유효기한 지난 재고, 오래 기다린 결재, 완료 예정이 지난 작업지시, 미등록 거래처 이름, 알림을 못 받는 결재자.
"""
from __future__ import annotations

from datetime import date

import config
from core import db, partners

SEVERITY = {"high": "높음", "mid": "보통", "low": "낮음"}


LIMIT = {"n": 30}


def _check(key, title, severity, rows, fix_hint="") -> dict:
    shown = rows if LIMIT["n"] is None else rows[:LIMIT["n"]]
    return {"key": key, "title": title, "severity": severity, "count": len(rows), "rows": shown, "hint": fix_hint}


def run(wh_ids=None, limit: int | None = 30) -> list[dict]:
    """limit: 항목마다 보여 줄 줄 수 (None = 전부, 엑셀 내려받기)."""
    LIMIT["n"] = limit
    frag, wp = db.in_clause(wh_ids)
    wh_and = (lambda col: f" AND {col}{frag}") if frag else (lambda col: "")
    out = []
    today = date.today().isoformat()

    # 1) 음수 재고 (있을 수 없음 — 마감 전 데이터 손상·직접 수정 의심)
    rows = db.query_df(f"""
        SELECT m.id, m.code, m.name, w.code AS wh, t.lot_no, {db.STOCK_EXPR} AS stock
        FROM transactions t JOIN materials m ON m.id = t.material_id JOIN warehouses w ON w.id = t.warehouse_id
        WHERE 1 = 1{wh_and('t.warehouse_id')} GROUP BY m.id, m.code, m.name, w.code, t.lot_no HAVING {db.STOCK_EXPR} < -0.000001""", wp)
    out.append(_check("negative", "음수 재고", "high",
                      [(f"[{r.code}] {r.name} · {r.wh}{' · ' + r.lot_no if r.lot_no else ''} · {r.stock:,.2f}",
                        f"/history/?material={r.id}&start=2000-01-01") for r in rows.itertuples()],
                      "거래 이력에서 거래를 확인하고 실사조정·취소로 바로잡습니다."))

    # 2) 발주보다 많이 받은 입고
    rows = db.query_df(f"""
        SELECT o.id, o.po_no, i.line_no, m.code, i.qty,
               COALESCE((SELECT SUM(t.qty) FROM transactions t WHERE t.po_no = o.po_no AND t.po_item = CAST(i.line_no AS TEXT)
                         AND t.tx_type = 'IN'), 0) AS received
        FROM po_items i JOIN purchase_orders o ON o.id = i.po_id JOIN materials m ON m.id = i.material_id
        WHERE o.status <> 'CANCELLED'{wh_and('o.warehouse_id')}""", wp)
    rows = rows[rows["received"] > rows["qty"] + 1e-6] if len(rows) else rows
    out.append(_check("over_receipt", "발주보다 많이 받은 입고", "high",
                      [(f"{r.po_no} / {r.line_no} · {r.code} 발주 {r.qty:,.4g} · 입고 {r.received:,.4g}", f"/purchase/po/{r.id}")
                       for r in rows.itertuples()], "초과분 입고를 취소하거나 발주를 고칩니다."))

    # 3) 거래처: 같은 사업자번호 · 검증번호 오류
    dup = db.query_df("SELECT biz_no, COUNT(*) AS n, MIN(name) AS a, MAX(name) AS b FROM partners WHERE active = 1 AND biz_no <> '' "
                      "GROUP BY biz_no HAVING COUNT(*) > 1")
    out.append(_check("partner_dup", "사업자번호가 같은 거래처 (중복 의심)", "mid",
                      [(f"{partners.biz_fmt(r.biz_no)} · {r.a} / {r.b} ({r.n}곳)", "/partners/?tab=merge") for r in dup.itertuples()],
                      "같은 회사면 '중복 · 병합'에서 하나로 합칩니다."))
    bad = [r for r in db.query_df("SELECT id, code, name, biz_no FROM partners WHERE active = 1 AND biz_no <> ''").itertuples()
           if not partners.biz_no_ok(r.biz_no)]
    out.append(_check("partner_biz", "사업자번호 검증번호 오류", "mid",
                      [(f"[{r.code}] {r.name} · {partners.biz_fmt(r.biz_no)}", f"/partners/{r.id}") for r in bad]))
    unknown = partners.unknown_names()
    out.append(_check("partner_unknown", "거래처 마스터에 없는 이름", "low",
                      [(f"{r.name} · {r.uses}번", "/partners/?tab=unknown") for r in unknown.itertuples()],
                      "등록하거나 기존 거래처의 다른 이름으로 연결합니다."))

    # 4) BOM 문제
    rows = db.query_df("""
        SELECT p.id AS pid, p.code AS pcode, c.code AS ccode, c.active AS cactive, c.lead_time_days AS lead, p.active AS pactive
        FROM boms b JOIN bom_items i ON i.bom_id = b.id JOIN materials p ON p.id = b.product_id JOIN materials c ON c.id = i.component_id
        WHERE b.active = 1""")
    inactive = [r for r in rows.itertuples() if not r.cactive or not r.pactive]
    out.append(_check("bom_inactive", "사용중지 자재가 든 BOM", "high",
                      [(f"{r.pcode} ← {r.ccode}{' (제품 중지)' if not r.pactive else ' (부품 중지)'}", f"/production/bom?product={r.pid}")
                       for r in inactive], "BOM에서 대체 부품으로 바꾸거나 BOM 사용을 멈춥니다."))
    no_lead = rows[(rows["lead"].fillna(0) == 0)].drop_duplicates("ccode") if len(rows) else rows
    out.append(_check("no_lead", "리드타임이 0인 BOM 부품 (MRP가 당일 발주로 계산)", "low",
                      [(f"{r.ccode} (← {r.pcode})", f"/materials/?tab=edit&id={_mid(r.ccode)}") for r in no_lead.itertuples()],
                      "자재 마스터에서 리드타임(일)을 넣습니다."))

    # 5) 재고는 있는데 기준단가가 0 (재고 금액·원가가 0으로 계산됨)
    rows = db.query_df(f"""
        SELECT m.id, m.code, m.name, {db.STOCK_EXPR} AS stock FROM materials m JOIN transactions t ON t.material_id = m.id
        WHERE m.active = 1 AND COALESCE(m.unit_price, 0) = 0{wh_and('t.warehouse_id')}
        GROUP BY m.id, m.code, m.name HAVING {db.STOCK_EXPR} > 0.000001""", wp)
    out.append(_check("no_price", "재고는 있는데 기준단가 0", "mid",
                      [(f"[{r.code}] {r.name} · 재고 {r.stock:,.4g}", f"/materials/?tab=edit&id={r.id}") for r in rows.itertuples()]))

    # 6) 유효기한 지난 재고
    rows = db.query_df(f"""
        SELECT m.code, m.name, w.code AS wh, l.lot_no, l.expiry_date, {db.STOCK_EXPR} AS stock
        FROM transactions t JOIN lots l ON l.material_id = t.material_id AND l.lot_no = t.lot_no
        JOIN materials m ON m.id = t.material_id JOIN warehouses w ON w.id = t.warehouse_id
        WHERE l.expiry_date <> '' AND l.expiry_date < ?{wh_and('t.warehouse_id')}
        GROUP BY m.code, m.name, w.code, l.lot_no, l.expiry_date HAVING {db.STOCK_EXPR} > 0.000001""", (today, *wp))
    out.append(_check("expired", "유효기한 지난 재고", "mid",
                      [(f"[{r.code}] {r.name} · {r.wh} · 로트 {r.lot_no} ({r.expiry_date}) · {r.stock:,.4g}", "/stock/?view=lot")
                       for r in rows.itertuples()], "폐기는 실사조정으로 0을 넣습니다."))

    # 7) 오래 기다린 결재 · 완료 예정 지난 작업지시
    sla = config.APPROVAL_SLA_HOURS or 24
    old = db.query_df("SELECT 'PR' AS k, id, pr_no AS no, requested_at AS at FROM purchase_requests WHERE status = 'PENDING' "
                      "UNION ALL SELECT 'ADJ', id, '#' || id, requested_at FROM approval_requests WHERE status = 'PENDING'")
    from datetime import datetime
    late = [r for r in old.itertuples()
            if (datetime.now() - datetime.fromisoformat(str(r.at)[:19])).total_seconds() > sla * 3600]
    out.append(_check("approval_late", f"결재 기한({sla}시간)을 넘긴 결재", "mid",
                      [(f"{'구매요청' if r.k == 'PR' else '실사 조정'} {r.no} · {str(r.at)[:16]}",
                        f"/purchase/pr/{r.id}" if r.k == "PR" else "/approvals/") for r in late]))
    rows = db.query_df(f"SELECT p.id, p.prod_no, m.code, p.due_date FROM productions p JOIN materials m ON m.id = p.product_id "
                       f"WHERE p.status IN ('PLANNED', 'RELEASED') AND p.due_date <> '' AND p.due_date < ?{wh_and('p.issue_wh_id')}",
                       (today, *wp))
    out.append(_check("wo_late", "완료 예정일이 지난 작업지시", "mid",
                      [(f"{r.prod_no} · {r.code} · 예정 {r.due_date}", f"/production/{r.id}") for r in rows.itertuples()]))

    # 8) 알림을 못 받는 결재자 (메일·메신저 아이디 없음 — 알림함에는 남음)
    rows = db.query_df("SELECT id, name, username, role FROM users WHERE active = 1 AND role IN ('MANAGER', 'ADMIN') "
                       "AND email = '' AND messenger_id = ''")
    out.append(_check("no_contact", "메일·메신저가 없는 결재자", "low",
                      [(f"{r.name} ({r.username})", "/admin/users") for r in rows.itertuples()],
                      "사용자 화면에서 알림 메일이나 메신저 아이디를 넣습니다."))
    order = {"high": 0, "mid": 1, "low": 2}
    return sorted(out, key=lambda c: (c["count"] == 0, order[c["severity"]]))


def _mid(code: str) -> int:
    return int(db.scalar("SELECT id FROM materials WHERE code = ?", (code,)) or 0)

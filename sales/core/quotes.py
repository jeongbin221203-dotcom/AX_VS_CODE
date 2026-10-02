"""견적 — 작성 · 개정 · 발송 · 수락/거절 · 매출 전환 · PDF

  상태: 작성중 → 발송 → 수락 / 거절   (유효기간이 지난 발송 건은 '만료'로 본다)
        수정은 '작성중' 에서만. 발송 뒤 조건을 바꾸려면 개정(Rev.2 …)한다 → 이전 판은 '대체됨'
  영업기회와 연결된 견적을 발송하면 정가·제안가·할인율을 영업기회에 반영한다.
  그 할인율에 결재가 필요한데 승인이 없으면 발송을 막는다(Deal Desk 통제를 견적에도 적용).
  수락된 견적은 품목별 매출로 한 번만 전환할 수 있다.
"""
from __future__ import annotations

import io
import os
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

from . import catalog
from . import sales_db as db

QUOTE_STATUS = ["작성중", "발송", "수락", "거절", "만료", "대체됨"]
def valid_days() -> int:
    from . import company
    return int(company.get("quote_valid_days"))


# ---------------------------------------------------------------------------
# 금액 계산
# ---------------------------------------------------------------------------
def compute(items: list[dict], customer_id: Optional[int], on: Optional[str] = None) -> tuple[list[dict], dict]:
    """품목 줄을 정리하고 합계를 계산한다. 단가를 비우면 거래처 특가 → 정가 순으로 채운다."""
    lines: list[dict] = []
    for raw in items:
        pid = int(raw["product_id"]) if str(raw.get("product_id") or "").isdigit() else None
        qty = int(raw.get("qty") or 0)
        if not pid and not str(raw.get("item_name") or "").strip():
            continue
        if qty <= 0:
            raise ValueError("수량은 1 이상이어야 합니다.")
        if pid:
            price = catalog.price_for(customer_id, pid, on)
            list_price = price["list_price"]
            unit_price = int(raw["unit_price"]) if str(raw.get("unit_price") or "").strip() else price["unit_price"]
            name, code, unit, tax = price["name"], price["code"], price["unit"], price["tax_type"]
            if price.get("spec"):
                name = f"{name} ({price['spec']})"
        else:
            unit_price = int(raw.get("unit_price") or 0)
            list_price = int(raw.get("list_price") or unit_price)
            name, code = str(raw["item_name"]).strip(), (raw.get("item_code") or None)
            unit, tax = raw.get("unit") or "EA", raw.get("tax_type") or "과세"
        if unit_price < 0 or list_price < 0:
            raise ValueError("단가는 0 이상이어야 합니다.")
        supply = qty * unit_price
        lines.append({
            "product_id": pid, "item_code": code, "item_name": name, "unit": unit, "qty": qty,
            "list_price": list_price, "unit_price": unit_price, "tax_type": tax,
            "discount_rate": round((1 - unit_price / list_price) * 100, 2) if list_price else 0.0,
            "supply_amount": supply, "vat_amount": db.vat_for(supply, tax),
        })
    if not lines:
        raise ValueError("견적 품목을 한 줄 이상 입력하세요.")
    list_total = sum(line["qty"] * line["list_price"] for line in lines)
    supply = sum(line["supply_amount"] for line in lines)
    vat = sum(line["vat_amount"] for line in lines)
    totals = {"list_total": list_total, "supply_amount": supply, "vat_amount": vat, "total_amount": supply + vat,
              "discount_rate": round((1 - supply / list_total) * 100, 2) if list_total else 0.0}
    return lines, totals


def _next_no() -> str:
    year = date.today().year
    last = db._one("SELECT quote_no FROM quotes WHERE quote_no LIKE ? ORDER BY quote_no DESC LIMIT 1",
                   [f"Q-{year}-%"])
    seq = int(last["quote_no"].rsplit("-", 1)[1]) + 1 if last else 1
    return f"Q-{year}-{seq:04d}"


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------
def effective_status(q: dict) -> str:
    if q["status"] == "발송" and q.get("valid_until") and q["valid_until"] < date.today().isoformat():
        return "만료"
    return q["status"]


def list_quotes(status: str = "", customer_id: Optional[int] = None, owner_id: Optional[int] = None,
                include_superseded: bool = False) -> pd.DataFrame:
    sql = ("SELECT q.id, q.quote_no AS 견적번호, q.revision AS 판, c.name AS 거래처, q.title AS 건명, "
           "q.issue_date AS 작성일, q.valid_until AS 유효기한, q.status AS 상태, q.supply_amount AS 공급가액, "
           "q.vat_amount AS 부가세, q.total_amount AS 합계, q.discount_rate AS 할인율, q.owner AS 담당자, "
           "d.title AS 영업기회, q.owner_id, q.customer_id FROM quotes q JOIN customers c ON c.id = q.customer_id "
           "LEFT JOIN deals d ON d.id = q.deal_id WHERE 1=1")
    params: list[Any] = []
    if not include_superseded:
        sql += " AND q.status <> '대체됨'"
    if status and status != "만료":
        sql += " AND q.status = ?"
        params.append(status)
    if customer_id:
        sql += " AND q.customer_id = ?"
        params.append(int(customer_id))
    if owner_id:
        sql += " AND q.owner_id = ?"
        params.append(int(owner_id))
    sc, sp = db._scope_clause("q")
    df = db._df(sql + sc + " ORDER BY q.id DESC", params + sp)
    if not df.empty:
        today = date.today().isoformat()
        expired = (df["상태"] == "발송") & df["유효기한"].fillna("9999").lt(today)
        df.loc[expired, "상태"] = "만료"
        if status == "만료":
            df = df[df["상태"] == "만료"]
    return df


def get_quote(quote_id: int) -> dict:
    q = db._one("SELECT q.*, c.name AS customer_name, c.biz_no AS customer_biz_no, c.address AS customer_address, "
                "c.manager AS customer_manager FROM quotes q JOIN customers c ON c.id = q.customer_id WHERE q.id=?",
                [quote_id])
    db.check_record_scope(q, "견적")
    q["items"] = db._df("SELECT * FROM quote_items WHERE quote_id=? ORDER BY line_no", [quote_id]).to_dict("records")
    q["effective_status"] = effective_status(q)
    q["revisions"] = db._df("SELECT id, revision, status, updated_at FROM quotes WHERE quote_no=? ORDER BY revision",
                            [q["quote_no"]]).to_dict("records")
    q["sales"] = db._df("SELECT id, sale_date, item, amount, total_amount, status FROM sales WHERE quote_id=?",
                        [quote_id]).to_dict("records")
    return q


# ---------------------------------------------------------------------------
# 작성 · 수정 · 개정
# ---------------------------------------------------------------------------
def _write_items(conn, quote_id: int, lines: list[dict]) -> None:
    conn.execute("DELETE FROM quote_items WHERE quote_id=?", (quote_id,))
    for n, line in enumerate(lines, start=1):
        conn.execute("INSERT INTO quote_items (quote_id, line_no, product_id, item_code, item_name, unit, qty, "
                     "list_price, unit_price, discount_rate, tax_type, supply_amount, vat_amount) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (quote_id, n, line["product_id"], line["item_code"], line["item_name"], line["unit"],
                      line["qty"], line["list_price"], line["unit_price"], line["discount_rate"],
                      line["tax_type"], line["supply_amount"], line["vat_amount"]))


def save_quote(data: dict, items: list[dict]) -> int:
    """새 견적 또는 '작성중' 견적 수정. data['row_version'] 으로 동시 수정 충돌을 막는다."""
    cust = db.get_customer(int(data.get("customer_id") or 0))
    db.check_record_scope(cust, "거래처")
    owner_id, owner_name = db.resolve_owner(data.get("owner_id") or db.current_actor_id())
    deal_id = int(data["deal_id"]) if str(data.get("deal_id") or "").isdigit() else None
    if deal_id:
        deal = db.get_deal(deal_id)
        db.check_record_scope(deal, "영업기회")
        if int(deal["customer_id"]) != int(cust["id"]):
            raise ValueError("영업기회의 거래처와 견적 거래처가 다릅니다.")
    issue = db._d(data.get("issue_date")) or date.today().isoformat()
    valid = db._d(data.get("valid_until")) or (date.fromisoformat(issue) + timedelta(days=valid_days())).isoformat()
    lines, totals = compute(items, int(cust["id"]), issue)
    from . import entities as ent_mod
    currency = str(data.get("currency") or "KRW").upper()
    _x, fx_rate = ent_mod.to_krw(currency, 1, data.get("fx_rate"), issue)
    header = {"customer_id": int(cust["id"]), "deal_id": deal_id, "owner": owner_name, "owner_id": owner_id,
              "entity_id": ent_mod.resolve(data.get("entity_id")), "currency": currency, "fx_rate": fx_rate,
              "foreign_amount": round(totals["total_amount"] / fx_rate, 2) if currency != "KRW" else None,
              "title": (data.get("title") or "").strip() or None, "issue_date": issue, "valid_until": valid,
              "terms": data.get("terms") or f"결제조건: 세금계산서 발행 후 {cust.get('payment_terms') or 30}일",
              "memo": data.get("memo"), **totals}
    cols = list(header)
    with db.get_conn() as conn:
        if data.get("id"):
            prev = get_quote(int(data["id"]))
            if prev["status"] != "작성중":
                raise ValueError("발송한 견적은 수정할 수 없습니다. '개정' 으로 새 판을 만드세요.")
            sql = (f"UPDATE quotes SET {', '.join(f'{c}=?' for c in cols)}, updated_at=?, "
                   f"row_version=row_version+1 WHERE id=?")
            params: list = [*header.values(), db._now(), prev["id"]]
            if data.get("row_version") not in (None, ""):
                sql += " AND row_version=?"
                params.append(int(data["row_version"]))
            if conn.execute(sql, params).rowcount == 0:
                raise db.ConflictError("다른 사용자가 먼저 이 견적을 수정했습니다. 새로고침 후 다시 저장하세요.")
            qid = int(prev["id"])
        else:
            cur = conn.execute(
                f"INSERT INTO quotes (quote_no, revision, status, {', '.join(cols)}, created_at, updated_at) "
                f"VALUES (?, 1, '작성중', {', '.join('?' * len(cols))}, ?, ?)",
                (_next_no(), *header.values(), db._now(), db._now()))
            qid = int(cur.lastrowid)
        _write_items(conn, qid, lines)
    db.audit("수정" if data.get("id") else "등록", "견적", qid,
             {"거래처": cust["name"], "공급가액": totals["supply_amount"], "합계": totals["total_amount"],
              "할인율": totals["discount_rate"], "품목수": len(lines)})
    return qid


def revise(quote_id: int) -> int:
    """발송·거절·만료된 견적을 고칠 때: 같은 번호의 다음 판을 '작성중' 으로 만들고 이전 판은 '대체됨'."""
    q = get_quote(quote_id)
    if q["status"] in ("작성중", "대체됨", "수락"):
        raise ValueError(f"'{q['status']}' 상태의 견적은 개정할 수 없습니다.")
    cols = ["quote_no", "customer_id", "deal_id", "owner", "owner_id", "title", "terms", "memo", "list_total",
            "supply_amount", "vat_amount", "total_amount", "discount_rate", "entity_id", "currency", "fx_rate",
            "foreign_amount"]
    today = date.today().isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO quotes ({', '.join(cols)}, revision, parent_id, status, issue_date, valid_until, "
            f"created_at, updated_at) VALUES ({', '.join('?' * len(cols))}, ?, ?, '작성중', ?, ?, ?, ?)",
            (*[q[c] for c in cols], int(q["revision"]) + 1, q["id"], today,
             (date.today() + timedelta(days=valid_days())).isoformat(), db._now(), db._now()))
        new_id = int(cur.lastrowid)
        _write_items(conn, new_id, q["items"])
        conn.execute("UPDATE quotes SET status='대체됨', updated_at=? WHERE id=?", (db._now(), q["id"]))
    db.audit("개정", "견적", new_id, {"견적번호": q["quote_no"], "판": int(q["revision"]) + 1})
    return new_id


# ---------------------------------------------------------------------------
# 발송 · 결정 · 매출 전환
# ---------------------------------------------------------------------------
def _sync_deal(q: dict) -> dict:
    """견적 조건(정가 합계·공급가액·할인율)을 영업기회에 반영한다. 바뀌면 기존 할인 승인은 무효가 된다."""
    deal = db.get_deal(int(q["deal_id"]))
    new = {"list_amount": int(q["list_total"]), "amount": int(q["supply_amount"]),
           "discount_rate": float(q["discount_rate"])}
    if db.diff(deal, new, db.COMMERCIAL_FIELDS):
        data = {**deal, **new, "row_version": deal.get("row_version")}
        db.upsert_deal(data)
    return db.get_deal(int(q["deal_id"]))


def send(quote_id: int) -> None:
    q = get_quote(quote_id)
    if q["status"] != "작성중":
        raise ValueError(f"'{q['status']}' 상태의 견적은 발송할 수 없습니다.")
    if (db.get_customer(int(q["customer_id"])) or {}).get("trade_blocked"):
        raise ValueError("거래정지 거래처에는 견적을 발송할 수 없습니다.")
    if q["deal_id"]:
        deal = _sync_deal(q)
        role = db.required_approval_role(deal.get("discount_rate"))
        if role and not (deal.get("approval_status") == "승인" and db.approval_covers(deal)):
            raise ValueError(f"할인 {float(deal['discount_rate']):.1f}% 는 {db.ROLE_LABEL[role]} 결재가 필요합니다. "
                             f"견적 조건을 영업기회에 반영했으니, 영업기회에서 할인 결재를 받은 뒤 발송하세요.")
    with db.get_conn() as conn:
        conn.execute("UPDATE quotes SET status='발송', sent_at=?, updated_at=? WHERE id=?",
                     (db._now(), db._now(), quote_id))
    db.audit("발송", "견적", quote_id, {"견적번호": q["quote_no"], "판": q["revision"], "합계": q["total_amount"]})


def decide(quote_id: int, accepted: bool, reason: str = "") -> None:
    q = get_quote(quote_id)
    if q["effective_status"] != "발송":
        raise ValueError(f"'{q['effective_status']}' 상태의 견적은 수락·거절할 수 없습니다.")
    if not accepted and not reason.strip():
        raise ValueError("거절 사유를 입력하세요.")
    status = "수락" if accepted else "거절"
    with db.get_conn() as conn:
        conn.execute("UPDATE quotes SET status=?, decided_at=?, memo=COALESCE(memo,'') || ?, updated_at=? WHERE id=?",
                     (status, db._now(), f"\n[{status}] {reason}".rstrip() if reason else "", db._now(), quote_id))
    db.audit(status, "견적", quote_id, {"견적번호": q["quote_no"], "사유": reason or None})


def convert_to_sales(quote_id: int, sale_date: Optional[str] = None) -> list[int]:
    """수락된 견적을 품목별 매출로 등록한다 (한 견적은 한 번만)."""
    q = get_quote(quote_id)
    if q["status"] != "수락":
        raise ValueError("수락된 견적만 매출로 전환할 수 있습니다.")
    if q["sales"]:
        raise ValueError("이미 매출로 전환한 견적입니다.")
    ids = []
    for line in q["items"]:
        ids.append(db.upsert_sale({
            "customer_id": q["customer_id"], "deal_id": q["deal_id"], "sale_date": sale_date,
            "item": line["item_name"], "item_code": line["item_code"], "product_id": line["product_id"],
            "qty": line["qty"], "unit_price": line["unit_price"], "amount": line["supply_amount"],
            "tax_type": line["tax_type"], "owner_id": q["owner_id"], "quote_id": quote_id,
            "entity_id": q.get("entity_id"), "currency": q.get("currency") or "KRW", "fx_rate": q.get("fx_rate") or 1,
            "memo": f"견적 {q['quote_no']} Rev.{q['revision']}"}))
    db.audit("매출전환", "견적", quote_id, {"견적번호": q["quote_no"], "매출": ids})
    return ids


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
FONT_DIR = os.path.join(db.BASE_DIR, "static", "fonts")


def _fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "NanumGothic" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("NanumGothic", os.path.join(FONT_DIR, "NanumGothic-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("NanumGothic-Bold", os.path.join(FONT_DIR, "NanumGothic-Bold.ttf")))
    return "NanumGothic", "NanumGothic-Bold"


def pdf(quote_id: int) -> bytes:
    """견적서 PDF (한글 글꼴 포함 — 어떤 PC 에서 열어도 깨지지 않는다)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    q = get_quote(quote_id)
    regular, bold = _fonts()
    body = ParagraphStyle("b", fontName=regular, fontSize=9.5, leading=14)
    title = ParagraphStyle("t", fontName=bold, fontSize=22, leading=28, alignment=1, spaceAfter=6)
    small = ParagraphStyle("s", fontName=regular, fontSize=8.5, leading=12, textColor=colors.HexColor("#555555"))
    from . import entities as ent_mod
    sup = ent_mod.info(q.get("entity_id"))
    company = {"상호": sup["name"] or "(관리자 > 회사 설정에서 회사명 입력)",
               "사업자번호": sup["biz_no"], "대표": sup["ceo"], "주소": sup["address"]}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"견적서 {q['quote_no']}", author=company["상호"])
    head = Table([
        [Paragraph(f"견적번호 <b>{q['quote_no']}</b> (Rev.{q['revision']})", body),
         Paragraph(f"공급자 <b>{company['상호']}</b>", body)],
        [Paragraph(f"작성일 {q['issue_date']} · 유효기한 {q['valid_until'] or '-'}", body),
         Paragraph(f"사업자번호 {company['사업자번호']} · 대표 {company['대표']}", body)],
        [Paragraph(f"수신 <b>{q['customer_name']}</b> 귀하", body), Paragraph(company["주소"], body)],
        [Paragraph(f"건명 {q.get('title') or '-'}", body), Paragraph(f"담당 {q['owner']}", body)],
    ], colWidths=[90 * mm, 88 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    rows = [["No", "품목", "단위", "수량", "정가", "단가", "공급가액", "세액", "과세"]]
    for n, line in enumerate(q["items"], start=1):
        rows.append([n, Paragraph(f"{line['item_name']}<br/><font size=7.5 color='#777777'>{line['item_code'] or ''}</font>",
                                  body), line["unit"], f"{int(line['qty']):,}", f"{int(line['list_price']):,}",
                     f"{int(line['unit_price']):,}", f"{int(line['supply_amount']):,}", f"{int(line['vat_amount']):,}",
                     line["tax_type"]])
    rows += [["", "정가 합계", "", "", "", "", f"{int(q['list_total']):,}", "", ""],
             ["", f"할인율 {float(q['discount_rate']):.1f}%", "", "", "", "공급가액", f"{int(q['supply_amount']):,}", "", ""],
             ["", "", "", "", "", "부가세", "", f"{int(q['vat_amount']):,}", ""],
             ["", "", "", "", "", "합계", f"{int(q['total_amount']):,}", "", ""]]
    table = Table(rows, colWidths=[9 * mm, 58 * mm, 11 * mm, 13 * mm, 20 * mm, 20 * mm, 22 * mm, 16 * mm, 11 * mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), regular), ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2A3C")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (3, 1), (7, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, len(q["items"])), 0.4, colors.HexColor("#BBBBBB")),
        ("LINEABOVE", (0, len(q["items"]) + 1), (-1, len(q["items"]) + 1), 0.8, colors.black),
        ("FONTNAME", (5, -1), (6, -1), bold), ("FONTSIZE", (5, -1), (6, -1), 10.5),
    ]))
    story = [Paragraph("견 적 서", title), head, Spacer(1, 6 * mm), table, Spacer(1, 6 * mm),
             Paragraph(f"거래조건: {q.get('terms') or '-'}", body)]
    if (q.get("currency") or "KRW") != "KRW":
        story.append(Paragraph(f"외화 합계: {q['currency']} {float(q.get('foreign_amount') or 0):,.2f} "
                               f"(환율 {float(q.get('fx_rate') or 1):,.4f}원 적용, 원화 금액이 기준)", body))
    if q.get("memo"):
        story.append(Paragraph(f"비고: {q['memo']}".replace("\n", "<br/>"), body))
    story += [Spacer(1, 8 * mm), Paragraph("위와 같이 견적합니다. 부가세는 과세 품목에 한해 공급가액의 10%(원 미만 절사)입니다.", small)]
    doc.build(story)
    return buf.getvalue()

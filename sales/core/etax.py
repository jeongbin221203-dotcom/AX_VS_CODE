"""전자세금계산서 발행 — 국세청 표준 XML 을 만들어 발행 대행(ASP)에 보내고, 승인번호를 받아 매출 증빙으로 남긴다.

  흐름: 매출 화면 '발행 요청' → 검증(사업자번호·과세구분·중복) → 작업 큐(etax.issue) → ASP 전송
        → 승인번호 회신 → 표준 XML(승인번호 포함)을 매출 증빙(전자세금계산서)으로 자동 등록 → 증빙 검증 그대로 적용
  ASP 가 멈춰도 요청은 큐에 남아 다시 시도한다. 같은 매출은 진행 중인 발행이 있으면 다시 요청할 수 없다.

  연결 방식 (SALES_ETAX_ADAPTER)
    none  : 발행 기능을 쓰지 않음 (기본)
    file  : SALES_ETAX_OUT_DIR 에 XML 을 쓰면 ASP 에이전트가 가져가 발행하고, 승인번호는 /api/v1/etax/acks 로 회신
    rest  : SALES_ETAX_REST_URL 로 XML 을 보내고 응답의 승인번호(SALES_ETAX_APPROVAL_PATH, 기본 approval_no)를 받는다
            (Bearer SALES_ETAX_REST_TOKEN, 재전송 중복 방지 Idempotency-Key)
    mock  : 개발·시연용 — 가짜 승인번호를 바로 돌려준다 (운영에서는 기동 거부)
  실제 ASP(국세청 연계 사업자) 계약과 공동인증서는 회사가 준비한다. 이 코드는 실제 ASP 와 연결해 확인하지 않았다.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from . import documents as docs
from . import sales_db as db

NS = "urn:kr:or:kec:standard:Tax:ReusableAggregateBusinessInformationEntitySchemaModule:1:0"
ACTIVE = ("발행요청", "전송중", "발행완료")


def adapter_name() -> str:
    return os.environ.get("SALES_ETAX_ADAPTER", "none")


def enabled() -> bool:
    return adapter_name() in ("file", "rest", "mock")


MODIFY_CODES = {"반품": "03", "정정": "02"}      # 수정사유: 03 환입(반품), 02 공급가액 변동


def build_xml(sale: dict, customer: dict, supplier: dict, issue_date: str, approval_no: str = "",
              original_approval: str = "") -> bytes:
    """국세청 전자세금계산서 표준(KEC) 구조의 XML. 금액은 원화. 반품·정정 행이면 수정세금계산서(수정사유·당초 승인번호)."""
    kind = sale.get("sale_kind") or "매출"
    modify = MODIFY_CODES.get(kind)
    type_code = ("0201" if modify else "0101") if (sale.get("tax_type") or "과세") == "과세" else         ("0202" if modify else "0102")                                  # 일반/영세율 · 수정이면 02xx
    d = issue_date.replace("-", "")
    item = escape(str(sale["item"]))[:100]

    def party(tag: str, p: dict) -> str:
        return (f"<{tag}><ID>{docs.digits(p.get('biz_no'))}</ID><NameText>{escape(str(p.get('name') or ''))}</NameText>"
                f"<SpecifiedPerson><NameText>{escape(str(p.get('ceo') or ''))}</NameText></SpecifiedPerson>"
                f"<SpecifiedAddress><LineOneText>{escape(str(p.get('address') or ''))}</LineOneText></SpecifiedAddress>"
                f"</{tag}>")

    body = (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<TaxInvoice xmlns="{NS}">'
            f"<ExchangedDocument><IssueDateTime>{d}{datetime.now():%H%M%S}</IssueDateTime></ExchangedDocument>"
            f"<TaxInvoiceDocument><IssueID>{approval_no}</IssueID><TypeCode>{type_code}</TypeCode>"
            f"<IssueDateTime>{d}</IssueDateTime><PurposeCode>02</PurposeCode>"
            + (f"<AmendmentStatusCode>{modify}</AmendmentStatusCode><OriginalIssueID>{original_approval}</OriginalIssueID>"
               if modify else "") + "</TaxInvoiceDocument>"
            f"<TaxInvoiceTradeSettlement>"
            f"{party('InvoicerParty', supplier)}"
            f"{party('InvoiceeParty', {'biz_no': customer.get('biz_no'), 'name': customer.get('name'), 'ceo': customer.get('manager'), 'address': customer.get('address')})}"
            f"<SpecifiedMonetarySummation><ChargeTotalAmount>{int(sale['amount'])}</ChargeTotalAmount>"
            f"<TaxTotalAmount>{int(sale.get('vat_amount') or 0)}</TaxTotalAmount>"
            f"<GrandTotalAmount>{int(sale.get('total_amount') or sale['amount'])}</GrandTotalAmount>"
            f"</SpecifiedMonetarySummation></TaxInvoiceTradeSettlement>"
            f"<TaxInvoiceTradeLineItem><SequenceNumeric>1</SequenceNumeric>"
            f"<PurchaseExpiryDateTime>{d}</PurchaseExpiryDateTime><NameText>{item}</NameText>"
            f"<ChargeableUnitQuantity>{int(sale['qty'])}</ChargeableUnitQuantity>"
            f"<UnitPrice><UnitAmount>{int(sale['unit_price'])}</UnitAmount></UnitPrice>"
            f"<InvoiceAmount>{int(sale['amount'])}</InvoiceAmount>"
            f"<TotalTax><CalculatedAmount>{int(sale.get('vat_amount') or 0)}</CalculatedAmount></TotalTax>"
            f"</TaxInvoiceTradeLineItem></TaxInvoice>")
    return body.encode("utf-8")


def _context(sale_id: int) -> tuple[dict, dict, dict]:
    from . import entities as ent_mod
    sale = db.get_sale(int(sale_id))
    db.check_record_scope(sale, "매출")
    customer = db.get_customer(int(sale["customer_id"])) or {}
    supplier = ent_mod.info(sale.get("entity_id"))
    return sale, customer, supplier


def check(sale: dict, customer: dict, supplier: dict) -> list[str]:
    problems = []
    if sale["status"] == db.SALE_CANCELLED:
        problems.append("취소된 매출입니다.")
    if (sale.get("tax_type") or "과세") == "면세":
        problems.append("면세 매출은 세금계산서가 아니라 계산서 발행 대상입니다.")
    if not docs.valid_biz_no(supplier.get("biz_no")):
        problems.append("공급자(우리 회사·법인) 사업자번호가 없거나 올바르지 않습니다 — 회사 설정·법인에서 입력하세요.")
    if not docs.valid_biz_no(customer.get("biz_no")):
        problems.append("거래처 사업자번호가 없거나 올바르지 않습니다 — 거래처 화면에서 입력하세요.")
    if not supplier.get("name"):
        problems.append("공급자 상호가 없습니다.")
    if (sale.get("sale_kind") or "매출") != "매출":
        from .returns import original_approval_no
        if not original_approval_no(int(sale["original_sale_id"])):
            problems.append("원매출의 전자세금계산서 승인번호가 없습니다 — 수정세금계산서는 당초 승인번호가 있어야 발행됩니다.")
    return problems


def request_issue(sale_id: int, issue_date: Optional[str], actor: dict) -> int:
    if not enabled():
        raise ValueError("전자세금계산서 발행 연결이 설정되지 않았습니다 (SALES_ETAX_ADAPTER).")
    sale, customer, supplier = _context(sale_id)
    problems = check(sale, customer, supplier)
    if problems:
        raise ValueError(" / ".join(problems))
    day = db._d(issue_date) or date.today().isoformat()
    deadline = docs.issue_deadline(sale["sale_date"])
    if datetime.strptime(day, "%Y-%m-%d").date() > deadline:
        raise ValueError(f"발급 기한({deadline:%Y-%m-%d})이 지났습니다. 지연발급은 세무 담당과 확인한 뒤 ASP 에서 직접 처리하세요.")
    from . import jobs
    with db.get_conn() as conn:
        active = conn.execute("SELECT COUNT(*) FROM etax_invoices WHERE sale_id=? AND status IN ('발행요청','전송중','발행완료')",
                              (sale["id"],)).fetchone()[0]
        if active:
            raise ValueError("이미 발행했거나 발행 중인 매출입니다.")
        cur = conn.execute("INSERT INTO etax_invoices (sale_id, status, issue_date, requested_by, requested_at) "
                           "VALUES (?, '발행요청', ?, ?, ?)", (sale["id"], day, actor.get("name"), db._now()))
        eid = int(cur.lastrowid)
        jobs.enqueue("etax.issue", {"id": eid}, dedupe_key=f"etax-{eid}", conn=conn)
    db.audit("세금계산서발행요청", "매출", int(sale["id"]), {"요청번호": eid, "작성일자": day})
    return eid


def _send(eid: int, xml: bytes, row: dict) -> Optional[str]:
    """ASP 로 보낸다. 승인번호를 바로 받으면 돌려주고, 파일 방식처럼 나중에 회신되면 None."""
    kind = adapter_name()
    if kind == "mock":
        return f"{row['issue_date'].replace('-', '')}41000000{secrets.randbelow(10**8):08d}"
    if kind == "file":
        folder = Path(os.environ.get("SALES_ETAX_OUT_DIR") or os.path.join(db.BASE_DIR, "data", "etax", "outbound"))
        folder.mkdir(parents=True, exist_ok=True)
        tmp, final = folder / f".ETAX_{eid}.tmp", folder / f"ETAX_{eid}_CRM-{row['sale_id']}.xml"
        tmp.write_bytes(xml)
        tmp.replace(final)
        return None
    if kind == "rest":
        url = os.environ.get("SALES_ETAX_REST_URL", "")
        if not url:
            raise RuntimeError("SALES_ETAX_REST_URL 이 없습니다.")
        payload = json.dumps({"crm_ref": f"CRM-{row['sale_id']}", "xml": base64.b64encode(xml).decode()}).encode()
        req = urllib.request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Authorization": f"Bearer {os.environ.get('SALES_ETAX_REST_TOKEN', '')}",
            "Idempotency-Key": f"CRM-ETAX-{eid}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as res:          # noqa: S310 - 관리자가 설정한 주소
                body = json.loads(res.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ASP 응답 {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ASP 연결 실패: {exc.reason}") from exc
        from .erp import _dig
        number = _dig(body, os.environ.get("SALES_ETAX_APPROVAL_PATH", "approval_no"))
        if not number:
            raise RuntimeError(f"ASP 응답에 승인번호가 없습니다: {str(body)[:200]}")
        return str(number)
    raise RuntimeError("전자세금계산서 발행 연결이 설정되지 않았습니다.")


def process(eid: int) -> dict:
    """작업 큐에서 호출. 실패하면 예외 → 큐가 간격을 두고 다시 시도한다."""
    row = db._one("SELECT * FROM etax_invoices WHERE id=?", [int(eid)])
    if not row or row["status"] != "발행요청":
        return {"skipped": True}
    with db.get_conn() as conn:          # 선점: 같은 요청을 두 워커가 동시에 보내지 않게
        if conn.execute("UPDATE etax_invoices SET status='전송중' WHERE id=? AND status='발행요청'", (eid,)).rowcount == 0:
            return {"skipped": True}
    db.set_context("system", None)
    sale, customer, supplier = _context(row["sale_id"])
    xml = build_xml(sale, customer, supplier, row["issue_date"], original_approval=_original(sale))
    try:
        number = _send(int(eid), xml, row)
    except Exception as exc:
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET status='발행요청', error=? WHERE id=?", (str(exc)[:500], eid))
        raise
    if number is None:                   # 파일 방식: 승인번호 회신을 기다린다
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET error=NULL WHERE id=?", (eid,))
        return {"sent": True, "waiting_ack": True}
    return complete(int(eid), number)


def complete(eid: int, approval_no: str, ok: bool = True, message: str = "") -> dict:
    """승인번호 확정(또는 ASP 거부) — 발행된 XML 을 매출 증빙으로 등록한다."""
    row = db._one("SELECT * FROM etax_invoices WHERE id=?", [int(eid)])
    if not row:
        raise ValueError("발행 요청을 찾을 수 없습니다.")
    if row["status"] == "발행완료":
        return {"id": eid, "status": "발행완료", "approval_no": row["approval_no"]}
    if not ok:
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET status='실패', error=? WHERE id=?", (message[:500] or "ASP 거부", eid))
        db.audit("세금계산서발행실패", "매출", int(row["sale_id"]), {"요청번호": eid, "사유": message})
        return {"id": eid, "status": "실패"}
    number = docs.digits(approval_no)
    if len(number) != 24:
        raise ValueError("승인번호는 24자리 숫자여야 합니다.")
    db.set_context("system", None)
    sale, customer, supplier = _context(row["sale_id"])
    xml = build_xml(sale, customer, supplier, row["issue_date"], number, _original(sale))
    doc_type = "수정세금계산서" if (sale.get("sale_kind") or "매출") != "매출" else "전자세금계산서"
    doc_id, warnings = docs.add_document(int(row["sale_id"]), {"doc_type": doc_type}, xml,
                                         f"전자세금계산서_{number}.xml", {"name": "전자세금계산서 발행", "id": None})
    key = docs.get_document(doc_id)["file_path"]
    with db.get_conn() as conn:
        conn.execute("UPDATE etax_invoices SET status='발행완료', approval_no=?, document_id=?, xml_key=?, error=NULL, "
                     "issued_at=? WHERE id=?", (number, doc_id, key, db._now(), eid))
    db.audit("세금계산서발행", "매출", int(row["sale_id"]), {"요청번호": eid, "승인번호": number, "증빙": doc_id})
    return {"id": eid, "status": "발행완료", "approval_no": number, "document_id": doc_id, "warnings": warnings}


def _original(sale: dict) -> str:
    if (sale.get("sale_kind") or "매출") == "매출" or not sale.get("original_sale_id"):
        return ""
    from .returns import original_approval_no
    return original_approval_no(int(sale["original_sale_id"])) or ""


def list_for_sale(sale_id: int) -> list[dict]:
    return db._df("SELECT * FROM etax_invoices WHERE sale_id=? ORDER BY id DESC", [int(sale_id)]).to_dict("records")

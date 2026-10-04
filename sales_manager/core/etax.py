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
from datetime import date, datetime, timedelta
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


MODIFY_CODES = {"반품": "03", "정정": "02"}      # 수정사유: 03 환입(반품), 02 공급가액 변동 (반품·정정 매출 행으로)
# 당초분을 통째로 음수로 지우는 수정세금계산서 (부가가치세법 시행령 제70조) — 매출 화면의 발행 기록에서 요청
MODIFY_REASONS = {"01": "기재사항 착오·정정", "04": "계약의 해제", "05": "내국신용장 사후개설", "06": "착오에 의한 이중발급"}
MODIFY_RULES = {
    "01": "작성일자 = 당초 작성일. 당초분 음수 발행 뒤 바로잡은 내용으로 다시 발행하세요.",
    "04": "작성일자 = 계약 해제일. 발행된 뒤 매출을 취소할 수 있습니다.",
    "05": "작성일자 = 당초 작성일. 내국신용장은 공급한 과세기간 끝난 뒤 25일 안에 개설된 것이어야 하고, 음수 발행 뒤 영세율로 다시 발행합니다.",
    "06": "작성일자 = 당초 작성일. 이중으로 발급된 한 장을 음수로 지웁니다.",
}


def build_xml(sale: dict, customer: dict, supplier: dict, issue_date: str, approval_no: str = "",
              original_approval: str = "") -> bytes:
    """국세청 전자세금계산서 표준(KEC) 구조의 XML. 금액은 원화. 반품·정정 행이면 수정세금계산서(수정사유·당초 승인번호)."""
    kind = sale.get("sale_kind") or "매출"
    modify = sale.get("_modify_code") or MODIFY_CODES.get(kind)
    tax_type = sale.get("tax_type") or "과세"
    # 종류 코드: 세금계산서 01xx(수정 02xx) 일반 x1 · 영세율 x2 / 계산서(면세) 0301(수정 0401)
    if tax_type == "면세":
        type_code = "0401" if modify else "0301"
        sale = {**sale, "vat_amount": 0, "total_amount": int(sale.get("amount") or 0)}
    else:
        type_code = ("0201" if modify else "0101") if tax_type == "과세" else ("0202" if modify else "0102")
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
    # 작성일자 = 공급시기(매출일, 반품·정정 행은 환입·변동일). 월합계 특례로 그 달 말일까지만 늦출 수 있다 (부가법 제34조)
    supply = datetime.strptime(sale["sale_date"][:10], "%Y-%m-%d").date()
    day = db._d(issue_date) or supply.isoformat()
    written = datetime.strptime(day, "%Y-%m-%d").date()
    month_end = (supply.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if not supply <= written <= month_end:
        raise ValueError(f"작성일자는 공급일({supply:%Y-%m-%d})부터 그 달 말일({month_end:%Y-%m-%d}) 사이여야 합니다 "
                         f"(공급시기 — 발급하는 날이 아닙니다).")
    deadline = docs.issue_deadline(sale["sale_date"])
    if date.today() > deadline:                 # 기한은 전송(발급)하는 날 기준
        raise ValueError(f"발급 기한({deadline:%Y-%m-%d})이 지났습니다. 지연발급은 세무 담당과 확인한 뒤 ASP 에서 직접 처리하세요.")
    from . import jobs
    with db.get_conn() as conn:
        active = conn.execute("SELECT COUNT(*) FROM etax_invoices WHERE sale_id=? AND modify_code IS NULL "
                              "AND status IN ('발행요청','전송중','발행완료')", (sale["id"],)).fetchone()[0]
        if active:
            raise ValueError("이미 발행했거나 발행 중인 매출입니다.")
        try:
            cur = conn.execute("INSERT INTO etax_invoices (sale_id, status, issue_date, requested_by, requested_at) "
                               "VALUES (?, '발행요청', ?, ?, ?)", (sale["id"], day, actor.get("name"), db._now()))
        except Exception as exc:                  # noqa: BLE001 - 동시에 두 번 요청 (ux_etax_active)
            if _unique_error(exc):
                raise ValueError("이미 발행했거나 발행 중인 매출입니다.") from exc
            raise
        eid = int(cur.lastrowid)
        jobs.enqueue("etax.issue", {"id": eid}, dedupe_key=f"etax-{eid}", conn=conn)
    db.audit("세금계산서발행요청", "매출", int(sale["id"]), {"요청번호": eid, "작성일자": day})
    return eid


def _quarter_end(day: date) -> date:
    month = ((day.month - 1) // 3 + 1) * 3
    return (date(day.year, month, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def modifiable(row: dict) -> bool:
    """이 발행 기록을 수정세금계산서(01·04·05·06)로 지울 수 있는지."""
    return bool(row.get("approval_no")) and not row.get("modify_code") and row.get("status") in ("발행완료", "실패")


def request_modify(eid: int, code: str, reason: str, actor: dict, day: Optional[str] = None) -> int:
    """당초 발행분을 음수로 지우는 수정세금계산서 요청. 금액은 당초 매출 그대로(부호만 반대)."""
    if not enabled():
        raise ValueError("전자세금계산서 발행 연결이 설정되지 않았습니다 (SALES_ETAX_ADAPTER).")
    if code not in MODIFY_REASONS:
        raise ValueError("수정 사유를 고르세요 (01·04·05·06). 반품은 03, 단가 변동은 02 — 매출의 반품·정정으로 하세요.")
    if not str(reason or "").strip():
        raise ValueError("수정 사유 설명을 입력하세요.")
    orig = db._one("SELECT * FROM etax_invoices WHERE id=?", [int(eid)])
    if not orig or not modifiable(orig):
        raise ValueError("승인번호가 있는 당초 발행만 수정세금계산서로 지울 수 있습니다.")
    if code != "06" and orig["status"] != "발행완료":
        raise ValueError("발행 완료된 세금계산서만 이 사유로 고칠 수 있습니다 (이중 발급분은 06).")
    sale, customer, supplier = _context(int(orig["sale_id"]))
    if (sale.get("sale_kind") or "매출") != "매출":
        raise ValueError("반품·정정 행의 수정세금계산서는 원매출에서 반대 방향 정정으로 처리하세요.")
    if db._one("SELECT id FROM etax_invoices WHERE original_etax_id=? AND status IN ('발행요청','전송중','발행완료')",
               [int(eid)]):
        raise ValueError("이 세금계산서는 이미 수정세금계산서를 발행(요청)했습니다.")
    original_day = datetime.strptime(str(orig["issue_date"])[:10], "%Y-%m-%d").date()
    if code == "04":
        written = datetime.strptime(db._d(day) or date.today().isoformat(), "%Y-%m-%d").date()
        if written < original_day or written > date.today():
            raise ValueError(f"계약 해제일은 당초 작성일({original_day})부터 오늘 사이여야 합니다.")
    else:
        written = original_day
    if code == "05":
        if (sale.get("tax_type") or "과세") != "과세":
            raise ValueError("내국신용장 사후개설은 과세로 발행한 매출을 영세율로 바꿀 때만 씁니다.")
        opened = datetime.strptime(db._d(day) or "", "%Y-%m-%d").date() if db._d(day) else None
        limit = _quarter_end(original_day) + timedelta(days=25)
        if not opened or not original_day <= opened <= limit:
            raise ValueError(f"내국신용장 개설일을 입력하세요 — 공급한 과세기간 끝난 뒤 25일({limit}) 안이어야 합니다.")
    from . import jobs
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO etax_invoices (sale_id, status, issue_date, requested_by, requested_at, modify_code, "
            "original_etax_id, supply_amount, vat_amount, note) VALUES (?, '발행요청', ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(orig["sale_id"]), written.isoformat(), actor.get("name"), db._now(), code, int(eid),
             -int(sale["amount"]), -int(sale.get("vat_amount") or 0), reason.strip()[:500]))
        new_id = int(cur.lastrowid)
        jobs.enqueue("etax.issue", {"id": new_id}, dedupe_key=f"etax-{new_id}", conn=conn)
    db.audit("수정세금계산서요청", "매출", int(orig["sale_id"]),
             {"요청번호": new_id, "당초": int(eid), "당초승인번호": orig["approval_no"], "사유코드": code,
              "사유": MODIFY_REASONS[code], "설명": reason.strip(), "작성일자": written.isoformat()})
    return new_id


def _xml_for(row: dict, sale: dict, customer: dict, supplier: dict, number: str = "") -> bytes:
    """발행 기록 하나의 XML — 수정분(01·04·05·06)이면 당초 금액의 음수와 당초 승인번호."""
    if row.get("modify_code"):
        orig = db._one("SELECT approval_no FROM etax_invoices WHERE id=?", [int(row["original_etax_id"])]) or {}
        supply, vat = int(row["supply_amount"] or 0), int(row["vat_amount"] or 0)
        neg = {**sale, "_modify_code": row["modify_code"], "amount": supply, "vat_amount": vat,
               "total_amount": supply + vat, "qty": -abs(int(sale["qty"]))}
        return build_xml(neg, customer, supplier, row["issue_date"], number, orig.get("approval_no") or "")
    return build_xml(sale, customer, supplier, row["issue_date"], number, _original(sale))


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


def _unique_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "unique" in text or "ux_etax_active" in text


def process(eid: int) -> dict:
    """작업 큐에서 호출. 실패하면 예외 → 큐가 간격을 두고 다시 시도한다."""
    row = db._one("SELECT * FROM etax_invoices WHERE id=?", [int(eid)])
    if not row or row["status"] != "발행요청":
        return {"skipped": True}
    with db.get_conn() as conn:          # 선점: 같은 요청을 두 워커가 동시에 보내지 않게
        if conn.execute("UPDATE etax_invoices SET status='전송중', claimed_at=?, sent_at=NULL "
                        "WHERE id=? AND status='발행요청'", (db._now(), eid)).rowcount == 0:
            return {"skipped": True}
    try:
        db.set_context("system", None)
        sale, customer, supplier = _context(row["sale_id"])
        if sale.get("status") == db.SALE_CANCELLED and not row.get("modify_code"):   # 요청 뒤 취소된 매출은 보내지 않는다
            with db.get_conn() as conn:
                conn.execute("UPDATE etax_invoices SET status='실패', error=? WHERE id=?", ("매출이 취소되어 발행하지 않음", eid))
            return {"skipped": True, "reason": "cancelled"}
        xml = _xml_for(row, sale, customer, supplier)
        number = _send(int(eid), xml, row)
    except Exception as exc:
        # 어디서 실패하든 '전송중' 으로 남기지 않는다 → 큐가 다시 시도 (REST 는 Idempotency-Key 로 두 번 발행되지 않음)
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET status='발행요청', claimed_at=NULL, error=? "
                         "WHERE id=? AND status='전송중'", (str(exc)[:500], eid))
        raise
    if number is None:                   # 파일 방식: 승인번호 회신을 기다린다
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET error=NULL, sent_at=? WHERE id=?", (db._now(), eid))
        return {"sent": True, "waiting_ack": True}
    return complete(int(eid), number)


def recover_stuck(minutes: int = 15) -> int:
    """전송을 시작했다가 서버가 꺼져 '전송중' 으로 멈춘 요청을 다시 발행요청으로 돌려 큐에 올린다.
    파일 방식에서 ASP 로 넘긴 뒤(sent_at) 회신을 기다리는 건은 정상이라 건드리지 않는다."""
    from datetime import timedelta

    from . import jobs
    limit = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db._df("SELECT id FROM etax_invoices WHERE status='전송중' AND sent_at IS NULL "
                  "AND (claimed_at IS NULL OR claimed_at < ?)", [limit])
    n = 0
    for eid in ([int(i) for i in rows["id"]] if not rows.empty else []):
        with db.get_conn() as conn:
            if conn.execute("UPDATE etax_invoices SET status='발행요청', claimed_at=NULL, "
                            "error='전송 도중 중단되어 다시 보냄' WHERE id=? AND status='전송중' AND sent_at IS NULL",
                            (eid,)).rowcount:
                jobs.enqueue("etax.issue", {"id": eid}, dedupe_key=f"etax-{eid}-r{secrets.token_hex(4)}", conn=conn)
                n += 1
    if n:
        db.audit("세금계산서전송복구", "매출", None, {"건수": n})
    return n


def complete(eid: int, approval_no: str, ok: bool = True, message: str = "") -> dict:
    """승인번호 확정(또는 ASP 거부) — 발행된 XML 을 매출 증빙으로 등록한다."""
    row = db._one("SELECT * FROM etax_invoices WHERE id=?", [int(eid)])
    if not row:
        raise ValueError("발행 요청을 찾을 수 없습니다.")
    if row["status"] == "발행완료" and row.get("document_id"):
        return {"id": eid, "status": "발행완료", "approval_no": row["approval_no"]}
    if not ok:
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET status='실패', claimed_at=NULL, error=? WHERE id=? AND status <> '발행완료'",
                         (message[:500] or "ASP 거부", eid))
        db.audit("세금계산서발행실패", "매출", int(row["sale_id"]), {"요청번호": eid, "사유": message})
        return {"id": eid, "status": "실패"}
    number = docs.digits(approval_no)
    if len(number) != 24:
        raise ValueError("승인번호는 24자리 숫자여야 합니다.")
    if row["status"] == "발행완료" and row.get("approval_no") and row["approval_no"] != number:
        raise ValueError(f"이미 승인번호 {row['approval_no']} 로 발행된 요청입니다.")
    if row["status"] != "발행완료":
        # 먼저 '발행완료' 를 차지한다 — 회신이 두 번 와도 증빙이 두 번 생기지 않게
        try:
            with db.get_conn() as conn:
                claimed = conn.execute("UPDATE etax_invoices SET status='발행완료', approval_no=?, issued_at=?, error=NULL "
                                       "WHERE id=? AND status <> '발행완료'", (number, db._now(), eid)).rowcount
        except Exception as exc:                  # noqa: BLE001
            if not _unique_error(exc):
                raise
            # 실패로 정리한 뒤 다시 요청해 새 발행이 진행 중인데 옛 요청의 승인이 늦게 왔다 → 국세청에는 두 건이 발행됨
            _alert(row, number, "같은 매출에 다른 발행 요청이 있는데 이 요청도 승인됨 — 이중 발행. "
                                "하나를 수정세금계산서(착오 발급)로 취소하세요.")
            with db.get_conn() as conn:
                conn.execute("UPDATE etax_invoices SET approval_no=?, error=? WHERE id=?",
                             (number, "이중 발행 — 수정세금계산서로 취소 필요", eid))
            return {"id": eid, "status": row["status"], "approval_no": number, "duplicate": True}
        if not claimed:                           # 그 사이 다른 회신이 먼저 차지해 증빙을 등록하는 중
            return {"id": eid, "status": "발행완료", "approval_no": number}
    db.set_context("system", None)
    sale, customer, supplier = _context(row["sale_id"])
    xml = _xml_for(row, sale, customer, supplier, number)
    modified = bool(row.get("modify_code")) or (sale.get("sale_kind") or "매출") != "매출"
    if (sale.get("tax_type") or "과세") == "면세":              # 면세 매출은 (전자)계산서
        doc_type = "수정계산서" if modified else "전자계산서"
    else:
        doc_type = "수정세금계산서" if modified else "전자세금계산서"
    try:
        doc_id, warnings = docs.add_document(int(row["sale_id"]), {"doc_type": doc_type}, xml,
                                             f"전자세금계산서_{number}.xml", {"name": "전자세금계산서 발행", "id": None})
    except Exception as exc:
        # 국세청에는 발행됐으므로 '발행완료' 는 유지하고, 증빙 등록만 다음 회신·재시도 때 다시 한다
        with db.get_conn() as conn:
            conn.execute("UPDATE etax_invoices SET error=? WHERE id=?", (f"증빙 등록 실패: {exc}"[:500], eid))
        raise
    key = docs.get_document(doc_id)["file_path"]
    with db.get_conn() as conn:
        conn.execute("UPDATE etax_invoices SET document_id=?, xml_key=?, error=NULL WHERE id=?", (doc_id, key, eid))
    db.audit("세금계산서발행", "매출", int(row["sale_id"]), {"요청번호": eid, "승인번호": number, "증빙": doc_id})
    if row.get("modify_code"):
        _close_original(row, number)
    elif sale.get("status") == db.SALE_CANCELLED:
        _alert(row, number, "취소된 매출에 전자세금계산서가 발행됨(늦은 승인) — 수정세금계산서(계약의 해제)를 발행하세요.")
    return {"id": eid, "status": "발행완료", "approval_no": number, "document_id": doc_id, "warnings": warnings}


def _close_original(row: dict, number: str) -> None:
    """수정분(음수)이 승인되면 당초 발행은 지워진 것 → 당초 증빙을 무효로 하고 상태를 '수정발행됨' 으로.
    그러면 매출 잠금이 풀려 사유에 맞게 이어서 처리한다 (01·05 다시 발행, 04 매출 취소)."""
    orig = db._one("SELECT * FROM etax_invoices WHERE id=?", [int(row["original_etax_id"])]) or {}
    with db.get_conn() as conn:
        conn.execute("UPDATE etax_invoices SET status='수정발행됨', error=? WHERE id=?",
                     (f"수정세금계산서 {row['modify_code']} {MODIFY_REASONS.get(row['modify_code'], '')} 승인 {number}",
                      int(row["original_etax_id"])))
        if orig.get("document_id"):
            conn.execute("UPDATE sale_documents SET voided_at=?, void_reason=?, voided_by=? WHERE id=? AND voided_at IS NULL",
                         (db._now(), f"수정세금계산서({row['modify_code']}) 승인번호 {number} 로 취소", "전자세금계산서 발행",
                          int(orig["document_id"])))


def _alert(row: dict, number: str, message: str) -> None:
    db.audit("세금계산서확인필요", "매출", int(row["sale_id"]), {"요청번호": row["id"], "승인번호": number, "내용": message})
    try:
        from . import notify
        notify.notify_role("ADMIN", "세금계산서", f"전자세금계산서 확인 필요 (매출 #{row['sale_id']})", message,
                           f"/sales?sid={row['sale_id']}")
    except Exception:   # noqa: BLE001 - 알림 실패가 회신 처리를 막지 않게
        pass


def _original(sale: dict) -> str:
    if (sale.get("sale_kind") or "매출") == "매출" or not sale.get("original_sale_id"):
        return ""
    from .returns import original_approval_no
    return original_approval_no(int(sale["original_sale_id"])) or ""


def list_for_sale(sale_id: int) -> list[dict]:
    rows = db._df("SELECT * FROM etax_invoices WHERE sale_id=? ORDER BY id DESC", [int(sale_id)]).to_dict("records")
    for r in rows:
        code = r.get("modify_code") if isinstance(r.get("modify_code"), str) else None
        r["modify_code"] = code
        r["modify_label"] = f"수정 {code} {MODIFY_REASONS.get(code, '')}" if code else ""
        r["can_modify"] = modifiable(r)
    return rows

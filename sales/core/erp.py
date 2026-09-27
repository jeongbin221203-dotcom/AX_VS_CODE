"""ERP(SAP) 연동 — 매출 전송(Outbound) · 입금 대사(Inbound)

구조
  * 매출을 등록하면 erp_outbox 에 '대기' 로 쌓인다(sales_db.upsert_sale). ERP 가 멈춰 있어도
    영업 입력은 계속되고, 전송은 관리자 화면이나 배치(python -m core.erp send)로 따로 돌린다
  * 전송 실패는 '실패' 로 남고 최대 MAX_ATTEMPTS 번까지 재시도한다. 보낸 내용(payload)은 대사 근거로 보관한다
  * 입금은 ERP 가 원장이다. ERP 의 매출별 '누적 입금액' 파일을 받아 CRM 과 차이만 반영하므로
    같은 파일을 두 번 올려도 이중 입금되지 않는다

어댑터 (SALES_ERP_ADAPTER)
  file      : data/erp/outbound/ 에 문서별 JSON 파일을 쓴다. SAP PI/PO·CPI·EAI 가 가져가는 방식 (기본값)
  sap_odata : SAP S/4HANA 표준 OData API_SALES_ORDER_SRV 로 판매오더를 직접 생성한다
              (CSRF 토큰 발급 → POST, 취소는 오더 품목 거부사유 PATCH)
  none      : 전송하지 않는다 (대기열만 쌓임)
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import enterprise as ent
from . import sales_db as db

MAX_ATTEMPTS = 5


def settings() -> dict:
    """환경변수 기반 연동 설정 (비밀번호는 화면에 노출하지 않는다)."""
    env = os.environ.get
    return {
        "adapter": env("SALES_ERP_ADAPTER", "file"),
        "outbound_dir": env("SALES_ERP_OUTBOUND_DIR", os.path.join(db.BASE_DIR, "data", "erp", "outbound")),
        "sap_base_url": env("SALES_SAP_BASE_URL", ""),   # 예: https://s4.example.com/sap/opu/odata/sap/API_SALES_ORDER_SRV
        "sap_client": env("SALES_SAP_CLIENT", ""),
        "sap_user": env("SALES_SAP_USER", ""),
        "sap_password": env("SALES_SAP_PASSWORD", ""),
        "sap_sales_org": env("SALES_SAP_SALES_ORG", "1000"),
        "sap_dist_channel": env("SALES_SAP_DIST_CHANNEL", "10"),
        "sap_division": env("SALES_SAP_DIVISION", "00"),
        "sap_order_type": env("SALES_SAP_ORDER_TYPE", "OR"),
        "sap_default_material": env("SALES_SAP_DEFAULT_MATERIAL", ""),
        "sap_unit": env("SALES_SAP_UNIT", "EA"),
        "sap_price_condition": env("SALES_SAP_PRICE_CONDITION", "PR00"),
        "sap_reject_reason": env("SALES_SAP_REJECT_REASON", "Z1"),
        "sap_verify_tls": env("SALES_SAP_VERIFY_TLS", "1") == "1",
        "timeout": int(env("SALES_ERP_TIMEOUT", "20")),
    }


# ---------------------------------------------------------------------------
# 전송 문서 만들기
# ---------------------------------------------------------------------------
def build_document(outbox: dict, cfg: dict) -> dict:
    """대기열 한 건 → ERP 중립 문서. 전송 직전 최신 데이터로 만든다."""
    sale = db.get_sale(int(outbox["ref_id"]))
    if not sale:
        raise ValueError("매출이 존재하지 않습니다.")
    cust = db.get_customer(int(sale["customer_id"])) or {}
    if not cust.get("erp_code"):
        raise ValueError(f"거래처 '{cust.get('name')}' 의 ERP 코드(SAP 고객번호)가 없습니다. 거래처 화면에서 입력하세요.")
    material = sale.get("item_code") or cfg["sap_default_material"]
    if not material:
        raise ValueError("품목코드가 없습니다. 매출에 품목코드를 넣거나 SALES_SAP_DEFAULT_MATERIAL 을 설정하세요.")
    return {
        "doc_type": outbox["doc_type"],
        "crm_ref": f"CRM-{sale['id']}",
        "erp_doc_no": sale.get("erp_doc_no"),
        "customer_code": cust["erp_code"],
        "customer_name": cust.get("name"),
        "customer_biz_no": cust.get("biz_no"),
        "sale_date": sale["sale_date"],
        "due_date": sale.get("due_date"),
        "material": material,
        "item_text": sale["item"],
        "qty": int(sale["qty"]),
        "unit_price": int(sale["unit_price"]),
        "amount": int(sale["amount"]),                         # 공급가액
        "tax_type": sale.get("tax_type") or "과세",
        "vat_amount": int(sale.get("vat_amount") or 0),
        "total_amount": int(sale.get("total_amount") or sale["amount"]),
        "currency": "KRW",
        "owner": sale.get("owner"),
        "cancel_reason": sale.get("cancel_reason"),
    }


def sap_sales_order_payload(doc: dict, cfg: dict) -> dict:
    """S/4HANA API_SALES_ORDER_SRV A_SalesOrder 생성 본문."""
    return {
        "SalesOrderType": cfg["sap_order_type"],
        "SalesOrganization": cfg["sap_sales_org"],
        "DistributionChannel": cfg["sap_dist_channel"],
        "OrganizationDivision": cfg["sap_division"],
        "SoldToParty": doc["customer_code"],
        "PurchaseOrderByCustomer": doc["crm_ref"],          # CRM 매출번호 — 역방향 대사 키
        "CustomerPurchaseOrderDate": f"{doc['sale_date']}T00:00:00",
        "to_Item": [{
            "SalesOrderItem": "10",
            "Material": doc["material"],
            "SalesOrderItemText": doc["item_text"][:40],
            "RequestedQuantity": str(doc["qty"]),
            "RequestedQuantityUnit": cfg["sap_unit"],
            "to_PricingElement": [{
                "ConditionType": cfg["sap_price_condition"],
                "ConditionRateValue": str(doc["unit_price"]),
                "ConditionCurrency": doc["currency"],
            }],
        }],
    }


# ---------------------------------------------------------------------------
# 어댑터
# ---------------------------------------------------------------------------
class FileAdapter:
    """중간 시스템(EAI·SAP PI/PO·CPI)이 가져갈 파일을 쓴다. 쓰기 성공 = 전송 완료로 본다."""

    name = "file"

    def __init__(self, cfg: dict):
        self.folder = Path(cfg["outbound_dir"])

    def send(self, outbox_id: int, doc: dict) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        kind = "CANCEL" if doc["doc_type"] == "매출취소" else "SALE"
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tmp = self.folder / f".{kind}_{outbox_id}.tmp"
        final = self.folder / f"{kind}_{stamp}_{outbox_id}.json"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(final)                 # 가져가는 쪽이 반쯤 쓴 파일을 읽지 않도록 이름을 바꿔 확정
        return doc.get("erp_doc_no") or f"FILE-{outbox_id}"


class SapODataAdapter:
    """SAP S/4HANA OData v2 (API_SALES_ORDER_SRV)."""

    name = "sap_odata"

    def __init__(self, cfg: dict):
        if not cfg["sap_base_url"]:
            raise ValueError("SALES_SAP_BASE_URL 이 설정되지 않았습니다.")
        self.cfg = cfg
        self.base = cfg["sap_base_url"].rstrip("/")
        jar = CookieJar()
        ctx = None if cfg["sap_verify_tls"] else ssl._create_unverified_context()   # noqa: SLF001
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                                  urllib.request.HTTPSHandler(context=ctx))
        self.token: Optional[str] = None

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.cfg["sap_user"]:
            raw = f"{self.cfg['sap_user']}:{self.cfg['sap_password']}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        if self.cfg["sap_client"]:
            headers["sap-client"] = self.cfg["sap_client"]
        headers.update(extra or {})
        return headers

    def _call(self, method: str, path: str, body: dict | None = None, extra: dict | None = None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(extra))
        try:
            with self.opener.open(req, timeout=self.cfg["timeout"]) as res:
                text = res.read().decode("utf-8") or "{}"
                return res.headers, json.loads(text) if text.strip().startswith("{") else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"SAP 응답 {exc.code}: {_sap_message(detail)}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"SAP 연결 실패: {exc.reason}") from exc

    def _fetch_token(self) -> None:
        headers, _ = self._call("GET", "/A_SalesOrder?$top=0&$format=json", extra={"x-csrf-token": "Fetch"})
        self.token = headers.get("x-csrf-token")
        if not self.token:
            raise RuntimeError("SAP CSRF 토큰을 받지 못했습니다.")

    def send(self, outbox_id: int, doc: dict) -> str:
        if not self.token:
            self._fetch_token()
        if doc["doc_type"] == "매출취소":
            if not doc.get("erp_doc_no"):
                raise ValueError("취소할 SAP 판매오더 번호가 없습니다.")
            self._call("PATCH", f"/A_SalesOrderItem(SalesOrder='{doc['erp_doc_no']}',SalesOrderItem='10')",
                       {"SalesDocumentRjcnReason": self.cfg["sap_reject_reason"]},
                       extra={"x-csrf-token": self.token})
            return doc["erp_doc_no"]
        _, result = self._call("POST", "/A_SalesOrder", sap_sales_order_payload(doc, self.cfg),
                               extra={"x-csrf-token": self.token})
        number = (result.get("d") or {}).get("SalesOrder")
        if not number:
            raise RuntimeError("SAP 가 판매오더 번호를 돌려주지 않았습니다.")
        return str(number)


def _sap_message(text: str) -> str:
    try:
        return json.loads(text)["error"]["message"]["value"]
    except Exception:   # noqa: BLE001 - SAP 오류 본문 형식이 일정하지 않다
        return text


def get_adapter(cfg: dict | None = None):
    cfg = cfg or settings()
    kind = cfg["adapter"]
    if kind == "file":
        return FileAdapter(cfg)
    if kind == "sap_odata":
        return SapODataAdapter(cfg)
    if kind == "none":
        return None
    raise ValueError(f"알 수 없는 ERP 어댑터입니다: {kind}")


# ---------------------------------------------------------------------------
# 전송 실행
# ---------------------------------------------------------------------------
def outbox_summary() -> dict:
    df = db._df("SELECT status, COUNT(*) AS n FROM erp_outbox GROUP BY status")
    return {r.status: int(r.n) for r in df.itertuples()}


def list_outbox(status: str = "", limit: int = 200) -> pd.DataFrame:
    sql = ("SELECT o.id, o.doc_type AS 문서, o.ref_id AS 매출번호, c.name AS 거래처, COALESCE(s.total_amount, s.amount) AS 금액, "
           'o.status AS 상태, o.attempts AS 시도, o.erp_doc_no AS "ERP번호", o.last_error AS 오류, '
           "o.created_at AS 생성, o.sent_at AS 전송 FROM erp_outbox o "
           "LEFT JOIN sales s ON s.id = o.ref_id LEFT JOIN customers c ON c.id = s.customer_id WHERE 1=1")
    params: list[Any] = []
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    return db._df(sql + " ORDER BY o.id DESC LIMIT ?", [*params, int(limit)])


def process_outbox(limit: int = 100, adapter=None, cfg: dict | None = None) -> dict:
    """대기·실패 건을 전송한다. 결과 건수 {'sent','failed','skipped'}."""
    cfg = cfg or settings()
    adapter = adapter if adapter is not None else get_adapter(cfg)
    if adapter is None:
        return {"sent": 0, "failed": 0, "skipped": 0, "message": "ERP 어댑터가 'none' 이라 전송하지 않았습니다."}
    rows = db._df("SELECT * FROM erp_outbox WHERE status IN ('대기','실패') AND attempts < ? "
                  "ORDER BY id LIMIT ?", [MAX_ATTEMPTS, int(limit)]).to_dict("records")
    sent = failed = 0
    for row in rows:
        try:
            doc = build_document(row, cfg)
            if row["doc_type"] == "매출취소" and not doc.get("erp_doc_no"):
                raise ValueError("원 매출이 ERP 에 전송된 기록이 없습니다.")
            number = adapter.send(int(row["id"]), doc)
            with db.get_conn() as conn:
                conn.execute("UPDATE erp_outbox SET status='전송완료', attempts=attempts+1, erp_doc_no=?, "
                             "payload=?, last_error=NULL, sent_at=? WHERE id=?",
                             (number, json.dumps(doc, ensure_ascii=False), db._now(), row["id"]))
                if row["doc_type"] == "매출취소":
                    conn.execute("UPDATE sales SET erp_status='취소완료' WHERE id=?", (row["ref_id"],))
                else:
                    conn.execute("UPDATE sales SET erp_status='전송완료', erp_doc_no=? WHERE id=?",
                                 (number, row["ref_id"]))
            sent += 1
        except Exception as exc:   # noqa: BLE001 - 한 건 실패가 나머지 전송을 막지 않도록
            with db.get_conn() as conn:
                conn.execute("UPDATE erp_outbox SET status='실패', attempts=attempts+1, last_error=? WHERE id=?",
                             (str(exc)[:500], row["id"]))
                conn.execute("UPDATE sales SET erp_status='실패' WHERE id=? AND erp_status IN ('대기','실패')",
                             (row["ref_id"],))
            failed += 1
    result = {"sent": sent, "failed": failed, "skipped": 0,
              "adapter": getattr(adapter, "name", type(adapter).__name__)}
    if rows:
        db.audit("ERP전송", "ERP", None, result)
    return result


def retry(outbox_id: int) -> None:
    """시도 횟수를 초기화해 다시 전송 대상으로 만든다(데이터를 고친 뒤 사용)."""
    with db.get_conn() as conn:
        conn.execute("UPDATE erp_outbox SET status='대기', attempts=0 WHERE id=? AND status='실패'", (outbox_id,))
    db.audit("ERP재시도", "ERP", outbox_id)


# ---------------------------------------------------------------------------
# 입금 대사 (ERP → CRM)
# ---------------------------------------------------------------------------
PAYMENT_COLUMNS = {"required": ["참조번호", "누적입금액"], "optional": ["ERP전표번호", "매출액", "입금일"]}


def payment_template() -> pd.DataFrame:
    return pd.DataFrame([{"참조번호": "CRM-123", "누적입금액": 5000000, "ERP전표번호": "90001234",
                          "매출액": 10000000, "입금일": "2026-09-30"}])


def _find_sale(ref: str, erp_no: str) -> Optional[dict]:
    m = re.search(r"(\d+)$", ref or "")
    if ref and ref.upper().startswith("CRM") and m:
        return db.get_sale(int(m.group(1)))
    if erp_no:
        return db._one("SELECT * FROM sales WHERE erp_doc_no=?", [erp_no])
    if m:
        return db._one("SELECT * FROM sales WHERE erp_doc_no=?", [ref])
    return None


def reconcile_payments(df: pd.DataFrame, apply: bool = False) -> pd.DataFrame:
    """ERP 누적 입금액과 CRM 입금액을 매출별로 대사한다.

    apply=True 이면 ERP 가 더 많은 만큼만 CRM 에 입금 등록한다(멱등). 결과표를 돌려준다.
    """
    missing = [c for c in PAYMENT_COLUMNS["required"] if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {', '.join(missing)}")
    out = []
    applied_total = 0
    for idx, row in df.dropna(how="all").iterrows():
        ref = str(row.get("참조번호") or "").strip()
        erp_no = str(row.get("ERP전표번호") or "").strip() if pd.notna(row.get("ERP전표번호")) else ""
        line = {"행": int(idx) + 2, "참조번호": ref, "ERP전표번호": erp_no}
        try:
            erp_paid = int(float(str(row.get("누적입금액")).replace(",", "")))
        except ValueError:
            out.append({**line, "결과": "오류", "내용": "누적입금액이 숫자가 아닙니다"})
            continue
        sale = _find_sale(ref, erp_no)
        if not sale:
            out.append({**line, "결과": "미일치", "내용": "CRM 매출을 찾지 못함"})
            continue
        crm_paid = int(sale.get("paid_amount") or 0)
        line.update({"매출번호": int(sale["id"]), "CRM입금": crm_paid, "ERP입금": erp_paid})
        notes = []
        if "매출액" in df.columns and pd.notna(row.get("매출액")):
            try:
                crm_total = int(sale.get("total_amount") or sale["amount"])     # ERP 채권은 부가세 포함
                if int(float(str(row["매출액"]).replace(",", ""))) != crm_total:
                    notes.append(f"매출액 불일치(CRM 합계 {crm_total:,})")
            except ValueError:
                notes.append("매출액 형식 오류")
        if sale["status"] == db.SALE_CANCELLED:
            out.append({**line, "결과": "확인필요", "내용": "CRM 에서 취소된 매출에 ERP 입금이 있음"})
            continue
        gap = erp_paid - crm_paid
        if gap == 0:
            out.append({**line, "결과": "일치", "내용": " / ".join(notes)})
        elif gap < 0:
            out.append({**line, "결과": "확인필요",
                        "내용": " / ".join([f"CRM 입금이 {-gap:,}원 더 많음(수기 입금 확인)", *notes])})
        elif erp_paid > int(sale.get("total_amount") or sale["amount"]):
            out.append({**line, "결과": "확인필요", "내용": "ERP 입금이 매출액을 넘음(과입금)"})
        else:
            if apply:
                ent.record_payment(int(sale["id"]), gap, source="ERP")
                applied_total += gap
            out.append({**line, "결과": "반영" if apply else "반영예정",
                        "내용": " / ".join([f"{gap:,}원 입금 반영", *notes])})
    result = pd.DataFrame(out)
    if apply:
        counts = result["결과"].value_counts().to_dict() if not result.empty else {}
        db.audit("ERP입금대사", "ERP", None, {"행수": len(result), "반영액": applied_total, **counts})
    return result


if __name__ == "__main__":      # 배치: python -m core.erp send
    db.init_db()
    db.set_context("batch", None)
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        print(process_outbox())
    else:
        print("사용법: python -m core.erp send")

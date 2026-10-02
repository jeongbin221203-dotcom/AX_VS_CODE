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
  rest      : 그 밖의 ERP(더존·영림원·자체 ERP·ERP 앞단 API 게이트웨이)에 JSON 으로 보낸다.
              인증(Bearer/Basic/API 키 헤더), 요청 서명(HMAC), 필드 이름 바꾸기, 응답의 전표번호 위치를 설정으로 맞춘다.
              재전송해도 ERP 가 중복을 거를 수 있게 Idempotency-Key(대기열 번호)를 함께 보낸다
  none      : 전송하지 않는다 (대기열만 쌓임)

ERP → CRM (실시간 수신, /api/v1/erp/* — erp:write 권한 키)
  입금(누적 입금액) · 전표번호 회신(파일·비동기 연동) · 여신한도 · 품목 마스터
"""
from __future__ import annotations

import base64
import hashlib
import hmac
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
        # 범용 REST ERP
        "rest_url": env("SALES_ERP_REST_URL", ""),                     # 매출 생성 POST 주소
        "rest_cancel_url": env("SALES_ERP_REST_CANCEL_URL", ""),       # 취소 POST 주소 ({doc_no} 치환). 비우면 rest_url/{doc_no}/cancel
        "rest_health_url": env("SALES_ERP_REST_HEALTH_URL", ""),       # 연결 테스트 GET 주소
        "rest_auth": env("SALES_ERP_REST_AUTH", "bearer"),             # bearer | basic | header | none
        "rest_token": env("SALES_ERP_REST_TOKEN", ""),
        "rest_user": env("SALES_ERP_REST_USER", ""),
        "rest_password": env("SALES_ERP_REST_PASSWORD", ""),
        "rest_header": env("SALES_ERP_REST_HEADER", "X-API-KEY"),      # header 방식의 헤더 이름
        "rest_hmac_secret": env("SALES_ERP_REST_HMAC_SECRET", ""),     # 있으면 X-Signature 로 본문 서명
        "rest_docno_path": env("SALES_ERP_REST_DOCNO_PATH", "doc_no"), # 응답 JSON 에서 전표번호 위치 (예: data.slipNo)
        "rest_field_map": env("SALES_ERP_REST_FIELD_MAP", ""),         # JSON {"customer_code": "CUST_CD", ...}
    }


# ---------------------------------------------------------------------------
# 전송 문서 만들기
# ---------------------------------------------------------------------------
def build_document(outbox: dict, cfg: dict) -> dict:
    """대기열 한 건 → ERP 중립 문서. 전송 직전 최신 데이터로 만든다.

    문서종류: 매출 · 매출취소 · 반품(마이너스 매출, 원 ERP 번호 참조) · 대손(결재 승인된 채권 정리)
    """
    if outbox["doc_type"] == "대손":
        req = db._one("SELECT * FROM fin_requests WHERE id=?", [int(outbox["ref_id"])])
        if not req:
            raise ValueError("대손 결재 기록이 없습니다.")
        base = build_document({**outbox, "doc_type": "매출", "ref_id": req["sale_id"]}, cfg)
        return {**base, "doc_type": "대손", "crm_ref": f"CRM-WO-{req['id']}", "writeoff_amount": int(req["amount"]),
                "writeoff_reason": req["reason"], "approved_by": req.get("decided_by"),
                "erp_doc_no": (db.get_sale(int(req["sale_id"])) or {}).get("erp_doc_no")}
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
        "currency": sale.get("currency") or "KRW",
        "fx_rate": float(sale.get("fx_rate") or 1),
        "foreign_amount": sale.get("foreign_amount"),
        "foreign_unit_price": (round(int(sale["unit_price"]) / float(sale.get("fx_rate") or 1), 2)
                               if (sale.get("currency") or "KRW") != "KRW" else None),
        **_entity_fields(sale.get("entity_id")),
        "sale_kind": sale.get("sale_kind") or "매출",
        "original_erp_doc_no": ((db.get_sale(int(sale["original_sale_id"])) or {}).get("erp_doc_no")
                                if sale.get("original_sale_id") else None),
        "owner": sale.get("owner"),
        "cancel_reason": sale.get("cancel_reason"),
    }


def _entity_fields(entity_id) -> dict:
    from . import entities as ent_mod
    sup = ent_mod.info(entity_id)
    return {"entity_code": sup["code"], "company_code": sup["erp_company_code"], "sap_sales_org": sup["sap_sales_org"],
            "supplier_biz_no": sup["biz_no"]}


def sap_sales_order_payload(doc: dict, cfg: dict) -> dict:
    """S/4HANA API_SALES_ORDER_SRV A_SalesOrder 생성 본문. 외화 매출은 거래 통화·외화 단가로 보낸다."""
    foreign = doc.get("currency", "KRW") != "KRW" and doc.get("foreign_unit_price") is not None
    return {
        "SalesOrderType": cfg["sap_order_type"],
        "SalesOrganization": doc.get("sap_sales_org") or cfg["sap_sales_org"],
        "TransactionCurrency": doc.get("currency", "KRW"),
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
                "ConditionRateValue": str(doc["foreign_unit_price"] if foreign else doc["unit_price"]),
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
        kind = {"매출취소": "CANCEL", "반품": "RETURN", "대손": "WRITEOFF"}.get(doc["doc_type"], "SALE")
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
        if doc["doc_type"] == "대손":
            raise ValueError("SAP 판매오더 API 로는 대손(FI) 전표를 보낼 수 없습니다. 재무팀이 FI 에서 처리한 뒤 '재시도' 대신 "
                             "ERP 수신 API 로 결과를 회신하거나, 대손은 파일·REST 연동으로 보내세요.")
        if doc["doc_type"] == "반품":
            payload = sap_sales_order_payload({**doc, "qty": abs(int(doc["qty"]))}, self.cfg)
            payload["SalesOrderType"] = os.environ.get("SALES_SAP_RETURN_ORDER_TYPE", "CBAR")
            if doc.get("original_erp_doc_no"):
                payload["ReferenceSDDocument"] = str(doc["original_erp_doc_no"])
            _, result = self._call("POST", "/A_SalesOrder", payload, extra={"x-csrf-token": self.token})
            number = (result.get("d") or {}).get("SalesOrder")
            if not number:
                raise RuntimeError("SAP 가 반품오더 번호를 돌려주지 않았습니다.")
            return str(number)
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


class RestAdapter:
    """범용 REST ERP 어댑터 — 표준 문서를 JSON 으로 보내고 응답에서 전표번호를 읽는다."""

    name = "rest"

    def __init__(self, cfg: dict):
        if not cfg["rest_url"]:
            raise ValueError("SALES_ERP_REST_URL 이 설정되지 않았습니다.")
        if cfg["rest_auth"] not in ("bearer", "basic", "header", "none"):
            raise ValueError(f"SALES_ERP_REST_AUTH 값이 올바르지 않습니다: {cfg['rest_auth']}")
        self.cfg = cfg
        try:
            self.field_map = json.loads(cfg["rest_field_map"]) if cfg["rest_field_map"] else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"SALES_ERP_REST_FIELD_MAP 이 JSON 이 아닙니다: {exc}") from exc
        ctx = None if cfg["sap_verify_tls"] else ssl._create_unverified_context()   # noqa: SLF001
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def _headers(self, body: bytes, extra: dict | None = None) -> dict:
        cfg = self.cfg
        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if cfg["rest_auth"] == "bearer" and cfg["rest_token"]:
            headers["Authorization"] = f"Bearer {cfg['rest_token']}"
        elif cfg["rest_auth"] == "basic" and cfg["rest_user"]:
            raw = f"{cfg['rest_user']}:{cfg['rest_password']}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        elif cfg["rest_auth"] == "header" and cfg["rest_token"]:
            headers[cfg["rest_header"]] = cfg["rest_token"]
        if cfg["rest_hmac_secret"]:
            stamp = str(int(datetime.now().timestamp()))
            digest = hmac.new(cfg["rest_hmac_secret"].encode(), stamp.encode() + b"." + body, hashlib.sha256)
            headers["X-Timestamp"] = stamp
            headers["X-Signature"] = "sha256=" + digest.hexdigest()
        headers.update(extra or {})
        return headers

    def call(self, method: str, url: str, payload: dict | None = None, extra: dict | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        req = urllib.request.Request(url, data=body if payload is not None else None, method=method,
                                     headers=self._headers(body, extra))
        try:
            with self.opener.open(req, timeout=self.cfg["timeout"]) as res:   # noqa: S310 - 관리자가 설정한 주소
                text = res.read().decode("utf-8") or "{}"
                return json.loads(text) if text.strip()[:1] in "{[" else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"ERP 응답 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ERP 연결 실패: {exc.reason}") from exc

    def _map(self, doc: dict) -> dict:
        return {self.field_map.get(k, k): v for k, v in doc.items() if self.field_map.get(k, k)}

    def send(self, outbox_id: int, doc: dict) -> str:
        key = {"Idempotency-Key": f"CRM-OUTBOX-{outbox_id}"}
        if doc["doc_type"] == "매출취소":
            if not doc.get("erp_doc_no"):
                raise ValueError("취소할 ERP 전표번호가 없습니다.")
            url = (self.cfg["rest_cancel_url"] or self.cfg["rest_url"].rstrip("/") + "/{doc_no}/cancel")
            self.call("POST", url.replace("{doc_no}", str(doc["erp_doc_no"])), self._map(doc), key)
            return str(doc["erp_doc_no"])
        result = self.call("POST", self.cfg["rest_url"], self._map(doc), key)
        number = _dig(result, self.cfg["rest_docno_path"])
        if not number:
            raise RuntimeError(f"ERP 응답에 전표번호({self.cfg['rest_docno_path']})가 없습니다: {str(result)[:200]}")
        return str(number)

    def ping(self) -> str:
        url = self.cfg["rest_health_url"] or self.cfg["rest_url"]
        self.call("GET", url)
        return f"GET {url} 응답 정상"


def _dig(data: Any, path: str) -> Any:
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(data, list) and part.isdigit():
            data = data[int(part)] if int(part) < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


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
    if kind == "rest":
        return RestAdapter(cfg)
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
           "LEFT JOIN sales s ON s.id = CASE WHEN o.doc_type = '대손' THEN (SELECT f.sale_id FROM fin_requests f "
           "WHERE f.id = o.ref_id) ELSE o.ref_id END LEFT JOIN customers c ON c.id = s.customer_id WHERE 1=1")
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
        with db.get_conn() as conn:           # 선점: 다른 워커·관리자 버튼이 같은 건을 동시에 보내지 않게
            claimed = conn.execute("UPDATE erp_outbox SET status='전송중', sent_at=? WHERE id=? "
                                   "AND status IN ('대기','실패')", (db._now(), row["id"])).rowcount
        if not claimed:
            continue
        try:
            doc = build_document(row, cfg)
            if row["doc_type"] == "매출취소" and not doc.get("erp_doc_no"):
                raise ValueError("원 매출이 ERP 에 전송된 기록이 없습니다.")
            number = adapter.send(int(row["id"]), doc)
            with db.get_conn() as conn:
                conn.execute("UPDATE erp_outbox SET status='전송완료', attempts=attempts+1, erp_doc_no=?, "
                             "payload=?, last_error=NULL, sent_at=? WHERE id=?",
                             (number, json.dumps(doc, ensure_ascii=False), db._now(), row["id"]))
                if row["doc_type"] == "대손":
                    pass                            # 대손 전표는 매출의 ERP 번호를 바꾸지 않는다
                elif row["doc_type"] == "매출취소":
                    conn.execute("UPDATE sales SET erp_status='취소완료', row_version=COALESCE(row_version,0)+1 "
                                 "WHERE id=?", (row["ref_id"],))
                else:
                    conn.execute("UPDATE sales SET erp_status='전송완료', erp_doc_no=?, "
                                 "row_version=COALESCE(row_version,0)+1 WHERE id=?", (number, row["ref_id"]))
            sent += 1
        except Exception as exc:   # noqa: BLE001 - 한 건 실패가 나머지 전송을 막지 않도록
            with db.get_conn() as conn:
                conn.execute("UPDATE erp_outbox SET status='실패', attempts=attempts+1, last_error=? WHERE id=?",
                             (str(exc)[:500], row["id"]))
                if row["doc_type"] != "대손":
                    conn.execute("UPDATE sales SET erp_status='실패', row_version=COALESCE(row_version,0)+1 "
                                 "WHERE id=? AND erp_status IN ('대기','실패')", (row["ref_id"],))
            failed += 1
    result = {"sent": sent, "failed": failed, "skipped": 0,
              "adapter": getattr(adapter, "name", type(adapter).__name__)}
    if rows:
        db.audit("ERP전송", "ERP", None, result)
    return result


def recover_stuck(minutes: int = 30) -> int:
    """'전송중' 으로 오래 멈춘 건(전송 도중 서버가 꺼진 경우)을 '실패' 로 돌린다.
    ERP 에 이미 들어갔을 수 있으므로 자동 재전송하지 않고, 관리자가 ERP 에서 확인한 뒤 재시도한다."""
    from datetime import timedelta
    limit = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        n = conn.execute("UPDATE erp_outbox SET status='실패', attempts=? , last_error=? "
                         "WHERE status='전송중' AND sent_at < ?",
                         (MAX_ATTEMPTS, "전송 도중 중단됨 — ERP 에 전표가 생겼는지 확인한 뒤 재시도하세요", limit)).rowcount
    if n:
        db.audit("ERP전송중단", "ERP", None, {"건수": n})
    return n


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
                try:
                    pay_day = (str(row.get("입금일"))[:10] if "입금일" in df.columns and pd.notna(row.get("입금일"))
                               and str(row.get("입금일")).strip() else None)
                    ent.record_payment(int(sale["id"]), gap, source="ERP", expected_before=crm_paid,
                                       pay_date=pay_day, method="계좌이체", ref_no=erp_no or "")
                except db.ConflictError as exc:
                    out.append({**line, "결과": "확인필요", "내용": str(exc)})
                    continue
                applied_total += gap
            out.append({**line, "결과": "반영" if apply else "반영예정",
                        "내용": " / ".join([f"{gap:,}원 입금 반영", *notes])})
    result = pd.DataFrame(out)
    if apply:
        counts = result["결과"].value_counts().to_dict() if not result.empty else {}
        db.audit("ERP입금대사", "ERP", None, {"행수": len(result), "반영액": applied_total, **counts})
    return result


# ---------------------------------------------------------------------------
# 연결 테스트 (관리자 화면)
# ---------------------------------------------------------------------------
def test_connection(cfg: dict | None = None) -> tuple[bool, str]:
    """실제 전표를 만들지 않고 연결·인증만 확인한다."""
    cfg = cfg or settings()
    try:
        adapter = get_adapter(cfg)
        if adapter is None:
            return False, "연동 방식이 'none' 입니다."
        if isinstance(adapter, FileAdapter):
            adapter.folder.mkdir(parents=True, exist_ok=True)
            probe = adapter.folder / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True, f"출력 폴더에 쓰기 가능: {adapter.folder}"
        if isinstance(adapter, SapODataAdapter):
            adapter._fetch_token()                                      # noqa: SLF001
            return True, "SAP 인증·CSRF 토큰 발급 정상"
        return True, adapter.ping()
    except Exception as exc:   # noqa: BLE001 - 원인을 화면에 그대로 보여 준다
        return False, str(exc)


# ---------------------------------------------------------------------------
# ERP → CRM 실시간 수신 (API 에서 호출)
# ---------------------------------------------------------------------------
def receive_payments(items: list[dict]) -> list[dict]:
    """ERP 누적 입금액 [{ref, erp_doc_no, paid_total, sale_total?}] → 차액만 반영 (같은 값을 다시 보내도 멱등)."""
    rows = [{"참조번호": str(i.get("ref") or ""), "ERP전표번호": str(i.get("erp_doc_no") or ""),
             "누적입금액": i.get("paid_total"), "입금일": i.get("pay_date"),
             **({"매출액": i["sale_total"]} if i.get("sale_total") is not None else {})}
            for i in items]
    frame = pd.DataFrame(rows, columns=["참조번호", "ERP전표번호", "누적입금액", "매출액"])
    result = reconcile_payments(frame, apply=True)
    return [{"ref": r["참조번호"], "erp_doc_no": r["ERP전표번호"] or None, "result": r["결과"],
             "sale_id": int(r["매출번호"]) if pd.notna(r.get("매출번호")) else None, "message": r.get("내용") or ""}
            for r in result.to_dict("records")]


def receive_ack(ref: str, erp_doc_no: str, ok: bool = True, message: str = "") -> dict:
    """파일·EAI 같은 비동기 연동에서 ERP 가 전표번호(또는 실패)를 돌려줄 때."""
    sale = _find_sale(ref, "")
    if not sale:
        raise ValueError(f"CRM 매출을 찾지 못했습니다: {ref}")
    db.check_record_scope(sale, "매출")
    with db.get_conn() as conn:
        if ok:
            if not erp_doc_no:
                raise ValueError("erp_doc_no 가 필요합니다.")
            status = "취소완료" if sale.get("erp_status") == "취소완료" else "전송완료"
            conn.execute("UPDATE sales SET erp_doc_no=?, erp_status=?, row_version=COALESCE(row_version,0)+1 WHERE id=?",
                     (erp_doc_no, status, sale["id"]))
            conn.execute("UPDATE erp_outbox SET erp_doc_no=? WHERE ref_id=? AND doc_type='매출' AND status='전송완료'",
                         (erp_doc_no, sale["id"]))
        else:
            conn.execute("UPDATE sales SET erp_status='실패', row_version=COALESCE(row_version,0)+1 WHERE id=?",
                     (sale["id"],))
            conn.execute("UPDATE erp_outbox SET status='실패', last_error=? WHERE id = (SELECT MAX(id) FROM erp_outbox "
                         "WHERE ref_id=?)", (f"ERP 회신: {message}"[:500], sale["id"]))
    db.audit("ERP회신", "매출", int(sale["id"]), {"ERP번호": erp_doc_no or None, "성공": ok, "내용": message or None})
    return {"sale_id": int(sale["id"]), "erp_doc_no": erp_doc_no or None, "status": "전송완료" if ok else "실패"}


def receive_credit(items: list[dict]) -> list[dict]:
    """ERP 여신한도 [{erp_code, credit_limit}] — 여신은 ERP(재무)가 원장이다."""
    out = []
    for i in items:
        code = str(i.get("erp_code") or "").strip()
        try:
            limit = int(i.get("credit_limit"))
            if limit < 0:
                raise ValueError
        except (TypeError, ValueError):
            out.append({"erp_code": code, "result": "오류", "message": "credit_limit 은 0 이상 정수"})
            continue
        cust = db._one("SELECT id, name, credit_limit, owner_id FROM customers WHERE erp_code=?", [code])
        if not cust:
            out.append({"erp_code": code, "result": "미일치", "message": "ERP 코드가 등록된 거래처가 없음"})
            continue
        if not db.in_scope(cust["owner_id"]):
            out.append({"erp_code": code, "result": "권한없음", "message": "API 대리 사용자의 범위 밖"})
            continue
        before = int(cust.get("credit_limit") or 0)
        if before != limit:
            with db.get_conn() as conn:
                conn.execute("UPDATE customers SET credit_limit=?, updated_at=? WHERE id=?", (limit, db._now(), cust["id"]))
            db.audit("ERP여신", "거래처", int(cust["id"]), {"거래처": cust["name"], "변경": [before, limit]})
        out.append({"erp_code": code, "customer_id": int(cust["id"]),
                    "result": "변경" if before != limit else "일치", "message": ""})
    return out


CUSTOMER_MASTER_FIELDS = {"name": "name", "biz_no": "biz_no", "payment_terms": "payment_terms",
                          "credit_limit": "credit_limit", "address": "address"}


def receive_customers(items: list[dict]) -> list[dict]:
    """ERP 거래처(고객) 마스터 [{erp_code, name, biz_no, payment_terms, credit_limit, address, blocked, owner_emp_no}].

    자재관리의 SAP 자재 마스터 동기화와 같은 규칙: ERP 코드로 맞추고, 받은 항목은 ERP 가 원본이 되어 화면에서 고칠 수 없다.
    영업 담당자·등급·메모처럼 CRM 만의 항목은 건드리지 않는다. 새 거래처의 담당자는 owner_emp_no, 없으면 API 대리 사용자.
    blocked=true 이면 거래정지(새 매출·견적 발송 불가).
    """
    from . import enterprise as ent_mod
    out = []
    for i in items:
        code = str(i.get("erp_code") or "").strip()
        try:
            if not code or not str(i.get("name") or "").strip():
                raise ValueError("erp_code 와 name 은 필수입니다.")
            prev = db._one("SELECT * FROM customers WHERE erp_code=? AND merged_into IS NULL", [code])
            data = {k: i[src] for src, k in CUSTOMER_MASTER_FIELDS.items() if i.get(src) not in (None, "")}
            if prev:
                db.check_record_scope(prev, "거래처")
                cid = db.upsert_customer({**prev, **data, "id": prev["id"], "row_version": None}, source="ERP")
                result = "수정"
            else:
                owner = (ent_mod.get_user(emp_no=str(i["owner_emp_no"])) or {}).get("id") if i.get("owner_emp_no") \
                    else db.current_actor_id()
                cid = db.upsert_customer({**data, "erp_code": code, "owner_id": owner, "grade": "B", "status": "활성"},
                                         source="ERP")
                result = "등록"
            with db.get_conn() as conn:
                conn.execute("UPDATE customers SET erp_synced_at=?, trade_blocked=? WHERE id=?",
                             (db._now(), 1 if i.get("blocked") else 0, cid))
            out.append({"erp_code": code, "customer_id": cid, "result": result, "message": "거래정지" if i.get("blocked") else ""})
        except (ValueError, PermissionError, TypeError) as exc:
            out.append({"erp_code": code, "result": "오류", "message": str(exc)})
    return out


def receive_products(items: list[dict]) -> list[dict]:
    """ERP 자재(품목) 마스터 [{code, name, unit, list_price, tax_type, category, active}] — 품목코드 기준 등록·수정."""
    from . import catalog
    out = []
    for i in items:
        code = str(i.get("code") or "").strip()
        try:
            prev = db._one("SELECT * FROM products WHERE code=?", [code])
            data = {**(prev or {}), **{k: v for k, v in i.items() if v is not None}, "id": prev["id"] if prev else None}
            data.setdefault("erp_material", code)
            pid = catalog.upsert_product(data)
            out.append({"code": code, "product_id": pid, "result": "수정" if prev else "등록", "message": ""})
        except (ValueError, TypeError) as exc:
            out.append({"code": code, "result": "오류", "message": str(exc)})
    return out


if __name__ == "__main__":      # 배치: python -m core.erp send
    db.init_db()
    db.set_context("batch", None)
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        print(process_outbox())
    else:
        print("사용법: python -m core.erp send")

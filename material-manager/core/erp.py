"""ERP 연결 방식. core/sap.py의 전송 대기열이 '어디로, 어떤 형식으로' 보낼지를 여기서 고른다.

대기열·멱등키·재시도·월 마감 확인은 모든 방식이 같고, 방식마다 다른 것은 아래 네 가지뿐이다.
  send(payload, attempt)  자재 이동 한 건 전기 → Posted(문서번호, 연도)
  ping()                  연결 확인 (화면의 '연결 확인', `flask erp test`)
  fetch_master(since)     자재·원가센터 마스터 (core/master_sync.py)
  fetch_stock(plants)     ERP 재고 (core/reconcile.py 재고 대사)

방식 (MM_ERP_MODE, 예전 이름 MM_SAP_MODE도 읽는다)
  off        전송하지 않는다.
  mock       ERP 없이 문서번호를 흉내 낸다 (시연·테스트).
  http       사내 연계서버(EAI · SAP Integration Suite · MuleSoft 등)로 이 시스템의 중립 JSON을 보낸다.
  sap_odata  SAP S/4HANA에 직접: API_MATERIAL_DOCUMENT_SRV (OData V2, CSRF 토큰, Basic 또는 OAuth2).
  sap_rfc    SAP ECC · S/4HANA에 직접: BAPI_GOODSMVT_CREATE / BAPI_GOODSMVT_CANCEL (pyrfc + SAP NW RFC SDK).
  rest       그 밖의 ERP(Oracle · MS Dynamics · 더존 · 영림원 · 자체 ERP 등) REST API.
             주소·필드 이름·인증은 매핑 파일(MM_ERP_REST_MAP, 예: erp_maps/*.json)로 맞춘다 — 코드 수정 없음.
  file       파일 연계: 공유 폴더에 거래마다 JSON/CSV 파일을 쓰고, ERP가 남긴 처리 결과(.ack.json)를 읽는다.

중복 전기 방지
  - 모든 요청에 멱등키 MM-TX-<거래ID>를 싣는다 (http·rest는 헤더/본문, SAP는 전표 헤더 텍스트, file은 파일 이름).
  - 응답을 못 받고 다시 보낼 때(2번째 시도부터) SAP·REST는 먼저 그 키로 이미 전기된 문서를 찾아보고, 있으면 그 번호를 쓴다.
    SAP 취소는 원문서를 취소한 문서가 이미 있는지 먼저 본다.

한계: 실제 SAP·ERP에 붙여 검증하지 않았다(테스트는 가짜 서버·가짜 pyrfc로 확인). 필드 이름·이동유형·단위 코드는
      회사 ERP 담당 팀과 확인해 config.py와 매핑 파일에서 맞춘다.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path

import config

LOOPBACK = ("127.0.0.1", "localhost", "::1")

MODES = {
    "off": "사용 안 함",
    "mock": "모의 전송 (실제 ERP에 전기되지 않음)",
    "http": "사내 연계서버(EAI) — 중립 JSON",
    "sap_odata": "SAP S/4HANA — OData API 직접",
    "sap_rfc": "SAP ECC·S/4HANA — RFC(BAPI) 직접",
    "rest": "기타 ERP — REST API (매핑 파일)",
    "file": "파일 연계 — 공유 폴더",
}


class ErpError(Exception):
    """retryable=True면 잠시 뒤 자동 재시도(연결 실패·5xx), False면 사람이 원인을 고쳐야 한다(매핑·업무 오류)."""

    def __init__(self, message: str, retryable: bool, status: int | None = None, headers: dict | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.headers = headers or {}


@dataclass
class Posted:
    doc_no: str
    year: str


# ── 공통 도우미 ──────────────────────────────────────────────
def require_secure(url: str, what: str = "ERP 연결 주소") -> None:
    """인증 정보와 거래 내용이 평문으로 나가지 않게 https만 허용한다 (같은 PC의 테스트 서버만 예외)."""
    if not url:
        raise ErpError(f"{what}가 설정되지 않았습니다.", retryable=False)
    host = urllib.parse.urlsplit(url).hostname or ""
    if not url.startswith("https://") and host not in LOOPBACK:
        raise ErpError(f"{what}는 https:// 여야 합니다.", retryable=False)


def _header(headers: dict, name: str) -> str:
    name = name.lower()
    return next((str(v) for k, v in headers.items() if k.lower() == name), "")


def _error_text(raw: str) -> str:
    """SAP OData 오류({"error": {"message": {"value": ...}}})나 흔한 JSON 오류에서 사람이 읽을 문장을 꺼낸다."""
    try:
        body = json.loads(raw)
    except ValueError:
        return raw.strip()[:300]
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, dict):
                msg = msg.get("value")
            code = err.get("code", "")
            if msg:
                return f"{code} {msg}".strip()[:300]
        for key in ("message", "errorMessage", "detail"):
            if body.get(key):
                return str(body[key])[:300]
    return raw.strip()[:300]


def http_json(opener, method: str, url: str, body=None, headers: dict | None = None,
              form: dict | None = None) -> tuple[object, dict]:
    """JSON 요청 한 번. (응답 본문, 응답 헤더). 4xx는 재시도해도 같으므로 retryable=False (408·429 제외)."""
    data, ctype = None, {}
    if form is not None:
        data, ctype = urllib.parse.urlencode(form).encode(), {"Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data, ctype = json.dumps(body, ensure_ascii=False).encode(), {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", **ctype, **(headers or {})})
    try:
        with opener.open(req, timeout=config.SAP_TIMEOUT) as res:
            raw = res.read().decode("utf-8", errors="replace")
            return (json.loads(raw) if raw.strip() else {}), dict(res.headers.items())
    except urllib.error.HTTPError as exc:
        detail = _error_text(exc.read().decode("utf-8", errors="replace"))
        raise ErpError(f"HTTP {exc.code}: {detail}", retryable=exc.code >= 500 or exc.code in (408, 429),
                       status=exc.code, headers=dict(exc.headers.items()) if exc.headers else {}) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErpError(f"연결 실패: {exc}", retryable=True) from exc
    except ValueError as exc:
        raise ErpError(f"응답이 JSON이 아닙니다: {exc}", retryable=False) from exc


def dig(obj, path: str):
    """'d.results.0.MaterialDocument' 같은 점 경로로 값을 꺼낸다. 없으면 None."""
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def erp_unit(unit: str) -> str:
    """이 시스템 단위 → ERP 단위 코드 (예: EA → ST). config.ERP_UNIT_MAP."""
    return config.ERP_UNIT_MAP.get(unit, unit)


def gm_code(movement_type: str) -> str:
    """SAP 이동유형 → 거래 코드(GM_CODE / GoodsMovementCode). 01 구매입고 · 03 출고 · 04 이전 · 05 기타입고."""
    return config.SAP_GM_CODES.get(movement_type, "05")


def _yyyymmdd(d: str) -> str:
    return d.replace("-", "")


def _alpha(value: str, width: int) -> str:
    """SAP ALPHA 변환: 숫자만 있는 번호는 앞에 0을 채운 내부 형식으로 보낸다 (RFC는 변환해 주지 않는다)."""
    value = (value or "").strip()
    return value.zfill(width) if value.isdigit() else value


# ── mock ─────────────────────────────────────────────────────
class MockConnector:
    """ERP 없이 동작을 확인하는 모의 전송. 같은 멱등키에는 항상 같은 문서번호를 돌려준다."""

    label = MODES["mock"]

    def send(self, payload: dict, attempt: int = 1) -> Posted:
        if payload["action"] == "POST" and str(payload.get("material", "")).upper().startswith("FAIL"):
            raise ErpError("(모의) M7 021: 자재가 플랜트에 없습니다", retryable=False)
        n = int(hashlib.sha1(payload["idempotencyKey"].encode(), usedforsecurity=False).hexdigest()[:8], 16) % 10**8
        return Posted(f"49{n:08d}", payload["postingDate"][:4])

    def ping(self) -> str:
        return "모의 전송 — 외부 연결 없음"

    def fetch_master(self, since: str):
        from core import master_sync
        return master_sync.MOCK_MATERIALS, master_sync.MOCK_COST_CENTERS

    def fetch_stock(self, plants: list[str]) -> list[dict]:
        raise ErpError("모의 모드 재고는 core/reconcile.py가 전기 완료 거래로 계산한다.", retryable=False)


# ── http: 사내 연계서버(EAI) ──────────────────────────────────
class EaiConnector:
    """사내 연계서버로 중립 JSON을 보낸다. 연계서버가 SAP BAPI/OData나 다른 ERP 형식으로 바꾼다.

    POST {MM_SAP_ENDPOINT}/goods-movements  (헤더 Idempotency-Key)  → {"materialDocument": "...", "year": "2026"}
    GET  {MM_SAP_ENDPOINT}/master/materials?changedSince=...        → [{material, description, unit, ...}]
    GET  {MM_SAP_ENDPOINT}/master/cost-centers?changedSince=...     → [{costCenter, name, active}]
    GET  {MM_SAP_ENDPOINT}/stock                                    → [{material, plant, storageLocation, quantity}]
    """

    label = MODES["http"]

    def __init__(self):
        self.opener = urllib.request.build_opener()

    @staticmethod
    def _check_endpoint() -> None:
        require_secure(config.SAP_ENDPOINT, "연계서버 주소(MM_SAP_ENDPOINT)")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {config.SAP_TOKEN}"} if config.SAP_TOKEN else {}

    def _url(self, path: str, query: dict | None = None) -> str:
        return config.SAP_ENDPOINT.rstrip("/") + path + (f"?{urllib.parse.urlencode(query)}" if query else "")

    def send(self, payload: dict, attempt: int = 1) -> Posted:
        self._check_endpoint()
        body, _ = http_json(self.opener, "POST", self._url("/goods-movements"), payload,
                            {**self._headers(), "Idempotency-Key": payload["idempotencyKey"]})
        doc = str(dig(body, "materialDocument") or "")
        if not doc:
            raise ErpError(f"응답에 문서번호가 없습니다: {str(body)[:200]}", retryable=False)
        return Posted(doc, str(dig(body, "year") or payload["postingDate"][:4]))

    def ping(self) -> str:
        self._check_endpoint()
        try:
            http_json(self.opener, "GET", self._url("/health"), headers=self._headers())
            return "연결됨"
        except ErpError as exc:
            if exc.status and exc.status < 500 and exc.status not in (401, 403):
                return f"연결됨 (HTTP {exc.status})"
            raise

    def fetch_master(self, since: str):
        self._check_endpoint()
        q = {"changedSince": since}
        mats, _ = http_json(self.opener, "GET", self._url("/master/materials", q), headers=self._headers())
        ccs, _ = http_json(self.opener, "GET", self._url("/master/cost-centers", q), headers=self._headers())
        return list(mats or []), list(ccs or [])

    def fetch_stock(self, plants: list[str]) -> list[dict]:
        self._check_endpoint()
        rows, _ = http_json(self.opener, "GET", self._url("/stock"), headers=self._headers())
        return list(rows or [])


# ── sap_odata: SAP S/4HANA OData ──────────────────────────────
def _odata_date(d: str) -> str:
    """OData V2 Edm.DateTime JSON 형식 /Date(밀리초)/ (UTC 자정)."""
    dt = datetime.strptime(d[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return f"/Date({int(dt.timestamp() * 1000)})/"


def _utc(local: str) -> str:
    """이 서버 현지 시각 'YYYY-MM-DD HH:MM:SS'(now_str) → UTC 'YYYY-MM-DDTHH:MM:SSZ'.
    그대로 Z를 붙이면 한국 서버는 9시간 늦은 시각부터 조회해 그 사이 바뀐 마스터를 놓친다."""
    dt = datetime.strptime(local[:19], "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_odata_date(v) -> str:
    m = re.match(r"/Date\((-?\d+)", str(v or ""))
    if not m:
        return ""
    # fromtimestamp는 Windows에서 9999-12-31(SAP의 '무기한')을 못 다룬다 → 기준일 + 경과 시간으로 계산
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(m.group(1)))).strftime("%Y-%m-%d")


class SapODataConnector:
    """SAP S/4HANA 표준 API에 직접 붙는다 (SAP API Business Hub의 OData V2 서비스).

    전기   POST .../API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader (품목 deep insert, 헤더 텍스트 = 멱등키)
    취소   POST .../API_MATERIAL_DOCUMENT_SRV/Cancel?MaterialDocument='..'&MaterialDocumentYear='..'&PostingDate=..
    재시도 전 확인  GET A_MaterialDocumentHeader?$filter=MaterialDocumentHeaderText eq 'MM-TX-..'
                    취소는 A_MaterialDocumentItem?$filter=ReversedMaterialDocument eq '원문서'
    마스터 API_PRODUCT_SRV/A_Product · API_COSTCENTER_SRV/A_CostCenter   재고 API_MATERIAL_STOCK_SRV
    변경 요청 전에는 X-CSRF-Token을 받아 쿠키와 함께 보낸다(만료되면 한 번 다시 받음).
    인증: MM_SAP_USER/MM_SAP_PASSWORD(Basic) 또는 MM_SAP_OAUTH_TOKEN_URL(+CLIENT_ID/SECRET, 클라이언트 자격 증명).
    """

    label = MODES["sap_odata"]
    MD = "/sap/opu/odata/sap/API_MATERIAL_DOCUMENT_SRV"

    def __init__(self):
        self.base = config.SAP_ODATA_URL.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.csrf = ""
        self._token: tuple[str, float] | None = None

    # 인증 · 주소
    def _auth(self) -> dict:
        if config.SAP_OAUTH_TOKEN_URL:
            return {"Authorization": f"Bearer {self._oauth_token()}"}
        if config.SAP_USER:
            raw = f"{config.SAP_USER}:{config.SAP_PASSWORD}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {}

    def _oauth_token(self) -> str:
        if self._token and self._token[1] > time.time() + 30:
            return self._token[0]
        require_secure(config.SAP_OAUTH_TOKEN_URL, "OAuth 토큰 주소")
        body, _ = http_json(self.opener, "POST", config.SAP_OAUTH_TOKEN_URL, form={
            "grant_type": "client_credentials", "client_id": config.SAP_OAUTH_CLIENT_ID,
            "client_secret": config.SAP_OAUTH_CLIENT_SECRET})
        token = str(dig(body, "access_token") or "")
        if not token:
            raise ErpError("OAuth 토큰을 받지 못했습니다.", retryable=False)
        self._token = (token, time.time() + float(dig(body, "expires_in") or 300))
        return token

    def _url(self, path: str, query: dict | None = None) -> str:
        q = dict(query or {})
        if config.SAP_CLIENT:
            q["sap-client"] = config.SAP_CLIENT
        qs = urllib.parse.urlencode(q, quote_via=urllib.parse.quote, safe="$',()=:")
        return self.base + path + (f"?{qs}" if qs else "")

    def _fetch_csrf(self) -> None:
        _, headers = http_json(self.opener, "GET", self._url(self.MD + "/"), headers={**self._auth(), "X-CSRF-Token": "Fetch"})
        self.csrf = _header(headers, "x-csrf-token")
        if not self.csrf or self.csrf.lower() == "required":
            raise ErpError("SAP에서 CSRF 토큰을 받지 못했습니다(권한·경로 확인).", retryable=True)

    def _call(self, method: str, path: str, body=None, query: dict | None = None):
        require_secure(self.base, "SAP 주소(MM_SAP_ODATA_URL)")
        if method != "GET" and not self.csrf:
            self._fetch_csrf()
        for tried in range(2):
            headers = {**self._auth(), **({"X-CSRF-Token": self.csrf} if method != "GET" else {})}
            try:
                return http_json(self.opener, method, self._url(path, query), body, headers)[0]
            except ErpError as exc:
                expired = exc.status == 403 and _header(exc.headers, "x-csrf-token").lower() == "required"
                if method != "GET" and expired and tried == 0:
                    self._fetch_csrf()                 # 토큰 만료 → 한 번만 다시 받는다
                    continue
                raise
        raise ErpError("SAP 요청 실패", retryable=True)

    def _all(self, path: str, query: dict) -> list[dict]:
        """페이지(__next)를 따라가며 전부 읽는다."""
        rows, url_q = [], dict(query)
        while True:
            body = self._call("GET", path, query=url_q)
            d = dig(body, "d") or {}
            rows += list(d.get("results", [])) if isinstance(d, dict) else []
            nxt = d.get("__next") if isinstance(d, dict) else None
            if not nxt:
                return rows
            skiptoken = urllib.parse.parse_qs(urllib.parse.urlsplit(nxt).query).get("$skiptoken")
            if not skiptoken:
                return rows
            url_q = {**query, "$skiptoken": skiptoken[0]}

    @staticmethod
    def _posted(body, payload: dict) -> Posted:
        d = dig(body, "d") or {}
        doc = str(d.get("MaterialDocument") or "")
        if not doc:
            raise ErpError(f"SAP 응답에 자재문서번호가 없습니다: {str(body)[:200]}", retryable=False)
        return Posted(doc, str(d.get("MaterialDocumentYear") or payload["postingDate"][:4]))

    def _find(self, key: str) -> Posted | None:
        body = self._call("GET", self.MD + "/A_MaterialDocumentHeader", query={
            "$filter": f"MaterialDocumentHeaderText eq '{key}'", "$select": "MaterialDocument,MaterialDocumentYear",
            "$top": "1"})
        rows = dig(body, "d.results") or []
        return Posted(str(rows[0]["MaterialDocument"]), str(rows[0]["MaterialDocumentYear"])) if rows else None

    @staticmethod
    def item(p: dict) -> dict:
        it = {"Material": p["material"], "Plant": p["plant"], "StorageLocation": p["storageLocation"],
              "GoodsMovementType": p["movementType"], "EntryUnit": erp_unit(p["unit"]),
              "QuantityInEntryUnit": f"{p['quantity']:.3f}", "Batch": p.get("batch", ""),
              "CostCenter": p.get("costCenter", ""), "PurchaseOrder": p.get("purchaseOrder", ""),
              "PurchaseOrderItem": p.get("purchaseOrderItem", ""),
              "IssuingOrReceivingPlant": p.get("receivingPlant", ""),
              "IssuingOrReceivingStorageLoc": p.get("receivingStorageLocation", "")}
        if p.get("purchaseOrder"):
            it["GoodsMovementRefDocType"] = "B"        # 구매오더 참조 입고
        return {k: v for k, v in it.items() if v not in ("", None)}

    def _find_reversal(self, doc: str, year: str) -> Posted | None:
        """원문서를 취소한 문서(품목의 ReversedMaterialDocument가 원문서)가 이미 있는지."""
        body = self._call("GET", self.MD + "/A_MaterialDocumentItem", query={
            "$filter": f"ReversedMaterialDocument eq '{doc}' and ReversedMaterialDocumentYear eq '{year}'",
            "$select": "MaterialDocument,MaterialDocumentYear", "$top": "1"})
        rows = dig(body, "d.results") or []
        return Posted(str(rows[0]["MaterialDocument"]), str(rows[0]["MaterialDocumentYear"])) if rows else None

    def send(self, payload: dict, attempt: int = 1) -> Posted:
        if payload["action"] == "CANCEL":
            if attempt > 1:                             # 지난번 취소 응답을 못 받았을 수 있다
                found = self._find_reversal(payload["originalDocument"], payload["originalYear"])
                if found:
                    return found
            body = self._call("POST", self.MD + "/Cancel", query={
                "MaterialDocumentYear": f"'{payload['originalYear']}'",
                "MaterialDocument": f"'{payload['originalDocument']}'",
                "PostingDate": f"datetime'{payload['postingDate']}T00:00:00'"})
            return self._posted(body, payload)
        key = payload["idempotencyKey"]
        if attempt > 1:                                 # 지난번 응답을 못 받았을 수 있다 → 이미 전기됐는지 먼저 본다
            found = self._find(key)
            if found:
                return found
        body = {"GoodsMovementCode": gm_code(payload["movementType"]),
                "PostingDate": _odata_date(payload["postingDate"]),
                "DocumentDate": _odata_date(payload.get("documentDate") or payload["postingDate"]),
                "MaterialDocumentHeaderText": key[:25],
                "ReferenceDocument": (payload.get("reference") or "")[:16],
                "to_MaterialDocumentItem": {"results": [self.item(payload)]}}
        return self._posted(self._call("POST", self.MD + "/A_MaterialDocumentHeader", body), payload)

    def ping(self) -> str:
        require_secure(self.base, "SAP 주소(MM_SAP_ODATA_URL)")
        self._fetch_csrf()
        return "연결됨 (API_MATERIAL_DOCUMENT_SRV, CSRF 토큰 받음)"

    def fetch_master(self, since: str):
        q = {"$select": "Product,BaseUnit,ProductGroup,IsMarkedForDeletion,IsBatchManagementRequired,to_Description",
             "$expand": "to_Description"}
        if since:
            q["$filter"] = f"LastChangeDateTime ge datetimeoffset'{_utc(since)}'"
        materials = []
        for r in self._all(config.SAP_ODATA_PRODUCT_PATH, q):
            descs = dig(r, "to_Description.results") or []
            desc = next((x.get("ProductDescription") for x in descs if x.get("Language") == config.SAP_LANGUAGE),
                        descs[0].get("ProductDescription") if descs else "")
            materials.append({"material": r.get("Product"), "description": desc, "unit": r.get("BaseUnit"),
                              "materialGroup": r.get("ProductGroup") or None, "standardPrice": None,
                              "deleted": bool(r.get("IsMarkedForDeletion")),
                              "batchManaged": bool(r.get("IsBatchManagementRequired"))})
        centers = []
        if config.SAP_ODATA_COSTCENTER_PATH:
            cq = {"$filter": f"ControllingArea eq '{config.SAP_CONTROLLING_AREA}'"} if config.SAP_CONTROLLING_AREA else {}
            today = datetime.now().strftime("%Y-%m-%d")
            for r in self._all(config.SAP_ODATA_COSTCENTER_PATH, cq):
                end = _from_odata_date(r.get("ValidityEndDate"))
                centers.append({"costCenter": r.get("CostCenter"),
                                "name": r.get("CostCenterName") or r.get("CostCenterDescription") or "",
                                "active": not end or end >= today})
        return materials, centers

    def fetch_stock(self, plants: list[str]) -> list[dict]:
        cond = " or ".join(f"Plant eq '{p}'" for p in plants)
        q = {"$select": "Material,Plant,StorageLocation,MatlWrhsStkQtyInMatlBaseUnit",
             "$filter": "InventoryStockType eq '01'" + (f" and ({cond})" if cond else "")}
        out: dict[tuple, float] = {}
        for r in self._all(config.SAP_ODATA_STOCK_PATH, q):
            k = (r.get("Material"), r.get("Plant"), r.get("StorageLocation"))
            out[k] = out.get(k, 0.0) + float(r.get("MatlWrhsStkQtyInMatlBaseUnit") or 0)
        return [{"material": m, "plant": p, "storageLocation": s, "quantity": q} for (m, p, s), q in out.items()]


# ── sap_rfc: SAP ECC / S/4HANA RFC(BAPI) ──────────────────────
RFC_RETRYABLE = {"CommunicationError", "ExternalRuntimeError", "TimeoutError"}


class SapRfcConnector:
    """pyrfc로 BAPI를 직접 부른다. 설치: SAP NW RFC SDK(SAP 계정 필요) + `pip install pyrfc`.

    전기  BAPI_GOODSMVT_CREATE (헤더 텍스트 = 멱등키) → BAPI_TRANSACTION_COMMIT(WAIT=X)
    취소  BAPI_GOODSMVT_CANCEL → COMMIT      오류(RETURN의 E·A)는 ROLLBACK 후 '실패(조치 필요)'
    재시도 전 확인  RFC_READ_TABLE MKPF (BKTXT = 멱등키) · 취소는 MSEG (SMBLN = 원문서)
    재고  RFC_READ_TABLE MARD (가용 재고 LABST)
    """

    label = MODES["sap_rfc"]

    def _connect(self):
        try:
            import pyrfc
        except ImportError as exc:
            raise ErpError("pyrfc가 설치되어 있지 않습니다 (SAP NW RFC SDK + pip install pyrfc).", retryable=False) from exc
        params = {k: v for k, v in {
            "ashost": config.SAP_RFC_ASHOST, "sysnr": config.SAP_RFC_SYSNR, "mshost": config.SAP_RFC_MSHOST,
            "sysid": config.SAP_RFC_SYSID, "group": config.SAP_RFC_GROUP, "client": config.SAP_CLIENT,
            "user": config.SAP_USER, "passwd": config.SAP_PASSWORD, "lang": config.SAP_LANGUAGE,
        }.items() if v}
        if not params.get("ashost") and not params.get("mshost"):
            raise ErpError("MM_SAP_RFC_ASHOST(또는 MM_SAP_RFC_MSHOST)가 설정되지 않았습니다.", retryable=False)
        try:
            return pyrfc.Connection(**params)
        except Exception as exc:
            raise ErpError(f"SAP RFC 연결 실패: {type(exc).__name__}: {exc}",
                           retryable=type(exc).__name__ in RFC_RETRYABLE) from exc

    @staticmethod
    def _call(conn, fm: str, **kw) -> dict:
        try:
            return conn.call(fm, **kw)
        except Exception as exc:
            raise ErpError(f"{fm}: {type(exc).__name__}: {exc}", retryable=type(exc).__name__ in RFC_RETRYABLE) from exc

    def _check(self, conn, ret) -> None:
        rows = ret if isinstance(ret, list) else [ret] if ret else []
        errors = [r for r in rows if r.get("TYPE") in ("E", "A")]
        if errors:
            self._call(conn, "BAPI_TRANSACTION_ROLLBACK")
            msg = " / ".join(f"{r.get('ID', '')} {r.get('NUMBER', '')}: {r.get('MESSAGE', '')}".strip() for r in errors)
            raise ErpError(f"SAP: {msg}"[:500], retryable=False)

    def _read_table(self, conn, table: str, fields: list[str], where: list[str], rows: int = 0) -> list[list[str]]:
        res = self._call(conn, "RFC_READ_TABLE", QUERY_TABLE=table, DELIMITER="|", ROWCOUNT=rows,
                         FIELDS=[{"FIELDNAME": f} for f in fields], OPTIONS=[{"TEXT": w} for w in where])
        return [[c.strip() for c in r["WA"].split("|")] for r in res.get("DATA", [])]

    def _find(self, conn, key: str) -> Posted | None:
        rows = self._read_table(conn, "MKPF", ["MBLNR", "MJAHR"], [f"BKTXT = '{key}'"], rows=1)
        return Posted(rows[0][0], rows[0][1]) if rows else None

    @staticmethod
    def item(p: dict) -> dict:
        matnr = _alpha(p["material"], 18)
        it = {"MATERIAL_LONG" if len(matnr) > 18 else "MATERIAL": matnr, "PLANT": p["plant"],
              "STGE_LOC": p["storageLocation"], "MOVE_TYPE": p["movementType"], "ENTRY_QNT": p["quantity"],
              "ENTRY_UOM": erp_unit(p["unit"]), "BATCH": p.get("batch", ""),
              "COSTCENTER": _alpha(p.get("costCenter", ""), 10),
              "PO_NUMBER": p.get("purchaseOrder", ""), "PO_ITEM": _alpha(p.get("purchaseOrderItem", ""), 5),
              "MOVE_PLANT": p.get("receivingPlant", ""), "MOVE_STLOC": p.get("receivingStorageLocation", "")}
        if p.get("purchaseOrder"):
            it["MVT_IND"] = "B"
        return {k: v for k, v in it.items() if v not in ("", None)}

    def send(self, payload: dict, attempt: int = 1) -> Posted:
        conn = self._connect()
        try:
            if payload["action"] == "CANCEL":
                if attempt > 1:
                    rows = self._read_table(conn, "MSEG", ["MBLNR", "MJAHR"],
                                            [f"SMBLN = '{payload['originalDocument']}' AND SJAHR = '{payload['originalYear']}'"],
                                            rows=1)
                    if rows:
                        return Posted(rows[0][0], rows[0][1])
                res = self._call(conn, "BAPI_GOODSMVT_CANCEL", MATERIALDOCUMENT=payload["originalDocument"],
                                 MATDOCUMENTYEAR=payload["originalYear"],
                                 GOODSMVT_PSTNG_DATE=_yyyymmdd(payload["postingDate"]))
                self._check(conn, res.get("RETURN"))
                self._call(conn, "BAPI_TRANSACTION_COMMIT", WAIT="X")
                head = res.get("GOODSMVT_HEADRET") or {}
                return Posted(str(head.get("MAT_DOC") or ""), str(head.get("DOC_YEAR") or payload["postingDate"][:4]))
            key = payload["idempotencyKey"]
            if attempt > 1:
                found = self._find(conn, key)
                if found:
                    return found
            res = self._call(conn, "BAPI_GOODSMVT_CREATE",
                             GOODSMVT_HEADER={"PSTNG_DATE": _yyyymmdd(payload["postingDate"]),
                                              "DOC_DATE": _yyyymmdd(payload.get("documentDate") or payload["postingDate"]),
                                              "HEADER_TXT": key[:25], "REF_DOC_NO": (payload.get("reference") or "")[:16]},
                             GOODSMVT_CODE={"GM_CODE": gm_code(payload["movementType"])},
                             GOODSMVT_ITEM=[self.item(payload)])
            self._check(conn, res.get("RETURN"))
            doc = str(res.get("MATERIALDOCUMENT") or "")
            if not doc:
                self._call(conn, "BAPI_TRANSACTION_ROLLBACK")
                raise ErpError("SAP가 자재문서번호를 돌려주지 않았습니다.", retryable=False)
            self._call(conn, "BAPI_TRANSACTION_COMMIT", WAIT="X")
            return Posted(doc, str(res.get("MATDOCUMENTYEAR") or payload["postingDate"][:4]))
        finally:
            conn.close()

    def ping(self) -> str:
        conn = self._connect()
        try:
            conn.ping()
            return "연결됨 (RFC)"
        finally:
            conn.close()

    def fetch_master(self, since: str):
        raise ErpError("RFC 모드는 마스터 동기화를 지원하지 않습니다. 연계서버(http)·OData를 쓰거나 엑셀로 올리세요.",
                       retryable=False)

    def fetch_stock(self, plants: list[str]) -> list[dict]:
        conn = self._connect()
        try:
            where = ["LABST <> 0"]
            if plants:
                where.append("AND (")
                where += [f"{'OR ' if i else ''}WERKS = '{p}'" for i, p in enumerate(plants)]
                where.append(")")
            rows = self._read_table(conn, "MARD", ["MATNR", "WERKS", "LGORT", "LABST"], where)
        finally:
            conn.close()
        return [{"material": r[0].lstrip("0") or r[0], "plant": r[1], "storageLocation": r[2],
                 "quantity": float(r[3] or 0)} for r in rows]


# ── rest: 기타 ERP (매핑 파일) ────────────────────────────────
DEFAULT_REST_MAP = {
    "post": {"method": "POST", "path": "/goods-movements"},          # body가 없으면 중립 JSON 그대로
    "cancel": {"method": "POST", "path": "/goods-movements"},
    "document_field": "materialDocument", "year_field": "year",
}


def render(template, ctx: dict, for_url: bool = False):
    """매핑 파일의 값 틀을 채운다.
      "{quantity}"                     → 값 그대로(숫자는 숫자로)
      "MM {reference}"                 → 문자열에 끼워 넣기
      {"$map": "movementType", "values": {"101": "I01"}, "default": "ETC"}   → 코드 변환
      {"$date": "postingDate", "format": "%Y%m%d"}                          → 날짜 형식
    """
    if isinstance(template, str):
        whole = re.fullmatch(r"\{(\w+)\}", template)
        if whole and not for_url:
            return ctx.get(whole.group(1), "")

        def sub(m):
            v = "" if ctx.get(m.group(1)) is None else str(ctx.get(m.group(1)))
            return urllib.parse.quote(v, safe="") if for_url else v
        return re.sub(r"\{(\w+)\}", sub, template)
    if isinstance(template, dict):
        if "$map" in template:
            v = ctx.get(template["$map"])
            return template.get("values", {}).get(str(v), template.get("default", v))
        if "$date" in template:
            v = str(ctx.get(template["$date"]) or "")
            return datetime.strptime(v[:10], "%Y-%m-%d").strftime(template.get("format", "%Y-%m-%d")) if v else ""
        return {k: render(v, ctx, for_url) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, ctx, for_url) for v in template]
    return template


def _pick(row: dict, spec):
    """마스터·재고 응답 한 행에서 값 꺼내기: "경로" 또는 {"field": "useYn", "equals": "N"} (참/거짓)."""
    if isinstance(spec, dict):
        v = dig(row, spec.get("field", ""))
        if "equals" in spec:
            return str(v) == str(spec["equals"])
        return v
    return dig(row, spec)


def load_rest_map() -> dict:
    path = config.ERP_REST_MAP
    if not path:
        return DEFAULT_REST_MAP
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ErpError(f"ERP 매핑 파일을 읽지 못했습니다({path}): {exc}", retryable=False) from exc
    return {**DEFAULT_REST_MAP, **data}


class RestConnector:
    """SAP가 아닌 ERP의 REST API. 주소·필드 이름·코드 변환은 매핑 파일에서 정한다(erp_maps/README.md).

    인증 MM_ERP_REST_AUTH: "bearer:토큰" | "basic:아이디:비밀번호" | "header:헤더이름:값"
    """

    label = MODES["rest"]

    def __init__(self):
        self.map = load_rest_map()
        self.base = config.ERP_REST_URL.rstrip("/")
        self.opener = urllib.request.build_opener()

    def _headers(self) -> dict:
        auth = config.ERP_REST_AUTH
        h = dict(self.map.get("headers", {}))
        kind, _, rest = auth.partition(":")
        if kind == "bearer" and rest:
            h["Authorization"] = f"Bearer {rest}"
        elif kind == "basic" and rest:
            h["Authorization"] = "Basic " + base64.b64encode(rest.encode()).decode()
        elif kind == "header" and ":" in rest:
            name, _, value = rest.partition(":")
            h[name] = value
        return h

    def _request(self, spec: dict, ctx: dict, idem: str = ""):
        require_secure(self.base, "ERP 주소(MM_ERP_REST_URL)")
        method = spec.get("method", "POST").upper()
        url = self.base + render(spec.get("path", ""), ctx, for_url=True)
        body = None
        if method not in ("GET", "DELETE"):
            body = render(spec["body"], ctx) if "body" in spec else {k: v for k, v in ctx.items() if k != "since"}
        headers = {**self._headers(), **({self.map.get("idempotency_header", "Idempotency-Key"): idem} if idem else {})}
        return http_json(self.opener, method, url, body, headers)[0]

    def _result(self, resp, payload: dict) -> Posted:
        ok_field = self.map.get("success_field")
        if ok_field and not _truthy(dig(resp, ok_field)):
            msg = dig(resp, self.map.get("error_field", "message")) or "ERP가 요청을 거부했습니다."
            raise ErpError(f"ERP: {msg}"[:500], retryable=False)
        doc = dig(resp, self.map["document_field"])
        if doc in (None, ""):
            raise ErpError(f"ERP 응답에 문서번호({self.map['document_field']})가 없습니다: {str(resp)[:200]}",
                           retryable=False)
        year = dig(resp, self.map.get("year_field", "")) if self.map.get("year_field") else None
        return Posted(str(doc), str(year or payload["postingDate"][:4]))

    def send(self, payload: dict, attempt: int = 1) -> Posted:
        if attempt > 1 and payload["action"] == "POST" and self.map.get("lookup"):
            resp = self._request(self.map["lookup"], payload)
            doc = dig(resp, self.map["lookup"].get("document_field") or self.map["document_field"])
            if doc not in (None, "", []):
                return Posted(str(doc), payload["postingDate"][:4])
        spec = self.map["cancel" if payload["action"] == "CANCEL" else "post"]
        return self._result(self._request(spec, payload, payload["idempotencyKey"]), payload)

    def ping(self) -> str:
        spec = self.map.get("ping") or {"method": "GET", "path": ""}
        try:
            self._request(spec, {})
            return "연결됨"
        except ErpError as exc:
            if exc.status and exc.status < 500 and exc.status not in (401, 403):
                return f"연결됨 (HTTP {exc.status})"
            raise

    def _list(self, spec: dict, ctx: dict) -> list[dict]:
        resp = self._request({"method": "GET", **spec}, ctx)
        rows = dig(resp, spec.get("list_field", "")) or []
        return [{k: _pick(r, src) for k, src in spec.get("fields", {}).items()} if spec.get("fields") else r
                for r in rows]

    def fetch_master(self, since: str):
        master = self.map.get("master") or {}
        if not master:
            raise ErpError("매핑 파일에 master(자재·원가센터) 항목이 없습니다.", retryable=False)
        mats = self._list(master["materials"], {"since": since}) if master.get("materials") else []
        ccs = self._list(master["cost_centers"], {"since": since}) if master.get("cost_centers") else []
        return mats, ccs

    def fetch_stock(self, plants: list[str]) -> list[dict]:
        if not self.map.get("stock"):
            raise ErpError("매핑 파일에 stock 항목이 없습니다.", retryable=False)
        return self._list(self.map["stock"], {"plants": ",".join(plants)})


def _truthy(v) -> bool:
    return v not in (None, False, 0, "", "0", "false", "False", "N", "n", "FAIL", "E")


# ── file: 공유 폴더 파일 연계 ─────────────────────────────────
FILE_FIELDS = ["idempotencyKey", "action", "postingDate", "documentDate", "movementType", "transactionType",
               "material", "itemCode", "plant", "storageLocation", "warehouseCode", "quantity", "unit", "batch",
               "purchaseOrder", "purchaseOrderItem", "costCenter", "receivingPlant", "receivingStorageLocation",
               "transferNo", "reference", "headerText", "enteredBy", "originalKey", "originalDocument",
               "originalYear", "reason", "sourceSystem"]


class FileConnector:
    """ERP가 API를 열어 주지 않을 때(더존·영림원 등 파일 수신, 야간 일괄 반영).

    보내기  {MM_ERP_FILE_DIR}/outbound/MM-TX-<id>.json(또는 .csv, UTF-8 BOM).
            임시 이름으로 쓴 뒤 바꿔 반쯤 쓴 파일이 읽히지 않게 한다.
            같은 거래는 같은 파일 이름이라 다시 써도 한 건이다.
    결과    ERP가 {dir}/inbound/MM-TX-<id>.ack.json 에 {"document": "..", "year": "..", "error": ""} 를 남기면
            배치가 문서번호를 저장하거나(error가 있으면) '실패(조치 필요)'로 바꾼다. 읽은 파일은 inbound/processed/로.
    마스터  inbound/master_materials.(json|csv), inbound/master_cost_centers.(json|csv)   재고  inbound/stock.(json|csv)
    """

    label = MODES["file"]

    def __init__(self):
        if not config.ERP_FILE_DIR:
            raise ErpError("MM_ERP_FILE_DIR(연계 폴더)이 설정되지 않았습니다.", retryable=False)
        self.root = Path(config.ERP_FILE_DIR)
        self.out, self.inbox = self.root / "outbound", self.root / "inbound"

    def _write(self, name: str, data: bytes) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        tmp = self.out / f".{name}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.out / name)

    def send(self, payload: dict, attempt: int = 1) -> Posted:
        key = payload["idempotencyKey"]
        try:
            if config.ERP_FILE_FORMAT == "csv":
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=FILE_FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerow({k: payload.get(k, "") for k in FILE_FIELDS})
                self._write(f"{key}.csv", buf.getvalue().encode("utf-8-sig"))
            else:
                self._write(f"{key}.json", json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError as exc:
            raise ErpError(f"연계 폴더에 쓰지 못했습니다: {exc}", retryable=True) from exc
        return Posted(f"FILE:{key}", payload["postingDate"][:4])      # ERP 결과 파일이 오면 실제 번호로 바뀐다

    def ping(self) -> str:
        try:
            self._write(".ping", b"ok")
            (self.out / ".ping").unlink()
        except OSError as exc:
            raise ErpError(f"연계 폴더에 쓸 수 없습니다: {exc}", retryable=False) from exc
        return f"연결됨 (폴더 쓰기 가능: {self.out})"

    def _done(self, path: Path) -> None:
        dest = self.inbox / "processed"
        dest.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest / f"{datetime.now():%Y%m%d%H%M%S}_{path.name}")

    def _read_rows(self, stem: str) -> list[dict] | None:
        for ext in ("json", "csv"):
            p = self.inbox / f"{stem}.{ext}"
            if p.exists():
                if ext == "json":
                    rows = json.loads(p.read_text(encoding="utf-8-sig"))
                else:
                    rows = list(csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig"))))
                self._done(p)
                return rows
        return None

    def fetch_master(self, since: str):
        mats = self._read_rows("master_materials") or []
        ccs = self._read_rows("master_cost_centers") or []
        for m in mats:                                  # CSV는 모두 문자열 → 참/거짓 칸 정리
            for k in ("deleted", "batchManaged", "shelfLifeManaged"):
                if isinstance(m.get(k), str):
                    m[k] = m[k].strip().upper() in ("1", "Y", "TRUE", "X")
        for c in ccs:
            if isinstance(c.get("active"), str):
                c["active"] = c["active"].strip().upper() not in ("0", "N", "FALSE")
        return mats, ccs

    def fetch_stock(self, plants: list[str]) -> list[dict]:
        rows = self._read_rows("stock")
        if rows is None:
            raise ErpError(f"재고 파일이 없습니다 ({self.inbox / 'stock.json|csv'}).", retryable=False)
        return rows

    def collect_acks(self, conn) -> dict:
        """ERP 처리 결과 파일을 읽어 대기열에 반영한다 (process_outbox가 부른다)."""
        from core.utils import now_str
        counts = {"acked": 0, "rejected": 0}
        if not self.inbox.exists():
            return counts
        for p in sorted(self.inbox.glob("MM-TX-*.ack.json")):
            m = re.fullmatch(r"MM-TX-(\d+)\.ack\.json", p.name)
            try:
                ack = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue                                # 아직 쓰는 중일 수 있다 → 다음 주기에
            if not m:
                continue
            tx_id = int(m.group(1))
            if ack.get("error"):
                conn.execute("UPDATE sap_outbox SET status = 'FAILED', last_error = ?, updated_at = ? WHERE tx_id = ?",
                             (f"ERP 처리 오류: {ack['error']}"[:500], now_str(), tx_id))
                counts["rejected"] += 1
            elif ack.get("document"):
                conn.execute("UPDATE sap_outbox SET sap_doc_no = ?, sap_doc_year = ?, updated_at = ? WHERE tx_id = ?",
                             (str(ack["document"]), str(ack.get("year") or ""), now_str(), tx_id))
                counts["acked"] += 1
            self._done(p)
        return counts


# ── 선택 ─────────────────────────────────────────────────────
CONNECTORS = {"mock": MockConnector, "http": EaiConnector, "sap_odata": SapODataConnector,
              "sap_rfc": SapRfcConnector, "rest": RestConnector, "file": FileConnector}


def enabled() -> bool:
    return config.SAP_MODE in CONNECTORS


def connector():
    cls = CONNECTORS.get(config.SAP_MODE)
    if cls is None:
        raise ErpError(f"ERP 연동이 꺼져 있거나 알 수 없는 방식입니다: {config.SAP_MODE}", retryable=False)
    return cls()


def settings_view() -> list[tuple[str, str]]:
    """화면에 보여 줄 연결 설정 (비밀번호·토큰은 넣었는지만)."""
    mode = config.SAP_MODE
    rows = [("방식", f"{mode} — {MODES.get(mode, '알 수 없음')}")]
    secret = lambda v: "설정됨" if v else "없음"  # noqa: E731
    if mode == "http":
        rows += [("연계서버", config.SAP_ENDPOINT or "(없음)"), ("토큰", secret(config.SAP_TOKEN))]
    elif mode == "sap_odata":
        rows += [("SAP 주소", config.SAP_ODATA_URL or "(없음)"), ("클라이언트", config.SAP_CLIENT or "-"),
                 ("인증", "OAuth2" if config.SAP_OAUTH_TOKEN_URL else ("Basic" if config.SAP_USER else "없음"))]
    elif mode == "sap_rfc":
        rows += [("애플리케이션 서버", config.SAP_RFC_ASHOST or config.SAP_RFC_MSHOST or "(없음)"),
                 ("시스템 번호 · 클라이언트", f"{config.SAP_RFC_SYSNR or '-'} · {config.SAP_CLIENT or '-'}"),
                 ("사용자", config.SAP_USER or "(없음)"), ("비밀번호", secret(config.SAP_PASSWORD))]
    elif mode == "rest":
        rows += [("ERP 주소", config.ERP_REST_URL or "(없음)"), ("매핑 파일", config.ERP_REST_MAP or "(기본: 중립 JSON)"),
                 ("인증", config.ERP_REST_AUTH.partition(":")[0] or "없음")]
    elif mode == "file":
        rows += [("연계 폴더", config.ERP_FILE_DIR or "(없음)"), ("파일 형식", config.ERP_FILE_FORMAT)]
    return rows


@dataclass
class PingResult:
    ok: bool
    message: str
    extra: dict = field(default_factory=dict)


def ping() -> PingResult:
    try:
        return PingResult(True, connector().ping())
    except ErpError as exc:
        return PingResult(False, str(exc))

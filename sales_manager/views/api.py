"""외부 연동 REST API (/api/v1) — 그룹웨어·BI·ERP 등 다른 시스템이 호출한다.

  인증: Authorization: Bearer sk_xxxxxxxx_... (관리자 > API 연동에서 발급)
  응답: JSON. 목록은 {"data": [...], "page": {"limit", "offset", "total"}}
        오류는 {"error": {"code", "message"}} (400 / 401 / 403 / 404 / 409 / 422 / 429)
  세션 쿠키·CSRF 를 쓰지 않는다. 데이터 범위는 키의 대리 사용자 권한을 따른다.
"""
from __future__ import annotations

import time
import re
from datetime import date
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from core import api_keys, database
from core.observability import client_ip
from core import enterprise as ent
from core import quotes as qt
from core import sales_db as db

bp = Blueprint("api", __name__, url_prefix="/api/v1")
MAX_LIMIT = 500


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


@bp.errorhandler(ApiError)
def _api_error(err: ApiError):
    res = jsonify(error={"code": err.code, "message": err.message})
    res.status_code = err.status
    res.headers.update(err.headers)
    return res


@bp.errorhandler(PermissionError)
def _forbidden(err):
    return _api_error(ApiError(403, "forbidden", str(err)))


@bp.errorhandler(404)
def _not_found(err):
    return _api_error(ApiError(404, "not_found", "요청한 자원이 없습니다."))


@bp.errorhandler(Exception)
def _server_error(err):
    if hasattr(err, "code") and isinstance(err.code, int) and err.code < 500:   # HTTPException
        return _api_error(ApiError(err.code, "http_error", getattr(err, "description", str(err))))
    current_app.logger.exception("API 오류")
    return _api_error(ApiError(500, "server_error", "서버 오류가 발생했습니다."))


# ----------------------------------------------------------------------------
# 인증 · 한도
# ----------------------------------------------------------------------------
@bp.before_request
def _authenticate():
    g.api_started = time.perf_counter()
    db.set_ip(client_ip())
    if request.endpoint in ("api.openapi",):
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Authorization: Bearer <API 키> 헤더가 필요합니다.",
                       {"WWW-Authenticate": 'Bearer realm="sales-api"'})
    client, message = api_keys.authenticate(header[7:].strip(), client_ip())
    if not client:
        db.audit("API인증실패", "시스템", None, {"IP": client_ip(), "사유": message})
        raise ApiError(401, "unauthorized", message, {"WWW-Authenticate": 'Bearer error="invalid_token"'})
    allowed, remaining, reset = api_keys.hit(client)
    g.rate = (int(client["rate_limit"]), remaining, reset)
    if not allowed:
        raise ApiError(429, "rate_limited", f"분당 호출 한도({client['rate_limit']}회)를 넘었습니다.",
                       {"Retry-After": str(reset)})
    user = client["user"]
    db.set_context(actor=f"API:{client['name']}", owner_scope=ent.visible_owners(user), actor_id=user["id"])
    g.api_client, g.user = client, user
    api_keys.touch(int(client["id"]))
    return None


@bp.after_request
def _headers(response):
    if getattr(g, "rate", None):
        limit, remaining, reset = g.rate
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
    response.headers["Cache-Control"] = "no-store"
    return response


def require(scope: str) -> None:
    if scope not in g.api_client["scope_set"]:
        raise ApiError(403, "insufficient_scope", f"이 키에는 '{scope}' 권한이 없습니다.")


def _page() -> tuple[int, int]:
    try:
        limit = min(MAX_LIMIT, max(1, int(request.args.get("limit", 100))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError as exc:
        raise ApiError(400, "bad_request", "limit·offset 은 정수여야 합니다.") from exc
    return limit, offset


def _list(sql: str, params: list, scope_alias: str | None, order: str) -> Any:
    limit, offset = _page()
    if scope_alias is not None:
        sc, sp = db._scope_clause(scope_alias)
        sql, params = sql + sc, params + sp
    total = int(db._scalar(f"SELECT COUNT(*) FROM ({sql}) t", params))
    data = database.rows(f"{sql} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset])
    return jsonify(data=data, page={"limit": limit, "offset": offset, "total": total})


def _since(col: str, sql: str, params: list) -> tuple[str, list]:
    since = request.args.get("updated_since")
    if since:
        sql += f" AND {col} >= ?"
        params.append(since.replace("T", " "))
    return sql, params


# ----------------------------------------------------------------------------
# 조회
# ----------------------------------------------------------------------------
@bp.route("/me")
def me():
    c = g.api_client
    return jsonify(client=c["name"], scopes=sorted(c["scope_set"]), acting_user=g.user["name"],
                   role=g.user["role"], rate_limit=int(c["rate_limit"]))


@bp.route("/customers")
def customers():
    require("customers:read")
    sql = ("SELECT id, name, biz_no, industry, grade, status, owner, owner_id, credit_limit, payment_terms, "
           "erp_code, updated_at FROM customers c WHERE 1=1")
    params: list = []
    if request.args.get("q"):
        sql += " AND name LIKE ?"
        params.append(f"%{request.args['q']}%")
    sql, params = _since("updated_at", sql, params)
    return _list(sql, params, "c", "id")


@bp.route("/deals")
def deals():
    require("deals:read")
    sql = ("SELECT id, customer_id, title, stage, list_amount, amount, discount_rate, probability, "
           "expected_close, forecast_category, approval_status, owner, owner_id, updated_at FROM deals d WHERE 1=1")
    params: list = []
    if request.args.get("stage"):
        sql += " AND stage = ?"
        params.append(request.args["stage"])
    sql, params = _since("updated_at", sql, params)
    return _list(sql, params, "d", "id")


SALE_COLS = ("id, customer_id, deal_id, quote_id, product_id, sale_date, item, item_code, qty, unit_price, "
             "amount AS supply_amount, tax_type, vat_amount, total_amount, paid_amount, status, due_date, "
             "erp_status, erp_doc_no, owner, owner_id, created_at")


@bp.route("/sales", methods=["GET"])
def sales():
    require("sales:read")
    sql = f"SELECT {SALE_COLS} FROM sales s WHERE 1=1"
    params: list = []
    for arg, cond in (("from", "sale_date >= ?"), ("to", "sale_date <= ?"), ("status", "status = ?"),
                      ("customer_id", "customer_id = ?")):
        if request.args.get(arg):
            value = request.args[arg]
            if arg in ("from", "to") and not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", value):
                raise ApiError(400, "bad_request", f"{arg} 는 YYYY-MM-DD 또는 YYYY-MM 형식이어야 합니다.")
            if arg == "customer_id" and not value.isdigit():
                raise ApiError(400, "bad_request", "customer_id 는 정수여야 합니다.")
            if arg == "to" and len(value) == 7:
                value += "-31"
            sql += f" AND {cond}"
            params.append(value)
    return _list(sql, params, "s", "sale_date, id")


@bp.route("/products")
def products():
    require("products:read")
    sql = ("SELECT id, code, name, category, unit, list_price, tax_type, erp_material, active, updated_at "
           "FROM products WHERE 1=1")
    params: list = []
    if request.args.get("active") in ("1", "true"):
        sql += " AND active = 1"
    return _list(sql, params, None, "code")


@bp.route("/products/<int:pid>/price")
def product_price(pid: int):
    require("products:read")
    from core import catalog
    cid = request.args.get("customer_id", type=int)
    if cid:
        db.check_record_scope(db.get_customer(cid), "거래처")
    try:
        return jsonify(catalog.price_for(cid, pid, request.args.get("on")))
    except ValueError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc


@bp.route("/quotes")
def quotes():
    require("quotes:read")
    sql = ("SELECT id, quote_no, revision, customer_id, deal_id, title, issue_date, valid_until, status, "
           "list_total, supply_amount, vat_amount, total_amount, discount_rate, owner, owner_id, updated_at "
           "FROM quotes q WHERE status <> '대체됨'")
    params: list = []
    if request.args.get("status"):
        sql += " AND status = ?"
        params.append(request.args["status"])
    return _list(sql, params, "q", "id")


@bp.route("/quotes/<int:qid>")
def quote(qid: int):
    require("quotes:read")
    try:
        q = qt.get_quote(qid)
    except (TypeError, KeyError, ValueError) as exc:
        raise ApiError(404, "not_found", "견적이 없습니다.") from exc
    keep = ["id", "quote_no", "revision", "customer_id", "customer_name", "deal_id", "title", "issue_date",
            "valid_until", "status", "effective_status", "list_total", "supply_amount", "vat_amount",
            "total_amount", "discount_rate", "terms", "owner", "owner_id"]
    items = [{k: it[k] for k in ("line_no", "product_id", "item_code", "item_name", "unit", "qty", "list_price",
                                 "unit_price", "discount_rate", "tax_type", "supply_amount", "vat_amount")}
             for it in q["items"]]
    return jsonify({**{k: q.get(k) for k in keep}, "items": items})


# ----------------------------------------------------------------------------
# 쓰기
# ----------------------------------------------------------------------------
def _create_sale(body: dict) -> tuple[int, dict]:
    missing = [k for k in ("customer_id", "item", "qty", "unit_price") if body.get(k) in (None, "")]
    if missing:
        raise ApiError(422, "validation_error", f"필수 항목이 없습니다: {', '.join(missing)}")
    try:
        qty, unit_price = int(body["qty"]), int(body["unit_price"])
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "validation_error", "qty·unit_price 는 정수여야 합니다.") from exc
    if not 1 <= qty <= 10 ** 9 or not 1 <= unit_price <= 10 ** 13:
        raise ApiError(422, "validation_error", "수량은 1~10억, 단가는 1원 이상이어야 합니다.")
    if body.get("status") not in (None, "", db.SALE_STATUS[0]):
        raise ApiError(422, "validation_error", "API 로 등록하는 매출은 '입금대기'로만 — 입금은 /erp/payments 로 보내세요.")
    if body.get("product_id") not in (None, "") and not database.rows("SELECT id FROM products WHERE id=?",
                                                                     [int(body["product_id"])]):
        raise ApiError(422, "validation_error", f"없는 품목입니다: {body['product_id']}")
    if body.get("deal_id") not in (None, ""):
        deal = database.rows("SELECT customer_id FROM deals WHERE id=?", [int(body["deal_id"])])
        if not deal or int(deal[0]["customer_id"]) != int(body["customer_id"]):
            raise ApiError(422, "validation_error", "deal_id 가 이 거래처의 영업기회가 아닙니다.")
    if body.get("tax_type") and body["tax_type"] not in db.TAX_TYPES:
        raise ApiError(422, "validation_error", f"tax_type 은 {', '.join(db.TAX_TYPES)} 중 하나입니다.")
    try:
        db.check_record_scope(db.get_customer(int(body["customer_id"])), "거래처")
        if body.get("deal_id"):
            db.check_record_scope(db.get_deal(int(body["deal_id"])), "영업기회")
        sid = db.upsert_sale({
            "customer_id": int(body["customer_id"]), "deal_id": body.get("deal_id"),
            "sale_date": body.get("sale_date") or date.today().isoformat(), "item": str(body["item"]),
            "item_code": body.get("item_code"), "product_id": body.get("product_id"),
            "qty": qty, "unit_price": unit_price, "amount": qty * unit_price,
            "tax_type": body.get("tax_type") or "과세", "owner_id": body.get("owner_id") or g.user["id"],
            "status": db.SALE_STATUS[0], "memo": (str(body["memo"])[:1000] if body.get("memo") is not None else None)})
    except (ValueError, TypeError) as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    return 201, database.rows(f"SELECT {SALE_COLS} FROM sales WHERE id=?", [sid])[0]


@bp.route("/sales", methods=["POST"])
def create_sale():
    require("sales:write")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "JSON 본문이 필요합니다 (Content-Type: application/json).")
    key = request.headers.get("Idempotency-Key", "").strip()
    cid = int(g.api_client["id"])
    if key:
        if len(key) > 200:
            raise ApiError(400, "bad_request", "Idempotency-Key 는 200자 이하여야 합니다.")
        state, saved = api_keys.idem_begin(cid, key, request.get_data())
        if state == "replay":
            res = jsonify(saved[1])
            res.status_code = saved[0]
            res.headers["Idempotent-Replayed"] = "true"
            return res
        if state == "conflict":
            raise ApiError(422, "idempotency_conflict", "같은 Idempotency-Key 로 다른 내용을 보냈습니다.")
        if state == "busy":
            raise ApiError(409, "idempotency_in_progress", "같은 Idempotency-Key 요청을 처리 중입니다.")
    try:
        status, payload = _create_sale(body)
    except ApiError as err:
        if key:
            api_keys.idem_finish(cid, key, err.status, {"error": {"code": err.code, "message": err.message}})
        raise
    except PermissionError as exc:
        if key:
            api_keys.idem_finish(cid, key, 403, {"error": {"code": "forbidden", "message": str(exc)}})
        raise
    except Exception:
        if key:
            api_keys.idem_finish(cid, key, 500, {})
        raise
    if key:
        api_keys.idem_finish(cid, key, status, payload)
    res = jsonify(payload)
    res.status_code = status
    return res


# ----------------------------------------------------------------------------
# ERP → CRM 수신 (ERP·EAI 가 호출). 본문 {"items": [...]} 최대 1,000건, 항목별 결과를 돌려준다
# ----------------------------------------------------------------------------
MAX_ITEMS = 1000


def _items() -> list[dict]:
    body = request.get_json(silent=True)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise ApiError(400, "bad_request", '본문은 {"items": [ {...}, ... ]} 형식이어야 합니다.')
    if len(items) > MAX_ITEMS:
        raise ApiError(413, "too_many_items", f"한 번에 {MAX_ITEMS:,}건까지 보낼 수 있습니다.")
    return items


def _erp_result(results: list[dict]):
    counts: dict[str, int] = {}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    return jsonify(results=results, summary=counts)


@bp.route("/erp/payments", methods=["POST"])
def erp_payments():
    """누적 입금액 — 같은 값을 여러 번 보내도 차액만 반영(멱등)."""
    require("erp:write")
    from core import erp
    return _erp_result(erp.receive_payments(_items()))


@bp.route("/erp/acks", methods=["POST"])
def erp_acks():
    """전표번호 회신 [{ref: "CRM-123", erp_doc_no, ok: true|false, message}] (파일·EAI 비동기 연동)."""
    require("erp:write")
    from core import erp
    results = []
    for i in _items():
        try:
            ok = i.get("ok", True)
            if isinstance(ok, str):                 # "false" 를 성공으로 읽지 않게 (bool("false") 는 True)
                if ok.strip().lower() not in ("true", "false", "1", "0"):
                    raise ValueError(f"ok 값이 올바르지 않습니다: {ok}")
                ok = ok.strip().lower() in ("true", "1")
            r = erp.receive_ack(str(i.get("ref") or ""), str(i.get("erp_doc_no") or ""), bool(ok),
                                str(i.get("message") or ""))
            results.append({"ref": i.get("ref"), "result": "반영", **r})
        except (ValueError, PermissionError) as exc:
            results.append({"ref": i.get("ref"), "result": "오류", "message": str(exc)})
    return _erp_result(results)


@bp.route("/erp/credit", methods=["POST"])
def erp_credit():
    """여신한도 [{erp_code, credit_limit}] — 재무(ERP)가 원장."""
    require("erp:write")
    from core import erp
    return _erp_result(erp.receive_credit(_items()))


@bp.route("/erp/fx", methods=["POST"])
def erp_fx():
    """환율 [{currency, rate_date, rate}] — 1 외화당 원."""
    require("erp:write")
    from core import entities as ent_mod
    results = []
    for i in _items():
        try:
            ent_mod.set_rate(i.get("currency"), i.get("rate_date"), i.get("rate"), "ERP")
            results.append({"currency": i.get("currency"), "result": "반영"})
        except ValueError as exc:
            results.append({"currency": i.get("currency"), "result": "오류", "message": str(exc)})
    return _erp_result(results)


@bp.route("/etax/acks", methods=["POST"])
def etax_acks():
    """전자세금계산서 발행 결과 회신 [{request_id, approval_no, ok, message}] (ASP·파일 연동)."""
    require("erp:write")
    from core import etax
    results = []
    for i in _items():
        try:
            r = etax.complete(int(i.get("request_id") or 0), str(i.get("approval_no") or ""),
                              bool(i.get("ok", True)), str(i.get("message") or ""))
            results.append({"request_id": i.get("request_id"), "result": r["status"], **r})
        except (ValueError, PermissionError, TypeError) as exc:
            results.append({"request_id": i.get("request_id"), "result": "오류", "message": str(exc)})
    return _erp_result(results)


@bp.route("/erp/customers", methods=["POST"])
def erp_customers():
    """거래처(고객) 마스터 — ERP 코드 기준 등록·수정, 거래정지."""
    require("erp:write")
    from core import erp
    return _erp_result(erp.receive_customers(_items()))


@bp.route("/erp/products", methods=["POST"])
def erp_products():
    """자재(품목) 마스터 [{code, name, unit, list_price, tax_type, category, active}]."""
    require("erp:write")
    if not ent.has_role(g.user, "ADMIN"):
        raise ApiError(403, "forbidden", "품목 마스터 수신은 대리 사용자가 시스템관리자인 키만 할 수 있습니다.")
    from core import erp
    return _erp_result(erp.receive_products(_items()))


# ----------------------------------------------------------------------------
# OpenAPI
# ----------------------------------------------------------------------------
def _list_op(summary: str, scope: str, params: list[dict] | None = None) -> dict:
    return {"summary": summary, "security": [{"bearer": [scope]}],
            "parameters": [{"$ref": "#/components/parameters/limit"}, {"$ref": "#/components/parameters/offset"},
                           *(params or [])],
            "responses": {"200": {"description": "목록", "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Page"}}}},
                "401": {"$ref": "#/components/responses/Error"}, "403": {"$ref": "#/components/responses/Error"},
                "429": {"$ref": "#/components/responses/Error"}}}


def _q(name: str, desc: str, typ: str = "string") -> dict:
    return {"name": name, "in": "query", "required": False, "description": desc, "schema": {"type": typ}}


@bp.route("/openapi.json")
def openapi():
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "영업관리 연동 API", "version": "1.0.0",
                 "description": "Bearer API 키로 호출합니다. 데이터 범위는 키의 대리 사용자 권한을 따릅니다. "
                                "분당 호출 한도를 넘으면 429 와 Retry-After 를 돌려줍니다."},
        "servers": [{"url": "/api/v1"}],
        "components": {
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "parameters": {"limit": _q("limit", f"한 번에 받을 건수 (1~{MAX_LIMIT}, 기본 100)", "integer"),
                           "offset": _q("offset", "건너뛸 건수", "integer")},
            "schemas": {
                "Page": {"type": "object", "properties": {"data": {"type": "array", "items": {"type": "object"}},
                                                          "page": {"type": "object", "properties": {
                                                              "limit": {"type": "integer"},
                                                              "offset": {"type": "integer"},
                                                              "total": {"type": "integer"}}}}},
                "Error": {"type": "object", "properties": {"error": {"type": "object", "properties": {
                    "code": {"type": "string"}, "message": {"type": "string"}}}}},
                "SaleInput": {"type": "object", "required": ["customer_id", "item", "qty", "unit_price"],
                              "properties": {"customer_id": {"type": "integer"}, "deal_id": {"type": "integer"},
                                             "product_id": {"type": "integer"}, "sale_date": {"type": "string",
                                                                                              "format": "date"},
                                             "item": {"type": "string"}, "item_code": {"type": "string"},
                                             "qty": {"type": "integer", "minimum": 1},
                                             "unit_price": {"type": "integer", "minimum": 0},
                                             "tax_type": {"type": "string", "enum": list(db.TAX_TYPES)},
                                             "status": {"type": "string", "enum": list(db.SALE_STATUS)},
                                             "memo": {"type": "string"}}},
            },
            "responses": {"Error": {"description": "오류", "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Error"}}}}},
        },
        "paths": {
            "/me": {"get": {"summary": "키 정보", "security": [{"bearer": []}],
                            "responses": {"200": {"description": "클라이언트·권한"}}}},
            "/customers": {"get": _list_op("거래처 목록", "customers:read",
                                           [_q("q", "거래처명 검색"), _q("updated_since", "수정일시 이후 (증분 동기화)")])},
            "/deals": {"get": _list_op("영업기회 목록", "deals:read",
                                       [_q("stage", "단계"), _q("updated_since", "수정일시 이후")])},
            "/sales": {
                "get": _list_op("매출 목록 (공급가액·부가세·합계)", "sales:read",
                                [_q("from", "매출일 시작 (YYYY-MM-DD 또는 YYYY-MM)"), _q("to", "매출일 끝"),
                                 _q("status", "수금상태"), _q("customer_id", "거래처 id", "integer")]),
                "post": {"summary": "매출 등록", "security": [{"bearer": ["sales:write"]}],
                         "parameters": [{"name": "Idempotency-Key", "in": "header", "required": False,
                                         "description": "재전송해도 한 번만 등록 (7일 보관)", "schema": {"type": "string"}}],
                         "requestBody": {"required": True, "content": {"application/json": {
                             "schema": {"$ref": "#/components/schemas/SaleInput"}}}},
                         "responses": {"201": {"description": "등록된 매출"},
                                       "409": {"$ref": "#/components/responses/Error"},
                                       "422": {"$ref": "#/components/responses/Error"}}}},
            "/products": {"get": _list_op("품목 목록", "products:read", [_q("active", "1 이면 사용 품목만")])},
            "/products/{id}/price": {"get": {"summary": "적용 단가 (거래처 특가 → 정가)",
                                             "security": [{"bearer": ["products:read"]}],
                                             "parameters": [{"name": "id", "in": "path", "required": True,
                                                             "schema": {"type": "integer"}},
                                                            _q("customer_id", "거래처 id", "integer"),
                                                            _q("on", "기준일 YYYY-MM-DD")],
                                             "responses": {"200": {"description": "단가"}}}},
            "/quotes": {"get": _list_op("견적 목록", "quotes:read", [_q("status", "상태")])},
            **{f"/erp/{name}": {"post": {
                "summary": summary, "security": [{"bearer": ["erp:write"]}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "required": ["items"],
                    "properties": {"items": {"type": "array", "maxItems": MAX_ITEMS,
                                             "items": {"type": "object", "properties": props}}}}}}},
                "responses": {"200": {"description": "항목별 결과 {results, summary}"},
                              "400": {"$ref": "#/components/responses/Error"},
                              "403": {"$ref": "#/components/responses/Error"}}}}
               for name, summary, props in (
                   ("payments", "ERP 누적 입금액 수신 (차액만 반영, 멱등)",
                    {"ref": {"type": "string", "example": "CRM-123"}, "erp_doc_no": {"type": "string"},
                     "paid_total": {"type": "integer"}, "sale_total": {"type": "integer"}}),
                   ("acks", "전표번호 회신 (비동기 연동)",
                    {"ref": {"type": "string"}, "erp_doc_no": {"type": "string"}, "ok": {"type": "boolean"},
                     "message": {"type": "string"}}),
                   ("credit", "여신한도 수신", {"erp_code": {"type": "string"}, "credit_limit": {"type": "integer"}}),
                   ("products", "자재(품목) 마스터 수신 (대리 사용자 관리자)",
                    {"code": {"type": "string"}, "name": {"type": "string"}, "unit": {"type": "string"},
                     "list_price": {"type": "integer"}, "tax_type": {"type": "string", "enum": list(db.TAX_TYPES)},
                     "category": {"type": "string"}, "active": {"type": "integer"}}))},
            "/quotes/{id}": {"get": {"summary": "견적 상세 (품목 포함)", "security": [{"bearer": ["quotes:read"]}],
                                     "parameters": [{"name": "id", "in": "path", "required": True,
                                                     "schema": {"type": "integer"}}],
                                     "responses": {"200": {"description": "견적"},
                                                   "404": {"$ref": "#/components/responses/Error"}}}},
        },
    }
    return jsonify(spec)

"""외부 연동 REST API (/api/v1) — MES·WMS·사내 포털용. 로그인 대신 API 키(Authorization: Bearer mmk_...).

  GET  /api/v1/materials            자재 (q=찾을 말, updated_since=YYYY-MM-DD, limit)          materials:read
  GET  /api/v1/stock                재고 (warehouse=창고코드, material=자재코드)               stock:read
  POST /api/v1/transactions         입고·출고 등록 (JSON, Idempotency-Key 헤더로 두 번 등록 방지)  transactions:write
  GET  /api/v1/openapi.json         명세 (키 없이)

입출고는 화면과 같은 규칙(services.register_transaction: 재고·마감·창고·로트·SAP·거래처 마스터)으로 판정하고,
키에 정한 창고만 다룬다. 기록은 'API:키 이름'으로 남는다.
"""
from __future__ import annotations

import hashlib
from datetime import date

from flask import Blueprint, jsonify, request

import config
from core import api_keys, db, once, repository as repo, services, uom

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _err(status: int, message: str):
    return jsonify(ok=False, error=message), status


def _auth(scope: str):
    raw = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    key, status, problem = api_keys.check(raw, request.remote_addr or "", scope)
    return key, (None if key else _err(status, problem))


@bp.get("/openapi.json")
def openapi():
    ok = {"description": "성공"}
    return jsonify({
        "openapi": "3.0.3",
        "info": {"title": f"{config.APP_TITLE} API", "version": config.APP_VERSION},
        "components": {"securitySchemes": {"key": {"type": "http", "scheme": "bearer", "description": "관리자 → 🔑 API 연동에서 만든 키"}}},
        "security": [{"key": []}],
        "paths": {
            "/api/v1/materials": {"get": {"summary": "자재 목록 (materials:read)", "parameters": [
                {"name": "q", "in": "query"}, {"name": "updated_since", "in": "query"}, {"name": "limit", "in": "query"}],
                "responses": {"200": ok}}},
            "/api/v1/stock": {"get": {"summary": "재고 (stock:read)", "parameters": [
                {"name": "warehouse", "in": "query"}, {"name": "material", "in": "query"}], "responses": {"200": ok}}},
            "/api/v1/transactions": {"post": {"summary": "입고·출고 등록 (transactions:write)", "parameters": [
                {"name": "Idempotency-Key", "in": "header", "description": "같은 값으로 다시 보내면 한 번만 등록"}],
                "requestBody": {"content": {"application/json": {"example": {
                    "type": "OUT", "material": "PT-BLT-001", "warehouse": "CW-PT", "qty": 2, "unit": "BOX",
                    "date": "2026-10-03", "ref_no": "MES-12345", "cost_center": "C100", "note": "1라인 투입"}}}},
                "responses": {"200": ok, "409": {"description": "같은 키 처리 중"}, "422": {"description": "규칙 위반(재고 부족 등)"}}}},
        }})


@bp.get("/materials")
def materials():
    key, err = _auth("materials:read")
    if err:
        return err
    sql, params = "SELECT id, code, name, spec, unit, category, barcode, sap_matnr, lot_managed, expiry_managed, unit_price, " \
                  "safety_stock, lead_time_days, updated_at FROM materials WHERE active = 1", []
    q = request.args.get("q", "").strip().lower()[:60]
    if q:
        sql += " AND (LOWER(code) LIKE ? OR LOWER(name) LIKE ? OR barcode = ?)"
        params += [f"%{q}%", f"%{q}%", q.upper()]
    since = request.args.get("updated_since", "")
    if since:
        sql += " AND updated_at >= ?"
        params.append(since)
    try:
        limit = max(1, min(int(request.args.get("limit", "500")), 5000))
    except ValueError:
        limit = 500
    df = db.query_df(sql + " ORDER BY code LIMIT ?", (*params, limit))
    units = uom.all_units()
    items = [{**{k: (v if v == v else None) for k, v in r.items() if k != "id"},
              "lot_managed": bool(r["lot_managed"]), "expiry_managed": bool(r["expiry_managed"]),
              "units": [{"unit": u, "factor": f, "barcode": b} for u, f, b in units.get(int(r["id"]), [])]}
             for r in df.to_dict("records")]
    return jsonify(ok=True, count=len(items), items=items)


@bp.get("/stock")
def stock():
    key, err = _auth("stock:read")
    if err:
        return err
    sql = (f"SELECT m.code AS material, m.name, m.unit, w.code AS warehouse, t.lot_no AS lot, {db.STOCK_EXPR} AS qty "
           "FROM transactions t JOIN materials m ON m.id = t.material_id JOIN warehouses w ON w.id = t.warehouse_id WHERE 1 = 1")
    params: list = []
    if key["wh_ids"] is not None:
        frag, wp = db.in_clause(key["wh_ids"])
        sql += f" AND t.warehouse_id{frag}"
        params += wp
    for arg, col in (("warehouse", "w.code"), ("material", "m.code")):
        if request.args.get(arg):
            sql += f" AND UPPER({col}) = ?"
            params.append(request.args[arg].strip().upper())
    df = db.query_df(sql + " GROUP BY m.code, m.name, m.unit, w.code, t.lot_no HAVING ABS(" + db.STOCK_EXPR + ") > 0.000001 "
                     "ORDER BY m.code, w.code, t.lot_no", params)
    return jsonify(ok=True, as_of=date.today().isoformat(), items=df.to_dict("records"))


@bp.post("/transactions")
def transactions():
    key, err = _auth("transactions:write")
    if err:
        return err
    body = request.get_json(silent=True) or {}
    idem = (request.headers.get("Idempotency-Key") or "").strip()[:100]
    token = hashlib.sha256(f"api:{key['id']}:{idem}".encode()).hexdigest()[:32] if idem else ""
    if token:
        first, status, location = once.claim(token, None)
        if not first:
            if status == "DONE":
                return jsonify(ok=True, duplicate=True, tx_id=int(location) if location.isdigit() else None,
                               message="이미 등록한 요청입니다 (두 번 등록하지 않음).")
            return _err(409, "같은 Idempotency-Key 요청을 처리하는 중입니다.")
    r, error = _register(key, body)
    if token:
        once.finish(token, bool(r and r.ok), str(r.tx_id) if r and r.ok else "")
    if error is not None:
        return error
    if not r.ok:
        return _err(422, r.message)
    return jsonify(ok=True, tx_id=r.tx_id, stock_after=r.stock_after, message=r.message, warning=r.warning,
                   pending=r.pending)


def _register(key: dict, body: dict):
    kind = str(body.get("type", "")).upper()
    if kind not in ("IN", "OUT"):
        return None, _err(400, "type 은 IN 또는 OUT 입니다.")
    code = str(body.get("material", "")).strip().upper()
    with db.get_conn() as conn:
        m = conn.execute("SELECT id, unit_price FROM materials WHERE active = 1 AND (UPPER(code) = ? OR barcode = ?)",
                         (code, code)).fetchone()
        w = conn.execute("SELECT id FROM warehouses WHERE active = 1 AND UPPER(code) = ?",
                         (str(body.get("warehouse", "")).strip().upper(),)).fetchone()
    if m is None:
        return None, _err(400, f"자재를 찾을 수 없습니다: {code}")
    if w is None:
        return None, _err(400, "창고 코드를 확인하세요.")
    if key["wh_ids"] is not None and int(w["id"]) not in key["wh_ids"]:
        return None, _err(403, "이 키로는 그 창고를 다룰 수 없습니다.")
    try:
        qty = float(body.get("qty"))
        price = float(body["unit_price"]) if body.get("unit_price") not in (None, "") else float(m["unit_price"] or 0)
        tx_date = date.fromisoformat(str(body.get("date") or date.today().isoformat())).isoformat()
    except (TypeError, ValueError):
        return None, _err(400, "qty·unit_price·date 형식을 확인하세요.")
    r = services.register_transaction(int(m["id"]), kind, qty, tx_date, price, ref_no=str(body.get("ref_no", ""))[:60],
                                      partner=str(body.get("partner", ""))[:100], note=str(body.get("note", ""))[:200],
                                      actor=api_keys.actor_of(key, request.remote_addr or ""), warehouse_id=int(w["id"]),
                                      wh_ids=key["wh_ids"], lot_no=str(body.get("lot_no", "")), expiry_date=str(body.get("expiry_date", "")),
                                      cost_center=str(body.get("cost_center", "")), unit=str(body.get("unit", "")))
    return r, None

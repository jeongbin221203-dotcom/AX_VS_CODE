"""데이터 운용 편의: 기준정보·목록 엑셀 내려받기 · 단위 환산·MRP 수요 일괄 등록 · 거래 취소 요청과 한꺼번에 승인 ·
여러 줄 화면(줄마다 현재고·발주 불러오기) · MRP 밤 자동 실행 · 변경 이력."""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r4_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from core import approvals, audit, auth, bulk, db, mrp, partners, purchasing, services, workflow  # noqa: E402
from test_advanced import fresh, mid, stock, wh  # noqa: E402,F401
from test_app import PW, app, client, csrf, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()


def _users():
    out = {}
    for u, role in (("req", "CLERK"), ("m1", "MANAGER"), ("m2", "MANAGER")):
        r = auth.create_user(f"user.{u}", f"{u}님", role, PW, audit.SYSTEM, must_change_pw=False)
        db.execute("UPDATE users SET all_warehouses = 1 WHERE id = ?", (r.user["id"],))
        out[u] = {**auth.get_user(r.user["id"]), "ip": ""}
    return out


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _open_po(u, qty=50, price=1000, code="PKG-001"):
    pr = purchasing.create_pr(wh(), [(mid(code), qty, price)], TODAY, "보충", u["req"])
    assert pr.ok and purchasing.decide_pr(pr.id, True, "", u["m1"]).ok
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "한국필름", {item: price}, "", "", u["m2"])
    assert po.ok, po.message
    if db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po.id,)) == "PENDING_APPROVAL":
        assert purchasing.approve_po(po.id, u["m1"]).ok
    return db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))


# ── 1·2. 내려받기 ──────────────────────────────────────────────
def test_master_and_list_exports(client):
    from core import seed, seed_mfg
    seed.seed(history=True)
    seed_mfg.seed_manufacturing()
    urls = ["/materials/?tab=list&export=xlsx", "/materials/units/export.xlsx", "/production/?tab=bom&export=xlsx",
            "/production/?tab=wo&export=xlsx", "/production/?tab=wip&export=xlsx", "/approvals/?export=xlsx",
            "/approvals/?tab=requests&export=xlsx", "/approvals/?tab=history&export=xlsx", "/quality/?export=xlsx",
            "/purchase/?export=xlsx", "/purchase/?tab=po&export=xlsx", "/documents/?export=xlsx",
            "/mrp/demand/import/template.xlsx"]
    for u in urls:
        res = client.get(u)
        assert res.status_code == 200, u
        assert res.data[:2] == b"PK", u                                       # xlsx (zip)
    master = pd.read_excel(io.BytesIO(client.get("/materials/?tab=list&export=xlsx").data), header=None)
    assert (master == "자재코드").any().any() and (master == "PKG-001").any().any()
    boms = pd.read_excel(io.BytesIO(client.get("/production/?tab=bom&export=xlsx").data), header=None)
    assert (boms == "부품코드").any().any()
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'EXPORT'") >= 10        # 내려받기 감사
    # MRP 계획: 실행이 있어야 내려받기
    plant = int(db.scalar("SELECT id FROM plants ORDER BY id LIMIT 1"))
    assert post(client, "/mrp/run", {"plant": plant, "horizon": 90}).status_code == 302
    res = client.get(f"/mrp/?plant={plant}&export=xlsx")
    assert res.status_code == 200 and res.data[:2] == b"PK"


def test_quality_export_is_not_cut_at_30(fresh):
    from core import quality
    shown = quality.run(None)
    full = quality.run(None, limit=None)
    assert sum(len(c["rows"]) if isinstance(c, dict) and "rows" in c else 0 for c in full) >= \
        sum(len(c["rows"]) if isinstance(c, dict) and "rows" in c else 0 for c in shown)


# ── 1·5. 단위 환산 · MRP 수요 일괄 등록 ───────────────────────
def test_unit_upload_add_change_delete(fresh):
    m = mid("PKG-001")
    df = pd.DataFrame({"자재코드": ["PKG-001", "PKG-001", "NOPE"], "단위": ["BOX", "PLT", "BOX"], "배수": [10, 400, 5],
                       "단위바코드": ["8800000000101", "", ""]})
    pv = bulk.preview_units(df)
    assert pv.errors and "NOPE" in pv.errors[0]
    pv = bulk.preview_units(df.iloc[:2])
    assert not pv.errors and [r["action"] for r in pv.rows] == ["추가", "추가"]
    assert bulk.apply_units(pv.rows, audit.SYSTEM).ok
    assert db.scalar("SELECT factor FROM material_units WHERE material_id = ? AND unit = 'BOX'", (m,)) == 10
    # 바꾸기 · 지우기(배수 0)
    pv = bulk.preview_units(pd.DataFrame({"자재코드": ["PKG-001", "PKG-001"], "단위": ["BOX", "PLT"], "배수": [12, 0],
                                          "단위바코드": ["8800000000101", ""]}))
    assert [r["action"] for r in pv.rows] == ["바꾸기", "지우기"]
    assert bulk.apply_units(pv.rows, audit.SYSTEM).ok
    assert db.scalar("SELECT factor FROM material_units WHERE material_id = ? AND unit = 'BOX'", (m,)) == 12
    assert not db.scalar("SELECT COUNT(*) FROM material_units WHERE material_id = ? AND unit = 'PLT'", (m,))
    # 바코드가 다른 자재와 겹치면 거부
    pv = bulk.preview_units(pd.DataFrame({"자재코드": ["PKG-002"], "단위": ["BOX"], "배수": [5], "단위바코드": ["8800000000101"]}))
    assert pv.errors
    # 내려받은 표를 그대로 올리면 바뀌는 것 없음 (형식 왕복)
    back = bulk.units_export()
    pv = bulk.preview_units(back.rename(columns=str))
    assert not pv.errors


def test_units_upload_screen(client):
    from core import seed
    seed.seed()
    data = _xlsx(pd.DataFrame({"자재코드": ["PKG-001"], "단위": ["BOX"], "배수": [20], "단위바코드": [""]}))
    page = post(client, "/materials/units/import", {"file": (io.BytesIO(data), "units.xlsx")},
                content_type="multipart/form-data").get_data(as_text=True)
    assert "미리보기" in page and "추가" in page
    import re
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    assert post(client, "/materials/units/import/apply", {"token": token}).status_code == 302
    assert db.scalar("SELECT factor FROM material_units WHERE unit = 'BOX'") == 20


def test_demand_upload_and_replace(fresh):
    plant = int(db.scalar("SELECT id FROM plants ORDER BY id LIMIT 1"))
    due = (date.today() + timedelta(days=20)).isoformat()
    df = pd.DataFrame({"제품코드": ["PKG-001", "PKG-002", "ZZZ"], "수량": [100, 0, 5], "납기": [due, due, "다음주"], "메모": ["", "", ""]})
    pv = bulk.preview_demands(df, plant, False)
    assert len(pv.errors) == 2                                                   # 수량 0 · 없는 코드·날짜
    pv = bulk.preview_demands(df.iloc[:1], plant, False)
    assert not pv.errors and bulk.apply_demands(pv.rows, audit.SYSTEM).ok
    pv = bulk.preview_demands(pd.DataFrame({"제품코드": ["PKG-002"], "수량": [7], "납기": [due], "메모": ["교체"]}), plant, True)
    r = bulk.apply_demands(pv.rows, audit.SYSTEM)
    assert r.ok and "기존 1줄 닫음" in r.message
    assert db.scalar("SELECT COUNT(*) FROM mrp_demands WHERE plant_id = ? AND active = 1", (plant,)) == 1


# ── 3. 거래 취소 요청 · 한꺼번에 승인 ─────────────────────────
def test_cancel_request_flow(fresh):
    u = _users()
    m = mid("PKG-001")
    before = stock("PKG-001", "WH1")
    tx = services.register_transaction(m, "IN", 5, TODAY, 1000, warehouse_id=wh(), actor=u["req"])
    assert tx.ok
    assert not approvals.request_cancel(tx.tx_id, "", u["req"]).ok                      # 사유 필수
    r = approvals.request_cancel(tx.tx_id, "수량 착오", u["req"])
    assert r.ok
    assert not approvals.request_cancel(tx.tx_id, "또", u["req"]).ok                    # 처리 중 중복
    q = workflow.queue(u["m1"])
    item = next(x for x in q if x["kind"] == "CANCEL")
    assert "수량 착오" in item["title"] or str(tx.tx_id) in item["title"]
    assert workflow.count(u["req"]) == 0                                               # 요청자 본인 차례 아님
    assert workflow.decide(u["m1"], "CANCEL", r.tx_id, True, "").ok
    assert db.scalar("SELECT COUNT(*) FROM transactions WHERE reversal_of = ?", (tx.tx_id,)) == 1
    assert abs(stock("PKG-001", "WH1") - before) < 1e-9                                  # 재고 원래대로
    assert not approvals.request_cancel(tx.tx_id, "다시", u["req"]).ok                  # 이미 취소됨
    rev = int(db.scalar("SELECT id FROM transactions WHERE reversal_of = ?", (tx.tx_id,)))
    assert not approvals.request_cancel(rev, "취소의 취소", u["req"]).ok
    # 반려하면 거래는 그대로
    tx2 = services.register_transaction(m, "IN", 3, TODAY, 1000, warehouse_id=wh(), actor=u["req"])
    r2 = approvals.request_cancel(tx2.tx_id, "중복 등록", u["req"])
    assert workflow.decide(u["m1"], "CANCEL", r2.tx_id, False, "맞는 거래").ok
    assert not db.scalar("SELECT COUNT(*) FROM transactions WHERE reversal_of = ?", (tx2.tx_id,))


def test_cancel_request_respects_scope(fresh):
    u = _users()
    tx = services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 1000, warehouse_id=wh(), actor=u["req"])
    assert not approvals.request_cancel(tx.tx_id, "범위 밖", u["req"], wh_ids=[999999]).ok
    assert approvals.request_cancel(tx.tx_id, "범위 안", u["req"], wh_ids=[wh()]).ok


def test_decide_many(fresh):
    u = _users()
    prs = [purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "보충", u["req"]).id for _ in range(3)]
    tx = services.register_transaction(mid("PKG-001"), "IN", 2, TODAY, 1000, warehouse_id=wh(), actor=u["req"])
    c = approvals.request_cancel(tx.tx_id, "착오", u["req"])
    keys = [f"PR:{i}" for i in prs] + [f"CANCEL:{c.tx_id}", "PR:999999", "BAD"]
    done, problems = workflow.decide_many(u["m1"], keys)
    assert done == 4 and len(problems) == 2
    assert db.scalar("SELECT COUNT(*) FROM purchase_requests WHERE status = 'APPROVED'") == 3
    assert workflow.count(u["m1"]) == 0
    # 요청자 본인 것은 한꺼번에 승인에서도 거부
    pr = purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "보충", u["m2"]).id
    done, problems = workflow.decide_many(u["m2"], [f"PR:{pr}"])
    assert done == 0 and problems


def test_cancel_request_and_bulk_screens(app):
    from core import seed
    seed.seed()
    u = _users()
    tx = services.register_transaction(mid("PKG-001"), "IN", 2, TODAY, 1000, warehouse_id=wh(), actor=u["req"])
    clerk = login(app.test_client(), "user.req")
    assert "거래 취소 요청" in clerk.get("/history/").get_data(as_text=True)
    assert post(clerk, "/history/cancel-request", {"tx_id": tx.tx_id, "reason": "착오"}).status_code == 302
    req_id = int(db.scalar("SELECT id FROM approval_requests WHERE kind = 'CANCEL'"))
    mgr = login(app.test_client(), "user.m1")
    page = mgr.get("/approvals/").get_data(as_text=True)
    assert 'id="bulk-approve"' in page and f'value="CANCEL:{req_id}"' in page
    res = mgr.post("/approvals/decide-many", data={"_csrf": csrf(mgr), "item": [f"CANCEL:{req_id}"]})
    assert res.status_code == 302
    assert db.scalar("SELECT status FROM approval_requests WHERE id = ?", (req_id,)) == "APPROVED"


# ── 4. 여러 줄 화면: 줄마다 현재고 · 발주 불러오기 ────────────
def test_batch_stock_and_po_lines(app):
    from core import seed
    seed.seed()
    u = _users()
    po_no = _open_po(u, qty=50)
    c = login(app.test_client(), "user.req")
    m1, m2 = mid("PKG-001"), mid("PKG-002")
    j = c.get(f"/transactions/stock.json?wh={wh()}&ids={m1},{m2},abc").get_json()
    assert j["ok"] and abs(j["stock"][str(m1)] - stock("PKG-001", "WH1")) < 1e-9 and str(m2) in j["stock"]
    assert not c.get("/transactions/stock.json?wh=999999&ids=1").get_json()["ok"]
    orders = c.get(f"/transactions/po-lines.json?wh={wh()}").get_json()["orders"]
    o = next(x for x in orders if x["po_no"] == po_no)
    line = o["lines"][0]
    assert line["qty"] == 50 and line["po"].startswith(po_no + "|") and o["supplier"] == "한국필름"
    page = c.get("/transactions/batch").get_data(as_text=True)
    assert "발주 불러오기" in page and "data-stock-url" in page and "창고 현재고" in page
    # 발주 줄로 여러 줄 입고 → 발주 입고로 연결, 상태 갱신, 초과는 거부
    base = {"_csrf": csrf(c), "kind": "IN", "warehouse_id": wh(), "tx_date": TODAY, "partner": "한국필름",
            "line_mid": [m1], "line_qty": ["60"], "line_lot": [""], "line_exp": [""], "line_price": ["1000"],
            "line_note": [""], "line_unit": [""], "line_po": [line["po"]]}
    c.post("/transactions/batch", data=base)
    assert not db.scalar("SELECT COUNT(*) FROM transactions WHERE po_no = ?", (po_no,)), "발주보다 많이 받으면 거부"
    c.post("/transactions/batch", data={**base, "_csrf": csrf(c), "line_qty": ["30"]})
    assert db.scalar("SELECT SUM(qty) FROM transactions WHERE po_no = ?", (po_no,)) == 30
    assert db.scalar("SELECT status FROM purchase_orders WHERE po_no = ?", (po_no,)) == "PARTIAL"
    o = next(x for x in c.get(f"/transactions/po-lines.json?wh={wh()}").get_json()["orders"] if x["po_no"] == po_no)
    assert o["lines"][0]["qty"] == 20                                                  # 남은 수량
    c.post("/transactions/batch", data={**base, "_csrf": csrf(c), "line_qty": ["20"]})
    assert db.scalar("SELECT status FROM purchase_orders WHERE po_no = ?", (po_no,)) == "CLOSED"
    assert all(x["po_no"] != po_no for x in c.get(f"/transactions/po-lines.json?wh={wh()}").get_json()["orders"])


def test_register_lines_ignores_po_on_out(fresh):
    u = _users()
    po_no = _open_po(u, qty=10)
    services.register_transaction(mid("PKG-001"), "IN", 10, TODAY, 1000, warehouse_id=wh(), actor=u["req"])
    r = services.register_lines("OUT", wh(), TODAY, [services.LineIn(mid("PKG-001"), 1, po_no=po_no, po_item="10")],
                                actor=u["req"])
    assert r.ok
    assert not db.scalar("SELECT COUNT(*) FROM transactions WHERE tx_type = 'OUT' AND po_no <> ''")


# ── 5. MRP 밤 자동 실행 ───────────────────────────────────────
def test_mrp_nightly_once_a_day(fresh, monkeypatch):
    plant = int(db.scalar("SELECT id FROM plants ORDER BY id LIMIT 1"))
    db.execute("INSERT INTO mrp_demands (plant_id, material_id, qty, due_date, note, active, created_by, created_at) "
               "VALUES (?, ?, 500, ?, '', 1, 't', ?)", (plant, mid("PKG-001"), TODAY, TODAY))
    monkeypatch.setattr(config, "MRP_NIGHTLY_HOUR", 2)
    early = datetime.combine(date.today(), datetime.min.time()).replace(hour=1)
    late = early.replace(hour=3)
    assert "이후" in mrp.nightly(early)
    assert not db.scalar("SELECT COUNT(*) FROM mrp_runs")
    msg = mrp.nightly(late)
    assert "MRP 실행" in msg
    assert db.scalar("SELECT COUNT(*) FROM mrp_runs WHERE plant_id = ? AND run_by = 'MRP 자동 실행'", (plant,)) == 1
    assert "오늘 실행함" in mrp.nightly(late)                                        # 같은 날 두 번 안 함
    monkeypatch.setattr(config, "MRP_NIGHTLY_HOUR", -1)
    assert "꺼짐" in mrp.nightly(late)
    from core import jobs
    assert "mrp_nightly" in jobs.JOBS and not jobs.JOBS["mrp_nightly"].enabled()


# ── 5. 변경 이력 ──────────────────────────────────────────────
def test_material_change_history(client):
    from core import seed
    seed.seed()
    m = mid("PKG-001")
    row = dict(db.query_df("SELECT * FROM materials WHERE id = ?", (m,)).iloc[0])
    data = {k: row[k] for k in ("name", "spec", "unit", "category", "safety_stock", "unit_price", "location", "supplier",
                                "sap_matnr", "lot_managed", "expiry_managed", "barcode", "lead_time_days", "min_order_qty",
                                "order_multiple")}
    assert services.update_material(m, {**data, "safety_stock": 777, "location": "A-9"}, actor={"id": None, "name": "홍담당", "ip": ""}).ok
    # 엑셀 일괄 반영도 자재마다 기록
    up = pd.DataFrame([{"code": "PKG-001", "name": row["name"], "unit_price": 4321, "_blank": ",".join(
        f for f in ("spec", "unit", "category", "safety_stock", "location", "supplier", "sap_matnr", "barcode",
                    "lead_time_days", "min_order_qty", "order_multiple"))}])
    assert services.import_materials(up, actor=audit.SYSTEM).ok
    hist = audit.entity_history("material", m)
    fields = {c["field"]: c for h in hist for c in h["changes"]}
    assert fields["안전재고"]["after"] == "777" and fields["보관위치"]["after"] == "A-9"
    assert fields["단가"]["after"] == "4,321" and any(h["via"] == "엑셀" for h in hist)
    page = client.get(f"/materials/?tab=edit&id={m}").get_data(as_text=True)
    assert "변경 이력" in page and "홍담당" in page and "777" in page


def test_partner_change_history(client):
    from core import seed
    seed.seed()
    r = partners.create({"name": "이력상사", "kind": "SUPPLIER", "contact": "김", "phone": "", "email": "", "note": "",
                         "biz_no": ""}, audit.SYSTEM)
    assert r.ok
    p = partners.get(r.id)
    assert partners.update(r.id, {**{f: p[f] for f in partners.FIELDS}, "contact": "박"}, audit.SYSTEM).ok
    pv = bulk.preview_partners(pd.DataFrame({"거래처코드": [p["code"]], "거래처명": ["이력상사"], "전화": ["02-111-2222"]}))
    assert not pv.errors and bulk.apply_partners(pv.rows, audit.SYSTEM).ok
    hist = audit.entity_history("partner", r.id)
    changes = [c for h in hist for c in h["changes"]]
    assert {"field": "담당자", "before": "김", "after": "박"} in changes
    assert any(c["field"] == "전화" and c["after"] == "02-111-2222" for c in changes)
    assert hist[-1]["action"] == "거래처 등록"
    page = client.get(f"/partners/{r.id}").get_data(as_text=True)
    assert "변경 이력" in page and "02-111-2222" in page

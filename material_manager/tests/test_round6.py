"""원가·구매·SAP 확장: 표준원가·차이 · 노무비/경비 배부 · SAP 생산오더(261/262/101)·오더 정산 · SAP 금액 ·
마감 점검(재공품·GR/IR) · 발주 납기일·작업 달력 · 3자 대조 허용오차·지급 보류."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r6_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
from core import costing, db, mrp, periods, production, purchasing, sap, services, valuation  # noqa: E402
from test_advanced import M1, M2, fresh, mid, stock, wh  # noqa: E402,F401
from test_app import app  # noqa: E402,F401

TODAY = date.today().isoformat()
CLERK = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}
ADMIN = {"id": None, "name": "관리자", "role": "ADMIN", "ip": ""}


def _mat(code, price=1000):
    assert services.create_material({"code": code, "name": code, "unit": "EA", "unit_price": price}).ok
    return mid(code)


@pytest.fixture()
def rates(monkeypatch):
    monkeypatch.setattr(config, "LABOR_RATE", 100.0)
    monkeypatch.setattr(config, "OVERHEAD_RATE", 50.0)
    yield


def _product(fg="ZF", comp="ZK", per=2, minutes=3, comp_price=1000):
    p, c = _mat(fg), _mat(comp, comp_price)
    assert production.save_bom(p, 1, [production.BomLine(c, per)], "", M1).ok
    assert production.save_routing(p, [production.Op("조립", "W1", minutes, "")], M1).ok
    assert services.register_transaction(c, "IN", 100, TODAY, comp_price, warehouse_id=wh(), actor=M2).ok
    return p, c


# ── 1·2. 표준원가 · 노무비/경비 · 차이 · 정산 ─────────────────
def test_standard_cost_rollup_multilevel(fresh, rates):
    sub, raw = _product("ZSUB", "ZRAW", per=3, minutes=2, comp_price=100)    # 반제품: 재료 300 + 2분 × 150 = 600
    top = _mat("ZTOP")
    assert production.save_bom(top, 1, [production.BomLine(sub, 2)], "", M1).ok
    assert production.save_routing(top, [production.Op("포장", "W2", 1, "")], M1).ok
    ok, msg = costing.rollup(ADMIN)
    assert ok, msg
    s_sub, s_top = costing.standard(sub), costing.standard(top)
    assert s_sub["material"] == pytest.approx(300) and s_sub["labor"] == pytest.approx(200) and s_sub["total"] == pytest.approx(600)
    assert s_top["material"] == pytest.approx(1200) and s_top["total"] == pytest.approx(1200 + 150)   # 2 × 600 + 1분 × 150
    assert costing.standard(raw)["total"] == pytest.approx(100)                                        # 구매품 = 기준단가


def test_labor_overhead_absorbed_and_variance_settle(fresh, rates):
    p, c = _product(per=2, minutes=3)
    costing.rollup(ADMIN)                                                       # 표준 = 2 × 1000 + 3분 × 150 = 2450
    r = production.create_wo(p, 10, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh())
    pid = r.tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    assert production.issue(pid, {line: 22}, TODAY, actor=CLERK).ok          # 표준 20 → 2개 더 씀
    op = int(db.scalar("SELECT id FROM wo_operations WHERE production_id = ?", (pid,)))
    assert production.report_operation(pid, op, 10, 0, 40, "", "", CLERK).ok    # 표준 30분 → 40분
    done = production.complete(pid, 10, 0, TODAY, actor=CLERK, backflush=False)
    assert done.ok and "노무비 ₩4,000" in done.message and "경비 ₩2,000" in done.message
    row = db.query_df("SELECT labor_cost, overhead_cost, material_cost FROM productions WHERE id = ?", (pid,)).iloc[0]
    assert (row["labor_cost"], row["overhead_cost"]) == (4000, 2000)
    fg_price = float(db.scalar("SELECT unit_price FROM transactions WHERE production_id = ? AND material_id = ?", (pid, p)))
    assert fg_price == pytest.approx((22000 + 6000) / 10)
    df, _ = valuation.report("2000-01-01", TODAY, "MAVG")                       # 재고 평가에도 노무·경비가 들어감
    fg = df.set_index("code").loc["ZF"]
    assert float(fg["close_value"]) == pytest.approx(22000 + 6000)
    v = costing.variance(pid)
    assert v["std_total"] == pytest.approx(24500) and v["qty_var"] == pytest.approx(2000)
    assert v["labor_var"] == pytest.approx(1000) and v["overhead_var"] == pytest.approx(500)
    assert v["total_var"] == pytest.approx(28000 - 24500)
    ok, msg = costing.settle(pid, M1)
    assert ok and "정산" in msg
    assert not costing.settle(pid, M1)[0]                                     # 한 번만
    db.execute("UPDATE materials SET unit_price = 9999 WHERE id = ?", (c,))
    costing.rollup(ADMIN)
    assert costing.variance(pid)["total_var"] == pytest.approx(3500)          # 정산한 값은 표준을 바꿔도 그대로


def test_purchase_price_variance(fresh):
    m = _mat("ZPPV", 1000)
    costing.rollup(ADMIN)
    assert services.register_transaction(m, "IN", 5, TODAY, 1200, warehouse_id=wh(), actor=M2).ok
    df = costing.purchase_price_variance(TODAY, TODAY)
    assert float(df.set_index("code").loc["ZPPV", "variance"]) == pytest.approx(1000)


# ── 3·4. SAP 생산오더 · 금액 ─────────────────────────────────
def test_sap_production_order_mode_and_amounts(fresh, monkeypatch):
    p, c = _product()                                           # (ERP 끄고) 제품·부품·입고 준비
    monkeypatch.setattr(config, "SAP_MODE", "mock")
    monkeypatch.setattr(config, "SAP_PRODUCTION_MODE", "order")
    with db.transaction() as conn:
        conn.execute("UPDATE materials SET sap_matnr = REPLACE(code, '-', '')")
        conn.execute("UPDATE plants SET sap_plant = '1000'")
        conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
    pid = production.create_wo(p, 5, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh()).tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    r = production.issue(pid, {line: 10}, TODAY, actor=CLERK)
    assert not r.ok and "SAP 생산오더" in r.message                          # 오더 번호 없으면 거부
    assert production.set_sap_order(pid, "1000123", M1).ok
    assert production.issue(pid, {line: 10}, TODAY, actor=CLERK).ok
    assert production.issue(pid, {line: -2}, TODAY, actor=CLERK).ok
    assert production.complete(pid, 5, 0, TODAY, actor=CLERK, backflush=False).ok
    mv = [r[0] for r in db.query_df("SELECT movement_type FROM transactions WHERE production_id = ? ORDER BY id",
                                     (pid,)).itertuples(index=False)]
    assert mv == ["261", "262", "101"]
    with db.get_conn() as conn:
        ids = [int(x) for x in db.query_df("SELECT id FROM transactions WHERE production_id = ? ORDER BY id", (pid,))["id"]]
        pay, why = sap.build_payload(conn, ids[0])
    assert not why and pay["productionOrder"] == "1000123" and pay["amount"] == pytest.approx(10 * pay["unitPrice"])
    assert pay["currency"] == config.SAP_CURRENCY
    from core import erp
    it = erp.SapRfcConnector.item({**pay, "movementType": "501", "amount": 1234.0})
    assert it["AMOUNT_LC"] == 1234.0 and it["ORDERID"].endswith("1000123")
    with db.get_conn() as conn:
        rec, _ = sap.build_payload(conn, ids[2])
    assert erp.SapODataConnector.item(rec)["GoodsMovementRefDocType"] == "F"


# ── 5. 마감 점검 ─────────────────────────────────────────────
def test_close_checks_wip_and_grir(fresh):
    p, c = _product()
    pid = production.create_wo(p, 5, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh()).tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    assert production.issue(pid, {line: 4}, TODAY, actor=CLERK).ok
    pr = purchasing.create_pr(wh(), [(c, 10, 1000)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    assert services.register_transaction(c, "IN", 3, TODAY, 1000, warehouse_id=wh(), po_no=po_no, po_item="10", actor=M2).ok
    chk = periods.close_checks(date.today().strftime("%Y-%m"))
    assert chk["wip_count"] == 1 and chk["wip_value"] > 0
    assert chk["gr_no_invoice_count"] == 1 and chk["gr_no_invoice_value"] == pytest.approx(3000)


# ── 6. 발주 납기일 · 작업 달력 ───────────────────────────────
def test_work_calendar_and_po_delivery_in_mrp(fresh, monkeypatch):
    monkeypatch.setattr(config, "WEEKEND_OFF", True)
    # 2026-10-05(월) 에서 근무일 1일 전 = 10-02(금), 10-02 를 휴일로 넣으면 10-01(목)
    assert costing.sub_workdays("2026-10-05", 1) == "2026-10-02"
    costing.add_holiday("2026-10-02", "테스트 휴일", ADMIN)
    assert costing.sub_workdays("2026-10-05", 1) == "2026-10-01"
    assert costing.sub_workdays("2026-10-04", 0) == "2026-10-01"                 # 일요일 → 그 앞 근무일
    costing.remove_holiday("2026-10-02", ADMIN)
    m = _mat("ZMRP", 1000)                                      # 재고 0 · 안전재고 0 · 리드타임 0
    pr = purchasing.create_pr(wh(), [(m, 10, 1000)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    later = (date.today() + timedelta(days=40)).isoformat()
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2, delivery_date=later)
    assert db.scalar("SELECT delivery_date FROM purchase_orders WHERE id = ?", (po.id,)) == later
    plant = int(db.scalar("SELECT plant_id FROM warehouses WHERE id = ?", (wh(),)))
    assert mrp.add_demand(plant, m, 5, (date.today() + timedelta(days=10)).isoformat(), "", M1).ok
    run = mrp.run(plant, M1)
    plans = mrp.plans_df(run.tx_id)
    assert (plans["code"] == "ZMRP").any()                        # 납기일이 수요 뒤라 공급으로 못 씀 → 계획이 생김
    assert purchasing.set_delivery_date(po.id, TODAY, M2).ok
    run2 = mrp.run(plant, M1)
    assert not (mrp.plans_df(run2.tx_id)["code"] == "ZMRP").any()


# ── 7. 3자 대조 허용오차 · 지급 보류 ─────────────────────────
def test_three_way_match_blocks_and_release(fresh, monkeypatch):
    monkeypatch.setattr(config, "MATCH_PRICE_TOL_PCT", 2.0)
    m = mid("PKG-001")
    pr = purchasing.create_pr(wh(), [(m, 10, 1000)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    tx = services.register_transaction(m, "IN", 10, TODAY, 1000, warehouse_id=wh(), po_no=po_no, po_item="10", actor=CLERK)
    assert purchasing.refresh_payment(po.id) == "WAIT"
    with db.transaction() as conn:
        conn.execute("INSERT INTO documents (tx_id, doc_type, issue_date, supply_amount, tax_amount, file_name, stored_name, mime, "
                     "size, sha256, created_at) VALUES (?, 'E_TAX_INVOICE', ?, 10500, 1050, 'x.png', 's1', 'image/png', 1, 'h1', ?)",
                     (tx.tx_id, TODAY, TODAY))
    assert purchasing.refresh_payment(po.id) == "BLOCKED"                     # 5% > 2%
    df = purchasing.payment_df()
    assert "5.0%" in df.set_index("po_no").loc[po_no, "reason"]
    assert not purchasing.release_payment(po.id, "", M1).ok
    assert not purchasing.release_payment(po.id, "운송비", M2).ok              # 발주자 본인
    assert purchasing.release_payment(po.id, "운송비 포함 — 승인", M1).ok
    assert purchasing.refresh_payment(po.id) == "RELEASED"                    # 금액이 그대로면 해제 유지
    db.execute("UPDATE documents SET supply_amount = 10000 WHERE stored_name = 's1'")
    assert purchasing.refresh_payment(po.id) == "MATCHED"                     # 계산서를 고치면 다시 대조


# ── 다시 확인에서 고친 것 ────────────────────────────────────
def test_issue_cost_equals_valuation_cost(fresh):
    p, c = _mat("ZVF"), _mat("ZVC", 110)
    assert production.save_bom(p, 1, [production.BomLine(c, 2)], "", M1).ok
    old = (date.today() - timedelta(days=200)).isoformat()
    assert services.register_transaction(c, "IN", 1000, old, 110, warehouse_id=wh(), actor=M2).ok
    assert services.register_transaction(c, "IN", 10, TODAY, 200, warehouse_id=wh(), actor=M2).ok
    avg = (1000 * 110 + 10 * 200) / 1010
    pid = production.create_wo(p, 10, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh()).tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    assert production.issue(pid, {line: 20}, TODAY, actor=CLERK).ok
    price = float(db.scalar("SELECT unit_price FROM transactions WHERE production_id = ? AND tx_type = 'OUT'", (pid,)))
    assert price == pytest.approx(avg, rel=1e-4)                                 # 최근 입고 단가(200)가 아니라 평가 단가


def test_gm_code_for_production_order_receipt():
    from core import erp
    assert erp.gm_code("101", {"productionOrder": "1000123"}) == "02"
    assert erp.gm_code("101", {"purchaseOrder": "4500000001"}) == "01"


def test_stale_standard_blocks_settle_and_settled_cannot_cancel(fresh, rates):
    p, c = _product("ZST", "ZSC")
    costing.rollup(ADMIN)
    pid = production.post(p, 5, wh(), TODAY, actor=CLERK, receipt_wh_id=wh()).tx_id
    db.execute("UPDATE boms SET updated_at = '2999-01-01 00:00:00' WHERE product_id = ?", (p,))   # 산정 뒤 BOM 변경
    ok, msg = costing.settle(pid, M1)
    assert not ok and "BOM" in msg
    assert "BOM" in costing.variances_df().set_index("id").loc[pid, "note"]
    db.execute("UPDATE boms SET updated_at = '2000-01-01 00:00:00' WHERE product_id = ?", (p,))
    assert costing.settle(pid, M1)[0]
    r = production.cancel(pid, "잘못 만듦", actor=M1)
    assert not r.ok and "정산" in r.message


def test_close_checks_as_of_month_end(fresh):
    p, c = _product("ZWP", "ZWC")
    last_end = date.today().replace(day=1) - timedelta(days=1)
    ym = last_end.strftime("%Y-%m")
    assert services.register_transaction(c, "IN", 10, last_end.isoformat(), 1000, warehouse_id=wh(), actor=M2).ok
    pid = production.create_wo(p, 5, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh()).tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    assert production.issue(pid, {line: 4}, last_end.isoformat(), actor=CLERK).ok   # 지난달 말에 투입
    assert production.complete(pid, 5, 0, TODAY, actor=CLERK, backflush=False).ok   # 이번 달에 완료
    chk = periods.close_checks(ym)
    assert chk["wip_count"] == 1 and chk["wip_value"] > 0                         # 지난달 말 기준으로는 재공품


def test_ppv_excludes_reversed_and_rollup_needs_all_scope(fresh):
    m = _mat("ZPR", 100)
    costing.rollup(ADMIN)
    tx = services.register_transaction(m, "IN", 5, TODAY, 300, warehouse_id=wh(), actor=M2)
    assert services.reverse_transaction(tx.tx_id, "착오", actor=M1).ok
    assert "ZPR" not in set(costing.purchase_price_variance(TODAY, TODAY)["code"]) if len(costing.purchase_price_variance(TODAY, TODAY)) else True
    po_m = mid("PKG-001")
    pr = purchasing.create_pr(wh(), [(po_m, 1, 1000)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2)
    assert not purchasing.set_delivery_date(po.id, "2020-01-01", M2).ok             # 과거 납기일 금지


def test_rollup_route_requires_all_warehouses(app):
    from core import audit, auth, seed
    from test_app import login, post
    seed.seed()
    u = auth.create_user("mgr.one", "한창고관리자", "MANAGER", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    db.execute("UPDATE users SET all_warehouses = 0 WHERE id = ?", (u["id"],))
    db.execute("INSERT INTO user_scopes (user_id, warehouse_id) VALUES (?, ?)", (u["id"], wh()))
    c = login(app.test_client(), "mgr.one")
    assert post(c, "/production/cost/rollup").status_code == 403

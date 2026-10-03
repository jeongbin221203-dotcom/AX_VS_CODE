"""4차 이후 점검에서 찾은 결함 — 생산 거래 한 줄 취소 · 로트 반납 · 창고 범위(작업지시·MRP 수요·거래처) ·
거래처 일괄 구분 · 단위 바코드 · BOM 순환 미리보기 · 엑셀 날짜 숫자 · MRP 입고 창고 없는 작업지시."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_rv2_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from core import approvals, audit, bulk, db, mrp, partners, production, services  # noqa: E402
from test_advanced import M1, M2, fresh, mid, stock, wh  # noqa: E402,F401

TODAY = date.today().isoformat()
CLERK = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}


def _mat(code, lot=False, price=1000):
    assert services.create_material({"code": code, "name": code, "unit": "EA", "unit_price": price,
                                     "lot_managed": 1 if lot else 0}).ok
    return mid(code)


def _wo(product, comp, per=2, qty=10, comp_lot=False, receipt=True):
    p, c = _mat(product), _mat(comp, lot=comp_lot)
    assert production.save_bom(p, 1, [production.BomLine(c, per)], "", M1).ok
    lot = "L-1" if comp_lot else ""
    assert services.register_transaction(c, "IN", 100, TODAY, 1000, warehouse_id=wh(), lot_no=lot, actor=M2).ok
    r = production.create_wo(p, qty, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh() if receipt else None)
    assert r.ok, r.message
    pid = r.tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    return p, c, pid, line


# ── 생산 거래는 한 줄만 취소할 수 없다 ────────────────────────
def test_production_tx_single_reverse_blocked(fresh):
    p, c, pid, line = _wo("ZP1", "ZC1")
    assert production.issue(pid, {line: 20}, TODAY, actor=CLERK).ok
    out_tx = int(db.scalar("SELECT id FROM transactions WHERE production_id = ? AND tx_type = 'OUT'", (pid,)))
    r = services.reverse_transaction(out_tx, "착오", actor=M1)
    assert not r.ok and "작업지시 전체" in r.message
    assert not approvals.request_cancel(out_tx, "착오", CLERK).ok
    assert production.cancel(pid, "취소", actor=M1).ok                          # 전체 취소는 된다
    assert stock("ZC1") == 100


# ── 로트 관리 부품 반납: 투입한 로트로 ────────────────────────
def test_lot_component_return(fresh):
    p, c, pid, line = _wo("ZP2", "ZL1", comp_lot=True)
    assert production.issue(pid, {line: 10}, TODAY, actor=CLERK).ok
    assert stock("ZL1") == 90
    r = production.issue(pid, {line: -2}, TODAY, actor=CLERK)
    assert r.ok, r.message
    assert stock("ZL1") == 92 and stock("ZL1", lot="L-1") == 92
    assert float(db.scalar("SELECT issued_qty FROM production_lines WHERE id = ?", (line,))) == 8


# ── 창고 범위 ────────────────────────────────────────────────
def test_work_order_scope(fresh):
    p, c, pid, line = _wo("ZP3", "ZC3")
    other = [999999]
    assert not production.complete(pid, 0, 3, TODAY, actor=CLERK, wh_ids=other, backflush=False).ok
    op = db.execute("INSERT INTO wo_operations (production_id, seq, op_name, status) VALUES (?, 10, '조립', 'WAIT')", (pid,))
    op_id = int(db.scalar("SELECT id FROM wo_operations WHERE production_id = ?", (pid,)))
    assert not production.report_operation(pid, op_id, 1, 0, 5, "", "", CLERK, wh_ids=other).ok
    assert production.report_operation(pid, op_id, 1, 0, 5, "", "", CLERK, wh_ids=[wh()]).ok
    assert not production.cancel(pid, "범위 밖", actor=M1, wh_ids=other).ok
    assert db.scalar("SELECT status FROM productions WHERE id = ?", (pid,)) == "PLANNED"
    assert production.cancel(pid, "범위 안", actor=M1, wh_ids=[wh()]).ok
    del op


def test_close_demand_scope(fresh):
    plant = int(db.scalar("SELECT plant_id FROM warehouses WHERE id = ?", (wh(),)))
    r = mrp.add_demand(plant, mid("PKG-001"), 5, TODAY, "", M1)
    assert not mrp.close_demand(r.tx_id, M1, wh_ids=[999999]).ok
    assert not mrp.close_demand(987654, M1).ok
    assert mrp.close_demand(r.tx_id, M1, wh_ids=[wh()]).ok
    assert not mrp.close_demand(r.tx_id, M1).ok                                # 이미 닫힘


def test_partner_tx_scope(fresh):
    r = partners.create({"name": "범위상사", "kind": "SUPPLIER", "contact": "", "phone": "", "email": "", "note": "",
                         "biz_no": ""}, audit.SYSTEM)
    assert services.register_transaction(mid("PKG-001"), "IN", 3, TODAY, 1000, warehouse_id=wh(), partner="범위상사", actor=M1).ok
    assert len(partners.recent_tx(r.id)) == 1
    assert len(partners.recent_tx(r.id, wh_ids=[999999])) == 0
    df = partners.list_df(wh_ids=[999999])
    assert float(df.loc[df["id"] == r.id, "in_amt"].iloc[0]) == 0
    df = partners.list_df()
    assert float(df.loc[df["id"] == r.id, "in_amt"].iloc[0]) == 3000


# ── 엑셀 일괄 ────────────────────────────────────────────────
def test_partner_bulk_blank_kind_keeps(fresh):
    r = partners.create({"name": "고객상사", "kind": "CUSTOMER", "contact": "", "phone": "", "email": "", "note": "",
                         "biz_no": ""}, audit.SYSTEM)
    pv = bulk.preview_partners(pd.DataFrame({"거래처명": ["고객상사", "새상사"], "전화": ["02-1", ""]}))
    assert not pv.errors
    assert bulk.apply_partners(pv.rows, audit.SYSTEM).ok
    assert partners.get(r.id)["kind"] == "CUSTOMER"
    assert db.scalar("SELECT kind FROM partners WHERE name = '새상사'") == "SUPPLIER"


def test_unit_barcode_not_own_code(fresh):
    m = _mat("ZBOX1")
    pv = bulk.preview_units(pd.DataFrame({"자재코드": ["ZBOX1"], "단위": ["BOX"], "배수": [12], "단위바코드": ["ZBOX1"]}))
    assert pv.errors
    rows = [{**pv.rows[0], "problem": ""}]
    assert not bulk.apply_units(rows, audit.SYSTEM).ok
    assert not db.scalar("SELECT COUNT(*) FROM material_units WHERE material_id = ?", (m,))


def test_bom_cycle_preview(fresh):
    a, b = _mat("ZA"), _mat("ZB")
    assert production.save_bom(a, 1, [production.BomLine(b, 1)], "", M1).ok
    cols = ["제품코드", "기준수량", "부품코드", "수량", "손실률", "출고창고", "메모"]
    pv = bulk.preview_boms(pd.DataFrame([["ZB", "", "ZA", 1, "", "", ""]], columns=cols))
    assert any("순환" in e for e in pv.errors)
    c, d = _mat("ZC"), _mat("ZD")
    pv = bulk.preview_boms(pd.DataFrame([["ZC", "", "ZD", 1, "", "", ""], ["ZD", "", "ZC", 1, "", "", ""]], columns=cols))
    assert any("순환" in e for e in pv.errors)
    pv = bulk.preview_boms(pd.DataFrame([["ZC", "", "ZD", 1, "", "", ""]], columns=cols))
    assert pv.ok
    del c, d


def test_demand_excel_serial_date(fresh):
    plant = int(db.scalar("SELECT id FROM plants ORDER BY id LIMIT 1"))
    pv = bulk.preview_demands(pd.DataFrame({"제품코드": ["PKG-001", "PKG-002"], "수량": [1, 1], "납기": [46300, "46301"],
                                            "메모": ["", ""]}), plant, False)
    assert not pv.errors
    assert [r["due"] for r in pv.rows] == ["2026-10-05", "2026-10-06"]
    pv = bulk.preview_demands(pd.DataFrame({"제품코드": ["PKG-001"], "수량": [1], "납기": [12], "메모": [""]}), plant, False)
    assert pv.errors


def test_mrp_ignores_wo_without_receipt_wh(fresh):
    p, c, pid, line = _wo("ZP9", "ZC9", qty=50, receipt=False)
    plant = int(db.scalar("SELECT plant_id FROM warehouses WHERE id = ?", (wh(),)))
    due = (date.today() + timedelta(days=10)).isoformat()
    assert mrp.add_demand(plant, p, 50, due, "", M1).ok
    r = mrp.run(plant, M1)
    assert r.ok
    plans = mrp.plans_df(r.tx_id)
    assert (plans["code"] == "ZP9").any()                                     # 입고되지 않을 작업지시는 공급으로 보지 않음

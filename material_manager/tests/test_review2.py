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
from test_app import app  # noqa: E402,F401

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


# ── 다시 점검 (2차) ──────────────────────────────────────────
def test_cancel_request_closed_when_already_reversed(fresh):
    from core import workflow
    tx = services.register_transaction(mid("PKG-001"), "IN", 4, TODAY, 1000, warehouse_id=wh(), actor=CLERK)
    req = approvals.request_cancel(tx.tx_id, "착오", CLERK)
    assert req.ok
    assert services.reverse_transaction(tx.tx_id, "관리자가 먼저 취소", actor=M1).ok
    r = approvals.decide(req.tx_id, True, "", M2)
    assert not r.ok and "닫았습니다" in r.message
    assert db.scalar("SELECT status FROM approval_requests WHERE id = ?", (req.tx_id,)) == "REJECTED"
    assert all(x["id"] != req.tx_id for x in workflow.queue(M2) if x["kind"] == "CANCEL")


def test_cancel_request_transfer_both_legs(fresh):
    other = int(db.scalar("SELECT id FROM warehouses WHERE id <> ? ORDER BY id LIMIT 1", (wh(),)) or 0)
    if not other:
        return
    r = services.transfer(mid("PKG-001"), wh(), other, 2, TODAY, actor=CLERK)
    assert r.ok, r.message
    legs = [int(x) for x in db.query_df("SELECT id FROM transactions WHERE transfer_no <> '' ORDER BY id")["id"]]
    assert approvals.request_cancel(legs[0], "착오", CLERK).ok
    assert not approvals.request_cancel(legs[1], "또", CLERK).ok              # 같은 이동의 다른 줄도 중복


def test_bom_bulk_blank_base_keeps(fresh):
    a, b = _mat("ZBA"), _mat("ZBB")
    assert production.save_bom(a, 10, [production.BomLine(b, 5)], "원본 메모", M1).ok
    cols = ["제품코드", "기준수량", "부품코드", "수량", "손실률", "출고창고", "메모"]
    pv = bulk.preview_boms(pd.DataFrame([["ZBA", "", "ZBB", 6, "", "", ""]], columns=cols))
    assert pv.ok and "그대로" in pv.summary
    assert bulk.apply_boms(pv.rows, M1).ok
    bom = production.get_bom(a)
    assert bom["base_qty"] == 10 and bom["note"] == "원본 메모"


def test_mrp_convert_only_latest_run(fresh):
    plant = int(db.scalar("SELECT plant_id FROM warehouses WHERE id = ?", (wh(),)))
    assert mrp.add_demand(plant, mid("PKG-001"), 500, TODAY, "", M1).ok
    old = mrp.run(plant, M1).tx_id
    new = mrp.run(plant, M1).tx_id
    ids = [int(i) for i in mrp.plans_df(old)["id"]]
    r = mrp.convert(old, ids, actor=M1)
    assert not r.ok and "더 새로운" in r.message
    assert not mrp.convert(new, [int(i) for i in mrp.plans_df(new)["id"]], actor=M1, wh_ids=[999999]).ok


def test_adjustment_pending_blocks_second(fresh):
    r = services.register_transaction(mid("PKG-001"), "ADJ", 5, TODAY, 18000, actor=CLERK)      # 35 → 5, 54만원 → 결재
    assert r.ok and r.pending
    again = services.register_transaction(mid("PKG-001"), "ADJ", 5, TODAY, 18000, actor=CLERK)
    assert not again.ok and "결재 대기" in again.message
    assert services.register_transaction(mid("PKG-001"), "IN", 100, TODAY, 18000, actor=CLERK).ok
    req = int(db.scalar("SELECT id FROM approval_requests WHERE kind = 'ADJ'"))
    assert approvals.decide(req, True, "", M1).ok
    assert stock("PKG-001") == 105


def test_close_month_blocked_by_pending_adjustment(fresh):
    from core import periods
    last = date.today().replace(day=1) - timedelta(days=1)
    r = services.register_transaction(mid("PKG-001"), "ADJ", 500, last.isoformat(), 18000, actor=CLERK)
    if not (r.ok and r.pending):
        raise AssertionError(r.message)
    ym = last.strftime("%Y-%m")
    while periods.next_closable() < ym:                                           # 앞 달들은 순서대로 마감
        assert periods.close_month(periods.next_closable(), M1).ok
    out = periods.close_month(ym, M1)
    assert not out.ok and "결재 대기" in out.message


def test_sap_reversal_after_attempt_waits(fresh):
    import config
    from core import sap
    config.SAP_MODE = "mock"
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE materials SET sap_matnr = REPLACE(code, '-', '')")
            conn.execute("UPDATE plants SET sap_plant = '1000'")
            conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
        tx = services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 1, actor=CLERK)
        db.execute("UPDATE sap_outbox SET status = 'ERROR', attempts = 1 WHERE tx_id = ?", (tx.tx_id,))  # 시간 초과 등
        rev = services.reverse_transaction(tx.tx_id, "취소", actor=M1)
        assert rev.ok
        st = dict(db.query_df("SELECT tx_id, status FROM sap_outbox").itertuples(index=False))
        assert st[tx.tx_id] == "ERROR" and st[rev.tx_id] == "PENDING"            # 원거래는 다시 보내고 취소는 기다림
    finally:
        config.SAP_MODE = "off"


def test_reconcile_shared_sap_key(fresh):
    import config
    from core import reconcile
    config.SAP_MODE = "mock"
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE materials SET sap_matnr = '100200' WHERE code IN ('PKG-001', 'PKG-002')")
            conn.execute("UPDATE plants SET sap_plant = '1000'")
            conn.execute("UPDATE warehouses SET sap_sloc = '0001'")
        total = stock("PKG-001", "WH1") + stock("PKG-002", "WH1")
        sap_stock = pd.DataFrame({"sap_matnr": ["100200"], "plant": ["1000"], "sloc": ["0001"], "sap_qty": [total]})
        res = reconcile.compare(sap_stock).set_index("code")
        assert res.loc["PKG-001", "verdict"].startswith("일치") and res.loc["PKG-002", "verdict"].startswith("일치")
    finally:
        config.SAP_MODE = "off"


# ── 다시 점검 (3차: 실행·업로드) ─────────────────────────────
def test_quality_late_approvals_scoped(fresh):
    from core import purchasing, quality
    pr = purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "보충", CLERK)
    db.execute("UPDATE purchase_requests SET requested_at = '2020-01-01 09:00:00' WHERE id = ?", (pr.id,))
    late = lambda ws: next(c for c in quality.run(ws) if c["key"] == "approval_late")["rows"]   # noqa: E731
    assert late(None) and not late([999999])


def test_csv_cp949_and_thousands(fresh):
    from core import excel_forms
    raw = "자재코드,자재명,단위,단가\nZK-1,한글자재,EA,\"1,000\"\n".encode("cp949")
    df, problem = excel_forms.read_import("material_upload", raw, "m.csv", 100)
    assert not problem and df.iloc[0]["자재명"] == "한글자재"
    up = services.normalize_upload(df)
    assert services.import_materials(up.df, actor=M1).ok
    assert db.scalar("SELECT unit_price FROM materials WHERE code = 'ZK-1'") == 1000


def test_bad_ids_do_not_500(app):
    from test_app import client as _c, login, post  # noqa: F401
    from core import seed
    seed.seed()
    c = login(app.test_client(), "admin")
    huge = "9" * 23
    for url, data in (("/mrp/convert", {"run_id": "abc"}), ("/mrp/convert", {"run_id": huge}),
                      ("/notifications/read", {"id": huge}), ("/partners/link", {"name": "x", "partner_id": huge}),
                      ("/production/routing", {"product": huge}), ("/production/run", {"product": huge})):
        assert post(c, url, data).status_code < 500, url


# ── 전문가 점검: 생산 원가가 재고 평가와 맞아야 한다 ─────────
def test_production_does_not_create_inventory_value(fresh):
    from datetime import date as _d, timedelta as _td
    from core import valuation
    p, c = _mat("ZFG"), _mat("ZCP", price=100)
    assert production.save_bom(p, 1, [production.BomLine(c, 1)], "", M1).ok
    old = (_d.today() - _td(days=200)).isoformat()
    recent = (_d.today() - _td(days=10)).isoformat()
    assert services.register_transaction(c, "IN", 10, old, 100, warehouse_id=wh(), actor=M2).ok
    assert services.register_transaction(c, "IN", 10, recent, 300, warehouse_id=wh(), actor=M2).ok
    r = production.post(p, 10, wh(), TODAY, actor=CLERK, receipt_wh_id=wh())
    assert r.ok, r.message
    df, _ = valuation.report("2000-01-01", TODAY, "MAVG")
    total = float(df["close_value"].sum() - 0)
    zc = df.set_index("code")
    assert abs(float(zc.loc["ZCP", "close_value"]) + float(zc.loc["ZFG", "close_value"]) - 4000) < 1   # 산 금액 그대로
    assert abs(float(zc.loc["ZFG", "close_value"]) - 2000) < 1                                         # 평균 200 × 10
    del total


def test_production_return_is_not_a_purchase(fresh):
    from core import valuation
    p, c, pid, line = _wo("ZP7", "ZC7")
    assert production.issue(pid, {line: 20}, TODAY, actor=CLERK).ok
    assert production.issue(pid, {line: -5}, TODAY, actor=CLERK).ok
    df, _ = valuation.report(TODAY, TODAY, "MAVG")
    row = df.set_index("code").loc["ZC7"]
    assert abs(float(row["receipts"]) - 100000) < 1                # 매입은 처음 100개 × 1,000 만
    assert abs(float(row["issues"]) - 15000) < 1                   # 출고 20 − 반납 5 = 15개

"""사용자·전문가 점검 7차: 생산 반납·선입선출 투입·마감 뒤 취소의 평가, 구매 직무 분리, 표준원가 이력·변경 감지,
원가 기록 잠금, SAP 생산 입고 이동유형."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r7_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
from core import db, periods, production, services, valuation  # noqa: E402
from test_advanced import M1, M2, fresh, mid, wh  # noqa: E402,F401

TODAY = date.today().isoformat()
CLERK = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}


def _mat(code, price=100):
    assert services.create_material({"code": code, "name": code, "unit": "EA", "unit_price": price}).ok
    return mid(code)


def _total(method, codes):
    df, _ = valuation.report("2000-01-01", TODAY, method)
    df = df.set_index("code")
    return sum(float(df.loc[c, "close_value"]) for c in codes if c in df.index)


@pytest.mark.parametrize("method", ["MAVG", "FIFO"])
def test_return_does_not_create_value(fresh, monkeypatch, method):
    monkeypatch.setattr(config, "VALUATION_DEFAULT", method)
    fg, c = _mat("ZRF"), _mat("ZRC")
    assert production.save_bom(fg, 1, [production.BomLine(c, 1)], "", M1).ok
    y = (date.today() - timedelta(days=3)).isoformat()
    assert services.register_transaction(c, "IN", 10, y, 100, warehouse_id=wh(), actor=M2).ok
    pid = production.create_wo(fg, 6, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh()).tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    assert production.issue(pid, {line: 10}, y, actor=CLERK).ok
    assert services.register_transaction(c, "IN", 10, TODAY, 300, warehouse_id=wh(), actor=M2).ok
    assert production.issue(pid, {line: -4}, TODAY, actor=CLERK).ok            # 반납 4 (투입 단가 100)
    assert production.complete(pid, 6, 0, TODAY, actor=CLERK, backflush=False).ok
    assert _total(method, ["ZRC", "ZRF"]) == pytest.approx(10 * 100 + 10 * 300)   # 산 금액 그대로


def test_fifo_issue_cost_spans_layers(fresh, monkeypatch):
    monkeypatch.setattr(config, "VALUATION_DEFAULT", "FIFO")
    fg, c = _mat("ZFF"), _mat("ZFC")
    assert production.save_bom(fg, 1, [production.BomLine(c, 1)], "", M1).ok
    y = (date.today() - timedelta(days=2)).isoformat()
    assert services.register_transaction(c, "IN", 10, y, 100, warehouse_id=wh(), actor=M2).ok
    assert services.register_transaction(c, "IN", 10, TODAY, 200, warehouse_id=wh(), actor=M2).ok
    pid = production.create_wo(fg, 12, wh(), due_date=TODAY, actor=M1, receipt_wh_id=wh()).tx_id
    line = int(db.scalar("SELECT id FROM production_lines WHERE production_id = ?", (pid,)))
    assert production.issue(pid, {line: 12}, TODAY, actor=CLERK).ok
    cost = float(db.scalar("SELECT issued_cost FROM production_lines WHERE id = ?", (line,)))
    assert cost == pytest.approx(10 * 100 + 2 * 200)                             # 두 층에서 꺼낸 금액
    assert production.complete(pid, 12, 0, TODAY, actor=CLERK, backflush=False).ok
    assert _total("FIFO", ["ZFC", "ZFF"]) == pytest.approx(3000)


def test_cancel_after_close_uses_original_price(fresh):
    fg, c = _mat("ZCF"), _mat("ZCC")
    assert production.save_bom(fg, 1, [production.BomLine(c, 1)], "", M1).ok
    last = date.today().replace(day=1) - timedelta(days=1)
    assert services.register_transaction(c, "IN", 4, last.isoformat(), 100, warehouse_id=wh(), actor=M2).ok
    r = production.post(fg, 4, wh(), last.isoformat(), actor=CLERK, receipt_wh_id=wh())
    assert r.ok, r.message
    ym = last.strftime("%Y-%m")
    while periods.next_closable() <= ym:
        assert periods.close_month(periods.next_closable(), M1).ok
    assert services.register_transaction(c, "IN", 6, TODAY, 500, warehouse_id=wh(), actor=M2).ok
    assert production.cancel(r.tx_id, "잘못 만듦", actor=M2).ok
    assert _total("MAVG", ["ZCC", "ZCF"]) == pytest.approx(4 * 100 + 6 * 500)


# ── 통제 ─────────────────────────────────────────────────────
def test_release_hold_needs_uninvolved_person(fresh):
    from core import purchasing
    m = mid("PKG-001")
    m3 = {"id": 23, "name": "팀장3", "role": "MANAGER", "ip": ""}
    pr = purchasing.create_pr(wh(), [(m, 10, 1000)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    tx = services.register_transaction(m, "IN", 10, TODAY, 1000, warehouse_id=wh(), po_no=po_no, po_item="10", actor=M1)
    with db.transaction() as conn:
        conn.execute("INSERT INTO documents (tx_id, doc_type, issue_date, supply_amount, tax_amount, file_name, stored_name, mime, "
                     "size, sha256, created_at, created_by_id) VALUES (?, 'E_TAX_INVOICE', ?, 20000, 2000, 'x.png', 's9', 'image/png', 1, "
                     "'h9', ?, ?)", (tx.tx_id, TODAY, TODAY, CLERK["id"]))
    assert purchasing.refresh_payment(po.id) == "BLOCKED"
    assert not purchasing.release_payment(po.id, "승인", M1).ok                   # 입고한 사람
    assert not purchasing.release_payment(po.id, "승인", CLERK).ok                # 계산서 올린 사람
    assert purchasing.release_payment(po.id, "승인", m3).ok


def test_sod_po_receipt_setting(fresh, monkeypatch):
    from core import purchasing
    monkeypatch.setattr(config, "SOD_PO_RECEIPT", True)
    m = mid("PKG-001")
    pr = purchasing.create_pr(wh(), [(m, 5, 1000)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    r = services.register_transaction(m, "IN", 5, TODAY, 1000, warehouse_id=wh(), po_no=po_no, po_item="10", actor=M2)
    assert not r.ok and "직무 분리" in r.message
    assert services.register_transaction(m, "IN", 5, TODAY, 1000, warehouse_id=wh(), po_no=po_no, po_item="10", actor=CLERK).ok


def test_standard_history_and_stale_on_rate_change(fresh, monkeypatch):
    from core import costing
    monkeypatch.setattr(config, "LABOR_RATE", 100.0)
    fg, c = _mat("ZHF"), _mat("ZHC", 100)
    assert production.save_bom(fg, 1, [production.BomLine(c, 1)], "", M1).ok
    assert production.save_routing(fg, [production.Op("조립", "W", 2, "")], M1).ok
    admin = {"id": None, "name": "관리자", "ip": ""}
    costing.rollup(admin)
    db.execute("UPDATE materials SET unit_price = 150 WHERE id = ?", (c,))
    costing.rollup(admin)
    assert db.scalar("SELECT COUNT(*) FROM standard_cost_history WHERE material_id = ?", (fg,)) == 1
    assert "ZHF" in (db.scalar("SELECT detail FROM audit_log WHERE action = 'STD_COST' ORDER BY id DESC LIMIT 1") or "")
    assert services.register_transaction(c, "IN", 10, TODAY, 150, warehouse_id=wh(), actor=M2).ok
    pid = production.post(fg, 2, wh(), TODAY, actor=CLERK, receipt_wh_id=wh()).tx_id
    monkeypatch.setattr(config, "LABOR_RATE", 200.0)                            # 산정 뒤 임률 변경
    assert "임률" in (costing.variance(pid).get("stale") or "")
    assert not costing.settle(pid, M1)[0]
    with pytest.raises(Exception):
        db.execute("UPDATE productions SET material_cost = 1 WHERE id = ?", (pid,))   # 완료된 원가는 잠김


def test_sap_costcenter_fg_receipt_is_521(fresh, monkeypatch):
    from core import sap
    assert sap.movement_type("IN", 1, "", production="receipt") == "521"
    monkeypatch.setattr(config, "SAP_PRODUCTION_MODE", "order")
    assert sap.movement_type("IN", 1, "", production="receipt") == "101"


# ── 관리자·데이터 관리 점검 ───────────────────────────────────
def test_hometax_item_rows_are_summed():
    import io as _io

    import pandas as pd
    from core import hometax
    no = "202609104100000099999999"
    head = ["작성일자", "승인번호", "발급일자", "공급자사업자등록번호", "상호", "합계금액", "공급가액", "세액", "품목명"]
    rows = [head, ["2026-09-10", no, "2026-09-10", "1018112345", "가", 110000, 100000, 10000, "A"],
            ["2026-09-10", no, "2026-09-10", "1018112345", "가", 55000, 50000, 5000, "B"]]
    buf = _io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, header=False)
    inv, problem = hometax.read(buf.getvalue(), "x.xlsx")
    assert not problem and float(inv.iloc[0]["supply"]) == 150000 and float(inv.iloc[0]["tax"]) == 15000


def test_scoped_shortage_ignores_other_plants(fresh):
    from core import repository as repo
    m = _mat("ZSS")
    db.execute("UPDATE materials SET safety_stock = 5 WHERE id = ?", (m,))
    other = int(db.scalar("SELECT id FROM warehouses WHERE id <> ? ORDER BY id LIMIT 1", (wh(),)) or 0)
    df = repo.stock_df(wh_ids={other} if other else {wh()})
    assert not bool(df.loc[df["code"] == "ZSS", "shortage"].iloc[0])           # 그 창고에서 다룬 적 없는 자재
    assert bool(repo.stock_df().set_index("code").loc["ZSS", "shortage"])         # 전사로는 미달


def test_partner_merge_blocks_different_biz_no(fresh):
    from core import partners
    a = partners.create({"name": "갑상사", "kind": "SUPPLIER", "biz_no": "1248100998"}, M1).id
    b = partners.create({"name": "갑상사 지점", "kind": "SUPPLIER", "biz_no": "1018100001"}, M1).id
    r = partners.merge(b, a, M1)
    assert not r.ok and "사업자번호" in r.message


def test_po_blocked_without_sap_number(fresh, monkeypatch):
    from core import purchasing, sap
    m = _mat("ZNOSAP")
    pr = purchasing.create_pr(wh(), [(m, 5, 100)], TODAY, "보충", CLERK)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    monkeypatch.setattr(sap, "enabled", lambda: True)
    r = purchasing.create_po(pr.id, "공급", {item: 100}, "", "", M2)
    assert not r.ok and "ZNOSAP" in r.message


def test_rollup_can_update_made_price(fresh):
    from core import costing
    fg, c = _mat("ZUPF", 999), _mat("ZUPC", 100)
    assert production.save_bom(fg, 1, [production.BomLine(c, 2)], "", M1).ok
    ok, msg = costing.rollup({"id": None, "name": "관리자", "ip": ""}, update_price=True)
    assert ok and float(db.scalar("SELECT unit_price FROM materials WHERE id = ?", (fg,))) == pytest.approx(200)
    assert float(db.scalar("SELECT unit_price FROM materials WHERE id = ?", (c,))) == 100

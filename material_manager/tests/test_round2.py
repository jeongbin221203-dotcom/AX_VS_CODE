"""DB 버전 관리 · 단위 환산 · 스캔 수량 · 끊김 대비(여러 줄) · 작업지시(투입·반납·공정·완료·실제 원가) · MRP ·
거래처·BOM 엑셀 일괄 등록 · 잔디·네이버웍스 알림 · 바코드 라벨 · 감시 지표."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r2_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402
from core import (audit, auth, barcode, bulk, db, migrate, mrp, notify, production, services,  # noqa: E402
                  uom)
from test_advanced import M1, M2, fresh, mid, stock, wh  # noqa: E402,F401
from test_app import app, client, csrf, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()
NOW_ISO = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ahead(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ── DB 버전 관리 ─────────────────────────────────────────────
def test_migrations_chain_downgrade_upgrade(fresh):
    ids = [m.revision for m in migrate.chain()]
    assert ids[:2] == ["0001", "0002"] and migrate.head() == ids[-1]

    def check(conn):
        assert migrate.current(conn) == migrate.head()
        assert migrate.downgrade(conn, "0001") == ids[1:][::-1]
        assert "lead_time_days" not in db._columns(conn, "materials")
        assert migrate.upgrade(conn) == ids[1:]
        assert "lead_time_days" in db._columns(conn, "materials") and migrate.current(conn) == migrate.head()
        assert [h["direction"] for h in migrate.history(conn)][-1] == "upgrade"
        with pytest.raises(migrate.MigrationError):
            migrate.upgrade(conn, "9999")

    if db.is_pg():                                       # PostgreSQL: 한 트랜잭션 (init_db 와 같음)
        with db.transaction() as conn:
            check(conn)
        return
    raw = db.connect_sqlite()
    try:
        check(db.Conn(raw, False))
    finally:
        raw.commit()
        raw.close()


def test_revision_file_template(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "VERSIONS", tmp_path)
    (tmp_path / "0001_a.py").write_text('revision = "0001"\ndown_revision = None\ndef upgrade(conn): pass\n', encoding="utf-8")
    path = migrate.create("거래처 등급 추가")
    assert path.name.startswith("0002_") and 'down_revision = "0001"' in path.read_text(encoding="utf-8")
    assert [m.revision for m in migrate.chain()] == ["0001", "0002"]
    (tmp_path / "0003_b.py").write_text('revision = "0003"\ndown_revision = "0001"\ndef upgrade(conn): pass\n', encoding="utf-8")
    with pytest.raises(migrate.MigrationError):                      # 갈래
        migrate.chain()


# ── 단위 환산 ─────────────────────────────────────────────────
def test_units_convert_and_box_barcode(fresh):
    m = mid("PKG-003")
    assert not uom.add(m, "EA", 10, "", M1)[0]                        # 기본 단위
    assert uom.add(m, "box", 50, "8800000000017", M1)[0]
    assert not uom.add(m, "PLT", 10, "8800000000017", M1)[0]         # 바코드 겹침
    assert not services.create_material({"code": "X1", "name": "x", "barcode": "8800000000017"}, M1).ok
    before = stock("PKG-003")
    r = services.register_transaction(m, "OUT", 2, TODAY, 1200, actor=M1, unit="BOX")
    assert r.ok and stock("PKG-003") == before - 100
    row = db.query_df("SELECT qty, entry_unit, entry_qty FROM transactions WHERE id = ?", (r.tx_id,)).iloc[0]
    assert (row["qty"], row["entry_unit"], row["entry_qty"]) == (100, "BOX", 2)
    assert not services.register_transaction(m, "OUT", 1, TODAY, 0, actor=M1, unit="PLT").ok
    r = services.register_lines("IN", wh(), TODAY, [services.LineIn(m, 3, unit="BOX"), services.LineIn(m, 5)], actor=M1)
    assert r.ok and stock("PKG-003") == before - 100 + 155


def test_lookup_has_units_and_batch_screen_units(client):
    from core import seed
    seed.seed()
    m = mid("PKG-003")
    uom.add(m, "BOX", 50, "8800000000017", M1)
    item = next(x for x in client.get("/materials/lookup.json").get_json()["items"] if x[1] == "PKG-003")
    assert item[10] == [["BOX", 50.0, "8800000000017"]]
    w = str(wh())
    data = {"kind": "IN", "warehouse_id": w, "tx_date": TODAY, "ref_no": "", "partner": "", "cost_center": "", "note": "",
            "line_mid": [str(m)], "line_qty": ["2"], "line_unit": ["BOX"], "line_lot": [""], "line_exp": [""],
            "line_price": [""], "line_note": [""]}
    before = stock("PKG-003")
    assert post(client, "/transactions/batch", data).status_code == 302
    assert stock("PKG-003") == before + 100
    assert "BOX" in client.get(f"/materials/?tab=edit&id={m}").get_data(as_text=True)


def test_offline_batch_queue(client):
    from core import seed
    seed.seed()
    m = mid("PKG-001")
    before = stock("PKG-001")
    data = {"kind": ["IN", "BATCH"], "warehouse_id": str(wh()), "tx_date": TODAY, "ref_no": "", "partner": "",
            "cost_center": "", "note": "", "line_mid": [str(m), str(m)], "line_qty": ["1", "2"], "line_unit": ["", ""],
            "line_lot": ["", ""], "line_exp": ["", ""], "line_price": ["", ""], "line_note": ["", ""],
            "captured_at": NOW_ISO, "_once": "a" * 32}
    res = client.post("/transactions/queue", data={**data, "_csrf": csrf(client)}, headers={"X-MM-Queue": "1"})
    assert res.get_json()["ok"] and stock("PKG-001") == before + 3
    res = client.post("/transactions/queue", data={**data, "_csrf": csrf(client)}, headers={"X-MM-Queue": "1"})
    assert res.get_json().get("duplicate") and stock("PKG-001") == before + 3   # 같은 입력은 한 번만


# ── 작업지시 ─────────────────────────────────────────────────
def _product(code="FG-1", lead=0):
    services.create_material({"code": code, "name": code, "unit": "EA", "lead_time_days": lead}, M1)
    return mid(code)


def test_work_order_lifecycle_actual_cost(fresh):
    fg = _product(lead=3)
    L = production.BomLine
    production.save_bom(fg, 1, [L(mid("LSH-001"), 2), L(mid("LBL-001"), 4)], "", M1)
    production.save_routing(fg, [production.Op("조립", "1라인", 1.5), production.Op("검사", "QC", 0.5)], M1)
    r = production.create_wo(fg, 10, wh(), due_date=ahead(10), actor=M1, receipt_wh_id=wh())
    assert r.ok
    p = production.get(r.tx_id)
    assert p["status"] == "PLANNED" and p["start_date"] == ahead(7) and p["planned_cost"] == pytest.approx(20 * 15000 + 40 * 80)
    lines = production.wo_lines(r.tx_id).set_index("code")
    ops = production.wo_ops(r.tx_id)
    assert list(ops["op_name"]) == ["조립", "검사"]
    before = (stock("LSH-001"), stock("LBL-001"))
    # 라싱벨트 22개 투입(2개 더 씀), 라벨 30개만 먼저
    res = production.issue(r.tx_id, {int(lines.loc["LSH-001", "id"]): 22, int(lines.loc["LBL-001", "id"]): 30}, TODAY, actor=M1)
    assert res.ok and production.get(r.tx_id)["status"] == "RELEASED"
    assert production.wip_df().iloc[0]["wip_cost"] == pytest.approx(22 * 15000 + 30 * 80)
    # 2개 반납, 투입보다 많은 반납은 거부
    assert not production.issue(r.tx_id, {int(lines.loc["LSH-001", "id"]): -50}, TODAY, actor=M1).ok
    assert production.issue(r.tx_id, {int(lines.loc["LSH-001", "id"]): -2}, TODAY, actor=M1).ok
    # 추가 투입 (BOM에 없는 장갑 5)
    assert production.issue(r.tx_id, {}, TODAY, actor=M1, extra=[(mid("SFT-001"), 5, wh())]).ok
    assert production.report_operation(r.tx_id, int(ops.iloc[0]["id"]), 10, 0, 14, "홍길동", "", M1).ok
    # 완료: 남은 라벨 10은 백플러시, 양품 9 · 불량 1
    res = production.complete(r.tx_id, 9, 1, TODAY, actor=M1, backflush=True)
    assert res.ok, res.message
    p = production.get(r.tx_id)
    cost = 20 * 15000 + 40 * 80 + 5 * 1500
    assert p["status"] == "DONE" and p["material_cost"] == pytest.approx(cost) and p["good_qty"] == 9
    receipt = db.query_df("SELECT unit_price, qty FROM transactions WHERE production_id = ? AND tx_type = 'IN' "
                          "AND note LIKE '생산 입고%'", (r.tx_id,)).iloc[0]
    assert receipt["qty"] == 9 and receipt["unit_price"] == pytest.approx(cost / 9, rel=1e-6)
    assert (stock("LSH-001"), stock("LBL-001")) == (before[0] - 20, before[1] - 40) and stock("FG-1") == 9
    assert set(production.wo_ops(r.tx_id)["status"]) == {"DONE"}
    assert not production.issue(r.tx_id, {int(lines.loc["LSH-001", "id"]): 1}, TODAY, actor=M1).ok   # 완료 뒤 투입 불가
    assert production.cancel(r.tx_id, "오입력", actor=M2).ok
    assert stock("FG-1") == 0 and (stock("LSH-001"), stock("LBL-001")) == before
    # 거래 없는 계획은 그냥 취소
    r2 = production.create_wo(fg, 1, wh(), due_date=TODAY, actor=M1)
    assert production.cancel(r2.tx_id, "안 함", actor=M1).ok and production.get(r2.tx_id)["status"] == "CANCELLED"


def test_quick_post_actual_and_scrap(fresh):
    fg = _product()
    production.save_bom(fg, 1, [production.BomLine(mid("LSH-001"), 1)], "", M1)
    before = stock("LSH-001")
    r = production.post(fg, 10, wh(), TODAY, actor=M1, receipt_wh_id=wh(), actual={mid("LSH-001"): 12}, scrap_qty=2)
    assert r.ok, r.message
    assert stock("LSH-001") == before - 12 and stock("FG-1") == 8
    p = production.get(r.tx_id)
    assert p["material_cost"] == pytest.approx(12 * 15000) and p["planned_cost"] == pytest.approx(10 * 15000)
    assert not production.post(fg, 1, wh(), TODAY, actor=M1, scrap_qty=2).ok


# ── MRP ──────────────────────────────────────────────────────
def test_mrp_multilevel_lead_time_lot_sizing_and_convert(fresh):
    fg, sa = _product("FG-1", lead=2), _product("SA-1", lead=3)
    buy = mid("LSH-001")
    db.execute("UPDATE materials SET lead_time_days = 5, min_order_qty = 100, order_multiple = 50, safety_stock = 0 "
               "WHERE id = ?", (buy,))
    L = production.BomLine
    production.save_bom(fg, 1, [L(sa, 2)], "", M1)
    production.save_bom(sa, 1, [L(buy, 3)], "", M1)
    plant = int(db.scalar("SELECT MIN(id) FROM plants"))
    assert mrp.add_demand(plant, fg, 10, ahead(20), "고객 A", M1).ok
    r = mrp.run(plant, M1)
    assert r.ok
    plans = mrp.plans_df(r.tx_id).set_index("code")
    assert plans.loc["FG-1", "kind"] == "MAKE" and plans.loc["FG-1", "qty"] == 10
    assert plans.loc["FG-1", "need_date"] == ahead(20) and plans.loc["FG-1", "order_date"] == ahead(18)
    assert plans.loc["SA-1", "qty"] == 20 and plans.loc["SA-1", "need_date"] == ahead(18) and plans.loc["SA-1", "order_date"] == ahead(15)
    have = stock("LSH-001")
    need = 60 - have                                                 # 반제품 20 × 3
    expect = max(need, 100)
    expect = -(-expect // 50) * 50
    assert plans.loc["LSH-001", "kind"] == "BUY" and plans.loc["LSH-001", "qty"] == expect
    assert plans.loc["LSH-001", "order_date"] == ahead(10) and plans.loc["LSH-001", "level"] == 2
    # 바꾸기: 구매 → 구매요청, 생산 → 작업지시
    res = mrp.convert(r.tx_id, plans["id"].astype(int).tolist(), actor=M1)       # 샘플의 안전재고 미달 구매 계획도 함께
    assert res.ok and res.qty == len(plans)
    assert db.scalar("SELECT COUNT(*) FROM productions WHERE source = 'MRP'") == 2
    assert db.scalar("SELECT COUNT(*) FROM purchase_requests") == 1
    assert set(mrp.plans_df(r.tx_id)["status"]) == {"CONVERTED"}
    # 다시 돌리면 이미 만든 작업지시·구매요청이 공급으로 잡혀 새 계획이 없다
    r2 = mrp.run(plant, M1)
    assert r2.qty == 0, mrp.plans_df(r2.tx_id)[["code", "qty", "pegging"]]


def test_mrp_late_and_safety_stock(fresh):
    m = mid("PKG-002")
    db.execute("UPDATE materials SET safety_stock = 1000, lead_time_days = 7 WHERE id = ?", (m,))
    r = mrp.run(int(db.scalar("SELECT MIN(id) FROM plants")), M1)
    p = mrp.plans_df(r.tx_id).set_index("code").loc["PKG-002"]
    assert p["qty"] == pytest.approx(1000 - stock("PKG-002")) and p["order_date"] < TODAY      # 이미 늦음
    assert "안전재고" in p["pegging"]


def test_mrp_and_wo_screens(client):
    from core import seed
    seed.seed()
    fg = _product()
    production.save_bom(fg, 1, [production.BomLine(mid("LSH-001"), 1)], "", M1)
    production.save_routing(fg, [production.Op("조립")], M1)
    plant = int(db.scalar("SELECT MIN(id) FROM plants"))
    assert post(client, "/mrp/demand", {"plant": str(plant), "material": str(fg), "qty": "5", "due_date": ahead(5)}).status_code == 302
    assert post(client, "/mrp/run", {"plant": str(plant), "horizon": "60"}).status_code == 302
    page = client.get(f"/mrp/?plant={plant}").get_data(as_text=True)
    assert "FG-1" in page and "고른 계획" in page
    res = post(client, "/production/wo", {"product": str(fg), "qty": "3", "due_date": TODAY, "wh": str(wh()), "receipt_wh": str(wh())})
    pid = int(res.headers["Location"].rstrip("/").split("/")[-1])
    page = client.get(f"/production/{pid}").get_data(as_text=True)
    assert "자재 투입·반납" in page and "조립" in page
    line = int(production.wo_lines(pid).iloc[0]["id"])
    assert post(client, f"/production/{pid}/issue", {f"line_{line}": "3", "tx_date": TODAY}).status_code == 302
    assert "재공" in client.get("/production/?tab=wip").get_data(as_text=True)
    assert post(client, f"/production/{pid}/complete", {"good_qty": "3", "scrap_qty": "0", "tx_date": TODAY,
                                                        "backflush": "1"}).status_code == 302
    assert production.get(pid)["status"] == "DONE"
    assert post(client, "/production/routing", {"product": str(fg), "op_name": ["조립", "포장", ""], "workcenter": ["", "", ""],
                                                "std_minutes": ["1", "x", ""], "op_note": ["", "", ""]}).status_code == 302


# ── 엑셀 일괄 등록 ───────────────────────────────────────────
def _xlsx(df: pd.DataFrame) -> io.BytesIO:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_partner_bulk_preview_and_apply(fresh):
    from core import partners
    partners.create({"name": "대한팔레트", "kind": "SUPPLIER"}, M1)
    raw = pd.DataFrame([["", "(주)대한팔레트", "", "124-81-00998", "김구매", "", "", "", "대한PLT, 대한팔레트 부산"],
                        ["P-NEW", "새상사", "납품처", "", "", "", "", "", ""],
                        ["", "틀린번호", "공급처", "1234567890", "", "", "", "", ""]],
                       columns=list(bulk.PARTNER_COLS.values()))
    pv = bulk.preview_partners(raw)
    assert [r["action"] for r in pv.rows] == ["갱신", "등록", "등록"] and len(pv.errors) == 1 and not pv.ok
    pv = bulk.preview_partners(raw.iloc[:2])
    assert pv.ok
    assert bulk.apply_partners(pv.rows, M1).ok
    p = db.query_df("SELECT * FROM partners WHERE name = '대한팔레트'").iloc[0]
    assert p["biz_no"] == "1248100998" and p["contact"] == "김구매"            # 갱신, 이름은 그대로
    assert db.scalar("SELECT kind FROM partners WHERE code = 'P-NEW'") == "CUSTOMER"
    assert db.scalar("SELECT COUNT(*) FROM partner_aliases") == 2


def test_bom_bulk_all_or_nothing(fresh):
    fg, sa = _product("FG-1"), _product("SA-1")
    cols = list(bulk.BOM_COLS.values())
    ok_rows = [["FG-1", 2, "SA-1", 2, "", "", ""], ["FG-1", "", "LSH-001", 1, 5, "WH1", "끈"], ["SA-1", "", "LBL-001", 3, "", "", ""]]
    pv = bulk.preview_boms(pd.DataFrame(ok_rows, columns=cols))
    assert pv.ok, pv.errors
    assert bulk.apply_boms(pv.rows, M1).ok
    assert production.get_bom(fg)["base_qty"] == 2 and len(production.bom_items_df(production.get_bom(fg)["id"])) == 2
    bad = ok_rows + [["SA-1", "", "FG-1", 1, "", "", ""]]                  # 순환
    pv = bulk.preview_boms(pd.DataFrame(bad, columns=cols))
    assert not pv.ok and any("순환" in e for e in pv.errors)               # 지금 BOM + 파일로 미리보기에서 순환을 알림
    r = bulk.apply_boms(pv.rows, M1)                                       # 그래도 반영을 부르면 거부
    assert not r.ok and "순환" in r.message
    assert len(production.bom_items_df(production.get_bom(sa)["id"])) == 1  # 아무것도 바뀌지 않음
    pv = bulk.preview_boms(pd.DataFrame([["FG-1", "", "없는것", 1, "", "", ""], ["FG-1", "", "SA-1", 0, "", "XX", ""]], columns=cols))
    assert len(pv.errors) == 2


def test_bulk_screens(client):
    from core import seed
    seed.seed()
    _product("FG-1")
    raw = pd.DataFrame([["FG-1", 1, "LSH-001", 2, "", "", ""]], columns=list(bulk.BOM_COLS.values()))
    res = post(client, "/production/bom/import", {"file": (_xlsx(raw), "bom.xlsx")}, content_type="multipart/form-data")
    page = res.get_data(as_text=True)
    assert "이대로 반영" in page
    import re
    token = re.search(r'name="token" value="([0-9a-f]{32})"', page).group(1)
    assert post(client, "/production/bom/import/apply", {"token": token}).status_code == 302
    assert production.get_bom(mid("FG-1")) is not None
    assert post(client, "/production/bom/import/apply", {"token": token}).status_code == 302          # 두 번째는 만료
    assert client.get("/partners/import/template.xlsx").status_code == 200
    assert client.get("/production/bom/import/template.xlsx").status_code == 200


# ── 잔디 · 네이버웍스 ─────────────────────────────────────────
def _rsa_pem() -> str:
    from joserfc.jwk import RSAKey
    return RSAKey.generate_key(2048).as_pem(private=True).decode()


def test_jandi_and_naverworks(fresh, monkeypatch):
    for k, v in {"NOTIFY_MODE": "send", "JANDI_WEBHOOK_URL": "https://wh.jandi.com/connect-api/webhook/1/abc",
                 "NW_BOT_ID": "bot1", "NW_CLIENT_ID": "cid", "NW_CLIENT_SECRET": "sec", "NW_SERVICE_ACCOUNT": "sa@x",
                 "NW_PRIVATE_KEY": _rsa_pem(), "NW_CHANNEL_ID": "ch-9", "NW_TO_USERS": True, "SMTP_HOST": ""}.items():
        monkeypatch.setattr(config, k, v)
    notify._nw_token.update(value="", until=0)
    r = auth.create_user("mgr.x", "관리자X", "MANAGER", "Passw0rd!", audit.SYSTEM, must_change_pw=False)
    auth.set_email(r.user["id"], "", audit.SYSTEM, messenger_id="mgr.x@works")
    clerk = auth.create_user("clerk.x", "담당X", "CLERK", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    from core import purchasing
    assert purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "보충", {**clerk, "ip": ""}).ok
    rows = db.query_df("SELECT channel, to_addr FROM notifications WHERE channel <> 'inbox' ORDER BY id")
    assert set(zip(rows["channel"], rows["to_addr"])) >= {("naverworks", "mgr.x@works"), ("naverworks", "channel:ch-9")}
    assert "jandi" in set(rows["channel"])
    calls = []

    def fake_post(url, data, headers, timeout=15):
        calls.append((url, data, headers))
        if url == notify.NW_TOKEN_URL:
            return 200, json.dumps({"access_token": "tok", "expires_in": 3600}).encode()
        return 201, b""
    monkeypatch.setattr(notify, "_post", fake_post)
    assert notify.send_pending().startswith("보냄 3")
    urls = [c[0] for c in calls]
    assert urls.count(notify.NW_TOKEN_URL) == 1                           # 토큰은 한 번 받아 재사용
    assert f"{notify.NW_API}/bots/bot1/users/mgr.x%40works/messages" in urls
    assert f"{notify.NW_API}/bots/bot1/channels/ch-9/messages" in urls
    jandi = next(c for c in calls if "jandi" in c[0])
    body = json.loads(jandi[1])
    assert jandi[2]["Accept"] == "application/vnd.tosslab.jandi-v2+json" and "구매요청" in body["body"]
    token_form = next(c for c in calls if c[0] == notify.NW_TOKEN_URL)[1].decode()
    assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in token_form and "scope=bot" in token_form
    # 실패하면 다시
    db.execute("UPDATE notifications SET status = 'PENDING' WHERE channel <> 'inbox'")
    monkeypatch.setattr(notify, "_post", lambda *a, **k: (500, b""))
    assert "실패 3" in notify.send_pending()


# ── 바코드 라벨 ──────────────────────────────────────────────
def test_code128_and_label_page(client):
    from core import seed
    seed.seed()
    assert barcode.encode("PJJ123C") == [104, 48, 42, 42, 17, 18, 19, 35, 55, 106]     # 위키백과 예시 (검사값 55)
    assert barcode.encode("12345")[:3] == [105, 12, 34] and barcode.encode("12345")[3:5] == [100, 21]
    assert barcode.svg("한글") is None
    m = mid("PKG-001")
    uom.add(m, "BOX", 20, "8801111111110", M1)
    page = client.get(f"/materials/labels/print?id={m}&n_{m}=2&u_{m}_BOX=1&layout=a4-10").get_data(as_text=True)
    assert page.count('class="label"') == 3 and "<svg" in page and "1 BOX = 20 EA" in page
    assert "라벨" in client.get("/materials/labels?q=PKG").get_data(as_text=True)


# ── 감시 지표 ────────────────────────────────────────────────
def test_metrics_endpoint(app, monkeypatch):
    c = app.test_client()
    c.get("/health")
    res = c.get("/metrics")                                              # 테스트 클라이언트는 127.0.0.1
    body = res.get_data(as_text=True)
    assert res.status_code == 200 and "mm_up 1" in body and "mm_db_up 1" in body and 'mm_http_requests_total{endpoint="health"' in body
    assert c.get("/metrics", environ_base={"REMOTE_ADDR": "10.1.2.3"}).status_code == 403
    monkeypatch.setattr(config, "METRICS_TOKEN", "s3cret")
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer s3cret"}, environ_base={"REMOTE_ADDR": "10.1.2.3"}).status_code == 200


def test_remote_search_when_many_materials(client, monkeypatch):
    from core import seed
    seed.seed()
    uom.add(mid("PKG-003"), "BOX", 50, "8800000000017", M1)
    monkeypatch.setattr(config, "LOOKUP_MAX", 3)
    data = client.get("/materials/lookup.json").get_json()
    assert data["remote"] and data["items"] == []
    found = client.get("/materials/search.json?q=팔레트").get_json()["items"]
    assert [x[1] for x in found] == ["PKG-001"]
    hit = client.get("/materials/search.json?exact=8800000000017").get_json()
    assert hit["items"][0][1] == "PKG-003" and hit["unit"] == "BOX"
    assert client.get("/materials/search.json?exact=nothing").get_json()["items"] == []

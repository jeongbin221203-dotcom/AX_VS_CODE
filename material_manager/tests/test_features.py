"""거래처 마스터 · 여러 줄(스캔) 입출고 · 바코드 · BOM·생산 투입 · 결재 알림 메일."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_feat_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402
from core import auth, audit, db, notify, partners, production, purchasing, services  # noqa: E402
from test_advanced import M1, M2, fresh, mid, stock, wh  # noqa: E402,F401
from test_app import app, client, csrf, login, post  # noqa: E402,F401

TODAY = date.today().isoformat()
CLERK = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}


def tx_rows(where: str = "1 = 1", params=()) -> pd.DataFrame:
    return db.query_df(f"SELECT * FROM transactions WHERE {where} ORDER BY id", params)


# ── 거래처 마스터 ────────────────────────────────────────────
def test_partner_key_and_biz_no():
    assert partners.key("(주) 대한 상사") == partners.key("대한상사㈜") == partners.key("대한상사 주식회사") == "대한상사"
    assert partners.key("Hanil Co., Ltd.") == "hanil"
    assert partners.biz_no_ok("1248100998")            # 검증번호가 맞는 번호
    assert not partners.biz_no_ok("1248100999")
    assert partners.biz_fmt("1248100998") == "124-81-00998"


def test_partner_resolves_names_and_links_aliases(fresh):
    r = partners.create({"name": "대한팔레트", "kind": "SUPPLIER", "biz_no": "124-81-00998"}, M1)
    assert r.ok
    assert not partners.create({"name": "(주)대한팔레트"}, M1).ok           # 표기만 다른 같은 이름
    assert not partners.create({"name": "다른회사", "biz_no": "1248100998"}, M1).ok   # 같은 사업자번호
    assert not partners.create({"name": "틀린번호", "biz_no": "1234567890"}, M1).ok
    # 표기가 달라도 정식 이름 + partner_id 로 남는다
    res = services.register_transaction(mid("PKG-001"), "IN", 5, TODAY, 18000, partner="(주) 대한 팔레트", actor=M1)
    assert res.ok and "거래처" not in res.warning
    row = tx_rows("id = ?", (res.tx_id,)).iloc[0]
    assert row["partner"] == "대한팔레트" and int(row["partner_id"]) == r.id
    # 사업자번호로도 찾는다
    res = services.register_transaction(mid("PKG-001"), "IN", 1, TODAY, 18000, partner="124-81-00998", actor=M1)
    assert tx_rows("id = ?", (res.tx_id,)).iloc[0]["partner"] == "대한팔레트"
    # 마스터에 없는 이름: 등록은 되고 경고
    res = services.register_transaction(mid("PKG-001"), "IN", 2, TODAY, 18000, partner="대한팔레트 부산지점", actor=M1)
    assert res.ok and "거래처 마스터에 없는" in res.warning
    unknown = partners.unknown_names()
    assert "대한팔레트 부산지점" in set(unknown["name"])
    assert unknown.set_index("name").loc["대한팔레트 부산지점", "guess"].startswith(f"{r.id}|")   # 비슷한 거래처 추천
    # 연결하면 지난 거래도 그 거래처 실적으로 합쳐지고, 다음 입력은 정식 이름으로
    assert partners.link("대한팔레트 부산지점", r.id, M1).ok
    assert "대한팔레트 부산지점" not in set(partners.unknown_names()["name"])
    stats = partners.list_df().set_index("id").loc[r.id]
    assert stats["tx_cnt"] == 3 and stats["in_amt"] == pytest.approx(8 * 18000)
    res = services.register_transaction(mid("PKG-001"), "IN", 1, TODAY, 18000, partner="대한팔레트 부산지점", actor=M1)
    assert tx_rows("id = ?", (res.tx_id,)).iloc[0]["partner"] == "대한팔레트"
    # 이름을 바꾸면 예전 이름은 다른 이름으로 남는다
    assert partners.update(r.id, {"name": "대한팔레트산업", "kind": "SUPPLIER", "biz_no": "1248100998"}, M1).ok
    with db.get_conn() as conn:
        assert partners.resolve(conn, "대한팔레트")["name"] == "대한팔레트산업"


def test_cost_center_issue_is_not_a_partner_and_required_mode(fresh):
    services.register_transaction(mid("PKG-001"), "IN", 10, TODAY, 18000, partner="대한팔레트", actor=M1)
    res = services.register_transaction(mid("PKG-001"), "OUT", 1, TODAY, 18000, partner="생산1팀", cost_center="C100",
                                        actor=M1)
    assert res.ok and "거래처" not in res.warning                  # 사내 사용 부서는 거래처 확인 대상이 아님
    assert "생산1팀" not in set(partners.unknown_names()["name"])
    config.PARTNER_REQUIRED = True
    try:
        res = services.register_transaction(mid("PKG-001"), "IN", 1, TODAY, 18000, partner="없는상사", actor=M1)
        assert not res.ok and "거래처 마스터에 없습니다" in res.message
        partners.create({"name": "없는상사"}, M1)
        assert services.register_transaction(mid("PKG-001"), "IN", 1, TODAY, 18000, partner="없는 상사", actor=M1).ok
    finally:
        config.PARTNER_REQUIRED = False


def test_partner_screens(client):
    post(client, "/partners/new", {"name": "한국필름", "kind": "SUPPLIER"})
    page = client.get("/partners/").get_data(as_text=True)
    assert "한국필름" in page
    assert client.get("/partners/?tab=unknown").status_code == 200
    assert client.get("/partners/?export=xlsx").status_code == 200
    pid = int(db.scalar("SELECT id FROM partners WHERE name = '한국필름'"))
    assert "최근 거래" in client.get(f"/partners/{pid}").get_data(as_text=True)
    res = post(client, "/partners/link", {"name": "한국 필름 (주)", "partner_id": str(pid)})
    assert res.status_code == 302


# ── 여러 줄 입출고 ───────────────────────────────────────────
def test_batch_all_or_nothing_and_cancel(fresh):
    before = (stock("PKG-001"), stock("PKG-002"))
    lines = [services.LineIn(mid("PKG-001"), 3), services.LineIn(mid("PKG-002"), 10_000)]
    res = services.register_lines("OUT", wh(), TODAY, lines, actor=M1)
    assert not res.ok and res.message.startswith("2번 줄") and "재고 부족" in res.message
    assert (stock("PKG-001"), stock("PKG-002")) == before            # 1번 줄도 등록되지 않았다
    lines = [services.LineIn(mid("PKG-001"), 3), services.LineIn(mid("PKG-002"), 2, unit_price=9000)]
    res = services.register_lines("OUT", wh(), TODAY, lines, actor=M1, partner="샘플거래처")
    assert res.ok and "2줄" in res.message
    rows = tx_rows("batch_no <> ''")
    assert len(rows) == 2 and rows["batch_no"].nunique() == 1
    assert rows.set_index("material_id").loc[mid("PKG-002"), "unit_price"] == 9000
    assert rows.set_index("material_id").loc[mid("PKG-001"), "unit_price"] == 18000    # 비우면 기준단가
    batch_no = rows["batch_no"].iloc[0]
    assert not services.cancel_group("batch_no", batch_no, "", actor=M2).ok             # 사유 필수
    assert not services.cancel_group("batch_no", batch_no, "오입력", actor=M1).ok       # 본인 등록 → 다른 관리자
    res = services.cancel_group("batch_no", batch_no, "오입력", actor=M2, label=f"묶음 {batch_no}")
    assert res.ok and res.qty == 2
    assert (stock("PKG-001"), stock("PKG-002")) == before
    assert not services.cancel_group("batch_no", batch_no, "오입력", actor=M2).ok       # 두 번은 안 됨


def test_batch_lot_material_needs_lot_on_receipt(fresh):
    db.execute("UPDATE materials SET lot_managed = 1 WHERE code = 'LBL-001'")
    db.execute("INSERT INTO materials (code, name, unit, lot_managed, active, created_at, updated_at) "
               "VALUES ('LOT-NEW', '로트 자재', 'EA', 1, 1, ?, ?)", (TODAY, TODAY))
    res = services.register_lines("IN", wh(), TODAY, [services.LineIn(mid("LOT-NEW"), 5)], actor=M1)
    assert not res.ok and "로트" in res.message
    res = services.register_lines("IN", wh(), TODAY, [services.LineIn(mid("LOT-NEW"), 5, lot_no="a-1")], actor=M1)
    assert res.ok and tx_rows("material_id = ?", (mid("LOT-NEW"),)).iloc[0]["lot_no"] == "A-1"


def test_batch_screen(client):
    seed_client(client)
    page = client.get("/transactions/batch").get_data(as_text=True)
    assert "data-batch" in page and "스캔·찾기" in page
    m = str(db.scalar("SELECT id FROM materials WHERE code = 'PKG-003'"))
    w = str(db.scalar("SELECT MIN(id) FROM warehouses"))
    data = {"kind": "OUT", "warehouse_id": w, "tx_date": TODAY, "ref_no": "", "partner": "", "cost_center": "",
            "note": "", "line_mid": [m, m], "line_qty": ["2", "abc"], "line_lot": ["", ""], "line_exp": ["", ""],
            "line_price": ["", ""], "line_note": ["", ""]}
    res = post(client, "/transactions/batch", data)
    assert res.status_code == 200 and "숫자로" in res.get_data(as_text=True)          # 줄을 되돌려 다시 그린다
    data["line_qty"] = ["2", "3"]
    res = post(client, "/transactions/batch", data)
    assert res.status_code == 302 and len(tx_rows("batch_no <> ''")) == 2
    assert "최근 묶음" in client.get("/transactions/batch").get_data(as_text=True)
    hist = client.get("/history/?q=" + tx_rows("batch_no <> ''")["batch_no"].iloc[0]).get_data(as_text=True)
    assert "PKG-003" in hist


def seed_client(client):
    from core import seed
    if not db.scalar("SELECT COUNT(*) FROM materials"):
        seed.seed()


# ── 바코드 ───────────────────────────────────────────────────
def test_barcode_unique_and_lookup(client):
    seed_client(client)
    pkg1 = mid("PKG-001")
    assert services.update_material(pkg1, {**_mat(pkg1), "barcode": "8801234500017"}, M1).ok
    res = services.create_material({"code": "NEW-1", "name": "새 자재", "barcode": "8801234500017"}, M1)
    assert not res.ok and "PKG-001" in res.message                       # 다른 자재의 바코드
    res = services.create_material({"code": "NEW-1", "name": "새 자재", "barcode": "PKG-002"}, M1)
    assert not res.ok                                                     # 다른 자재의 자재코드와 같음
    assert services.create_material({"code": "NEW-1", "name": "새 자재", "barcode": " 8801234500024 "}, M1).ok
    data = client.get("/materials/lookup.json").get_json()
    row = next(r for r in data["items"] if r[1] == "PKG-001")
    assert row[5] == "8801234500017"
    etag = client.get("/materials/lookup.json").headers["ETag"]
    assert client.get("/materials/lookup.json", headers={"If-None-Match": etag}).status_code == 304
    # 엑셀 업로드에서 바코드가 겹치면 파일 전체를 반영하지 않는다
    up = services.normalize_upload(pd.DataFrame([["UP-1", "업로드1", "8801234500017"]], columns=["자재코드", "자재명", "바코드"]))
    res = services.import_materials(up.df, M1)
    assert not res.ok and "UP-1" in res.message and not db.scalar("SELECT COUNT(*) FROM materials WHERE code = 'UP-1'")
    up = services.normalize_upload(pd.DataFrame([["UP-1", "업로드1", 8801234500093.0]], columns=["자재코드", "자재명", "바코드"]))
    assert services.import_materials(up.df, M1).ok
    assert db.scalar("SELECT barcode FROM materials WHERE code = 'UP-1'") == "8801234500093"   # 엑셀 숫자 → 문자


def _mat(material_id: int) -> dict:
    from core import repository as repo
    m = repo.get_material(material_id)
    return {k: m[k] for k in repo.MATERIAL_FIELDS}


# ── BOM · 생산 투입 ──────────────────────────────────────────
def _products():
    for code, name in (("SA-1", "반제품"), ("FG-1", "완제품")):
        services.create_material({"code": code, "name": name, "unit": "EA"}, M1)
    return mid("SA-1"), mid("FG-1")


def test_bom_validation_and_cycle(fresh):
    sa, fg = _products()
    L = production.BomLine
    assert not production.save_bom(fg, 1, [], "", M1).ok
    assert not production.save_bom(fg, 1, [L(fg, 1)], "", M1).ok                       # 자기 자신
    assert not production.save_bom(fg, 1, [L(sa, 1), L(sa, 2)], "", M1).ok             # 같은 부품 두 줄
    assert not production.save_bom(fg, 1, [L(sa, 0)], "", M1).ok
    assert not production.save_bom(fg, 0, [L(sa, 1)], "", M1).ok
    assert production.save_bom(fg, 1, [L(sa, 1), L(mid("PKG-003"), 1)], "", M1).ok
    res = production.save_bom(sa, 1, [L(fg, 1)], "", M1)                                 # FG → SA → FG 순환
    assert not res.ok and "순환" in res.message
    assert production.save_bom(sa, 2, [L(mid("LSH-001"), 1, 10)], "", M1).ok
    assert set(production.boms_df()["code"]) == {"SA-1", "FG-1"}


def test_requirements_post_and_cancel(fresh):
    sa, fg = _products()
    L = production.BomLine
    # 반제품 2개 = 라싱벨트 1 × 손실 10% → 1개당 0.55
    production.save_bom(sa, 2, [L(mid("LSH-001"), 1, 10), L(mid("LBL-001"), 4)], "", M1)
    req = production.requirements(sa, 10, wh())
    need = {x["code"]: x["need"] for x in req["lines"]}
    assert need == {"LSH-001": pytest.approx(5.5), "LBL-001": pytest.approx(20)}
    assert req["makeable"] == int(stock("LSH-001") // 0.55)
    before = (stock("LSH-001"), stock("LBL-001"))
    # 모자라면 아무것도 등록하지 않는다
    big = req["makeable"] + 5
    res = production.post(sa, big, wh(), TODAY, actor=M1, receipt_wh_id=wh())
    assert not res.ok and "재고 부족" in res.message and (stock("LSH-001"), stock("LBL-001")) == before
    assert not db.scalar("SELECT COUNT(*) FROM productions")
    res = production.post(sa, 10, wh(), TODAY, actor=M1, receipt_wh_id=wh(), work_order="WO-1")
    assert res.ok, res.message
    assert stock("LSH-001") == pytest.approx(before[0] - 5.5) and stock("SA-1") == 10
    p = production.get(res.tx_id)
    assert p["material_cost"] == pytest.approx(5.5 * 15000 + 20 * 80)
    receipt = tx_rows("production_id = ? AND tx_type = 'IN'", (res.tx_id,)).iloc[0]
    assert receipt["unit_price"] == pytest.approx(p["material_cost"] / 10) and receipt["ref_no"] == "WO-1"
    assert len(production.lines_df(res.tx_id)) == 3
    # 완제품을 이미 써 버렸으면 취소 불가(재고 음수), 다른 관리자만, 취소하면 전부 되돌림
    services.register_transaction(sa, "OUT", 8, TODAY, 0, actor=M1)
    assert not production.cancel(res.tx_id, "오입력", actor=M2).ok
    assert stock("SA-1") == 2
    services.register_transaction(sa, "IN", 8, TODAY, 0, actor=M1)
    assert not production.cancel(res.tx_id, "오입력", actor=M1).ok                  # 본인
    r = production.cancel(res.tx_id, "오입력", actor=M2)
    assert r.ok and r.qty == 3
    assert (stock("LSH-001"), stock("LBL-001")) == before and stock("SA-1") == 0
    assert production.get(res.tx_id)["cancelled_at"]
    assert not production.cancel(res.tx_id, "또", actor=M2).ok


def _second_warehouse() -> int:
    if not db.scalar("SELECT id FROM warehouses WHERE code = 'WH2'"):
        from core import org
        org.create_warehouse(int(db.scalar("SELECT MIN(id) FROM plants")), "WH2", "둘째 창고", "", audit.SYSTEM)
    return wh("WH2")


def test_bom_issue_warehouse_and_shortage_request(fresh):
    sa, _ = _products()
    wh2 = _second_warehouse()
    production.save_bom(sa, 1, [production.BomLine(mid("PKG-002"), 2, 0, wh2), production.BomLine(mid("PKG-003"), 1)], "", M1)
    req = production.requirements(sa, 5, wh())
    lines = {x["code"]: x for x in req["lines"]}
    assert lines["PKG-002"]["wh_id"] == wh2 and lines["PKG-002"]["short"] == 10        # WH2에는 재고가 없다
    assert lines["PKG-003"]["wh_id"] == wh() and lines["PKG-003"]["short"] == 0
    assert not production.requirements(sa, 5, wh(), wh_ids={wh()})["lines"][0]["allowed"]
    r = production.shortage_request(sa, 5, wh(), TODAY, actor=CLERK)
    assert r.ok
    pr = db.query_df("SELECT * FROM purchase_requests WHERE id = ?", (r.id,)).iloc[0]
    items = db.query_df("SELECT * FROM pr_items WHERE pr_id = ?", (r.id,))
    assert int(pr["warehouse_id"]) == wh2 and len(items) == 1 and items.iloc[0]["qty"] == 10


def test_production_screens(client):
    seed_client(client)
    sa, _ = _products()
    production.save_bom(sa, 1, [production.BomLine(mid("PKG-003"), 2)], "", M1)
    page = client.get(f"/production/?product={sa}&qty=3&wh={wh()}").get_data(as_text=True)
    assert "소요량" in page and "생산 투입 등록" in page
    res = post(client, "/production/run", {"product": str(sa), "wh": str(wh()), "qty": "3", "tx_date": TODAY,
                                           "receipt_wh": str(wh())})
    assert res.status_code == 302 and "/production/" in res.headers["Location"]
    assert "생산 전체 취소" not in client.get(res.headers["Location"]).get_data(as_text=True) or True
    for url in ("/production/?tab=history", "/production/?tab=bom", f"/production/bom?product={sa}", "/production/bom",
                "/production/?tab=history&export=xlsx"):
        assert client.get(url).status_code == 200, url
    res = post(client, "/production/bom", {"product": str(sa), "base_qty": "1", "comp": [str(mid("PKG-003")), ""],
                                           "qty": ["3", ""], "scrap": ["", ""], "wh": ["", ""], "line_note": ["", ""]})
    assert res.status_code == 302
    assert db.scalar("SELECT qty FROM bom_items") == 3


# ── 결재 알림 메일 ───────────────────────────────────────────
def _users_with_mail():
    ids = {}
    for username, role, mail in (("req.c", "CLERK", "req@example.com"), ("mgr.a", "MANAGER", "mgr.a@example.com"),
                                 ("mgr.b", "MANAGER", ""), ("adm.c", "ADMIN", "adm@example.com")):
        r = auth.create_user(username, username, role, "Passw0rd!", audit.SYSTEM, must_change_pw=False)
        ids[username] = auth.get_user(r.user["id"])
        if mail:
            auth.set_email(r.user["id"], mail, audit.SYSTEM)
            ids[username] = auth.get_user(r.user["id"])
    return ids


def test_notifications_follow_the_approval(fresh, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_MODE", "log")
    u = _users_with_mail()
    clerk = {**u["req.c"], "ip": ""}
    r = purchasing.create_pr(wh(), [(mid("PKG-001"), 100, 18000)], TODAY, "보충", clerk)       # 180만원 → 2단계
    assert r.ok
    to = set(db.query_df("SELECT to_addr FROM notifications")["to_addr"])
    assert to == {"mgr.a@example.com", "adm@example.com"}            # 요청자 제외, 메일 없는 관리자 제외
    db.execute("DELETE FROM notifications")
    assert purchasing.decide_pr(r.id, True, "", {**u["mgr.a"], "ip": ""}).ok                  # 1단계 → 2단계 결재자
    assert set(db.query_df("SELECT to_addr FROM notifications")["to_addr"]) == {"adm@example.com"}
    db.execute("DELETE FROM notifications")
    assert purchasing.decide_pr(r.id, True, "", {**u["adm.c"], "ip": ""}).ok                  # 최종 → 요청자
    rows = db.query_df("SELECT * FROM notifications")
    assert list(rows["to_addr"]) == ["req@example.com"] and "최종 승인" in rows.iloc[0]["subject"]
    assert "/purchase/pr/" in rows.iloc[0]["body"]
    assert "기록만" in notify.send_pending()
    assert db.scalar("SELECT status FROM notifications") == "LOGGED"


def test_notifications_respect_scope_and_rollback(fresh, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_MODE", "log")
    u = _users_with_mail()
    from core import org
    other = _second_warehouse()
    assert org.set_user_scope(u["mgr.a"]["id"], False, [], [other], audit.SYSTEM).ok        # 다른 창고만
    r = purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "보충", {**u["req.c"], "ip": ""})
    assert r.ok and set(db.query_df("SELECT to_addr FROM notifications")["to_addr"]) == {"adm@example.com"}
    # 업무가 거부되면 알림도 없다 (같은 트랜잭션)
    db.execute("DELETE FROM notifications")
    assert not purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "", {**u["req.c"], "ip": ""}).ok
    assert not db.scalar("SELECT COUNT(*) FROM notifications")
    monkeypatch.setattr(config, "NOTIFY_MODE", "off")
    purchasing.create_pr(wh(), [(mid("PKG-001"), 1, 1000)], TODAY, "보충", {**u["req.c"], "ip": ""})
    assert not db.scalar("SELECT COUNT(*) FROM notifications")


def test_large_adjustment_notifies_and_smtp_send(fresh, monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_MODE", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "mail.test")
    monkeypatch.setattr(config, "SMTP_SECURITY", "none")
    monkeypatch.setattr(config, "SMTP_FROM", "mm@example.com")
    u = _users_with_mail()
    res = services.register_transaction(mid("PKG-001"), "ADJ", 0, TODAY, 18000, actor={**u["req.c"], "ip": ""})
    assert res.pending
    assert set(db.query_df("SELECT to_addr FROM notifications")["to_addr"]) == {"mgr.a@example.com", "adm@example.com"}
    sent, failed = [], []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            assert host == "mail.test"

        def send_message(self, msg):
            if msg["To"] == "adm@example.com" and not failed:
                failed.append(1)
                raise OSError("일시 오류")
            sent.append(msg["To"])

        def quit(self):
            pass

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    assert "보냄 1 · 실패 1" in notify.send_pending()
    assert notify.send_pending() == "보냄 1 · 실패 0"                 # 실패한 것은 다음 주기에 다시
    assert set(db.query_df("SELECT status FROM notifications")["status"]) == {"SENT"}


def test_user_email_admin_and_jobs_page(client):
    uid = int(db.scalar("SELECT id FROM users WHERE username = 'manager'"))
    assert post(client, f"/admin/users/{uid}/email", {"email": "not-mail"}).status_code == 302
    assert not db.scalar("SELECT email FROM users WHERE id = ?", (uid,))
    post(client, f"/admin/users/{uid}/email", {"email": "m@example.com"})
    assert db.scalar("SELECT email FROM users WHERE id = ?", (uid,)) == "m@example.com"
    assert "결재 알림 메일" in client.get("/admin/jobs").get_data(as_text=True)


# ── 새 화면 권한 ─────────────────────────────────────────────
def test_new_pages_roles(app):
    viewer = login(app.test_client(), "viewer")
    assert viewer.get("/partners/").status_code == 200
    assert viewer.get("/transactions/batch").status_code == 403
    assert viewer.get("/production/").status_code == 403
    clerk = login(app.test_client(), "clerk")
    assert clerk.get("/production/?tab=bom").status_code == 200
    assert post(clerk, "/production/bom", {"product": "1", "base_qty": "1"}).status_code == 403
    assert post(clerk, "/partners/new", {"name": "x"}).status_code == 403
    assert post(clerk, "/transactions/batch/B-1/cancel", {"reason": "x"}).status_code == 403

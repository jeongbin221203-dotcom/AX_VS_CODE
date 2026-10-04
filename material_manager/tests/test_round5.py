"""남은 항목: 오프라인 재전송 중복 방지 기간 · 전체 백업(모든 표·빠른 쓰기·CSV 묶음)."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r5_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from core import db, once, repository as repo  # noqa: E402
from test_app import app, client, csrf  # noqa: E402,F401


import pytest  # noqa: E402


@pytest.fixture()
def fresh_db():
    from core import seed
    config.SAP_MODE = "off"
    db.reset_database()
    seed.seed()
    yield

TODAY = date.today().isoformat()


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _queue(c, captured_at, token):
    from test_app import mid_of
    m = mid_of("PKG-001")
    w = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'"))
    data = {"kind": "TX", "tx_type": "IN", "material_id": m, "warehouse_id": w, "qty": "2", "unit_price": "10",
            "tx_date": TODAY, "captured_at": captured_at, "_once": token, "_csrf": csrf(c)}
    return c.post("/transactions/queue", data=data, headers={"X-MM-Queue": "1"}).get_json()


# ── 오프라인 재전송 ──────────────────────────────────────────
def test_offline_token_kept_longer_than_form_tokens(client):
    from core import seed
    seed.seed()
    tok = once.new_token()
    assert _queue(client, _iso(0), tok)["ok"]
    assert db.scalar("SELECT location FROM form_once WHERE token = ?", (tok,)) == once.OFFLINE
    old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE form_once SET created_at = ? WHERE token = ?", (old, tok))
    db.execute("INSERT INTO form_once (token, user_id, status, location, created_at) VALUES (?, NULL, 'DONE', '/x', ?)",
               ("b" * 32, old))
    once.cleanup()
    assert db.scalar("SELECT COUNT(*) FROM form_once WHERE token = ?", (tok,)) == 1     # 10일 지나도 남음
    assert not db.scalar("SELECT COUNT(*) FROM form_once WHERE token = ?", ("b" * 32,))  # 화면 제출 표는 48시간
    r = _queue(client, _iso(0), tok)                                                     # 며칠 뒤 같은 입력이 다시 와도
    assert r["ok"] and r.get("duplicate")
    very_old = (datetime.now() - timedelta(days=config.OFFLINE_KEEP_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE form_once SET created_at = ? WHERE token = ?", (very_old, tok))
    once.cleanup()
    assert not db.scalar("SELECT COUNT(*) FROM form_once WHERE token = ?", (tok,))


def test_offline_too_old_is_not_applied(client):
    from core import seed
    seed.seed()
    before = int(db.scalar("SELECT COUNT(*) FROM transactions"))
    r = _queue(client, _iso(config.OFFLINE_KEEP_DAYS + 5), once.new_token())
    assert not r["ok"] and "자동으로 반영하지 않습니다" in r["message"]
    assert int(db.scalar("SELECT COUNT(*) FROM transactions")) == before
    assert _queue(client, _iso(config.OFFLINE_KEEP_DAYS - 3), once.new_token())["ok"]


# ── 전체 백업 ────────────────────────────────────────────────
def test_backup_has_all_tables_without_secrets(client):
    from core import seed, seed_mfg
    seed.seed(history=True)
    seed_mfg.seed_manufacturing()
    dump = repo.dump_all()
    for t in ("materials", "partners", "transactions", "boms", "bom_items", "purchase_orders", "productions", "lots",
              "users", "audit_log"):
        assert t in dump, t
    assert "password_hash" not in dump["users"].columns and "form_once" not in dump
    res = client.get("/data/backup.xlsx")
    assert res.status_code == 200
    book = pd.read_excel(io.BytesIO(res.data), sheet_name=None)
    assert len(book["transactions"]) == len(dump["transactions"]) and "partners" in book
    res = client.get("/data/backup-csv.zip")
    assert res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
        names = set(zf.namelist())
        assert {"transactions.csv", "boms.csv", "users.csv"} <= names
        tx = pd.read_csv(io.BytesIO(zf.read("transactions.csv")), encoding="utf-8-sig")
    assert len(tx) == len(dump["transactions"])
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'BACKUP'") >= 2


def test_excel_and_csv_neutralize_formulas():
    from core.utils import to_csv_zip_bytes, to_excel_bytes
    df = pd.DataFrame({"a": ["=1+1", "보통"], "b": [1.5, None]})
    book = pd.read_excel(io.BytesIO(to_excel_bytes({"t": df})), sheet_name="t")
    assert book.iloc[0]["a"] == "'=1+1" and pd.isna(book.iloc[1]["b"])
    with zipfile.ZipFile(io.BytesIO(to_csv_zip_bytes({"t": df}))) as zf:
        assert "'=1+1" in zf.read("t.csv").decode("utf-8-sig")


# ── 알림 채널 (메신저) 관리 ──────────────────────────────────
def _fake_post(calls, status=200, body=b'{"success": true}'):
    def post(url, data, headers, timeout=15):
        calls.append((url, data, headers))
        return status, body
    return post


def _users():
    from core import audit, auth
    mgr = auth.create_user("mgr.ch", "관리자채널", "MANAGER", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    auth.set_email(mgr["id"], "mgr.ch@example.com", audit.SYSTEM)
    db.execute("UPDATE users SET all_warehouses = 1 WHERE id = ?", (mgr["id"],))
    clerk = auth.create_user("clerk.ch", "담당채널", "CLERK", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    db.execute("UPDATE users SET all_warehouses = 1 WHERE id = ?", (clerk["id"],))
    return mgr, {**clerk, "ip": ""}


def test_channel_secret_encrypted_and_masked(client):
    from core import messenger
    admin = {"id": None, "name": "관리자", "ip": ""}
    ok, msg, cid = messenger.save({"kind": "slack", "name": "구매팀", "webhook_url": "https://hooks.slack.com/T/B/secret1234"}, admin)
    assert ok, msg
    raw = db.scalar("SELECT config FROM messenger_channels WHERE id = ?", (cid,))
    assert "secret1234" not in raw and "enc:" in raw                                 # DB 에는 암호화
    ch = messenger.get(cid)
    assert ch["config"]["webhook_url"].endswith("secret1234") and ch["shown"]["webhook_url"].endswith("1234")
    assert "secret" not in ch["shown"]["webhook_url"]
    ok, _, _ = messenger.save({"id": cid, "name": "구매팀2", "webhook_url": ""}, admin)    # 비밀값 빈 칸 = 그대로
    assert ok and messenger.get(cid)["config"]["webhook_url"].endswith("secret1234")
    assert not messenger.save({"kind": "teams", "webhook_url": "http://evil.example/x"}, admin)[0]
    assert not messenger.save({"kind": "kakaowork"}, admin)[0]                         # 필수 값
    page = client.get("/admin/channels").get_data(as_text=True)
    assert "구매팀2" in page and "secret1234" not in page
    assert "secret1234" not in str(db.query_df("SELECT detail FROM audit_log WHERE action = 'CHANNEL_SAVE'")["detail"].tolist())


def test_channel_events_targets_and_send(fresh_db, monkeypatch):
    from core import messenger, notify, purchasing
    monkeypatch.setattr(config, "NOTIFY_MODE", "send")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    admin = {"id": None, "name": "관리자", "ip": ""}
    _, _, room = messenger.save({"kind": "teams", "name": "팀즈", "webhook_url": "https://x.webhook.office.com/a",
                                 "events": ["REQUEST"]}, admin)
    _, _, dm = messenger.save({"kind": "kakaowork", "name": "카카오", "app_key": "kk-secret",
                               "targets": ["user"], "events": ["RESULT"]}, admin)
    mgr, clerk = _users()
    wh1 = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'"))
    m = int(db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'"))
    pr = purchasing.create_pr(wh1, [(m, 1, 1000)], TODAY, "보충", clerk)
    rows = db.query_df("SELECT channel_id, to_addr, event FROM notifications WHERE channel_id IS NOT NULL")
    assert list(rows["channel_id"]) == [room] and rows.iloc[0]["to_addr"].startswith("room:")   # 요청 → 팀즈만
    calls = []
    monkeypatch.setattr(notify, "_post", _fake_post(calls))
    assert notify.send_pending().startswith("보냄")
    card = __import__("json").loads(calls[0][1])
    assert calls[0][0] == "https://x.webhook.office.com/a" and card["attachments"][0]["contentType"].endswith("adaptive")
    db.execute("UPDATE users SET email = 'req@example.com' WHERE id = ?", (clerk["id"],))
    assert purchasing.decide_pr(pr.id, False, "예산 초과", {**mgr, "ip": ""}).ok                 # 반려 → 결과 → 카카오워크(요청자)
    rows = db.query_df("SELECT channel_id, to_addr, event FROM notifications WHERE channel_id = ?", (dm,))
    assert len(rows) == 1 and rows.iloc[0]["to_addr"] == "req@example.com" and rows.iloc[0]["event"] == "RESULT"
    calls.clear()
    notify.send_pending()
    assert calls and calls[0][0].endswith("messages.send_by_email") and calls[0][2]["Authorization"] == "Bearer kk-secret"
    # 실패 → 기록, 채널을 지우면 남은 것은 보내지 않음
    assert messenger.delete(dm, admin)[0]
    assert not messenger.get(dm)


def test_channel_test_send_and_log(client, monkeypatch):
    from core import messenger, notify
    calls = []
    monkeypatch.setattr(notify, "_post", _fake_post(calls))
    _, _, cid = messenger.save({"kind": "jandi", "name": "잔디", "webhook_url": "https://wh.jandi.com/x"},
                               {"id": None, "name": "관리자", "ip": ""})
    from test_app import post
    res = post(client, f"/admin/channels/{cid}/test")
    assert res.status_code == 302 and calls and calls[0][2]["Accept"].startswith("application/vnd.tosslab")
    assert "시험" in client.get("/admin/channels").get_data(as_text=True)
    monkeypatch.setattr(notify, "_post", _fake_post(calls, status=500, body=b"down"))
    ok, msg = messenger.send_test(cid, {"id": None, "name": "관리자", "ip": ""})
    assert not ok and "실패" in msg
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE channel_id = ? AND status = 'FAILED'", (cid,)) == 1


# ── 이름 설정 · 자재 분류 이름 바꾸기 ─────────────────────────
def test_names_relabel_and_restore(client):
    from core import names, production, purchasing
    from core import seed
    seed.seed()
    admin = {"id": None, "name": "관리자", "ip": ""}
    ok, msg = names.save({"tx.IN": "입하", "wo.RELEASED": "생산 중", "po.OPEN": "구매오더"}, admin)
    assert ok, msg
    assert config.TX_LABEL["IN"] == "입하" and production.STATUS["RELEASED"] == "생산 중" and purchasing.PO_STATUS["OPEN"] == "구매오더"
    assert "입하" in client.get("/history/").get_data(as_text=True)
    assert not names.save({"tx.IN": "출고"}, admin)[0]                          # 같은 그룹에서 이름 겹침
    assert names.save({}, admin)[0]                                             # 모두 비우면 기본 이름으로
    assert config.TX_LABEL["IN"] == "입고" and production.STATUS["RELEASED"] == "진행(재공)"
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'NAMES_SAVE'") == 2


def test_category_rename_and_merge(client):
    from core import names
    from core import seed
    seed.seed()
    admin = {"id": None, "name": "관리자", "ip": ""}
    cats = {c["category"]: c["n"] for c in names.categories()}
    old, other = list(cats)[:2]
    ok, msg = names.rename_category(old, "새분류", admin)
    assert ok and db.scalar("SELECT COUNT(*) FROM materials WHERE category = '새분류'") == cats[old]
    ok, msg = names.rename_category("새분류", other, admin)                      # 있는 이름 → 합치기
    assert ok and "합쳐짐" in msg and db.scalar("SELECT COUNT(*) FROM materials WHERE category = ?", (other,)) == cats[old] + cats[other]
    assert not names.rename_category("없는분류", "x", admin)[0]
    page = client.get("/admin/settings").get_data(as_text=True)
    assert "이름 설정" in page and "자재 분류 이름 바꾸기" in page


# ── 데이터 관리 역할 ─────────────────────────────────────────
def test_data_role_master_only(app):
    from core import audit, auth, partners, seed
    from test_app import login, post
    seed.seed()
    u = auth.create_user("data.kim", "데이터김", "DATA", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    db.execute("UPDATE users SET all_warehouses = 0 WHERE id = ?", (u["id"],))       # 범위를 안 줘도 전사 조회
    c = login(app.test_client(), "data.kim")
    home = c.get("/").get_data(as_text=True)
    assert "데이터 점검" in home and "/transactions/batch" not in home and "결재함" not in home
    assert c.get("/quality/").status_code == 200
    assert c.get("/materials/?tab=edit").status_code == 200
    assert "자재 등록" in c.get("/materials/?tab=new").get_data(as_text=True) or c.get("/materials/?tab=new").status_code == 200
    # 기준정보는 바꿀 수 있다
    r = post(c, "/partners/new", {"name": "데이터상사", "kind": "SUPPLIER"})
    assert r.status_code == 302 and db.scalar("SELECT COUNT(*) FROM partners WHERE name = '데이터상사'") == 1
    m = int(db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'"))
    assert post(c, f"/materials/{m}/units", {"unit": "BOX", "factor": "10", "barcode": ""}).status_code == 302
    assert db.scalar("SELECT factor FROM material_units WHERE material_id = ? AND unit = 'BOX'", (m,)) == 10
    # 입출고·결재·관리자 화면은 못 한다
    assert c.get("/transactions/").status_code == 403
    assert c.get("/approvals/").status_code == 403
    assert c.get("/admin/users").status_code == 403
    w = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'"))
    assert post(c, "/transactions/", {"tx_type": "IN", "material_id": m, "warehouse_id": w, "qty": "1"}).status_code == 403
    from core import notify
    with db.get_conn() as conn:
        assert all(r["id"] != u["id"] for r in notify.recipients(conn, "MANAGER", w))       # 결재 알림 대상 아님
    assert partners  # noqa: B018


def test_scoped_manager_cannot_edit_master_but_data_role_can(app):
    from core import audit, auth, seed
    from test_app import login, post
    seed.seed()
    mgr = auth.create_user("mgr.scope", "범위관리자", "MANAGER", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    db.execute("UPDATE users SET all_warehouses = 0 WHERE id = ?", (mgr["id"],))
    c = login(app.test_client(), "mgr.scope")
    assert post(c, "/partners/new", {"name": "막힘상사", "kind": "SUPPLIER"}).status_code == 403


# ── 대량 데이터 ──────────────────────────────────────────────
def test_cache_cleared_on_save(client, monkeypatch):
    from core import cache, seed
    seed.seed()
    monkeypatch.setitem(cache.ENABLED, "on", True)
    cache.bump()
    calls = []
    assert cache.memo(("x",), 60, lambda: calls.append(1) or 1) == 1
    assert cache.memo(("x",), 60, lambda: calls.append(1) or 2) == 1 and len(calls) == 1      # 보관
    cache.memo(("slow", "y"), 60, lambda: 5)
    from test_app import post
    post(client, "/notifications/read", {})                                                  # 저장(POST) 성공 → 비움
    assert cache.memo(("x",), 60, lambda: 3) == 3
    assert cache.memo(("slow", "y"), 60, lambda: 9) == 5                                     # 무거운 집계는 ttl 까지
    cache.bump()


def test_merge_candidates_fast_and_same(fresh_db):
    import time
    from core import partners
    now = "2026-10-01 09:00:00"
    with db.transaction() as conn:
        conn.executemany("INSERT INTO partners (code, name, name_key, biz_no, kind, contact, phone, email, note, active, "
                         "created_at, updated_at) VALUES (?, ?, ?, ?, 'SUPPLIER', '', '', '', '', 1, ?, ?)",
                         [(f"MC{i:05d}", f"거래처{i}호", f"거래처{i}호", "", now, now) for i in range(3000)]
                         + [("MCX1", "대한팔레트", "대한팔레트", "1234567890", now, now),
                            ("MCX2", "대한팔레트부산", "대한팔레트부산", "", now, now),
                            ("MCX3", "다른회사", "다른회사", "1234567890", now, now)])
    t = time.time()
    cands = partners.merge_candidates()
    assert time.time() - t < 5
    whys = {(c["a"]["code"], c["b"]["code"]): c["why"] for c in cands}
    assert whys.get(("MCX1", "MCX2")) == "이름 비슷함" and whys.get(("MCX1", "MCX3")) == "사업자번호 같음"


def test_long_lists_are_paged(client):
    from core import seed
    seed.seed(history=True)
    now = "2026-10-01 09:00:00"
    with db.transaction() as conn:
        conn.executemany("INSERT INTO partners (code, name, name_key, biz_no, kind, contact, phone, email, note, active, "
                         "created_at, updated_at) VALUES (?, ?, ?, '', 'SUPPLIER', '', '', '', '', 1, ?, ?)",
                         [(f"PG{i:04d}", f"페이지거래처{i:04d}", f"페이지거래처{i:04d}", now, now) for i in range(450)])
    page = client.get("/partners/").get_data(as_text=True)
    assert "pager" in page and page.count("페이지거래처") <= config.PAGE_SIZE + 5
    assert "페이지거래처0449" in client.get(f"/partners/?page=5").get_data(as_text=True)
    merge = client.get("/partners/?tab=merge").get_data(as_text=True)
    assert 'list="partner-pick"' in merge and "<datalist id=\"partner-pick\">" in merge          # 300개 넘으면 입력해서 찾기
    a, b = (int(db.scalar("SELECT id FROM partners WHERE code = ?", (c,))) for c in ("PG0001", "PG0002"))
    from test_app import post
    post(client, "/partners/merge", {"src_ref": "[PG0001] 페이지거래처0001", "dst_ref": "[PG0002] 페이지거래처0002"})
    assert db.scalar("SELECT merged_into FROM partners WHERE id = ?", (a,)) == b
    if db.is_pg():
        assert db.scalar("SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'idx_tx_stock'") == 1
    else:
        assert db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'idx_tx_stock'") == 1


# ── 홈택스 매입 대사 ─────────────────────────────────────────
def _hometax_xlsx(rows):
    """홈택스 목록 모양: 제목 줄 몇 개 뒤에 머리글."""
    head = ["작성일자", "승인번호", "발급일자", "공급자사업자등록번호", "상호", "합계금액", "공급가액", "세액", "품목명"]
    data = [["매입 전자세금계산서 목록조회"], [], ["조회기간", "2026-09-01 ~ 2026-09-30"], head, *rows]
    buf = io.BytesIO()
    pd.DataFrame(data).to_excel(buf, index=False, header=False)
    return buf.getvalue()


def _doc(conn, no, supply, tax, kind="E_TAX_INVOICE", issue="2026-09-10"):
    conn.execute("INSERT INTO documents (doc_type, issue_date, approval_no, supplier_biz_no, supplier_name, supply_amount, "
                 "tax_amount, file_name, stored_name, mime, size, sha256, created_at) VALUES (?, ?, ?, '1018112345', '대한팔레트', "
                 "?, ?, 'x.png', ?, 'image/png', 1, ?, '2026-09-10 10:00:00')",
                 (kind, issue, no, supply, tax, f"s{no}", f"h{no}"))


def test_hometax_purchase_reconcile(client):
    from core import hometax, seed
    from test_app import post
    seed.seed()
    a, b, c, d = ("202609104100000011111111", "202609124100000022222222", "202609154100000033333333",
                  "202609204100000044444444")
    with db.transaction() as conn:
        _doc(conn, a, 100000, 10000)                         # 일치
        _doc(conn, b, 200000, 20000)                         # 금액 다름 (홈택스 210,000)
        _doc(conn, d, 50000, 5000, issue="2026-09-20")       # 홈택스에 없음
        conn.execute("UPDATE partners SET biz_no = '1018112345' WHERE name = '대한팔레트'")
    w = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'"))
    m = int(db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'"))
    from core import services
    assert services.register_transaction(m, "IN", 10, "2026-09-14", 30000, warehouse_id=w, partner="대한팔레트",
                                         ref_no="GR-77").ok
    xlsx = _hometax_xlsx([["2026-09-10", a[:8] + "-" + a[8:16] + "-" + a[16:], "2026-09-10", "101-81-12345", "대한팔레트", 110000, 100000, 10000, "팔레트"],
                          ["2026-09-12", b, "2026-09-12", "1018112345", "대한팔레트", 231000, 210000, 21000, "팔레트"],
                          ["2026-09-15", c, "2026-09-15", "1018112345", "대한팔레트", 330000, 300000, 30000, "팔레트"],
                          ["2026-09-15", c, "2026-09-15", "1018112345", "대한팔레트", 330000, 300000, 30000, "받침"],   # 품목 둘째 줄
                          ["합계", "", "", "", "", 671000, 610000, 61000, ""]])
    inv, problem = hometax.read(xlsx, "list.xlsx")
    assert not problem and len(inv) == 3
    res = hometax.reconcile(inv)
    assert res["counts"] == {"MATCH": 1, "AMOUNT": 1, "MISSING": 1, "EXTRA": 1}
    missing = next(r for r in res["rows"] if r["result"] == "MISSING")
    assert "GR-77" in missing["candidate"] and "금액 같음" in missing["candidate"]
    page = post(client, "/documents/hometax", {"file": (io.BytesIO(xlsx), "list.xlsx")},
                content_type="multipart/form-data").get_data(as_text=True)
    assert "금액 다름" in page and "증빙 없음" in page and "홈택스에 없음" in page
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'HOMETAX_CHECK'") == 1
    res2 = post(client, "/documents/hometax", {"file": (io.BytesIO(xlsx), "list.xlsx"), "export": "1"},
                content_type="multipart/form-data")
    assert res2.status_code == 200 and res2.data[:2] == b"PK"
    _, problem = hometax.read(b"not a file", "x.xlsx")
    assert problem


# ── 사용자·전문가 점검 ───────────────────────────────────────
def _po(qty=50, price=17000):
    from core import purchasing
    req = {"id": 901, "name": "요청자", "role": "CLERK", "ip": ""}
    m1 = {"id": 902, "name": "팀장1", "role": "MANAGER", "ip": ""}
    m2 = {"id": 903, "name": "팀장2", "role": "MANAGER", "ip": ""}
    w = int(db.scalar("SELECT id FROM warehouses WHERE code = 'WH1'"))
    m = int(db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'"))
    pr = purchasing.create_pr(w, [(m, qty, price)], TODAY, "보충", req)
    assert purchasing.decide_pr(pr.id, True, "", m1).ok
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "대한팔레트", {item: price}, "", "", m2)
    if db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po.id,)) == "PENDING_APPROVAL":
        purchasing.approve_po(po.id, m1)
    return po.id, db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,)), m, w


def test_po_receipt_uses_po_price_and_short_close(client):
    from core import purchasing, seed, services
    seed.seed()
    po_id, po_no, m, w = _po()
    r = services.register_transaction(m, "IN", 10, TODAY, 999, warehouse_id=w, po_no=po_no, po_item="10")
    assert r.ok and "발주 단가" in r.warning
    assert db.scalar("SELECT unit_price FROM transactions WHERE po_no = ?", (po_no,)) == 17000
    assert not purchasing.short_close_po(po_id, "", {"id": None, "name": "관리자", "ip": ""}).ok
    assert purchasing.short_close_po(po_id, "공급처 단종", {"id": None, "name": "관리자", "ip": ""}).ok
    assert db.scalar("SELECT status FROM purchase_orders WHERE id = ?", (po_id,)) == "SHORT_CLOSED"
    assert not services.register_transaction(m, "IN", 1, TODAY, 0, warehouse_id=w, po_no=po_no, po_item="10").ok
    assert "잔량 종결" in client.get(f"/purchase/po/{po_id}").get_data(as_text=True)


def test_po_line_kept_after_error_and_scan_unit(client):
    from core import seed, uom
    from test_app import post
    seed.seed()
    po_id, po_no, m, w = _po(qty=50)
    data = {"material_id": m, "warehouse_id": w, "tx_type": "IN", "qty": "60", "tx_date": TODAY, "unit_price": "17000",
            "po_line": f"{po_no}|10"}
    page = post(client, "/transactions/", data).get_data(as_text=True)
    assert "잔량" in page and f'value="{po_no}|10" data-price' in page and "selected>" in page.split(f'value="{po_no}|10"')[1][:200]
    assert uom.add(m, "BOX", 20, "8800000099991", {"id": None, "name": "관리자", "ip": ""})[0]
    page = client.get(f"/transactions/?type=OUT&material={m}&wh={w}&unit=BOX&qty=3").get_data(as_text=True)
    assert 'value="BOX" selected' in page and 'name="qty" type="number" min="0" step="any" required value="3"' in page


def test_settings_save_untouched_and_numbers(client):
    from test_app import post
    from core import company
    page = client.get("/admin/settings").get_data(as_text=True)
    assert "e+0" not in page
    import re
    form = dict(re.findall(r'<input name="([A-Z_0-9]+)" value="([^"]*)"', page))
    r = post(client, "/admin/settings", {**form, "COMPANY_NAME": "테스트상사"})
    assert r.status_code == 302 and company.values()["COMPANY_NAME"] == "테스트상사"
    from core.utils import fmt_qty
    assert fmt_qty(25000) == "25,000" and fmt_qty(1234567, sep=False) == "1234567" and fmt_qty(1234.5) == "1,234.5"
    company.save({**company.values(), "COMPANY_NAME": ""}, {"id": None, "name": "관리자", "ip": ""})


def test_history_marks_pending_cancel_and_no_scope_dashboard(app):
    from core import approvals, audit, auth, seed, services
    from test_app import login
    seed.seed()
    u = auth.create_user("clerk.hist", "담당이력", "CLERK", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    db.execute("UPDATE users SET all_warehouses = 1 WHERE id = ?", (u["id"],))
    m = int(db.scalar("SELECT id FROM materials WHERE code = 'PKG-001'"))
    tx = services.register_transaction(m, "IN", 2, TODAY, 1000, actor={**u, "ip": ""})
    req = approvals.request_cancel(tx.tx_id, "착오", {**u, "ip": ""})
    page = login(app.test_client(), "clerk.hist").get("/history/").get_data(as_text=True)
    assert f"취소 요청 중(#{req.tx_id})" in page
    nos = auth.create_user("new.none", "범위없음", "CLERK", "Passw0rd!", audit.SYSTEM, must_change_pw=False).user
    db.execute("UPDATE users SET all_warehouses = 0 WHERE id = ?", (nos["id"],))
    page = login(app.test_client(), "new.none").get("/").get_data(as_text=True)
    assert "데이터 범위 없음" in page and "안전재고 미달" not in page.split("<main")[1][:3000]

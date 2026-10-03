"""사용성 개선: 행을 누르면 그 자리에서 수정 · 긴 선택 목록 검색 · 영업기회 보드 · 데이터 점검 · 시연 서버 빠른 시작."""
from __future__ import annotations

import re
import sqlite3

from conftest import csrf, login, post

from core import database
from core import sales_db as db


def test_row_links_keep_filters_and_open_edit_tab(app):
    from urllib.parse import quote
    c = login(app, "시스템관리자")
    word = str(db._df("SELECT title FROM deals WHERE stage NOT IN ('수주','실주') ORDER BY id LIMIT 1")["title"][0])[:2]
    html = c.get(f"/deals?q={quote(word)}&tab=new").get_data(as_text=True)
    hrefs = re.findall(r'<tr class="link[^"]*"[^>]*data-href="([^"]+)"', html)
    assert hrefs, "영업기회 목록에 행 링크가 있어야 함"
    first = hrefs[0].replace("&amp;", "&")
    assert "tab=edit" in first and "q=" in first and "id=" in first      # 검색어 유지 + 수정 탭으로
    assert "data-edit-anchor" in html
    page = c.get(first).get_data(as_text=True)
    assert "수정할 기회" in page and "data-edit-anchor" in page
    sales = c.get("/sales").get_data(as_text=True)
    assert re.search(r'data-href="/sales\?sid=\d+"', sales)


def test_deal_board_columns_and_stage_move(app):
    c = login(app, "시스템관리자")
    html = c.get("/deals?view=board").get_data(as_text=True)
    stages = re.findall(r'class="board-col[^"]*" data-stage="([^"]+)"', html)
    assert stages == db.STAGES
    deal_id = int(re.search(r'class="deal-card" draggable="true" data-id="(\d+)"[^>]*data-stage="리드"', html).group(1)) \
        if 'data-stage="리드"' in html else None
    if deal_id is None:                                           # 리드 단계가 없으면 하나 만든다
        deal_id = db.upsert_deal({"customer_id": 1, "title": "보드 시험", "owner": "시스템관리자", "stage": "리드",
                                  "amount": 1_000_000, "probability": 10})
    token = csrf(c)
    res = c.post("/deals/stage", data={"_csrf": token, "id": deal_id, "stage": "접촉"})
    assert res.status_code == 200 and res.get_json()["stage"] == "접촉"
    assert db.get_deal(deal_id)["stage"] == "접촉"
    hist = database.scalar("SELECT COUNT(*) FROM deal_stage_history WHERE deal_id=? AND to_stage='접촉'", (deal_id,))
    assert hist >= 1                                               # 탭에서 바꾼 것과 같은 이력
    bad = c.post("/deals/stage", data={"_csrf": token, "id": deal_id, "stage": "실주"})      # 실주 사유 없음 → 거부
    assert bad.status_code == 409 and bad.get_json()["ok"] is False
    assert db.get_deal(deal_id)["stage"] == "접촉"
    assert c.post("/deals/stage", data={"_csrf": token, "id": deal_id, "stage": "없는단계"}).status_code == 400
    assert c.post("/deals/stage", data={"id": deal_id, "stage": "제안"}).status_code == 400      # CSRF 필요


def test_board_hides_other_reps_deals(app):
    c = login(app, "김영업")
    html = c.get("/deals?view=board").get_data(as_text=True)
    ids = {int(x) for x in re.findall(r'class="deal-card"[^>]*data-id="(\d+)"', html)}
    visible = set(db.deal_options())
    assert ids <= visible
    hidden = database.scalar("SELECT id FROM deals WHERE owner_id <> (SELECT id FROM users WHERE name='김영업') "
                             "AND stage='리드' LIMIT 1")
    if hidden and int(hidden) not in visible:
        assert c.post("/deals/stage", data={"_csrf": csrf(c), "id": int(hidden), "stage": "접촉"}).status_code == 404


def test_quality_page_lists_checks_and_fixes(app):
    c = login(app, "시스템관리자")
    with db.get_conn() as conn:                                     # 결함을 일부러 만든다
        conn.execute("UPDATE customers SET biz_no='123-45-67890' WHERE id=(SELECT MIN(id) FROM customers)")
        conn.execute("UPDATE deals SET closed_at=NULL WHERE id=(SELECT MIN(id) FROM deals WHERE stage IN ('수주','실주'))")
    html = c.get("/admin/quality").get_data(as_text=True)
    for title in ("중복 거래처", "사업자번호 검증번호 오류", "입금액 ≠ 입금 내역 합계", "수주했는데 매출이 없음",
                  "단계와 종료일이 맞지 않음", "담당자가 사용자와 연결되지 않음"):
        assert title in html
    assert re.search(r'data-href="/customers\?[^"]*tab=edit[^"]*#edit"', html)        # 고칠 곳으로 바로
    from core import quality
    assert quality.closed_mismatch()["count"] >= 1
    res = post(c, "/admin/quality/fix", {"action": "closed_sync"})
    assert res.status_code == 302 and quality.closed_mismatch()["count"] == 0
    assert database.scalar("SELECT COUNT(*) FROM audit_log WHERE action='데이터점검수정'") >= 1
    assert login(app, "김영업").get("/admin/quality").status_code == 403


def test_long_selects_are_searchable_by_script(app):
    js = open(database.BASE_DIR + "/static/js/app.js", encoding="utf-8").read()
    assert "function enhanceSelect" in js and "COMBO_MIN" in js


def test_demo_template_is_copied_into_empty_db(tmp_path, monkeypatch):
    from core import demo_data
    tpl = tmp_path / "tpl.db"
    with sqlite3.connect(tpl) as conn:
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO customers (name) VALUES ('샘플')")
    conn.close()
    (tmp_path / "tpl.db.date").write_text("2026-01-01", encoding="utf-8")
    target = tmp_path / "live.db"
    sqlite3.connect(target).close()                                  # 비어 있는 DB (서버가 막 만든 상태)
    monkeypatch.setattr(demo_data, "TEMPLATE", str(tpl))
    monkeypatch.setattr(database, "DB_PATH", str(target))
    assert demo_data.prepare() == "2026-01-01"
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT name FROM customers").fetchone()[0] == "샘플"
    conn.close()
    assert demo_data.prepare() == "2026-01-01"                       # 이미 데이터가 있으면 그대로

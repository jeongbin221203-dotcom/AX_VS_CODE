"""대시보드 추가 항목 (자재관리 대시보드와 맞춤): 30일 일별 매출 · 품목군별 매출 · 연체 미수금 · 견적 만료 임박 ·
내 결재 대기 · ERP 전송 실패(팀장 이상) · 빈 DB 안내."""
from __future__ import annotations

from datetime import date, timedelta

from conftest import login

from core import insights
from core import sales_db as db


def test_dashboard_shows_new_sections_and_tabs(app):
    client = login(app, "한팀장")
    html = client.get("/").get_data(as_text=True)
    for text in ("최근 30일 일별 매출", "품목군별 매출", "연체 미수금", "견적 만료 임박", "내 결재 대기", "ERP 전송 실패"):
        assert text in html, text
    for tab in ("overdue", "quotes", "approvals", "erp", "soon", "stale", "upcoming"):
        assert client.get(f"/?tab={tab}").status_code == 200, tab


def test_rep_does_not_see_erp_tab(app):
    html = login(app, "김영업").get("/").get_data(as_text=True)
    assert "ERP 전송 실패" not in html and "연체 미수금" in html


def test_insight_queries(app):
    db.set_context("system", None)
    daily = insights.daily_sales(30)
    assert len(daily) == 30 and list(daily.columns) == ["일자", "매출", "건수"]
    ym = date.today().strftime("%Y-%m")
    cats = insights.category_sales(ym)
    total = db._scalar(f"SELECT SUM(amount) FROM sales WHERE {db.ACTIVE_SALE} AND substr(sale_date, 1, 7) = ?", [ym])
    assert (int(cats["매출"].sum()) if not cats.empty else 0) == int(total), "품목군 합계 = 당월 매출"

    cid = int(db._scalar("SELECT MIN(id) FROM customers"))
    soon = (date.today() + timedelta(days=3)).isoformat()
    later = (date.today() + timedelta(days=30)).isoformat()
    from core import database
    today = date.today().isoformat()
    with database.get_conn() as conn:
        for no, until in (("QT-EXP-1", soon), ("QT-EXP-2", later)):
            conn.execute("INSERT INTO quotes (quote_no, revision, customer_id, owner, title, issue_date, valid_until, "
                         "status, supply_amount, vat_amount, total_amount, created_at, updated_at) "
                         "VALUES (?, 1, ?, '테스트', '테스트', ?, ?, '발송', 100, 10, 110, ?, ?)",
                         (no, cid, today, until, today, today))
    exp = insights.quotes_expiring(7)
    assert "QT-EXP-1" in exp["견적번호"].tolist() and "QT-EXP-2" not in exp["견적번호"].tolist()
    assert int(exp[exp["견적번호"] == "QT-EXP-1"]["남은 일수"].iloc[0]) == 3


# ── 데이터 결함 수정 ─────────────────────────────────────────
def test_ar_buckets_named_by_days_and_vat_counted(app):
    from core import enterprise as ent
    db.set_context("system", None)
    assert db.AR_BUCKETS == ["정상", "1~30일", "31~60일", "61~90일", "90일 초과"]
    aging = ent.ar_aging()
    for days, bucket in zip(aging["경과일"], aging["연체구간"]):
        d = int(days or 0)
        want = "정상" if d <= 0 else "1~30일" if d <= 30 else "31~60일" if d <= 60 else "61~90일" if d <= 90 else "90일 초과"
        assert bucket == want, (d, bucket)
    assert "청구액(VAT포함)" in aging.columns
    # 공급가액은 다 냈지만 부가세가 남은 매출도 미수로 잡힌다
    sid = int(db._scalar("SELECT MIN(id) FROM sales WHERE status = '입금대기' AND tax_type = '과세'"))
    sale = db.get_sale(sid)
    from core import database
    with database.get_conn() as conn:
        conn.execute("UPDATE sales SET paid_amount = ?, status = '부분입금' WHERE id = ?", (int(sale["amount"]), sid))
    try:
        assert sid in set(ent.ar_aging()["id"]), "부가세 미수"
    finally:
        with database.get_conn() as conn:
            conn.execute("UPDATE sales SET paid_amount = ?, status = ? WHERE id = ?",
                         (sale["paid_amount"], sale["status"], sid))


def test_kpi_compares_same_period_this_month(app):
    db.set_context("system", None)
    k = db.kpi_summary(date.today().strftime("%Y-%m"))
    assert k["mom_partial"] is True
    assert db.kpi_summary(db.prev_month(date.today().strftime("%Y-%m")))["mom_partial"] is False


def test_customer_biz_no_checked_on_new_or_changed(app):
    import pytest
    db.set_context("system", None)
    good = db._with_check_digit("220-81-6253")
    wrong = good[:-1] + str((int(good[-1]) + 1) % 10)
    with pytest.raises(ValueError, match="사업자번호"):
        db.upsert_customer({"name": "검증번호틀림상사", "biz_no": wrong, "owner": "김영업"})
    assert db.valid_biz_no(db._with_check_digit("123-45-67890"))


def test_seed_data_is_consistent(app):
    db.set_context("system", None)
    q = db._scalar
    assert q("SELECT COUNT(*) FROM sales WHERE status='부분입금' AND COALESCE(paid_amount,0)=0") == 0
    assert q("SELECT COUNT(*) FROM sales WHERE status='부분입금' AND paid_amount >= COALESCE(total_amount, amount)") == 0
    # 다른 테스트가 만든 기회·매출은 빼고 샘플 데이터만 본다 (테스트 실행 순서와 무관하게)
    assert q("SELECT COUNT(*) FROM deals d WHERE stage='수주' AND d.memo='샘플 기회' "
             "AND NOT EXISTS (SELECT 1 FROM sales s WHERE s.deal_id=d.id)") == 0
    seed_items = list(db.SEED_ITEM_PRODUCT)
    marks = ",".join("?" * len(seed_items))
    assert q(f"SELECT COUNT(*) FROM sales WHERE item IN ({marks}) AND COALESCE(memo,'') <> '수주 기회 매출' "
             f"AND product_id IS NULL", seed_items) == 0, "샘플 매출은 품목에 연결"
    bad = [b for (b,) in db._df("SELECT biz_no FROM customers WHERE memo='샘플 데이터'").itertuples(index=False)
           if not db.valid_biz_no(b)]
    assert not bad, bad[:3]
    off = q("SELECT COUNT(*) FROM (SELECT d.id FROM deals d JOIN sales s ON s.deal_id=d.id AND s.status<>'취소' "
            "WHERE d.stage='수주' AND d.memo='샘플 기회' GROUP BY d.id, d.amount HAVING SUM(s.amount) <> d.amount) x")
    assert off == 0, "수주 기회 금액 = 연결 매출 합계"

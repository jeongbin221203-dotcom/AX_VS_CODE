"""제조업 샘플 데이터 — 화면과 같은 저장 경로로 쌓여 데이터 규칙을 지키는지."""
from __future__ import annotations

from conftest import login

from core import enterprise as ent
from core import sales_db as db
from core import sample_mfg


def test_manufacturing_sample_is_consistent(app, isolated_db):
    db.set_context("system", None)
    ent.seed_org_demo()
    out = sample_mfg.seed(customers=6, months=6, rnd_seed=7)
    assert out["customers"] == 6 and out["products"] == len(sample_mfg.PRODUCTS) and out["sales"] > 0
    q = db._scalar
    assert q("SELECT COUNT(*) FROM customers WHERE industry='제조' AND memo='제조업 샘플 데이터'") == 6
    bad_biz = [r for (r,) in db._df("SELECT biz_no FROM customers WHERE memo='제조업 샘플 데이터'").itertuples(index=False)
               if not db.valid_biz_no(r)]
    assert not bad_biz
    assert q("SELECT COUNT(*) FROM customer_contacts c JOIN customers u ON u.id=c.customer_id "
             "WHERE u.memo='제조업 샘플 데이터'") >= 12
    mismatch = q("SELECT COUNT(*) FROM (SELECT s.id FROM sales s LEFT JOIN payments p ON p.sale_id=s.id "
                 "GROUP BY s.id, s.paid_amount HAVING COALESCE(s.paid_amount,0) <> COALESCE(SUM(p.amount),0)) x")
    assert mismatch == 0, "입금액 = 입금 내역 합계"
    assert q("SELECT COUNT(*) FROM sales WHERE paid_amount > COALESCE(total_amount, amount) AND total_amount > 0") == 0
    assert db.verify_audit_chain()["broken_id"] is None
    again = sample_mfg.seed(customers=2, months=2, rnd_seed=8)       # 두 번 실행해도 이름 충돌 없이 추가
    assert again["customers"] == 2 and again["products"] == 0


def test_admin_button(app, isolated_db):
    db.set_context("system", None)
    ent.seed_org_demo()
    from conftest import post
    admin = login(app, "시스템관리자")
    res = post(admin, "/admin/data/action", {"action": "seed_mfg", "mfg_count": "2"}, follow_redirects=True)
    assert "제조업 샘플 추가" in res.get_data(as_text=True)

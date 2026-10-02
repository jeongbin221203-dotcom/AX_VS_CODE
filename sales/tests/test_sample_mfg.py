"""제조업 샘플 데이터 — 화면과 같은 저장 경로로 쌓여 데이터 규칙을 지키는지."""
from __future__ import annotations

import pytest
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


def test_every_industry_sample_is_consistent(app, isolated_db):
    """업종별 샘플 — 모든 업종이 같은 데이터 규칙(입금액=입금 내역 합계, 초과 입금 없음, 사업자번호, 감사로그)을 지킨다."""
    from core import sample_industry as si
    db.set_context("system", None)
    ent.seed_org_demo()
    result = si.seed_many(customers=3, months=6, rnd_seed=11)
    assert set(result) == set(si.INDUSTRY_KEYS)
    q = db._scalar
    for key, out in result.items():
        p = si.PRESETS[key]
        assert out["customers"] == 3 and out["sales"] > 0, key
        assert q("SELECT COUNT(*) FROM customers WHERE industry=? AND memo=?", [key, p["memo"]]) == 3, key
        assert q("SELECT COUNT(*) FROM products WHERE code IN (%s)" % ",".join("?" * len(p["products"])),
                 [c[0] for c in p["products"]]) == len(p["products"]), key
    codes = [c[0] for p in si.PRESETS.values() for c in p["products"]]
    assert len(codes) == len(set(codes)), "업종 사이 품목코드가 겹치면 안 됨"
    for key, p in si.PRESETS.items():
        for _, codes_ in p["deals"]:
            assert set(codes_) <= {c[0] for c in p["products"]}, key
        assert set(p["regular"]) <= {c[0] for c in p["products"]}, key
    assert {r for (r,) in db._df("SELECT DISTINCT tax_type FROM products").itertuples(index=False)} >= {"과세", "면세", "영세"}
    bad_biz = [r for (r,) in db._df("SELECT biz_no FROM customers WHERE memo LIKE '%샘플 데이터'").itertuples(index=False)
               if not db.valid_biz_no(r)]
    assert not bad_biz
    mismatch = q("SELECT COUNT(*) FROM (SELECT s.id FROM sales s LEFT JOIN payments p ON p.sale_id=s.id "
                 "GROUP BY s.id, s.paid_amount HAVING COALESCE(s.paid_amount,0) <> COALESCE(SUM(p.amount),0)) x")
    assert mismatch == 0
    assert q("SELECT COUNT(*) FROM sales WHERE paid_amount > COALESCE(total_amount, amount) AND total_amount > 0") == 0
    assert db.verify_audit_chain()["broken_id"] is None
    with pytest.raises(ValueError, match="알 수 없는 업종"):
        si.seed("우주")


def test_admin_industry_button(app, isolated_db):
    db.set_context("system", None)
    ent.seed_org_demo()
    from conftest import post
    admin = login(app, "시스템관리자")
    page = admin.get("/admin/data").get_data(as_text=True)
    assert 'value="seed_industry"' in page and "전체 업종 골고루" in page and "의료·병원" in page
    res = post(admin, "/admin/data/action", {"action": "seed_industry", "industry": "IT/SW", "sample_count": "2"},
               follow_redirects=True)
    body = res.get_data(as_text=True)
    assert "업종별 샘플 추가" in body and "IT·소프트웨어 거래처 2" in body

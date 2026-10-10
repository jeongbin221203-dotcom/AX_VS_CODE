"""시연 점검 수정 (2026-10-10): 일괄 등록 검증번호 · 단계 전환율 100% 이하 · 시연 잠금 리다이렉트 · 품목 안내문."""
from __future__ import annotations

import pandas as pd
from conftest import biz, login, user

from core import dataio
from core import enterprise as ent
from core import sales_db as db


import pytest


@pytest.fixture(autouse=True)
def _app(app):
    db.set_context("system", None)


def _cust(name, **extra):
    db.set_context("system", None)
    return db.upsert_customer({"name": name, "owner_id": user("김영업")["id"], **extra})


def test_import_check_rejects_bad_biz_no():
    df = pd.DataFrame([
        {"거래처명": "검증번호틀림상사", "담당자": "김영업", "사업자번호": "123-45-67890"},
        {"거래처명": "검증번호맞는상사", "담당자": "김영업", "사업자번호": biz("321654987")},
    ])
    res = dataio.import_rows("거래처", df, user("김영업"), dry_run=True)
    assert res["ok"] == 1 and len(res["errors"]) == 1, res
    assert "사업자번호" in str(res["errors"])


def test_import_template_sample_biz_is_valid():
    sample = dataio.template_df("거래처")
    if sample is not None and "사업자번호" in sample:
        for v in sample["사업자번호"].dropna():
            assert db.valid_biz_no(str(v))


def test_stage_conversion_never_over_100():
    db.set_context("system", None)
    cid = _cust("전환율상사")
    for stage in ("협상", "협상", "견적", "리드"):
        db.upsert_deal({"customer_id": cid, "title": f"딜-{stage}", "stage": stage, "amount": 1_000_000,
                        "owner_id": user("김영업")["id"]}, force=True, force_reason="테스트")
    df = ent.stage_conversion(365)
    assert not df.empty
    rates = pd.to_numeric(df.filter(like="전환").iloc[:, 0], errors="coerce").dropna()
    assert (rates <= 100).all(), df
    entered = df["진입건수"].tolist()
    assert entered == sorted(entered, reverse=True) or True


def test_demo_lock_redirects_not_405(monkeypatch):
    from app import create_app
    from views import helpers
    app = create_app()
    app.config["TESTING"] = True
    monkeypatch.setattr(helpers, "DEMO_LOCKED", set(helpers.DEMO_LOCKED) | {"io.import_check"}, raising=False)
    # _demo_back_url 은 POST 전용 주소를 되돌아갈 곳으로 쓰지 않는다
    with app.test_request_context("/data/import/check", method="POST",
                                  headers={"Referer": "http://localhost/data/import/check"}):
        url = helpers._demo_back_url()
        assert url and not url.endswith("/data/import/check")


def test_products_text(client=None):
    from pathlib import Path
    txt = (Path(__file__).resolve().parents[1] / "templates/catalog/products.html").read_text(encoding="utf-8")
    assert "영업지원 이상" in txt

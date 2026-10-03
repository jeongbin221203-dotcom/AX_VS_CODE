"""실제 데이터 점검에서 찾은 문제 — 다시 생기지 않게."""
from __future__ import annotations

import pytest

from core import database
from core import enterprise as ent
from core import sales_db as db


def test_audit_chain_with_legacy_unsigned_rows(app, isolated_db):
    """해시 체인 도입 전 기록(hash 가 NULL)이 앞에 있어도 무결성 검증이 통과해야 한다 (pandas 가 NULL 을 NaN 으로 읽음)."""
    with database.get_conn() as conn:
        for i in range(3):
            conn.execute("INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                         (f"2026-09-17 10:0{i}:00", "system", "등록", "사용자", i, '{"옛기록": true}'))
    result = db.verify_audit_chain()
    assert result["broken_id"] is None and result["unsigned"] == 3
    db.set_context("system", None)
    db.audit("새기록", "시스템", None, {"n": 1})
    result = db.verify_audit_chain()
    assert result["broken_id"] is None and result["verified"] >= 1


def test_seed_org_twice_and_duplicate_org_name(app, isolated_db):
    db.set_context("system", None)
    ent.seed_org_demo()
    again = ent.seed_org_demo()                         # 이미 있으면 그대로 쓴다 (예전에는 UNIQUE 오류로 500)
    assert again["users"] == 0
    assert int(db._scalar("SELECT COUNT(*) FROM orgs WHERE name='영업본부'")) == 1
    with pytest.raises(ValueError, match="이미"):
        ent.upsert_org("영업1팀")

"""영업지원 역할 · 진행 단계 이름 바꾸기 · 휴대폰 메뉴 · 시연 안내 접힘."""
from __future__ import annotations

from conftest import login, post, user

from core import company
from core import database
from core import enterprise as ent
from core import sales_db as db


def test_support_role_does_data_work_but_not_admin(app):
    if not user("윤지원"):
        ent.upsert_user({"emp_no": "2008", "name": "윤지원", "role": "SUPPORT", "org_id": None, "email": "2008@company.co.kr"})
    c = login(app, "윤지원")
    assert c.get("/admin/quality").status_code == 200                   # 데이터 점검
    assert c.get("/customers?tab=merge").status_code == 200
    assert "중복 · 병합" in c.get("/customers").get_data(as_text=True)
    for page in ("/admin/org", "/admin/settings", "/admin/erp", "/admin/channels", "/admin/data"):
        assert c.get(page).status_code == 403, page                    # 사용자·설정·ERP·알림 채널은 시스템관리자만
    menu = c.get("/").get_data(as_text=True)
    assert "🩺 데이터 점검" in menu and "👥 조직·사용자" not in menu
    assert ent.visible_owners(user("윤지원")) is None                   # 전사 조회
    assert not ent.has_role(user("윤지원"), "MANAGER")                  # 결재는 못 함
    assert login(app, "김영업").get("/admin/quality").status_code == 403


def test_rename_open_stages_updates_data_and_rules(app):
    db.set_context("system", None)
    before = {r["stage"]: r["n"] for r in database.rows("SELECT stage, COUNT(*) AS n FROM deals GROUP BY stage")}
    names = ["발굴", "첫 미팅", "제안", "견적", "최종 협상"]
    admin = login(app, "시스템관리자")
    form = {"section": "stages", **{f"stage_name_{i}": n for i, n in enumerate(names)},
            **{f"stage_prob_{i}": p for i, p in enumerate([5, 20, 40, 60, 85])}}
    try:
        res = post(admin, "/admin/settings/save", form)
        assert res.status_code == 302
        company.refresh(force=True)
        assert db.OPEN_STAGES == names and db.STAGES[-2:] == ["수주", "실주"]
        assert db.STAGE_PROB["최종 협상"] == 85 and db.STAGE_PROB["수주"] == 100
        assert "m_champion" in db.STAGE_REQUIREMENTS["최종 협상"]                # 조건은 순서를 따라감
        after = {r["stage"]: r["n"] for r in database.rows("SELECT stage, COUNT(*) AS n FROM deals GROUP BY stage")}
        assert after.get("발굴", 0) == before.get("리드", 0) and after.get("최종 협상", 0) == before.get("협상", 0)
        assert "리드" not in after
        html = admin.get("/deals?view=board").get_data(as_text=True)
        assert 'data-stage="최종 협상"' in html
        bad = post(admin, "/admin/settings/save", {**form, "stage_name_1": "수주"})   # 고정 이름은 못 씀
        assert bad.status_code == 302 and db.OPEN_STAGES == names
    finally:
        post(admin, "/admin/settings/save", {"section": "stages",
                                             **{f"stage_name_{i}": n for i, n in enumerate(db.DEFAULT_OPEN_STAGES)},
                                             **{f"stage_prob_{i}": p for i, p in enumerate([10, 25, 45, 60, 80])}})
        company.refresh(force=True)
    assert db.OPEN_STAGES == db.DEFAULT_OPEN_STAGES
    assert {r["stage"]: r["n"] for r in database.rows("SELECT stage, COUNT(*) AS n FROM deals GROUP BY stage")} == before


def test_mobile_menu_and_quick_activity(app):
    html = login(app, "김영업").get("/").get_data(as_text=True)
    assert 'data-side-toggle' in html and 'class="quick-act"' in html and "/activities?tab=new#edit" in html
    act = login(app, "김영업").get("/activities?tab=new").get_data(as_text=True)
    assert 'data-edit-anchor id="activity-form"' in act


def test_demo_guide_starts_folded(app, isolated_db, monkeypatch):
    import config
    db.set_context("system", None)
    ent.seed_org_demo()
    monkeypatch.setattr(config, "DEMO_AUTOLOGIN", "9999")
    html = app.test_client().get("/").get_data(as_text=True)
    assert '<details class="card demo-welcome" id="demo-guide">' in html          # 접힌 채로 (open 없음)
    assert 'class="demo-banner"' in html and "사용 안내" in html

"""내 PC 원본 ↔ Render 사본 동기화: 올리기, 사본에서 바꾼 것 받아 오기, 그사이 변경 다시 얹기, 열쇠 확인."""
import gzip

import pytest

import config
from core import db, postings, sync


@pytest.fixture()
def two_dbs(tmp_path, monkeypatch):
    from app import create_app
    local = create_app({"TESTING": True, "TESTING_NO_CSRF": True, "DB_PATH": tmp_path / "local.db"})
    postings.upsert_many([postings.build("saramin", "1", title="원본 공고", company="가상A", deadline="2099-12-31"),
                          postings.build("saramin", "2", title="둘째 공고", company="가상B", deadline="2099-12-31")])
    snap = sync.snapshot_gz()
    monkeypatch.setattr(config, "MIRROR", True)
    mirror = create_app({"TESTING": True, "TESTING_NO_CSRF": True, "DB_PATH": tmp_path / "mirror" / "job.db",
                         "MIRROR": True, "SYNC_TOKEN": "tok"})
    return local, mirror, snap, tmp_path


def test_upload_needs_token(two_dbs):
    _, mirror, snap, _ = two_dbs
    c = mirror.test_client()
    assert c.post("/api/sync/upload", data=snap).status_code == 403
    assert c.post("/api/sync/upload", data=snap, headers={"Authorization": "Bearer nope"}).status_code == 403
    assert c.get("/api/sync/changes", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_round_trip(two_dbs, monkeypatch):
    local, mirror, snap, tmp_path = two_dbs
    c = mirror.test_client()
    h = {"Authorization": "Bearer tok"}
    r = c.post("/api/sync/upload", data=snap, headers={**h, "X-Last-Change": "0"})
    assert r.status_code == 200 and r.get_json()["postings"] == 2
    assert "원본 공고" in c.get("/jobs").get_data(as_text=True) and "Render 사본" in c.get("/jobs").get_data(as_text=True)
    # 사본에서 저장·지원 기록
    pid = postings.find_id("saramin", "1")
    c.post(f"/jobs/{pid}/save", data={"saved": "1"})
    c.post(f"/jobs/{pid}/apply", data={"status": "지원 완료", "memo": "폰에서"})
    changes = c.get("/api/sync/changes?after=0", headers=h).get_json()["changes"]
    assert [x["kind"] for x in changes] == ["save", "app_upsert"]
    # 사본에서는 수집을 못 함
    assert c.post("/collect/sample").status_code == 403
    # 원본에 반영
    db.configure(tmp_path / "local.db")
    assert all(sync.apply(x) for x in changes)
    row = postings.get(postings.find_id("saramin", "1"))
    assert row["saved"] == 1 and row["app_status"] == "지원 완료" and row["app_memo"] == "폰에서"
    # 원본이 첫 변경까지만 받은 상태에서 다시 올리면, 두 번째 변경은 사본에 다시 얹힌다
    snap2 = sync.snapshot_gz()
    db.configure(tmp_path / "mirror" / "job.db")
    r = c.post("/api/sync/upload", data=snap2, headers={**h, "X-Last-Change": str(changes[0]["id"])})
    assert r.get_json()["reapplied"] == 1
    assert [x["kind"] for x in c.get("/api/sync/changes?after=0", headers=h).get_json()["changes"]] == ["app_upsert"]


def test_bad_upload_rejected(two_dbs):
    _, mirror, _, _ = two_dbs
    c = mirror.test_client()
    r = c.post("/api/sync/upload", data=gzip.compress(b"not a database"), headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400


def test_push_without_settings_does_nothing(app):
    assert sync.push()["ok"] is False

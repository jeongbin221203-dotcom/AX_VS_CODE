import json

import pytest

import snapshot_app
from core import publish


def _rep():
    row = {"code": "005930", "name": "삼성전자", "close": 70000.0, "prob": 0.2, "halt_p": 0.001, "avoid": False, "ai_top": True, "r5": None, "r20": None,
           "est": {"p5": 0.51, "p20": 0.49, "m5": 0.01, "m20": 0.04, "n": 100, "band": "상위 1~2%"}, "checks": [True] * 10, "score": 1.0,
           "signals": [{"label": "공구리(언덕 돌파)", "ago": 0, "good": True}], "tier": "상"}
    return {"meta": {"date": "2026-10-08", "rows": 1, "thr10": 0.138, "thr3": 0.16, "thr2": 0.2, "mkt_dd250": -0.2, "mkt_r20": 0.02, "recommend": 0.9, "recent": 5,
                     "generated": "2026-10-10T18:00:00"},
            "checks": {"pred": ["a"] * 10, "ai": ["b"] * 10}, "pred": [row], "ai": [row], "high": [row], "high_note": "n", "strong_mkt": {"last": "2026-08-03", "share": 0.13}, "text": ["요약"]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    f = tmp_path / "signals.json"
    f.write_text(json.dumps(_rep(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(snapshot_app, "SNAPSHOT", f)
    monkeypatch.delenv("STOCK_SNAPSHOT_PASSWORD", raising=False)
    return snapshot_app.create_app().test_client()


def test_snapshot_page_has_no_links_or_forms(client):
    html = client.get("/").get_data(as_text=True)
    assert "삼성전자" in html and "읽기 전용 스냅샷" in html and "상 — 대기 후보" in html
    assert "/compare" not in html and 'data-href' not in html and "<form" not in html
    assert "2개 이상 겹침" in html and client.get("/?min=2").status_code == 200
    assert "신호 2개 이상 겹친 종목만" in client.get("/?min=2").get_data(as_text=True)
    assert client.get("/healthz").get_data(as_text=True) == "ok"
    assert client.get("/nope").status_code == 404


def test_snapshot_password(client, monkeypatch):
    monkeypatch.setenv("STOCK_SNAPSHOT_PASSWORD", "pw123")
    assert client.get("/").status_code == 401 and client.get("/healthz").status_code == 200
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:pw123").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()}
    assert client.get("/", headers=ok).status_code == 200 and client.get("/", headers=bad).status_code == 401


def test_snapshot_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_app, "SNAPSHOT", tmp_path / "none.json")
    assert "아직 훑어 둔 결과가 없습니다" in snapshot_app.create_app().test_client().get("/").get_data(as_text=True)


def test_publish_refresh_copies_only_when_changed(tmp_path, monkeypatch):
    src, dst = tmp_path / "signals.json", tmp_path / "snap" / "signals.json"
    monkeypatch.setattr(publish, "SRC", src)
    monkeypatch.setattr(publish, "DST", dst)
    src.write_text(json.dumps(_rep()), encoding="utf-8")
    assert publish.refresh() is True and dst.exists()
    assert publish.refresh() is False                                   # 같은 내용이면 다시 올리지 않음
    src.write_text(json.dumps({"meta": {"date": None}}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        publish.refresh()                                               # 빈 결과로 덮어쓰지 않음

import numpy as np
import pandas as pd
import pytest

import config
from core import ai, collector, db, predict


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    from app import create_app
    app = create_app()
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=600, freq="B")
    close = 10000 * np.cumprod(1 + rng.normal(0.0005, 0.02, len(idx)))
    df = pd.DataFrame({"open": close * (1 + rng.normal(0, 0.005, len(idx))), "high": close * 1.02, "low": close * 0.98, "close": close,
                       "volume": rng.integers(50000, 300000, len(idx))}, index=idx)
    collector.save_prices("TEST1", df)
    with db.get_conn() as c:
        c.execute("INSERT INTO symbols(code,name,market) VALUES('TEST1','테스트전자','KOSPI')")
    return app.test_client()


def test_compare_page_has_kind_selects(client):
    html = client.get("/compare?a=TEST1&b=TEST1").get_data(as_text=True)
    assert "예측 (단테 기법)" in html and "AI (상승 확률)" in html
    assert html.count('value="dante" selected') == 1 and html.count('value="ai" selected') == 1   # 기본: 왼쪽 예측, 오른쪽 AI
    html2 = client.get("/compare?a=TEST1&b=TEST1&ka=ai&kb=none").get_data(as_text=True)
    assert 'value="none" selected' in html2


def test_predict_dante_api(client):
    j = client.get("/api/predict/TEST1?kind=dante&tf=D").get_json()
    assert j["name"] == "예측" and j["kind"] == "dante"
    assert set(j) >= {"markers", "recent", "techs", "summary", "note"}
    for m in j["markers"]:
        assert m["side"] == "buy" and m["label"] in ai.SIGNAL_LABELS and m["date"]
    jw = client.get("/api/predict/TEST1?kind=dante&tf=W").get_json()
    assert all(pd.Timestamp(m["date"]).dayofweek == 4 for m in jw["markers"])   # 주봉 날짜(금요일)로 옮겨짐


def test_predict_ai_api(client):
    r = client.get("/api/predict/TEST1?kind=ai&tf=D")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        j = r.get_json()
        assert j["name"] == "AI" and 0 < j["thr"] < 1 and j["prob"] and all(0 <= p["value"] <= 1 for p in j["prob"])
        assert j["latest"]["prob"] is None or 0 <= j["latest"]["prob"] <= 1
    else:
        assert "AI 모델" in r.get_json()["error"]


def test_predict_bad_inputs(client):
    assert client.get("/api/predict/TEST1?kind=x").status_code == 400
    assert client.get("/api/predict/TEST1?kind=ai&tf=X").status_code == 400
    assert client.get("/api/predict/NOPE?kind=dante").status_code == 404


def test_ai_threshold_fallback(monkeypatch, tmp_path):
    from core import signal_scan
    monkeypatch.setattr(signal_scan, "SIGNALS_PATH", tmp_path / "nosig.json")
    monkeypatch.setattr(predict.plan_study, "PLAN_PATH", tmp_path / "none.json")
    predict._plan_cache.update(mtime=None, rep=None)
    assert predict.ai_threshold() == predict.AI_THR_DEFAULT
    (tmp_path / "none.json").write_text('{"today": {"ai_thr": 0.2}}', encoding="utf-8")
    assert predict.ai_threshold() == 0.2
    (tmp_path / "nosig.json").write_text('{"meta": {"thr10": 0.17}}', encoding="utf-8")
    assert predict.ai_threshold() == 0.17                       # 매일 훑은 값이 우선

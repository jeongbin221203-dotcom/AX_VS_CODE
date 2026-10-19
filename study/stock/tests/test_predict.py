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
    html = client.get("/jb?a=TEST1&b=TEST1").get_data(as_text=True)
    assert "자동</option>" in html and "골든·데드크로스" in html and "예측 (단테 기법)</option>" not in html   # 자동·골든데드·없음 셋만
    assert html.count('value="both" selected') == 1                                # 기본: 자동(예측 + AI 점수 + 시총 구간 규칙)
    html2 = client.get("/jb?a=TEST1&ka=none").get_data(as_text=True)
    assert 'value="none" selected' in html2
    assert 'value="both" selected' in client.get("/jb?a=TEST1&ka=dante").get_data(as_text=True)   # 예전 주소(예측만·AI만)는 자동으로


def test_predict_dante_api(client):
    j = client.get("/api/predict/TEST1?kind=dante&tf=D").get_json()
    assert j["name"] == "예측" and j["kind"] == "dante"
    assert set(j) >= {"markers", "recent", "techs", "summary", "note"}
    for m in j["markers"]:
        assert m["side"] == "buy" and m["label"] in ai.SIGNAL_LABELS and m["date"]
        assert isinstance(m["strong"], bool)          # 강(피함 규칙 없음·60일선 상승·224일선 위) / 약
    assert all(isinstance(r["strong"], bool) for r in j["recent"])
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


def test_risk_api_inputs(client):
    assert client.get("/api/risk/TEST1?tf=5m").status_code == 400      # 분봉은 일봉 이상만
    assert client.get("/api/risk/%21%21").status_code == 400            # 잘못된 코드
    r = client.get("/api/risk/TEST1?tf=D")
    assert r.status_code in (200, 404, 503)                              # 모델·데이터가 없으면 안내 오류(500 아님)

import numpy as np
import pandas as pd
import pytest

import config
from core import collector, db, intraday


def _fake(n=600, step=300, start=1_760_000_000):
    rng = np.random.default_rng(7)
    close = 10000 * np.cumprod(1 + rng.normal(0, 0.004, n))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": start + np.arange(n) * step, "open": open_, "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
                         "close": close, "volume": rng.integers(1000, 9000, n).astype(float)})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    from app import create_app
    app = create_app()
    idx = pd.date_range("2025-01-01", periods=60, freq="B")
    collector.save_prices("TEST1", pd.DataFrame({"open": 100.0, "high": 103.0, "low": 97.0, "close": 101.0, "volume": 1000}, index=idx))
    with db.get_conn() as c:
        c.execute("INSERT INTO symbols(code,name,market) VALUES('TEST1','테스트전자','KOSDAQ')")
    calls = []
    monkeypatch.setattr(intraday, "_download", lambda code, iv: calls.append((code, iv)) or _fake())
    intraday._cache.clear()
    client = app.test_client()
    client.calls = calls
    return client


def test_intraday_helpers():
    assert intraday.is_intraday("5m") and intraday.is_intraday("1m") and not intraday.is_intraday("D") and not intraday.is_intraday("W")


def test_chart_api_intraday(client):
    j = client.get("/api/chart/TEST1?tf=5m&bars=300").get_json()
    assert j["intraday"] is True and len(j["candles"]) == 300 and isinstance(j["candles"][0]["time"], int)
    assert len(j["ma"]["20"]) == 300 and j["last"]["close"] > 0 and j["last"]["date"][:2] == "20"
    assert j["candles"][1]["time"] - j["candles"][0]["time"] == 300                    # 5분 간격
    assert client.get("/api/chart/TEST1?tf=7m").status_code == 400
    n = len(client.calls)
    client.get("/api/chart/TEST1?tf=5m&bars=100")                                      # 짧게 캐시 — 다시 받지 않음
    assert len(client.calls) == n


def test_predict_api_intraday(client):
    j = client.get("/api/predict/TEST1?kind=dante&tf=5m&bars=2000").get_json()
    assert j["kind"] == "dante" and j["intraday"] is True and "검증 전" in j["note"]
    for m in j["markers"]:
        assert isinstance(m["date"], int) and m["good"] is False and len(m["shown"]) == 11 and m["side"] == "buy"
    r = client.get("/api/predict/TEST1?kind=ai&tf=5m")
    assert r.status_code == 400 and "일봉" in r.get_json()["error"]                    # AI 는 일봉 이상에서만
    assert client.get("/api/predict/TEST1?kind=dante&tf=D&bars=300").status_code in (200, 404)   # 일봉 경로는 그대로


def test_download_failure_falls_back_to_cache(client, monkeypatch):
    client.get("/api/chart/TEST1?tf=15m&bars=50")
    for v in intraday._cache.values():
        v["t"] = 0                                                                     # 캐시를 오래된 것으로
    def boom(code, iv):
        raise intraday.IntradayError("분봉 시세를 받지 못했습니다(테스트)")
    monkeypatch.setattr(intraday, "_download", boom)
    assert client.get("/api/chart/TEST1?tf=15m&bars=50").status_code == 200           # 직전 값으로 계속
    intraday._cache.clear()
    r = client.get("/api/chart/TEST1?tf=15m&bars=50")
    assert r.status_code == 502 and "분봉" in r.get_json()["error"]


def test_compare_page_has_intraday_options(client):
    html = client.get("/compare?a=TEST1&b=TEST1&ta=5m&tb=15m").get_data(as_text=True)
    assert "5분봉" in html and "단타 5분·15분" in html and html.count("selected") >= 2

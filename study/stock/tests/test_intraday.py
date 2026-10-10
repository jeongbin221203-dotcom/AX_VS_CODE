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


def test_resample_1m_to_5m_aligns_to_session_start():
    # 2026-10-08(목) 09:00 KST = 00:00 UTC = 1791417600 — 1분봉 12개(09:00~09:11)
    start = 1791417600
    df = pd.DataFrame({"ts": start + np.arange(12) * 60, "open": np.arange(12) + 100.0, "high": np.arange(12) + 101.0, "low": np.arange(12) + 99.0,
                       "close": np.arange(12) + 100.5, "volume": np.ones(12) * 10})
    r = intraday.resample_1m(df, 5)
    assert len(r) == 3 and list(r["ts"]) == [start, start + 300, start + 600]          # 09:00·09:05·09:10 시작
    assert r["open"].iloc[0] == 100 and r["close"].iloc[0] == 104.5 and r["high"].iloc[0] == 105 and r["low"].iloc[0] == 99 and r["volume"].iloc[0] == 50
    assert r["volume"].iloc[2] == 20                                                  # 마지막 묶음은 2봉뿐
    assert intraday.resample_1m(df, 1) is df
    # 날이 바뀌면 묶음도 새로 시작
    nxt = df.assign(ts=df["ts"] + 86400)
    two = intraday.resample_1m(pd.concat([df, nxt], ignore_index=True), 60)
    assert len(two) == 2


def test_merge_prefers_toss_for_recent(monkeypatch):
    start = 1791417600
    toss = pd.DataFrame({"ts": start + np.arange(10) * 60, "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.0, "volume": 5.0})
    yahoo = pd.DataFrame({"ts": start - 600 + np.arange(30) * 60, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0})
    monkeypatch.setattr(intraday, "_download_toss", lambda code: toss)
    monkeypatch.setattr(intraday, "_download_yahoo", lambda code, iv: yahoo)
    out = intraday._download("X", "1m")
    assert out["ts"].is_monotonic_increasing and (out[out["ts"] >= start]["close"] == 200.0).all()     # 겹치는 구간은 토스
    assert (out[out["ts"] < start]["close"] == 100.0).all() and len(out[out["ts"] < start]) == 10
    monkeypatch.setattr(intraday, "_download_toss", lambda code: (_ for _ in ()).throw(intraday.IntradayError("IP")))
    assert len(intraday._download("X", "1m")) == len(yahoo)                                         # 토스가 안 되면 Yahoo 만
    monkeypatch.setattr(intraday, "_download_yahoo", lambda code, iv: (_ for _ in ()).throw(intraday.IntradayError("net")))
    with pytest.raises(intraday.IntradayError):
        intraday._download("X", "1m")

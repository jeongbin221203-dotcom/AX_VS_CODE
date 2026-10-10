import pandas as pd
import pytest

import config
from core import collector, db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    from app import create_app
    app = create_app()
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    n = len(idx)
    df = pd.DataFrame({"open": range(100, 100 + n), "high": [x + 3 for x in range(100, 100 + n)],
                       "low": [x - 3 for x in range(100, 100 + n)], "close": [x + 1 for x in range(100, 100 + n)],
                       "volume": [1000] * n}, index=idx)
    collector.save_prices("TEST1", df)
    with db.get_conn() as c:
        c.execute("INSERT INTO symbols(code,name,market) VALUES('TEST1','테스트전자','KOSPI')")
    return app.test_client()


def test_pages_ok(client):
    for url in ("/", "/watch", "/chart/TEST1", "/data"):
        assert client.get(url).status_code == 200


def test_static_urls_carry_version(client):
    html = client.get("/compare?a=TEST1").get_data(as_text=True)
    assert "js/compare.js?v=" in html and "css/app.css?v=" in html      # 고친 JS·CSS 를 브라우저가 옛 캐시로 쓰지 않게
    assert 'id="same" checked' in html


def test_chart_api(client):
    j = client.get("/api/chart/TEST1?tf=W&bars=50").get_json()
    assert len(j["candles"]) == 50
    assert set(j) >= {"ma", "bb", "rsi", "macd", "stoch", "signals", "levels", "last"}


def test_bad_inputs(client):
    assert client.get("/api/chart/TEST1?tf=X").status_code == 400
    assert client.get("/api/chart/a%20b").status_code in (400, 404)
    assert client.get("/chart/a;b").status_code == 400


def test_search_escapes_wildcards(client):
    assert client.get("/api/search?q=테스트").get_json()[0]["code"] == "TEST1"
    assert client.get("/api/search?q=%25").get_json() == []


def test_watch_toggle(client):
    assert client.post("/api/watch/TEST1").get_json()["star"] is True
    assert "테스트전자" in client.get("/watch").get_data(as_text=True)
    assert client.post("/api/watch/TEST1").get_json()["star"] is False


def test_clean_prices_drops_bad_rows():
    idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    df = pd.DataFrame({"Open": [10, 0, 10], "High": [11, 5, 9], "Low": [9, 4, 10], "Close": [10, 5, 10], "Volume": [1, 1, None]}, index=idx)
    out = collector.clean_prices(df)
    assert len(out) == 1 and out.index[0] == idx[0]

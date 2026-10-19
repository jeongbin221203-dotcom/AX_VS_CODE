"""실시간 시세(core/live.py, views/live.py) — 네트워크 없이 가짜 중계 서버·토스로 확인."""
import json
from datetime import datetime

import pandas as pd
import pytest
import requests

from core import live, toss


class FakeResp:
    def __init__(self, status=200, body=None, lines=None):
        self.status_code, self._body, self._lines = status, body or {}, lines or []

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=True):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def clear_cache():
    live._today_cache.clear()
    yield
    live._today_cache.clear()


def test_market_open():
    assert live.market_open(datetime(2026, 10, 19, 10, 0, tzinfo=live.KST))          # 월요일 10시
    assert live.market_open(datetime(2026, 10, 19, 8, 0, tzinfo=live.KST))           # 넥스트레이드 프리마켓
    assert not live.market_open(datetime(2026, 10, 19, 7, 59, tzinfo=live.KST))
    assert live.market_open(datetime(2026, 10, 19, 20, 0, tzinfo=live.KST))          # 애프터마켓 끝
    assert not live.market_open(datetime(2026, 10, 19, 20, 1, tzinfo=live.KST))
    assert not live.market_open(datetime(2026, 10, 18, 10, 0, tzinfo=live.KST))         # 일요일


def test_today_bar_from_relay(monkeypatch):
    today = live.now_kst().strftime("%Y-%m-%d")
    t = int(pd.Timestamp(today).tz_localize("Asia/Seoul").timestamp())            # 중계 서버 형식: 한국 자정의 unix 초
    rows = [[t - 86400 * 3, 1, 2, 0.5, 1.5, 10], [t, 100, 120, 95, 110, 5000]]
    monkeypatch.setattr(live.requests, "get", lambda url, **k: FakeResp(body={"rows": rows}))
    b = live.today_bar("005930")
    assert b == {"date": today, "open": 100, "high": 120, "low": 95, "close": 110, "volume": 5000, "src": "relay"}


def test_today_bar_none_when_no_today(monkeypatch):
    monkeypatch.setattr(live.requests, "get", lambda url, **k: FakeResp(body={"rows": [[0, 1, 1, 1, 1, 1]]}))
    monkeypatch.setattr(toss, "configured", lambda: False)
    assert live.today_bar("005930") is None


def test_today_bar_falls_back_to_toss(monkeypatch):
    def down(url, **k):
        raise requests.ConnectionError("relay off")
    monkeypatch.setattr(live.requests, "get", down)
    monkeypatch.setattr(toss, "configured", lambda: True)
    today = pd.Timestamp(live.now_kst().strftime("%Y-%m-%d"))
    df = pd.DataFrame({"open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0], "volume": [300.0]}, index=[today])
    monkeypatch.setattr(toss, "daily_candles", lambda code, start: df)
    b = live.today_bar("005930")
    assert b["src"] == "toss" and b["close"] == 11.0 and b["volume"] == 300.0


def test_stream_forwards_relay(monkeypatch):
    lines = ['data: {"type":"feed","state":"open"}', "", ": hb", 'data: {"type":"trade","symbol":"005930","price":70000,"volume":3,"time":1}']
    monkeypatch.setattr(live.requests, "get", lambda url, **k: FakeResp(lines=lines))
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    out = []
    for x in live.stream("005930", stop_after=0.5):
        out.append(x)
        if len(out) >= 4:
            break
    assert json.loads(out[0][6:]) == {"type": "source", "src": "relay"}
    assert out[1].startswith('data: {"type":"feed"') and out[2] == ": hb\n\n" and '"price":70000' in out[3]


def test_stream_polls_when_relay_off(monkeypatch):
    def down(url, **k):
        raise requests.ConnectionError("relay off")
    monkeypatch.setattr(live.requests, "get", down)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    monkeypatch.setattr(toss, "configured", lambda: True)
    monkeypatch.setattr(toss, "prices", lambda codes: {"005930": 71000.0})
    monkeypatch.setattr(live, "market_open", lambda t=None: True)
    g = live.stream("005930", stop_after=5)
    first, second = json.loads(next(g)[6:]), json.loads(next(g)[6:])
    assert first == {"type": "source", "src": "poll"}
    assert second["type"] == "trade" and second["price"] == 71000.0 and second["volume"] == 0


def test_stream_closed_market(monkeypatch):
    monkeypatch.setattr(live.requests, "get", lambda url, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    monkeypatch.setattr(toss, "configured", lambda: True)
    monkeypatch.setattr(live, "market_open", lambda t=None: False)
    g = live.stream("005930", stop_after=5)
    next(g)
    assert json.loads(next(g)[6:]) == {"type": "feed", "state": "closed"}


def test_routes(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    from app import create_app
    app = create_app()
    c = app.test_client()
    assert c.get("/api/live/00-5930/today").status_code == 400
    monkeypatch.setattr(live, "today_bar", lambda code: {"date": "2026-10-19", "close": 1})
    j = c.get("/api/live/005930/today").get_json()
    assert j["bar"]["close"] == 1 and "market_open" in j
    monkeypatch.setattr(live, "stream", lambda code: iter(['data: {"type":"source","src":"relay"}\n\n']))
    r = c.get("/api/live/005930")
    assert r.mimetype == "text/event-stream" and b'"src":"relay"' in r.data

from datetime import date

import pytest

from core import collector, toss


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("toss", "TOSS", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    toss._token.update(value=None, exp=0.0)


def candle(d, o=100, c=105):
    return {"timestamp": f"{d}T00:00:00+09:00", "openPrice": str(o), "highPrice": "110", "lowPrice": "95",
            "closePrice": str(c), "volume": "1000", "currency": "KRW"}


@pytest.mark.parametrize("raw", ["id1:sec1", "id1,sec1", "id1 sec1", '"id1:sec1"', '{"client_id":"id1","client_secret":"sec1"}'])
def test_credential_formats(monkeypatch, raw):
    monkeypatch.setenv("toss", raw)
    assert toss.credentials() == ("id1", "sec1")


def test_two_env_vars(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "a")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "b")
    assert toss.credentials() == ("a", "b")


def test_single_token_gives_guidance_without_leaking(monkeypatch):
    monkeypatch.setenv("toss", "onlyonevalue")
    with pytest.raises(toss.TossError) as e:
        toss.credentials()
    assert "onlyonevalue" not in str(e.value)
    assert toss.configured() is False


def test_not_configured():
    assert toss.credentials() is None and not toss.configured()


def test_daily_candles_paginates_and_sorts(monkeypatch):
    monkeypatch.setenv("toss", "id1:sec1")
    posted = []
    monkeypatch.setattr(toss.requests, "post", lambda *a, **k: posted.append(k) or Resp(200, {"access_token": "T", "expires_in": 86400}))
    pages = [
        {"result": {"candles": [candle("2026-10-06"), candle("2026-10-05")], "nextBefore": "2026-10-02T00:00:00+09:00"}},
        {"result": {"candles": [candle("2026-10-02"), candle("2026-09-01")], "nextBefore": None}},
    ]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((params, headers))
        return Resp(200, pages[len(calls) - 1])
    monkeypatch.setattr(toss.requests, "get", fake_get)
    df = toss.daily_candles("005930", date(2026, 10, 1))
    assert list(df.index.strftime("%Y-%m-%d")) == ["2026-10-02", "2026-10-05", "2026-10-06"]
    assert calls[1][0]["before"] == "2026-10-02T00:00:00+09:00"
    assert calls[0][1]["Authorization"] == "Bearer T"
    assert len(posted) == 1  # 토큰은 한 번만 발급해 재사용


def test_401_reissues_token_then_succeeds(monkeypatch):
    monkeypatch.setenv("toss", "id1:sec1")
    n = {"post": 0}

    def fake_post(*a, **k):
        n["post"] += 1
        return Resp(200, {"access_token": f"T{n['post']}", "expires_in": 86400})
    seq = [Resp(401), Resp(200, {"result": {"candles": [], "nextBefore": None}})]
    monkeypatch.setattr(toss.requests, "post", fake_post)
    monkeypatch.setattr(toss.requests, "get", lambda *a, **k: seq.pop(0))
    assert toss.daily_candles("005930", date(2026, 1, 1)).empty
    assert n["post"] == 2


def test_error_message_has_no_secret(monkeypatch):
    monkeypatch.setenv("toss", "id1:sec1")
    monkeypatch.setattr(toss.requests, "post", lambda *a, **k: Resp(401, {"error": "invalid_client"}))
    with pytest.raises(toss.TossError) as e:
        toss.check()
    assert "sec1" not in str(e.value) and "invalid_client" in str(e.value)


def test_collector_falls_back_to_fdr(monkeypatch):
    import pandas as pd
    monkeypatch.setenv("toss", "id1:sec1")
    monkeypatch.setattr(collector.config, "SOURCE", "auto")

    def boom(*a, **k):
        raise toss.TossError("HTTP 500")
    monkeypatch.setattr(toss, "daily_candles", boom)
    fdr_df = pd.DataFrame({"Open": [1]}, index=pd.to_datetime(["2026-10-01"]))

    class F:
        def DataReader(self, code, start):
            return fdr_df
    monkeypatch.setattr(collector, "_fdr", lambda: F())
    assert collector.fetch_daily("005930", date(2026, 10, 1)) is fdr_df
    assert collector.LAST_FALLBACK["why"] == "HTTP 500"

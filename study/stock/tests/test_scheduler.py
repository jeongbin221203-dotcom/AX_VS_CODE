from datetime import date, datetime

import pytest

import config
from core import collector, db, scheduler


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def test_due_only_weekdays_after_time(fresh):
    assert not scheduler.due(datetime(2026, 10, 10, 17, 0))   # 토
    assert not scheduler.due(datetime(2026, 10, 7, 16, 9))    # 수 16:09
    assert scheduler.due(datetime(2026, 10, 7, 16, 10))       # 수 16:10


def test_due_once_per_day(fresh):
    with db.get_conn() as c:
        c.execute("INSERT INTO meta VALUES('last_auto_update','2026-10-07T16:20:00')")
    assert not scheduler.due(datetime(2026, 10, 7, 18, 0))
    assert scheduler.due(datetime(2026, 10, 8, 16, 30))


def test_last_trading_day_skips_weekend():
    assert collector.last_trading_day(date(2026, 10, 10)) == date(2026, 10, 9)  # 토 → 금
    assert collector.last_trading_day(date(2026, 10, 11)) == date(2026, 10, 9)  # 일 → 금


def test_no_api_call_when_already_latest(fresh, monkeypatch):
    import pandas as pd
    idx = pd.to_datetime([collector.last_trading_day().isoformat()])
    df = pd.DataFrame({"open": [1.0], "high": [2.0], "low": [1.0], "close": [2.0], "volume": [1]}, index=idx)
    collector.save_prices("X", df)
    monkeypatch.setattr(collector, "fetch_daily", lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출하면 안 됨")))
    assert collector.update_prices("X") == 0


def test_run_update_records_meta(fresh, monkeypatch):
    from core import signal_scan
    called = []
    monkeypatch.setattr(signal_scan, "run", lambda **k: called.append(1) or {})   # 실제 훑기(전 종목·data/signals.json 저장)는 하지 않음
    monkeypatch.setattr(collector, "update_symbols", lambda m="KRX": 0)
    monkeypatch.setattr(collector, "update_many", lambda codes, progress=None: {"ok": len(codes), "fail": 0})
    with db.get_conn() as c:
        c.execute("INSERT INTO symbols(code,name,marcap) VALUES('A','a',2),('B','b',1)")
        c.execute("INSERT INTO watchlist(code) VALUES('B')")
    assert scheduler.all_codes() == ["B", "A"]  # 관심종목 먼저
    assert scheduler.run_update() == {"ok": 2, "fail": 0}
    assert called == [1] and "신호 갱신" in scheduler.status()["msg"]       # 갱신 뒤 첫 화면 '신호'를 다시 훑는다
    assert not scheduler.due(datetime.now().replace(hour=23, minute=0))

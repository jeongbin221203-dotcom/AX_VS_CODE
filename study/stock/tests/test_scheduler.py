from datetime import date, datetime

import pytest

import config
from core import collector, db, scheduler


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def test_due_only_weekdays_after_time(fresh):
    assert scheduler.due(datetime(2026, 10, 7, 9, 0))         # 갱신 기록이 없으면 바로(첫 실행·기록 삭제 뒤)
    with db.get_conn() as c:
        c.execute("INSERT INTO meta VALUES('last_auto_update','2026-10-06T16:20:00')")   # 화
    assert not scheduler.due(datetime(2026, 10, 7, 16, 9))    # 수 16:09 — 아직 수요일 몫 전
    assert scheduler.due(datetime(2026, 10, 7, 16, 10))       # 수 16:10
    with db.get_conn() as c:
        c.execute("UPDATE meta SET value='2026-10-09T16:20:00' WHERE key='last_auto_update'")   # 금
    assert not scheduler.due(datetime(2026, 10, 10, 17, 0))   # 토 — 금요일 갱신이면 충분


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


def test_due_catches_up_missed_run(fresh):
    """PC 가 꺼져 있어 놓친 갱신은 켜지는 즉시 따라잡고, 주말에는 금요일 갱신이면 충분하다."""
    from datetime import datetime
    from core import db, scheduler
    with db.get_conn() as c:
        c.execute("DELETE FROM meta WHERE key='last_auto_update'")
    assert scheduler.due(datetime(2026, 10, 19, 7, 0))                       # 기록이 없으면 due
    def last(ts):
        with db.get_conn() as c:
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_auto_update',?)", (ts,))
    last("2026-10-16T16:27:00")                                              # 금요일 장 마감 뒤 갱신
    assert not scheduler.due(datetime(2026, 10, 17, 12, 0))                  # 토요일: 그대로
    assert not scheduler.due(datetime(2026, 10, 19, 8, 0))                   # 월요일 아침: 아직 월요일 16:10 전
    assert scheduler.due(datetime(2026, 10, 19, 16, 11))                     # 월요일 16:10 지나면 due
    last("2026-10-14T16:20:00")                                              # 목요일 갱신 뒤 금요일을 놓침
    assert scheduler.due(datetime(2026, 10, 17, 9, 0))                       # 토요일 아침에 켜져도 금요일 몫을 따라잡음


def test_publish_due_tracks_published_date(fresh, monkeypatch):
    from core import db, scheduler
    monkeypatch.setenv("STOCK_PUBLISH", "1")
    monkeypatch.setattr(scheduler, "_signals_date", lambda: "2026-10-16")
    with db.get_conn() as c:
        c.execute("DELETE FROM meta WHERE key='published_signals_date'")
    assert scheduler.publish_due()
    with db.get_conn() as c:
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('published_signals_date','2026-10-16')")
    assert not scheduler.publish_due()
    monkeypatch.setenv("STOCK_PUBLISH", "0")
    with db.get_conn() as c:
        c.execute("DELETE FROM meta WHERE key='published_signals_date'")
    assert not scheduler.publish_due()                                        # 게시를 안 쓰면 재시도도 없음


def test_unfinished_today_bar_is_not_saved():
    """장중에 받은 일봉의 '오늘' 봉은 저장하지 않는다(저장되면 다음 증분 수집이 그 봉을 영영 고치지 못함)."""
    import pandas as pd
    idx = pd.to_datetime(["2026-10-15", "2026-10-16"])
    df = pd.DataFrame({"open": [1.0, 2.0], "high": [2.0, 3.0], "low": [1.0, 2.0], "close": [2.0, 3.0], "volume": [1, 1]}, index=idx)
    during = datetime(2026, 10, 16, 11, 0)
    after = datetime(2026, 10, 16, 15, 45)
    assert list(collector.drop_unfinished_today(df, during).index) == [pd.Timestamp("2026-10-15")]
    assert len(collector.drop_unfinished_today(df, after)) == 2
    assert collector.drop_unfinished_today(df, datetime(2026, 10, 17, 11, 0)).equals(df)       # 토요일: 어제(금) 봉은 확정

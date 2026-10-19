"""장 마감 후 자동 갱신: 평일 UPDATE_AT(기본 16:10) 이후 하루 한 번 전체 종목 일봉을 이어 받는다.
장중(평일 09:10~15:35)에는 30분마다 오늘 미완성 일봉으로 신호를 다시 훑는다(core/live_scan.py, STOCK_LIVE_SCAN=0 이면 끔).

놓친 갱신은 앱이 켜지는 즉시 따라잡는다(PC 가 꺼져 있었거나 갱신이 실패한 경우) — 가장 최근 평일 16:10 이후의 갱신이 없으면 due.
휴대폰(Render) 게시(STOCK_PUBLISH=1)는 실패하면 PUBLISH_RETRY 초마다 다시 시도한다(신호 날짜가 게시된 날짜와 같아질 때까지).
"""
import os
import threading
import time
from datetime import datetime

from core import collector, db

UPDATE_AT = (16, 10)
PUBLISH_RETRY = 600        # 게시 실패 시 재시도 간격(초)
_pub = {"last_try": 0.0}
_state = {"running": False, "last": "", "msg": "", "done": 0, "total": 0}


def all_codes() -> list[str]:
    """관심종목 먼저, 그 다음 시가총액 순. 상장폐지·우선주 등 데이터가 없는 종목은 호출이 0행으로 끝난다."""
    with db.get_conn() as c:
        wl = [r["code"] for r in c.execute("SELECT code FROM watchlist")]
        rest = [r["code"] for r in c.execute("SELECT code FROM symbols ORDER BY marcap DESC")]
    return list(dict.fromkeys(wl + rest))


def run_update(limit: int = 0) -> dict:
    if _state["running"]:
        return {"error": "이미 실행 중"}
    _state.update(running=True, done=0, msg="종목 목록 갱신")
    try:
        collector.update_symbols("KRX")
        codes = all_codes()[: limit or None]
        _state["total"] = len(codes)

        def prog(i, n, code, msg):
            _state.update(done=i, msg=f"{code} {msg}")
        res = collector.update_many(codes, progress=prog)
        _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']}"
        _state["last"] = datetime.now().isoformat(timespec="seconds")
        with db.get_conn() as c:
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_auto_update',?)", (_state["last"],))
        try:                                                     # 첫 화면 '신호'(예측·AI 추천)를 새 일봉으로 다시 훑는다
            from core import signal_scan
            _state["msg"] += " · 신호 훑는 중"
            signal_scan.run(log=lambda m: _state.update(msg=f"신호: {m}"))
            _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']} · 신호 갱신"
            if os.environ.get("STOCK_PUBLISH") == "1":          # Render 스냅샷도 같이 올린다(.env 에 STOCK_PUBLISH=1)
                _state["msg"] += " · 스냅샷 " + run_publish()
        except Exception as e:
            _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']} · 신호 갱신 실패: {e}"
        try:                                                     # 연습용 잡주 후보 — 일봉 갱신 뒤 오늘 점수를 다시 계산(모델은 30일마다 재학습)
            from core import picks
            _state["msg"] += " · 잡주 후보 계산"
            picks.refresh(log=lambda m: _state.update(msg=f"잡주 후보: {m}"))
            if picks.wf_stale():                                 # 과거 차트용 걸어가며 검증 모델은 1년에 한 번(약 20분)
                picks.train_walkforward(log=lambda m: _state.update(msg=f"걸어가며 검증 모델: {m}"))
        except Exception as e:
            _state["msg"] += f" · 잡주 후보 실패: {e}
        try:                                                     # 신호 화면 '오늘 할 일'(시총 구간별 규칙: 살 종목·보유 중·매도 신호)
            from core import jb_daily
            _state["msg"] += " · 오늘 할 일 계산"
            jb_daily.run(log=lambda m: _state.update(msg=f"오늘 할 일: {m}"))
        except Exception as e:
            _state["msg"] += f" · 오늘 할 일 실패: {e}""
        try:                                                     # 하락 위험·전망 모델은 한 달마다 새 데이터로 다시 학습
            from core import drop_risk
            _state["msg"] += " · 위험 모델 점검"
            if drop_risk.retrain_if_due(log=lambda m: _state.update(msg=f"위험 모델: {m}")):
                _state["msg"] = f"완료: 성공 {res['ok']}, 실패 {res['fail']} · 위험 모델 재학습"
        except Exception as e:
            _state["msg"] += f" · 위험 모델 재학습 실패: {e}"
        return res
    except Exception as e:
        _state["msg"] = f"오류: {e}"
        return {"error": str(e)}
    finally:
        _state["running"] = False


def _expected_run(now: datetime) -> datetime:
    """지금까지 돌았어야 하는 가장 최근 갱신 시각 = 가장 최근 평일의 UPDATE_AT."""
    from datetime import timedelta
    t = now.replace(hour=UPDATE_AT[0], minute=UPDATE_AT[1], second=0, microsecond=0)
    if t > now:
        t -= timedelta(days=1)
    while t.weekday() >= 5:
        t -= timedelta(days=1)
    return t


def due(now: datetime | None = None) -> bool:
    """가장 최근 평일 갱신 시각 뒤에 갱신한 적이 없으면 True(그날 안 켜져 있었어도 켜지면 바로 따라잡는다)."""
    now = now or datetime.now()
    with db.get_conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key='last_auto_update'").fetchone()
    if not r:
        return True
    try:
        last = datetime.fromisoformat(r["value"])
    except ValueError:
        return True
    return last < _expected_run(now)


def _signals_date() -> str:
    import json
    from core import signal_scan
    try:
        return json.loads(signal_scan.SIGNALS_PATH.read_text(encoding="utf-8"))["meta"]["date"]
    except (OSError, ValueError, KeyError):
        return ""


def publish_due() -> bool:
    """휴대폰 게시가 켜져 있는데 최신 신호 날짜가 아직 게시되지 않았으면 True."""
    if os.environ.get("STOCK_PUBLISH") != "1":
        return False
    d = _signals_date()
    if not d:
        return False
    with db.get_conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key='published_signals_date'").fetchone()
    return not (r and r["value"] == d)


def run_publish() -> str:
    """스냅샷을 만들어 stock 브랜치에 올린다. 성공하면 게시한 신호 날짜를 기록한다."""
    from core import publish
    out = publish.run()
    d = _signals_date()
    if d and not str(out).startswith("실패"):
        with db.get_conn() as c:
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('published_signals_date',?)", (d,))
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('published_at',?)", (datetime.now().isoformat(timespec="seconds"),))
    return out


def start_background(check_every: int = 60):
    def loop():
        while True:
            try:
                if due() and not _state["running"]:
                    run_update()
                elif not _state["running"] and publish_due() and time.time() - _pub["last_try"] >= PUBLISH_RETRY:
                    _pub["last_try"] = time.time()                     # 게시만 실패한 경우 — 갱신은 다시 하지 않고 게시만 재시도
                    _state["msg"] = "스냅샷 게시 재시도: " + run_publish()
                elif not _state["running"] and os.environ.get("STOCK_LIVE_SCAN", "1") == "1":
                    from core import live_scan                              # 장중(평일 09:10~15:35) 30분마다 오늘 미완성 봉으로 신호를 다시 훑는다
                    if live_scan.due():
                        live_scan.start_thread()
            except Exception as e:  # 스레드가 죽지 않게
                _state["msg"] = f"스케줄러 오류: {e}"
                _pub["last_try"] = time.time()
            time.sleep(check_every)
    threading.Thread(target=loop, daemon=True, name="stock-scheduler").start()


def status() -> dict:
    return dict(_state)
